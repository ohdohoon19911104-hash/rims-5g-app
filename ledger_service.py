import re
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Any, List
import pandas as pd
from datetime import datetime, timedelta
from database import supabase

router = APIRouter(prefix="/api", tags=["Ledger Service"])

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
    clean_s = clean_date_str(start_date_str)
    if not clean_s: return 0
    try:
        start = datetime.strptime(clean_s, "%Y-%m-%d")
        end = datetime.now()
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
# Supabase 実テーブルのカラム判定
# (実在しないカラムを送信すると PGRST204 エラーでアップロード全体が
#  失敗するため、実在するカラムだけを抽出して保存する)
# =========================================================================

# 実テーブルに確実に存在するコアカラム(2026-10-01 検証済み)
LEDGER_CORE_COLUMNS = [
    "request_no", "sheet_name", "status", "sn_large", "sn_small", "status2",
    "alarm_name", "category_name", "customer", "tech_category", "model_code",
    "part_code", "part_desc", "symptom", "re_defect", "no_of_request",
    "difference_day", "receive_date", "received_date", "is_outbound"
]

# 将来テーブルに追加カラム(ADD COLUMN)を実施した場合に自動で保存対象へ含める拡張カラム
LEDGER_EXTENDED_COLUMNS = ["period_return_can"]

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
        print(f"[Ledger upload] column check failed -> save with core columns only")
    else:
        print(f"[Ledger upload] saving {len(allowed)} existing columns / skipped {len(set(core_columns + extended_columns) - allowed)} not-defined columns")
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
async def get_ledger_data(sheet_name: str = "BBU", mode: str = "in_progress", is_outbound: bool = False):
    try:
        target_outbound = True if mode == "completed" or is_outbound else False

        all_data = []
        step = 1000
        start = 0
        while True:
            res = supabase.table("ledger_data").select("*").eq("sheet_name", sheet_name).eq("is_outbound", target_outbound).order("id", desc=False).range(start, start + step - 1).execute()
            rows = res.data or []
            all_data.extend(rows)
            if len(rows) < step:
                break
            start += step

        result = []
        for i, r in enumerate(all_data):
            rec_date = clean_date_str(r.get("receive_date"))
            calc_tat = calculate_working_days(rec_date)

            p_can = r.get("period_return_can")
            is_checked = True if p_can in [True, "true", "True", 1, "1", "OK", "ok"] else False

            result.append({
                "rowIdx": r["id"], "no": i + 1, "status": r.get("status", "再現試験待機"),
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
                "periodReturnCan": is_checked, "delayReason": r.get("delay_reason", "")
            })
        return result
    except Exception as e:
        print(f"Error fetching ledger data: {e}")
        return []

@router.post("/upload-ledger-excel")
async def upload_ledger_excel(file: UploadFile = File(...)):
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
                "period_return_can": False,
                "is_outbound": False
            })

        if records:
            # 実テーブルに存在しないカラムは自動で除外してから保存する
            filtered = _filter_records_by_live_columns(records, "ledger_data", LEDGER_CORE_COLUMNS, LEDGER_EXTENDED_COLUMNS)
            filtered = [r for r in filtered if r.get("request_no")]
            if filtered:
                _upsert_with_fallback("ledger_data", filtered, "request_no")

        return {"inserted": len(records), "ignored": 0, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ファイル解析・DB同期エラー: {str(e)}")

class LedgerStatusUpdate(BaseModel):
    sheetName: str
    rowIdx: int
    newStatus: str

@router.post("/update-ledger-status")
async def update_ledger_status(req: LedgerStatusUpdate):
    try:
        is_out = (req.newStatus == '出庫済み')
        status2 = 'CLOSE' if is_out else 'REQ] REPAIR IN'
        supabase.table("ledger_data").update({"status": req.newStatus, "status2": status2, "is_outbound": is_out}).eq("id", req.rowIdx).execute()
        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}

class LedgerCellFieldUpdate(BaseModel):
    sheetName: str
    rowIdx: int
    fieldName: str
    fieldValue: Any

@router.post("/update-ledger-cell-field")
async def update_ledger_cell_field(req: LedgerCellFieldUpdate):
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

        # 1. ledger_data 테이블 업데이트 (period_return_can)
        supabase.table("ledger_data").update({db_field: bool_val}).eq("id", req.rowIdx).execute()

        # 2. tat_data 테이블 연동 업데이트 (기존 컬럼 repair_can 활용: 체크 시 "OK", 해제 시 "")
        if req.fieldName == "periodReturnCan":
            repair_can_str = "OK" if bool_val else ""
            res = supabase.table("ledger_data").select("sn_large, sn_small, request_no").eq("id", req.rowIdx).execute()
            if res.data:
                item = res.data[0]
                sn_l = str(item.get("sn_large") or '').strip()
                sn_s = str(item.get("sn_small") or '').strip()
                req_no = str(item.get("request_no") or '').strip()

                # tat_data의 기존 컬럼 repair_can 업데이트
                if sn_l:
                    supabase.table("tat_data").update({"repair_can": repair_can_str}).eq("sn", sn_l).execute()
                if sn_s and sn_s != sn_l:
                    supabase.table("tat_data").update({"repair_can": repair_can_str}).eq("sn", sn_s).execute()
                if req_no:
                    supabase.table("tat_data").update({"repair_can": repair_can_str}).eq("wq", req_no).execute()

        return {"success": True}
    except Exception as e:
        print(f"Update Field Error: {e}")
        return {"success": False, "message": str(e)}
