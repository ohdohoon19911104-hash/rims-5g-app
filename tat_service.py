import re
import errno
import time
import threading
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Any, List
import pandas as pd
from openpyxl import load_workbook
from functools import lru_cache
from datetime import datetime, timedelta
from database import supabase

router = APIRouter(prefix="/api", tags=["TAT Service"])

JP_HOLIDAYS = [
    "2025-01-01", "2025-01-13", "2025-02-11", "2025-02-23", "2025-02-24", "2025-03-20",
    "2025-04-29", "2025-05-03", "2025-05-04", "2025-05-05", "2025-05-06", "2025-07-21",
    "2025-08-11", "2025-09-15", "2025-09-23", "2025-10-13", "2025-11-03", "2025-11-23", "2025-11-24",
    "2026-01-01", "2026-01-12", "2026-02-11", "2026-02-23", "2026-03-20", "2026-04-29",
    "2026-05-03", "2026-05-04", "2026-05-05", "2026-05-06", "2026-07-20", "2026-08-11",
    "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12", "2026-11-03", "2026-11-23"
]

def clean_date_str(val: Any) -> str:
    if val is None or pd.isna(val): return ''
    s = str(val).strip()
    if s in ['-', 'None', 'nan', 'null', '', 'NaT']: return ''
    if ' ' in s: s = s.split(' ')[0]
    if 'T' in s: s = s.split('T')[0]
    return s.replace('/', '-')

def safe_cell(row: Any, idx: int, default: str = "") -> str:
    try:
        if idx < len(row) and pd.notna(row[idx]):
            v = str(row[idx]).strip()
            return "" if v.lower() in ["nan", "none", "null"] else v
    except: pass
    return default

# =====================================================================
# 事業者TAT(KDDI,UQ MUST 수리) 계산
# REQ] REPAIR IN Date(入庫日) 기준으로 주말/일본공휴일을 "포함해서"
# 달력 일수로 카운트 한다 (UQ 90일 / KDDI 60일 / NEC 45일 / DOCOMO 30일)
# =====================================================================
@lru_cache(maxsize=4096)
def calculate_carrier_deadline(repair_in_str: str, customer: str) -> str:
    clean_s = clean_date_str(repair_in_str)
    if not clean_s: return '-'
    try:
        dt = datetime.strptime(clean_s, "%Y-%m-%d")
        cust = str(customer or '').upper().strip()
        add_days = 30
        if "UQ" in cust: add_days = 90
        elif "KDDI" in cust: add_days = 60
        elif "NEC" in cust: add_days = 45
        elif "DOCOMO" in cust: add_days = 30
        return (dt + timedelta(days=add_days)).strftime("%Y-%m-%d")
    except: return '-'

# =====================================================================
# 13日TAT(修理センター MUST 수리) 계산
# REQ] REPAIR IN Date(入庫日) 기준으로 주말/일본공휴일을 "포함하지 않고"
# 영업일 기준으로 13일을 카운트 한다
# =====================================================================
@lru_cache(maxsize=4096)
def calculate_13_working_days_deadline(repair_in_str: str) -> str:
    clean_s = clean_date_str(repair_in_str)
    if not clean_s: return '-'
    try:
        cur = datetime.strptime(clean_s, "%Y-%m-%d")
        added = 0
        while added < 13:
            cur += timedelta(days=1)
            date_s = cur.strftime("%Y-%m-%d")
            if cur.weekday() < 5 and date_s not in JP_HOLIDAYS:
                added += 1
        return cur.strftime("%Y-%m-%d")
    except: return '-'

# =========================================================================
# Supabase 실제 테이블의 컬럼 판별 로직
# (존재하지 않는 컬럼을 보내면 PGRST204 오류로 업로드 전체가 실패하기 때문에
#  실제 존재하는 컬럼만 자동 판별해서 저장한다. SQL로 컬럼을 추가하면
#  업로드 시점에 자동으로 해당 컬럼들의 저장이 시작된다)
# =========================================================================

# 현재 DB 테이블에 존재하는 기본 컬럼(2026-10-01 검증 완료)
TAT_CORE_COLUMNS = [
    "wq", "customer", "tech_category", "product_category", "sn",
    "defect_type", "over_category", "status",
    "carrier_deadline", "tat13_deadline", "period_return_can",
    "reason", "reproduce_detail", "fault_location", "sys_manager", "flag_mark"
]

# add_missing_columns.sql 실행 후 자동으로 저장 대상에 포함되는 확장 컬럼
TAT_EXTENDED_COLUMNS = [
    "kddi_uq_tat", "kddi_uq_must", "req_receive_date", "req_repair_in_date",
    "ret_repaired_out_date", "center_tat", "center_must", "countermeasure2",
    "du_ru_type", "pba_name", "pba_recv_date", "pba_re_recv_date",
    "repair_pos", "ship_status", "remark", "pba_in", "pba_out",
    "pba_open_close", "sub_alarm", "sub_date", "summary_use", "delay_reason", "repair_can"
]

# 판별 결과 캐시 (폴링/업로드가 같은 판별을 반복하지 않도록)
_live_col_cache: dict = {}

def _probe_existing_columns(table_name: str, candidates: List[str]) -> set:
    """후보 컬럼 중 실제 테이블에 존재하는 것만 추려서 반환 (결과 캐시)"""
    cached = _live_col_cache.get(table_name)
    if cached is not None:
        return cached
    remaining = list(candidates)
    existing: List[str] = []
    for _ in range(len(candidates) + 1):
        if not remaining: break
        try:
            _execute_with_retry(lambda: supabase.table(table_name).select(",".join(remaining)).limit(1).execute())
            existing = remaining
            break
        except Exception as e:
            msg = str(e)
            m = re.search(r"'([A-Za-z0-9_]+)' column of", msg) or re.search(r"column [A-Za-z0-9_]+\.([A-Za-z0-9_]+) does not exist", msg)
            if not m:
                raise
            missing = m.group(1)
            if missing in remaining: remaining.remove(missing)
    if existing:
        _live_col_cache[table_name] = set(existing)
    return set(existing)

def _filter_records_by_live_columns(records: List[dict], table_name: str, core_columns: List[str], extended_columns: List[str]) -> List[dict]:
    """존재하는 컬럼만 포함하도록 레코드를 정제 (판별 실패 시 기본 컬럼만)"""
    live = _probe_existing_columns(table_name, core_columns + extended_columns)
    allowed = live if live else set(core_columns)
    if not live:
        print(f"[TAT upload] column check failed -> save with core columns only")
    else:
        print(f"[TAT upload] saving {len(allowed)} existing columns / skipped {len(set(core_columns + extended_columns) - allowed)} not-defined columns")
    return [{k: rec[k] for k in rec if k in allowed} for rec in records]

def _chunked_list(seq: List[Any], size: int = 500):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

_upload_lock = threading.Lock()

def _is_temporary_error(error: Exception) -> bool:
    current = error
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, OSError) and current.errno in (errno.EAGAIN, errno.ETIMEDOUT, errno.ECONNRESET):
            return True
        if isinstance(current, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code in (429, 502, 503, 504):
            return True
        if '[Errno 11]' in str(current) or 'Resource temporarily unavailable' in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False

def _execute_with_retry(operation):
    """一時的な通信・資源エラーだけを最大5回実行します。"""
    for attempt in range(5):
        try:
            return operation()
        except Exception as error:
            if attempt == 4 or not _is_temporary_error(error):
                raise
            time.sleep(min(2 ** attempt, 8))

def _upsert_with_fallback(table_name: str, records: List[dict], conflict_col: str) -> None:
    """同じキーで再試行するため、同一バッチの重複登録を防ぎます。"""
    unique_records = {r[conflict_col]: r for r in records}
    for batch in _chunked_list(list(unique_records.values()), 200):
        _execute_with_retry(lambda: supabase.table(table_name).upsert(
            batch, on_conflict=conflict_col, returning="minimal"
        ).execute())

@router.get("/tat-data")
def get_tat_data():
    try:
        all_data = []
        step = 1000
        start = 0
        while True:
            res = supabase.table("tat_data").select("*").order("id", desc=False).range(start, start + step - 1).execute()
            rows = res.data or []
            all_data.extend(rows)
            if len(rows) < step:
                break
            start += step

        result = []
        for r in all_data:
            repair_in = clean_date_str(r.get("req_repair_in_date") or r.get("req_receive_date"))
            cust = r.get("customer", "")
            # 事業者TAT: 엑셀 H열 값 우선, 없으면 입고일 기준 계산값(주말/공휴일 포함 달력일수)
            carrier_dl = r.get("kddi_uq_must") or r.get("carrier_deadline") or calculate_carrier_deadline(repair_in, cust)
            # 13日TAT: 엑셀 M열 값 우선, 없으면 입고일 기준 계산값(주말/공휴일 제외 영업일 13일)
            tat13_dl = r.get("center_must") or r.get("tat13_deadline") or calculate_13_working_days_deadline(repair_in)

            result.append({
                "rowIdx": r["id"],
                "wq": r.get("wq", "-"),
                "customer": cust,
                "techCategory": r.get("tech_category", "-"),
                "productCategory": r.get("product_category", "-"),
                "sn": r.get("sn", "-"),
                "defectType": r.get("defect_type", ""),
                "overCategory": r.get("over_category", "-"),
                "carrierDeadline": carrier_dl,
                "kddiUqMust": carrier_dl,
                "reqReceiveDate": clean_date_str(r.get("req_receive_date")),
                "reqRepairInDate": repair_in,
                "retRepairedOutDate": clean_date_str(r.get("ret_repaired_out_date")),
                "centerTat": r.get("center_tat", "-"),
                "centerMust": tat13_dl,
                "tat13Deadline": tat13_dl,
                "status": r.get("status", "-"),
                "reproduceDetail": r.get("reproduce_detail", ""),
                "faultLocation": r.get("fault_location", ""),
                "countermeasure2": r.get("countermeasure2", ""),
                "sysManager": r.get("sys_manager", ""),
                "duRuType": r.get("du_ru_type", ""),
                "pbaName": r.get("pba_name", ""),
                "pbaRecvDate": clean_date_str(r.get("pba_recv_date")),
                "pbaReRecvDate": clean_date_str(r.get("pba_re_recv_date")),
                # 대장관리 기능과 동일한 이름(period_return_can)으로 연동되는 체크값
                "periodReturnCan": bool(r.get("period_return_can")),
                "repairPos": r.get("repair_pos", ""),
                "shipStatus": r.get("ship_status", ""),
                "remark": r.get("remark", ""),
                "pbaIn": r.get("pba_in", ""),
                "pbaOut": clean_date_str(r.get("pba_out")),
                "pbaOpenClose": r.get("pba_open_close", ""),
                "flagMark": r.get("flag_mark", ""),
                "subAlarm": r.get("sub_alarm", ""),
                "subDate": clean_date_str(r.get("sub_date")),
                "summaryUse": r.get("summary_use", ""),
                # 대장관리 기능과 동일한 이름(delay_reason)으로 연동되는 지연 사유
                "delayReason": r.get("delay_reason", ""),
                "reason": r.get("reason", ""),
                # 画面用の計算値とは分離し、原本の33項目を返します。
                "excelValues": [r.get(column) if r.get(column) is not None else "" for column in (
                    "wq", "customer", "tech_category", "product_category", "sn",
                    "defect_type", "kddi_uq_tat", "kddi_uq_must", "req_receive_date",
                    "req_repair_in_date", "ret_repaired_out_date", "center_tat", "center_must",
                    "status", "reproduce_detail", "fault_location", "countermeasure2",
                    "sys_manager", "du_ru_type", "pba_name", "pba_recv_date", "pba_re_recv_date",
                    "repair_can", "repair_pos", "ship_status", "remark", "pba_in", "pba_out",
                    "pba_open_close", "flag_mark", "sub_alarm", "sub_date", "summary_use"
                )]
            })
        return result
    except Exception as e:
        print(f"Error fetching tat data: {e}")
        return []

@router.post("/upload-excel")
def upload_excel(file: UploadFile = File(...)):
    workbook = None
    saved_count = 0
    if not _upload_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="別の日報アップロードを処理中です。完了後に再実行してください。")
    try:
        # 毎回最新のカラムを確認し、原本の項目を省略しません。
        _live_col_cache.pop("tat_data", None)
        required = (set(TAT_CORE_COLUMNS) - {"reason"}) | set(TAT_EXTENDED_COLUMNS)
        live = _probe_existing_columns("tat_data", sorted(required))
        missing = required - live
        if missing:
            raise HTTPException(
                status_code=400,
                detail="必要なデータベース項目が不足しています。SQLを実行してください: " + ", ".join(sorted(missing))
            )
        file.file.seek(0)
        if (file.filename or "").lower().endswith(".xls"):
            dataframe = pd.read_excel(file.file, header=None)
            rows = dataframe.itertuples(index=False, name=None)
        else:
            workbook = load_workbook(file.file, read_only=True, data_only=True)
            sheet = workbook.worksheets[0]
            rows = sheet.iter_rows(values_only=True)

        header_found = False
        records = []
        for row in rows:
            if not header_found:
                if "修理" in safe_cell(row, 0) or "Customer" in safe_cell(row, 1):
                    header_found = True
                continue
            wq_val = safe_cell(row, 0)
            if not wq_val or wq_val == '-' or "修理" in wq_val or wq_val.upper() == "WQ": continue

            cust = safe_cell(row, 1)
            sn_val = safe_cell(row, 4)
            repair_in = clean_date_str(safe_cell(row, 9)) or clean_date_str(safe_cell(row, 8))

            # W열 (Col 22: 수리가능여부 O, X) -> 대장관리와 동일한 이름 period_return_can 으로 저장
            can_return_raw = safe_cell(row, 22).strip()

            records.append({
                # A열 ~ AG열: 엑셀 원본과 동일하게 전체 저장
                "wq": wq_val,                                                          # A
                "customer": cust,                                                      # B
                "tech_category": safe_cell(row, 2),                                    # C
                "product_category": safe_cell(row, 3),                                 # D
                "sn": sn_val,                                                          # E
                "defect_type": safe_cell(row, 5),     # F
                "kddi_uq_tat": safe_cell(row, 6),                                      # G
                "kddi_uq_must": clean_date_str(safe_cell(row, 7)),                     # H (事業者TAT 표시 우선값)
                "req_receive_date": clean_date_str(safe_cell(row, 8)),                 # I
                "req_repair_in_date": clean_date_str(safe_cell(row, 9)),                                       # J (TAT 계산 기준 입고일)
                "ret_repaired_out_date": clean_date_str(safe_cell(row, 10)),           # K
                "over_category": safe_cell(row, 11),                                   # L (超過区分 표시값)
                "center_tat": safe_cell(row, 11),                                      # L (동일 값)
                "center_must": clean_date_str(safe_cell(row, 12)),                     # M (13日TAT 표시 우선값)
                "status": safe_cell(row, 13),                                          # N
                "reproduce_detail": safe_cell(row, 14),                                # O
                "fault_location": safe_cell(row, 15),                                  # P
                "countermeasure2": safe_cell(row, 16),                                 # Q
                "sys_manager": safe_cell(row, 17),                                     # R
                "du_ru_type": safe_cell(row, 18),                                      # S
                "pba_name": safe_cell(row, 19),                                        # T
                "pba_recv_date": clean_date_str(safe_cell(row, 20)),                   # U
                "pba_re_recv_date": clean_date_str(safe_cell(row, 21)),                # V
                "period_return_can": can_return_raw.upper() in ["O", "OK", "TRUE", "1", "YES"],  # W
                "repair_can": can_return_raw,
                "repair_pos": safe_cell(row, 23),                                      # X
                "ship_status": safe_cell(row, 24),                                     # Y
                "remark": safe_cell(row, 25),                                          # Z
                # AA열(PBA_IN) -> 대장관리와 동일한 이름 delay_reason 으로 저장 (遅延理由 표시값)
                "delay_reason": safe_cell(row, 26),                                    # AA
                "pba_in": safe_cell(row, 26),                          # AA (원본 동일 저장용)
                "pba_out": clean_date_str(safe_cell(row, 27)),                         # AB
                "pba_open_close": safe_cell(row, 28),                                  # AC
                "flag_mark": safe_cell(row, 29),                                       # AD
                "sub_alarm": safe_cell(row, 30),                                       # AE
                "sub_date": clean_date_str(safe_cell(row, 31)),                        # AF
                "summary_use": safe_cell(row, 32),                                     # AG
                # 입고일(J열) 기준 계산값: H/M열이 비어 있을 때 표시 폴백으로 사용
                "carrier_deadline": calculate_carrier_deadline(repair_in, cust),
                "tat13_deadline": calculate_13_working_days_deadline(repair_in)
            })

            if len(records) >= 200:
                _upsert_with_fallback("tat_data", records, "wq")
                saved_count += len(records)
                records.clear()

        if not header_found:
            raise HTTPException(status_code=400, detail="簡易版日報の見出し行が見つかりません。")
        if records:
            _upsert_with_fallback("tat_data", records, "wq")
            saved_count += len(records)
        if saved_count == 0:
            raise HTTPException(status_code=400, detail="保存対象のデータがありません。")
        return {"inserted": saved_count, "ignored": 0, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"簡易版日報の解析・保存に失敗しました。保存済み: {saved_count}件。再アップロードで続行できます。詳細: {str(e)}"
        )
    finally:
        try:
            if workbook is not None:
                workbook.close()
        finally:
            _upload_lock.release()
