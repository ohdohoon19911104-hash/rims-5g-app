from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Any
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

# KDDI,UQ MUST 수리 ➔ REQ RECEIVE Date 기준 사업자 TAT 가산 (UQ:90일, KDDI:60일, NEC:45일, DOCOMO:30일)
def calculate_carrier_deadline(req_receive_date_str: str, customer: str) -> str:
    clean_s = clean_date_str(req_receive_date_str)
    if not clean_s: return '-'
    try:
        base_date = datetime.strptime(clean_s, "%Y-%m-%d")
        cust = str(customer or '').upper().strip()
        days_to_add = 30
        if "UQ" in cust: days_to_add = 90
        elif "KDDI" in cust: days_to_add = 60
        elif "NEC" in cust: days_to_add = 45
        elif "DOCOMO" in cust: days_to_add = 30
        return (base_date + timedelta(days=days_to_add)).strftime("%Y-%m-%d")
    except: return '-'

# 修理センター MUST 수리 ➔ REQ REPAIR IN Date 기준 13 영업일 TAT 가산
def calculate_13_working_days(repair_in_date_str: str) -> str:
    clean_s = clean_date_str(repair_in_date_str)
    if not clean_s: return '-'
    try:
        cur = datetime.strptime(clean_s, "%Y-%m-%d")
        added_days = 0
        while added_days < 13:
            cur += timedelta(days=1)
            if cur.weekday() < 5 and cur.strftime("%Y-%m-%d") not in JP_HOLIDAYS: added_days += 1
        return cur.strftime("%Y-%m-%d")
    except: return '-'

@router.get("/tat-data")
async def get_tat_data():
    try:
        all_data = []
        page_size = 1000
        start_idx = 0
        
        while True:
            res = supabase.table("tat_data").select("*").order("id", desc=False).range(start_idx, start_idx + page_size - 1).execute()
            rows = res.data or []
            all_data.extend(rows)
            if len(rows) < page_size:
                break
            start_idx += page_size

        result = []
        for r in all_data:
            result.append({
                "rowIdx": r["id"], 
                "wq": r.get("wq", "-"), 
                "customer": r.get("customer", "-"),
                "techCategory": r.get("tech_category", "-"), 
                "productCategory": r.get("product_category", "-"),
                "sn": r.get("sn", "-"), 
                "overCategory": r.get("over_category", "-"),         # Col 6 원본 (15, 17 등)
                "centerTat": r.get("center_tat", "-"),               # Col 11 원본 (9, 11 등 - 화면의 超過区分 표시용)
                "status": r.get("status", "-"),
                "carrierDeadline": clean_date_str(r.get("carrier_deadline")), 
                "tat13Deadline": clean_date_str(r.get("tat13_deadline")),
                "periodReturnCan": r.get("period_return_can", False), 
                "reason": r.get("reason", ""),
                "reproduceDetail": r.get("reproduce_detail", ""), 
                "faultLocation": r.get("fault_location", ""),
                "sysManager": r.get("sys_manager", ""), 
                "defectType": r.get("defect_type", "Function / Performance Defect"),
                "flagMark": r.get("flag_mark", ""), 
                "kddiUqMust": r.get("kddi_uq_must", ""),
                "reqReceiveDate": clean_date_str(r.get("req_receive_date")), 
                "reqRepairInDate": clean_date_str(r.get("req_repair_in_date")),
                "retRepairedOutDate": clean_date_str(r.get("ret_repaired_out_date")), 
                "centerMust": r.get("center_must", ""), 
                "countermeasure2": r.get("countermeasure2", ""),
                "duRuType": r.get("du_ru_type", ""), 
                "pbaName": r.get("pba_name", ""),
                "pbaRecvDate": r.get("pba_recv_date", ""), 
                "pbaReRecvDate": r.get("pba_re_recv_date", ""),
                "repairCan": r.get("repair_can", ""), 
                "repairPos": r.get("repair_pos", ""),
                "shipStatus": r.get("ship_status", ""), 
                "remark": r.get("remark", ""),                       # Col 25 備考 원본
                "pbaIn": r.get("pba_in", ""), 
                "pbaOut": r.get("pba_out", ""),
                "pbaOpenClose": r.get("pba_open_close", ""), 
                "subAlarm": r.get("sub_alarm", ""),
                "subDate": r.get("sub_date", ""), 
                "summaryUse": r.get("summary_use", "")
            })
        return result
    except Exception as e: return []

@router.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    try:
        df = pd.read_excel(file.file, header=None)
        header_idx = 0
        for idx, row in df.iterrows():
            if str(row[0]).strip().startswith("修理"): header_idx = idx; break
        data_df = df.iloc[header_idx + 1:].copy()
        
        records = []
        for _, row in data_df.iterrows():
            wq_val = safe_cell(row, 0)
            if not wq_val or "修理センター" in wq_val or wq_val == "WQ": continue
            
            cust = safe_cell(row, 1, "-")
            req_recv_dt = clean_date_str(safe_cell(row, 8))
            repair_in_dt = clean_date_str(safe_cell(row, 9)) or req_recv_dt
            ret_out_dt = clean_date_str(safe_cell(row, 10))
            
            orig_kddi_uq_tat = safe_cell(row, 6, "-")  # Col 6 원본 값
            orig_center_tat = safe_cell(row, 11, "-")  # Col 11 원본 값

            # 업로드하는 엑셀 최신 데이터 원본 그대로 대입 (Upsert 전면 덮어쓰기)
            records.append({
                "wq": wq_val,
                "customer": cust,
                "tech_category": safe_cell(row, 2, "-"),
                "product_category": safe_cell(row, 3, "-"),
                "sn": safe_cell(row, 4, "-"),
                "defect_type": safe_cell(row, 5, "Function / Performance Defect"),
                "over_category": orig_kddi_uq_tat,                                 # Col 6 엑셀 원본 값
                "kddi_uq_must": calculate_carrier_deadline(req_recv_dt, cust),     # KDDI,UQ MUST 수리 (사업자 TAT)
                "req_receive_date": req_recv_dt,                                   # Col 8
                "req_repair_in_date": repair_in_dt,                                # Col 9
                "ret_repaired_out_date": ret_out_dt,                               # Col 10
                "center_tat": orig_center_tat,                                     # Col 11 엑셀 원본 값
                "center_must": calculate_13_working_days(repair_in_dt),            # 修理センター MUST 수리 (13 영업일)
                "status": safe_cell(row, 13, "-"),                                 # Col 13
                "carrier_deadline": calculate_carrier_deadline(req_recv_dt, cust),
                "tat13_deadline": calculate_13_working_days(repair_in_dt),
                "period_return_can": safe_cell(row, 18).upper() == "OK",
                "reason": "",                                                      # 지연이유는 대장 연동용 공란
                "reproduce_detail": safe_cell(row, 14, ""),
                "fault_location": safe_cell(row, 15, ""),
                "countermeasure2": safe_cell(row, 16, ""),
                "sys_manager": safe_cell(row, 17, ""),
                "du_ru_type": safe_cell(row, 18, ""),
                "pba_name": safe_cell(row, 19, ""),
                "pba_recv_date": safe_cell(row, 20, ""),
                "pba_re_recv_date": safe_cell(row, 21, ""),
                "repair_can": safe_cell(row, 22, ""),
                "repair_pos": safe_cell(row, 23, ""),
                "ship_status": safe_cell(row, 24, ""),
                "remark": safe_cell(row, 25, ""),                                  # Col 25 備考 원본
                "pba_in": safe_cell(row, 26, ""),
                "pba_out": safe_cell(row, 27, ""),
                "pba_open_close": safe_cell(row, 28, ""),
                "flag_mark": safe_cell(row, 29, ""),
                "sub_alarm": safe_cell(row, 30, ""),
                "sub_date": safe_cell(row, 31, ""),
                "summary_use": safe_cell(row, 32, "")
            })

        # WQ 키 기준으로 DB의 기존 데이터를 업로드한 최신 엑셀 내용으로 즉시 100% 덮어쓰기 (Upsert)
        if records:
            for i in range(0, len(records), 500):
                supabase.table("tat_data").upsert(records[i:i+500], on_conflict="wq").execute()

        return {
            "inserted": len(records), 
            "success": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class PeriodReturnUpdate(BaseModel):
    rowIdx: int
    isChecked: bool

@router.post("/update-tat-period-return")
async def update_tat_period_return(req: PeriodReturnUpdate):
    try:
        supabase.table("tat_data").update({"period_return_can": req.isChecked}).eq("id", req.rowIdx).execute()
        return {"success": True}
    except Exception as e: return {"success": False, "error": str(e)}