import re
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Any, List
import pandas as pd
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

        target_dt = dt + timedelta(days=add_days)
        return target_dt.strftime("%Y-%m-%d")
    except: return '-'

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
# Supabase 実テーブルのカラム判定
# (実在しないカラムを送信すると PGRST204 エラーでアップロード全体が
#  失敗するため、実在するカラムだけを抽出して保存する)
# =========================================================================

# 実テーブルに確実に存在するコアカラム(2026-10-01 検証済み)
TAT_CORE_COLUMNS = [
    "wq", "customer", "tech_category", "product_category", "sn",
    "defect_type", "over_category", "status",
    "carrier_deadline", "tat13_deadline",
    "reason", "reproduce_detail", "fault_location", "sys_manager", "flag_mark"
]

# 将来テーブルに追加カラム(ADD COLUMN)を実施した場合に自動で保存対象へ含める拡張カラム
TAT_EXTENDED_COLUMNS = [
    "kddi_uq_must", "req_receive_date", "req_repair_in_date", "ret_repaired_out_date",
    "center_tat", "center_must", "countermeasure2", "du_ru_type", "pba_name",
    "pba_recv_date", "pba_re_recv_date", "repair_pos", "ship_status",
    "remark", "pba_in", "pba_out", "pba_open_close", "sub_alarm", "sub_date", "summary_use"
]

def _probe_existing_columns(table_name: str, candidates: List[str]) -> set:
    """候補カラムの中で実テーブルに存在するものだけを抽出して返す"""
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
    return set(existing)

def _filter_records_by_live_columns(records: List[dict], table_name: str, core_columns: List[str], extended_columns: List[str]) -> List[dict]:
    """実在カラムのみを含むようにレコードを絞り込む(判定できない場合はコアカラムのみ)"""
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

def _upsert_with_fallback(table_name: str, records: List[dict], conflict_col: str) -> None:
    """一括 upsert 失敗時(on_conflict 対象列にユニーク制約がない等)は
    既存キーの照合による手動 upsert に切り替えて保存する"""
    try:
        supabase.table(table_name).upsert(records, on_conflict=conflict_col).execute()
        return
    except Exception as e:
        print(f"[TAT upload] bulk upsert failed -> manual sync: {e}")

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

@router.get("/tat-data")
async def get_tat_data():
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
            carrier_dl = r.get("kddi_uq_must") or r.get("carrier_deadline") or calculate_carrier_deadline(repair_in, cust)
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
                "repairPos": r.get("repair_pos", ""),
                "shipStatus": r.get("ship_status", ""),
                "remark": r.get("remark", ""),
                "pbaIn": clean_date_str(r.get("pba_in")),
                "pbaOut": clean_date_str(r.get("pba_out")),
                "pbaOpenClose": r.get("pba_open_close", ""),
                "flagMark": r.get("flag_mark", ""),
                "subAlarm": r.get("sub_alarm", ""),
                "subDate": clean_date_str(r.get("sub_date")),
                "summaryUse": r.get("summary_use", ""),
                "reason": r.get("reason", "")
            })
        return result
    except Exception as e:
        print(f"Error fetching tat data: {e}")
        return []

@router.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    try:
        df = pd.read_excel(file.file, header=None)
        header_idx = 0
        for idx, row in df.iterrows():
            first_cell = safe_cell(row, 0)
            if "修理" in first_cell or "Customer" in str(safe_cell(row, 1)):
                header_idx = idx
                break

        data_df = df.iloc[header_idx + 1:].copy()
        records = []
        for idx, row in data_df.iterrows():
            wq_val = safe_cell(row, 0)
            if not wq_val or wq_val == '-' or "修理" in wq_val or wq_val.upper() == "WQ": continue

            cust = safe_cell(row, 1)
            sn_val = safe_cell(row, 4)
            repair_in = clean_date_str(safe_cell(row, 9)) or clean_date_str(safe_cell(row, 8))

            records.append({
                "wq": wq_val,
                "customer": cust,
                "tech_category": safe_cell(row, 2),
                "product_category": safe_cell(row, 3),
                "sn": sn_val,
                "defect_type": safe_cell(row, 5, "Function / Performance Defect"),
                "over_category": safe_cell(row, 6),
                "kddi_uq_must": safe_cell(row, 7),
                "req_receive_date": clean_date_str(safe_cell(row, 8)),
                "req_repair_in_date": repair_in,
                "ret_repaired_out_date": clean_date_str(safe_cell(row, 10)),
                "center_tat": safe_cell(row, 11),
                "center_must": safe_cell(row, 12),
                "status": safe_cell(row, 13),
                "reproduce_detail": safe_cell(row, 14),
                "fault_location": safe_cell(row, 15),
                "countermeasure2": safe_cell(row, 16),
                "sys_manager": safe_cell(row, 17),
                "du_ru_type": safe_cell(row, 18),
                "pba_name": safe_cell(row, 19),
                "pba_recv_date": clean_date_str(safe_cell(row, 20)),
                "pba_re_recv_date": clean_date_str(safe_cell(row, 21)),
                "repair_pos": safe_cell(row, 23),
                "ship_status": safe_cell(row, 24),
                "remark": safe_cell(row, 25),
                "pba_in": clean_date_str(safe_cell(row, 26)),
                "pba_out": clean_date_str(safe_cell(row, 27)),
                "pba_open_close": safe_cell(row, 28),
                # 実テーブルの担当カラムに計算値を保存(画面表示がこの値を優先使用)
                "carrier_deadline": calculate_carrier_deadline(repair_in, cust),
                "tat13_deadline": calculate_13_working_days_deadline(repair_in),
                "flag_mark": safe_cell(row, 29),
                "sub_alarm": safe_cell(row, 30),
                "sub_date": clean_date_str(safe_cell(row, 31)),
                "summary_use": safe_cell(row, 32)
            })

        if records:
            filtered = _filter_records_by_live_columns(records, "tat_data", TAT_CORE_COLUMNS, TAT_EXTENDED_COLUMNS)
            filtered = [r for r in filtered if r.get("wq")]
            if filtered:
                _upsert_with_fallback("tat_data", filtered, "wq")

        return {"inserted": len(records), "ignored": 0, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TAT 엑셀 파일 해석/DB 저장 오류: {str(e)}")
