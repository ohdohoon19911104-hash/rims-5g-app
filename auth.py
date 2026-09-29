from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import supabase

router = APIRouter(prefix="/api", tags=["Auth"])

class LoginRequest(BaseModel):
    id: str
    password: str

@router.post("/login")
async def login(req: LoginRequest):
    try:
        res = supabase.table("users").select("*").eq("user_id", req.id.strip()).eq("password", req.password.strip()).execute()
        if res.data and len(res.data) > 0:
            u = res.data[0]
            return {"success": True, "user": {"id": u["user_id"], "name": u["name"], "role": u["role"]}}
        return {"success": False, "message": "IDまたはパスワードが正しくありません。"}
    except Exception as e:
        return {"success": False, "message": f"認証エラー: {str(e)}"}

class CreateUserRequest(BaseModel):
    newUserId: str
    password: str
    userName: str
    role: str
    currentRole: str

@router.post("/create-user")
async def create_user(req: CreateUserRequest):
    if req.currentRole != "MASTER":
        raise HTTPException(status_code=403, detail="アカウント作成権限がありません。(総括マスターのみ可能)")
    try:
        data = {"user_id": req.newUserId.strip(), "password": req.password.strip(), "name": req.userName.strip(), "role": req.role}
        supabase.table("users").insert(data).execute()
        return {"success": True, "message": "アカウントが正常に作成されました。"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))