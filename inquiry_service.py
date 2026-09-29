from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime
from database import supabase

router = APIRouter(prefix="/api", tags=["Inquiry Service"])

def clean_date_str(val: Any) -> str:
    if not val or str(val).strip() in ['-', 'None', 'nan', 'null', '']: return ''
    s = str(val).strip()
    if ' ' in s: s = s.split(' ')[0]
    if 'T' in s: s = s.split('T')[0]
    return s.replace('/', '-')

@router.get("/inquiry-dashboard")
async def get_inquiry_dashboard(userRole: str = ""):
    try:
        res = supabase.table("inquiries").select("*").order("id", desc=False).execute()
        raw_list = res.data or []
        team_stats = {
            "KDDI_RRH": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "list": []},
            "5G": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "list": []},
            "UQ_RRH": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "list": []},
            "BBU": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "list": []},
            "PICO": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "list": []}
        }
        total = 0; open_cnt = 0; pending_cnt = 0; close_cnt = 0
        full_status_map = {}

        for r in raw_list:
            s_name = r.get("sheet_name", "BBU")
            if s_name not in team_stats: s_name = "BBU"
            st = r.get("status", "PENDING_TERAMOTO")
            sn_val = str(r.get("sn", "")).replace(" ", "").strip()
            hist = str(r.get("history", ""))
            
            if sn_val and sn_val != "-":
                full_status_map[sn_val] = {
                    "status": st, "answer": r.get("answer", ""), "history": hist,
                    "phenomenon": r.get("phenomenon", ""), "inboundCount": r.get("inbound_count", ""),
                    "selfSolve": r.get("self_solve", ""), "reportDate": clean_date_str(r.get("report_date")), "occurDate": r.get("occur_date", "")
                }

            # KCCS 관리자는 승인 이관되었거나 KCCS용 재질문 건만 대시보드 목록에 노출
            if userRole == "KCCS_MGR":
                has_kccs_history = ("承認] KCCSへ問い合わせを移管しました" in hist) or ("宛先: KCCS管理者" in hist)
                if not has_kccs_history:
                    continue

            team_stats[s_name]["total"] += 1
            total += 1

            if st == "CLOSE": 
                team_stats[s_name]["close"] += 1; close_cnt += 1
            elif st == "PENDING_TERAMOTO": 
                team_stats[s_name]["pendingTeramoto"] += 1; pending_cnt += 1
            else: 
                team_stats[s_name]["open"] += 1; open_cnt += 1

            team_stats[s_name]["list"].append({
                "rowIdx": r["id"], "sheetName": s_name, "inquiryId": r.get("inquiry_id", ""),
                "status": st, "team": r.get("team", ""), "phenomenon": r.get("phenomenon", ""),
                "inboundCount": r.get("inbound_count", ""), "selfSolve": r.get("self_solve", ""),
                "reportDate": clean_date_str(r.get("report_date")), "occurDate": r.get("occur_date", ""),
                "model": r.get("model", ""), "sn": r.get("sn", ""), "reporterName": r.get("reporter_name", ""),
                "detail": r.get("detail", ""), "remark": r.get("remark", ""),
                "createdAt": clean_date_str(r.get("created_at")),
                "ledgerSheet": r.get("ledger_sheet", ""), "ledgerRowIdx": r.get("ledger_row_idx", ""),
                "answer": r.get("answer", ""), "history": hist
            })

        resp_rate = f"{((close_cnt / total) * 100):.1f}" if total > 0 else "0.0"
        return {
            "summary": {"total": total, "open": open_cnt, "pendingTeramoto": pending_cnt, "close": close_cnt, "responseRate": resp_rate},
            "teamStats": team_stats, "fullStatusMap": full_status_map
        }
    except Exception as e:
        return {"summary": {"total": 0, "open": 0, "pendingTeramoto": 0, "close": 0, "responseRate": "0.0"}, "teamStats": {}, "fullStatusMap": {}}

class SubmitInquiryRequest(BaseModel):
    sheetName: Optional[str] = ""
    ledgerRowIdx: Optional[int] = None
    team: str
    phenomenon: str
    inboundCount: str
    selfSolve: str
    reportDate: str
    occurDate: str
    model: str
    sn: str
    reporterName: str
    detail: str
    remark: Optional[str] = ""

@router.post("/submit-inquiry")
async def submit_inquiry(req: SubmitInquiryRequest):
    try:
        now_str = datetime.now().strftime("%Y%m%d%H%M%S")
        time_str = datetime.now().strftime("%m/%d %H:%M")
        clean_sn = req.sn.replace(" ", "").strip()

        existing = supabase.table("inquiries").select("*").eq("sn", clean_sn).execute()

        if existing.data and len(existing.data) > 0:
            inq_obj = existing.data[0]
            cur_hist = inq_obj.get("history") or inq_obj.get("detail") or ""
            new_entry = f"\n\n[{time_str} {req.reporterName or '作業者'} 再問合せ] {req.detail or ''}"
            updated_hist = cur_hist + new_entry

            supabase.table("inquiries").update({
                "status": "PENDING_TERAMOTO",
                "phenomenon": req.phenomenon,
                "inbound_count": req.inboundCount,
                "self_solve": req.selfSolve,
                "detail": req.detail,
                "history": updated_hist,
                "ledger_row_idx": req.ledgerRowIdx
            }).eq("id", inq_obj["id"]).execute()

            inq_id = inq_obj.get("inquiry_id")
        else:
            inq_id = f"QA-{now_str}"
            init_history = f"[{time_str} {req.reporterName or '作業者'}] {req.detail or ''}"

            data = {
                "inquiry_id": inq_id, "status": "PENDING_TERAMOTO", "team": req.team, "sheet_name": req.sheetName or "BBU",
                "phenomenon": req.phenomenon, "inbound_count": req.inboundCount, "self_solve": req.selfSolve,
                "report_date": req.reportDate, "occur_date": req.occurDate, "model": req.model, "sn": req.sn,
                "reporter_name": req.reporterName, "detail": req.detail, "remark": req.remark,
                "ledger_sheet": req.sheetName, "ledger_row_idx": req.ledgerRowIdx, "history": init_history
            }
            supabase.table("inquiries").insert(data).execute()
        
        if req.ledgerRowIdx:
            supabase.table("ledger_data").update({"status": "問い合わせ"}).eq("id", req.ledgerRowIdx).execute()

        return {"success": True, "inquiryId": inq_id}
    except Exception as e: return {"success": False, "message": str(e)}

class ApproveInquiryRequest(BaseModel):
    sheetName: str
    rowIdx: int
    managerName: str
    memoText: str

@router.post("/approve-inquiry-kccs")
async def approve_inquiry_kccs(req: ApproveInquiryRequest):
    try:
        time_str = datetime.now().strftime("%m/%d %H:%M")
        res = supabase.table("inquiries").select("history").eq("id", req.rowIdx).execute()
        cur_hist = res.data[0]["history"] if res.data else ""
        new_entry = ""
        if req.memoText and req.memoText.strip():
            new_entry += f"\n\n[{time_str} {req.managerName or 'Teramoto管理者'}] {req.memoText.strip()}"
        new_entry += f"\n\n[{time_str} {req.managerName or 'Teramoto管理者'} 承認] KCCSへ問い合わせを移管しました。"

        supabase.table("inquiries").update({
            "status": "OPEN", "answer": req.memoText.strip() if req.memoText else "", "history": cur_hist + new_entry
        }).eq("id", req.rowIdx).execute()
        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}

class ResolveAnswerRequest(BaseModel):
    sheetName: str
    rowIdx: int
    answerText: str
    adminName: str

@router.post("/resolve-inquiry-answer")
async def resolve_inquiry_answer(req: ResolveAnswerRequest):
    try:
        time_str = datetime.now().strftime("%m/%d %H:%M")
        res = supabase.table("inquiries").select("history").eq("id", req.rowIdx).execute()
        cur_hist = res.data[0]["history"] if res.data else ""
        new_entry = f"\n\n[{time_str} {req.adminName or '管理者'} 回答] {req.answerText}"

        supabase.table("inquiries").update({
            "status": "ANSWERED", "answer": req.answerText, "history": cur_hist + new_entry
        }).eq("id", req.rowIdx).execute()
        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}

class ReInquiryRequest(BaseModel):
    snVal: str
    detailText: str
    userName: str
    ledgerSheet: str
    ledgerRowIdx: Optional[int] = None
    targetRole: str

# 지목된 수신자(targetRole)에 따른 상태 분기 (Teramoto -> PENDING_TERAMOTO, KCCS -> OPEN)
@router.post("/append-re-inquiry-sn")
async def append_re_inquiry_sn(req: ReInquiryRequest):
    try:
        time_str = datetime.now().strftime("%m/%d %H:%M")
        clean_sn = req.snVal.replace(" ", "").strip()
        next_st = "OPEN" if req.targetRole == "KCCS" else "PENDING_TERAMOTO"
        target_txt = " (宛先: KCCS管理者)" if req.targetRole == "KCCS" else " (宛先: Teramoto管理者)"

        res = supabase.table("inquiries").select("id, history").eq("sn", clean_sn).execute()
        if res.data:
            for item in res.data:
                cur_h = item.get("history", "")
                new_h = cur_h + f"\n\n[{time_str} {req.userName or '作業者'} 再問合せ{target_txt}] {req.detailText}"
                supabase.table("inquiries").update({"status": next_st, "history": new_h}).eq("id", item["id"]).execute()

        if req.ledgerRowIdx:
            supabase.table("ledger_data").update({"status": "問い合わせ"}).eq("id", req.ledgerRowIdx).execute()

        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}

class CloseInquiryRequest(BaseModel):
    snVal: str
    ledgerSheet: str
    ledgerRowIdx: Optional[int] = None

@router.post("/close-inquiry-sn")
async def close_inquiry_sn(req: CloseInquiryRequest):
    try:
        clean_sn = req.snVal.replace(" ", "").strip()
        supabase.table("inquiries").update({"status": "CLOSE"}).eq("sn", clean_sn).execute()
        return {"success": True}
    except Exception as e: return {"success": False, "message": str(e)}