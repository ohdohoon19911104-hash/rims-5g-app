import re
import threading
import asyncio
import json
from functools import lru_cache
from fastapi.responses import StreamingResponse
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Any, List
import pandas as pd
from datetime import datetime, timedelta
from database import supabase

router = APIRouter(prefix="/api", tags=["Ledger Service"])

_change_revision = 0
_change_guard = threading.Lock()

def notify_ledger_change():
    global _change_revision
    with _change_guard:
        _change_revision += 1

@router.get("/data-change-events")
async def data_change_events():
    async def events():
        last = _change_revision
        yield "event: ready\ndata: {}\n\n"
        idle = 0
        while True:
            await asyncio.sleep(0.2)
            revision = _change_revision
            if revision != last:
                last = revision
                idle = 0
                yield "event: changed\ndata: " + json.dumps({"revision": revision}) + "\n\n"
            else:
                idle += 1
                if idle >= 75:
                    idle = 0
                    yield ": keepalive\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

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

def calculate_working_days(start_date_str: str) -> int:
    return _calculate_working_days_cached(start_date_str, datetime.now().strftime("%Y-%m-%d"))

@lru_cache(maxsize=4096)
def _calculate_working_days_cached(start_date_str: str, today: str) -> int:
    clean_s = clean_date_str(start_date_str)
    if not clean_s: return 0
    try:
        start = datetime.strptime(clean_s, "%Y-%m-%d")
        end = datetime.strptime(today, "%Y-%m-%d")
        if start > end: return 0
        count = 0
        cur = start
        while cur <= end:
            if cur.weekday() < 5 and cur.strftime("%Y-%m-%d") not in JP_HOLIDAYS: count += 1
            cur += timedelta(days=1)
        return count
    except: return 0

def classify_target_sheet(customer: str, tech_cat: str, prod_cat: str, cat_name: str, model_code: str) -> str:
    cust = str(customer or '').upper().strip()
    tech = str(tech_cat or '').upper().strip()
    prod = str(prod_cat or '').upper().strip()
    cat = str(cat_name or '').upper().strip()
    model = str(model_code or '').upper().strip()

    if "BBU" in cat or "BBU" in prod or "DU_" in prod or "BBU" in model: return "BBU"
    if "PICO" in prod or "PICO" in cat: return "PICO"
    if "DOCOMO" in cust: return "docomo"
    if tech == "5G" or "NEC" in cust: return "5G"
    if "UQ" in cust: return "UQ RRH"
    if "KDDI" in cust: return "KDDI RRH"
    return "BBU"

# =========================================================================
# Supabase 실제 테이블의 컬럼 판별 로직
# (존재하지 않는 컬럼을 보내면 PGRST204 오류로 업로드 전체가 실패하기 때문에
#  실제 존재하는 컬럼만 자동 판별해서 저장한다)
# =========================================================================

# 확정판 Rawdata 구조 기준 기본 컬럼(2026-10-01 確定版 파일과 대조 검증 완료)
LEDGER_CORE_COLUMNS = [
    "request_no", "sheet_name", "status", "sn_large", "sn_small", "status2",
    "alarm_name", "category_name", "customer", "tech_category", "model_code",
    "part_code", "part_desc", "symptom", "re_defect", "no_of_request",
    "difference_day", "receive_date", "received_date", "is_outbound"
]

# add_missing_columns.sql 실행 후 자동으로 저장/연동 대상에 포함되는 컬럼
# (수리TAT 기능과 동일한 이름으로 연동: period_return_can, delay_reason)
LEDGER_EXTENDED_COLUMNS = ["period_return_can"]

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
            supabase.table(table_name).select(",".join(remaining)).limit(1).execute()
            existing = remaining
            break
        except Exception as e:
            msg = str(e)
            m = re.search(r"'([A-Za-z0-9_]+)' column of", msg) or re.search(r"column [A-Za-z0-9_]+\.([A-Za-z0-9_]+) does not exist", msg)
            if not m: break
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
        print(f"[Ledger upload] column check failed -> save with core columns only")
    else:
        print(f"[Ledger upload] saving {len(allowed)} existing columns / skipped {len(set(core_columns + extended_columns) - allowed)} not-defined columns")
    return [{k: rec[k] for k in rec if k in allowed} for rec in records]

def _chunked_list(seq: List[Any], size: int = 500):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

def _upsert_with_fallback(table_name: str, records: List[dict], conflict_col: str) -> None:
    """일괄 upsert 실행 실패 시(on_conflict 대상 컬럼에 유니크 제약이 없는 경우 등)
    기존 키 조회 방식의 수동 upsert로 전환해서 저장한다"""
    try:
        supabase.table(table_name).upsert(records, on_conflict=conflict_col).execute()
        return
    except Exception as e:
        print(f"[Ledger upload] bulk upsert failed -> manual sync: {e}")

    keys = [str(r[conflict_col]) for r in records if r.get(conflict_col) not in (None, "")]
    existing_keys = set()
    for chunk in _chunked_list(keys):
        if not chunk: continue
        res = supabase.table(table_name).select(conflict_col).in_(conflict_col, chunk).execute()
        for r in (res.data or []):
            existing_keys.add(str(r.get(conflict_col)))

    inserts = [r for r in records if str(r.get(conflict_col)) not in existing_keys]
    updates = [r for r in records if str(r.get(conflict_col)) in existing_keys]

    if inserts:
        supabase.table(table_name).insert(inserts).execute()
    for r in updates:
        supabase.table(table_name).update(r).eq(conflict_col, r[conflict_col]).execute()

@router.get("/ledger-data")
def get_ledger_data(sheet_name: str = "BBU", mode: str = "in_progress", is_outbound: bool = False, offset: int = 0, limit: int = 0, known_total: int = -1):
    try:
        target_outbound = True if mode == "completed" or is_outbound else False

        all_data = []
        step = 1000
        start = max(offset, 0) if limit else 0
        total = None
        if limit:
            step = min(max(limit, 1), 1000)
        # is_outbound 컬럼이 테이블에 없는 경우(수동 DB 작업 등)에도
        # 조회 오류로 화면 전체가 깨지지 않도록 존재 여부를 확인 후 필터링한다
        live_cols = _probe_existing_columns("ledger_data", LEDGER_CORE_COLUMNS + LEDGER_EXTENDED_COLUMNS)
        while True:
            q = supabase.table("ledger_data").select("*", count="exact" if limit and known_total < 0 else None).eq("sheet_name", sheet_name)
            if "is_outbound" in live_cols:
                q = q.eq("is_outbound", target_outbound)
            res = q.order("id", desc=False).range(start, start + step - 1).execute()
            if limit:
                total = known_total if known_total >= 0 else res.count
            rows = res.data or []
            all_data.extend(rows)
            if limit or len(rows) < step:
                break
            start += step

        result = []
        for i, r in enumerate(all_data):
            rec_date = clean_date_str(r.get("receive_date"))
            calc_tat = calculate_working_days(rec_date)

            # 수리TAT 기능과 동일한 이름(period_return_can)으로 연동되는 체크값
            p_can = r.get("period_return_can")
            is_checked = True if p_can in [True, "true", "True", 1, "1", "OK", "ok"] else False

            result.append({
                "rowIdx": r["id"], "no": i + 1 + (offset if limit else 0), "status": r.get("status", "再現試験待機"),
                "cartNo": r.get("cart_no", ""), "chkLabel": r.get("chk_label", False),
                "snLarge": r.get("sn_large", ""), "snSmall": r.get("sn_small", ""), "internalInfo": r.get("internal_info", ""),
                "status2": r.get("status2", "-"), "alarmName": r.get("alarm_name", "-"), "categoryName": r.get("category_name", "-"),
                "tat": calc_tat, "receiveDate": rec_date, "chkInboundLog": r.get("chk_inbound_log", False),
                "chkFmHisLog": r.get("chk_fm_his_log", False), "chkCal": r.get("chk_cal", False), "chkCharacteristic": r.get("chk_characteristic", False),
                "chkNtfLog": r.get("chk_ntf_log", False), "chkOutboundLog": r.get("chk_outbound_log", False), "chkLock": r.get("chk_lock", False),
                "reproduceResult": r.get("reproduce_result", ""), "reproduceResult1_1": r.get("reproduce_result1_1", ""),
                "reproduceResult1_2": r.get("reproduce_result1_2", ""), "reproduceResult2_1": r.get("reproduce_result2_1", ""),
                "reproduceResult2_2": r.get("reproduce_result2_2", ""), "partCodeSelect": r.get("part_code_select", ""),
                "serialInput": r.get("serial_input", ""), "repairDetail": r.get("repair_detail", ""), "manager": r.get("manager", "未定"),
                "requestNo": r.get("request_no", ""), "customer": r.get("customer", ""), "techCategory": r.get("tech_category") or "LTE",
                "modelCode": r.get("model_code", ""), "partCode": r.get("part_code", ""), "partDesc": r.get("part_desc", ""),
                "symptom": r.get("symptom", ""), "reDefect": r.get("re_defect", ""), "noOfRequest": r.get("no_of_request", ""),
                "differenceDay": r.get("difference_day", ""), "receivedDate": clean_date_str(r.get("received_date")), 
                # 대장관리의 期間内返却可否 (수리TAT의 period_return_can과 동일 이름으로 연동)
                "periodReturnCan": is_checked,
                # 대장관리의 遅延理由 (수리TAT의 delay_reason과 동일 이름으로 연동)
                "delayReason": r.get("delay_reason", "")
            })
        return {"rows": result, "total": total} if limit else result
    except Exception as e:
        print(f"Error fetching ledger data: {e}")
        return []

_ledger_upload_lock = threading.Lock()

@router.post("/upload-ledger-excel")
def upload_ledger_excel(file: UploadFile = File(...)):
    if not _ledger_upload_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="台帳取り込みを処理中です。完了後に再実行してください。")
    try:
        df = pd.read_excel(file.file, header=None)
        header_idx = 0
        for idx, row in df.iterrows():
            first_cell = safe_cell(row, 0)
            if first_cell == "NO" or first_cell.startswith("修理"):
                header_idx = idx
                break

        data_df = df.iloc[header_idx + 1:].copy()
        records = []
        for idx, row in data_df.iterrows():
            req_no = safe_cell(row, 16) or safe_cell(row, 15) or safe_cell(row, 0)
            if not req_no or req_no == '-' or "REQUEST" in req_no.upper() or "NO" in req_no.upper(): continue

            cust = safe_cell(row, 19)
            tech = safe_cell(row, 26) or "LTE"
            prod_s = safe_cell(row, 29) or safe_cell(row, 5)
            cat_name = safe_cell(row, 27) or safe_cell(row, 28) or prod_s
            model_code = safe_cell(row, 30)

            target_sheet = classify_target_sheet(cust, tech, prod_s, cat_name, model_code)
            repair_in_dt = clean_date_str(safe_cell(row, 7)) or clean_date_str(safe_cell(row, 110))
            st2 = safe_cell(row, 3, "REQ] REPAIR IN")

            alarm_val = safe_cell(row, 4)
            part_desc_val = safe_cell(row, 32)
            symptom_val = safe_cell(row, 46) or alarm_val

            records.append({
                "request_no": req_no, 
                "sheet_name": target_sheet, 
                "status": "再現試験待機",
                "sn_large": safe_cell(row, 1), 
                "sn_small": safe_cell(row, 2), 
                "status2": st2,
                "alarm_name": alarm_val, 
                "category_name": prod_s,
                "customer": cust, 
                "tech_category": tech, 
                "model_code": model_code,
                "part_code": safe_cell(row, 31), 
                "part_desc": part_desc_val, 
                "symptom": symptom_val,
                "re_defect": safe_cell(row, 48, "N"), 
                "no_of_request": safe_cell(row, 49, "1"),
                "difference_day": safe_cell(row, 50, "-"), 
                "receive_date": repair_in_dt,
                "received_date": clean_date_str(safe_cell(row, 56)), 
                # 수리TAT 기능과 동일한 이름(period_return_can)으로 연동
                "period_return_can": False,
                "is_outbound": False
            })

        existing = {}
        for keys in _chunked_list(list({r["request_no"] for r in records}), 200):
            start = 0
            while True:
                response = supabase.table("ledger_data").select("request_no,sn_large,sn_small").in_("request_no", keys).range(start, start + 999).execute()
                found = response.data or []
                for row in found:
                    existing[row["request_no"]] = row
                if len(found) < 1000:
                    break
                start += 1000
        new_records = []
        ignored = 0
        for row in records:
            key = row["request_no"]
            previous = existing.get(key)
            if previous is not None:
                if (str(previous.get("sn_large") or "").strip(), str(previous.get("sn_small") or "").strip()) != (row["sn_large"], row["sn_small"]):
                    raise HTTPException(status_code=409, detail=f"同じWQ番号でシリアルが異なります。既存データは変更しません: {key}")
                ignored += 1
                continue
            existing[key] = row
            new_records.append(row)
        if new_records:
            filtered = _filter_records_by_live_columns(new_records, "ledger_data", LEDGER_CORE_COLUMNS, LEDGER_EXTENDED_COLUMNS)
            for batch in _chunked_list(filtered, 200):
                supabase.table("ledger_data").insert(batch, returning="minimal").execute()
        if new_records:
            notify_ledger_change()
        return {"inserted": len(new_records), "ignored": ignored, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ファイル解析・DB同期エラー: {str(e)}")
    finally:
        _ledger_upload_lock.release()

class LedgerStatusUpdate(BaseModel):
    sheetName: str
    rowIdx: int
    newStatus: str

@router.post("/update-ledger-status")
def update_ledger_status(req: LedgerStatusUpdate):
    try:
        is_out = (req.newStatus == '出庫済み')
        status2 = 'CLOSE' if is_out else 'REQ] REPAIR IN'
        supabase.table("ledger_data").update({"status": req.newStatus, "status2": status2, "is_outbound": is_out}).eq("id", req.rowIdx).execute()
        notify_ledger_change()
        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}

class LedgerCellFieldUpdate(BaseModel):
    sheetName: str
    rowIdx: int
    fieldName: str
    fieldValue: Any

@router.post("/update-ledger-cell-field")
def update_ledger_cell_field(req: LedgerCellFieldUpdate):
    try:
        field_map = {
            "cartNo": "cart_no", "chkLabel": "chk_label", "internalInfo": "internal_info",
            "reproduceResult1_1": "reproduce_result1_1", "reproduceResult1_2": "reproduce_result1_2",
            "reproduceResult2_1": "reproduce_result2_1", "reproduceResult2_2": "reproduce_result2_2",
            "partCodeSelect": "part_code_select", "serialInput": "serial_input",
            "repairDetail": "repair_detail", "manager": "manager", "delayReason": "delay_reason",
            "reproduceResult": "reproduce_result", "chkInboundLog": "chk_inbound_log",
            "chkFmHisLog": "chk_fm_his_log", "chkCal": "chk_cal", "chkCharacteristic": "chk_characteristic",
            "chkNtfLog": "chk_ntf_log", "chkOutboundLog": "chk_outbound_log", "chkLock": "chk_lock",
            "periodReturnCan": "period_return_can"
        }
        db_field = field_map.get(req.fieldName, req.fieldName)
        bool_val = bool(req.fieldValue) if req.fieldName == "periodReturnCan" else req.fieldValue

        # 1. ledger_data 테이블 업데이트
        supabase.table("ledger_data").update({db_field: bool_val}).eq("id", req.rowIdx).execute()

        # 2. 期間内返却可否 체크 시 수리TAT(tat_data)에도 동일 이름 컬럼으로 실시간 연동
        #    (대장 S/N = TAT 시리얼, 대장 Request No. = TAT WQ번호 기준 매칭)
        if req.fieldName == "periodReturnCan":
            res = supabase.table("ledger_data").select("sn_large, sn_small, request_no").eq("id", req.rowIdx).execute()
            if res.data:
                item = res.data[0]
                sn_l = str(item.get("sn_large") or '').strip()
                sn_s = str(item.get("sn_small") or '').strip()
                req_no = str(item.get("request_no") or '').strip()

                if req_no:
                    for serial in set(filter(None, (sn_l, sn_s))):
                        supabase.table("tat_data").update({"period_return_can": bool_val}).eq("wq", req_no).eq("sn", serial).execute()

        notify_ledger_change()
        return {"success": True}
    except Exception as e:
        print(f"Update Field Error: {e}")
        return {"success": False, "message": str(e)}
