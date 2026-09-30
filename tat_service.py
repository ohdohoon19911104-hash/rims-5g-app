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

@router.get("/tat-data")
async def get_tat_data():
    try:
        # 원본 소스 코드의 전량(15,000건) 페이징 조회
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
            carrier_dl = r.get("kddi_uq_must") or calculate_carrier_deadline(repair_in, cust)
            tat13_dl = r.get("center_must") or calculate_13_working_days_deadline(repair_in)
            
            p_can = r.get("period_return_can")
            p_can_legacy = r.get("repair_can")
            is_checked = True if p_can in [True, "true", "True", 1, "1"] or str(p_can_legacy).upper() in ["OK", "O", "TRUE", "1", "YES"] else False

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
                "periodReturnCan": is_checked,
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
            
            # W열(Col 22: 수리가능여부) 문자열을 Boolean 타입으로 변환하여 DB 에러 수정
            can_return_raw = safe_cell(row, 22).strip().upper()
            can_return_bool = can_return_raw in ["OK", "O", "TRUE", "1", "YES"]

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
                "period_return_can": can_return_bool,
                "repair_pos": safe_cell(row, 23),
                "ship_status": safe_cell(row, 24),
                "remark": safe_cell(row, 25),
                "pba_in": clean_date_str(safe_cell(row, 26)),
                "pba_out": clean_date_str(safe_cell(row, 27)),
                "pba_open_close": safe_cell(row, 28),
                "flag_mark": safe_cell(row, 29),
                "sub_alarm": safe_cell(row, 30),
                "sub_date": clean_date_str(safe_cell(row, 31)),
                "summary_use": safe_cell(row, 32)
            })

        if records:
            # 500건씩 분할 upsert 수행
            batch_size = 500
            for i in range(0, len(records), batch_size):
                batch = records[i:i+batch_size]
                supabase.table("tat_data").upsert(batch, on_conflict="wq").execute()

        return {"inserted": len(records), "ignored": 0, "success": True}
    except Exception as e:
        print(f"Excel Upload Error: {e}")
        raise HTTPException(status_code=500, detail=f"TAT 엑셀 파일 해석/DB 저장 오류: {str(e)}")