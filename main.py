import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import auth
import tat_service
import ledger_service
import inquiry_service

app = FastAPI(title="RIMS 統合管理システム")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 모듈별 라우터 등록
app.include_router(auth.router)
app.include_router(tat_service.router)
app.include_router(ledger_service.router)
app.include_router(inquiry_service.router)

# 監視サービスのHEADリクエストに応答します。
@app.head("/")
async def monitor_head():
    return Response(status_code=200)

@app.get("/", response_class=HTMLResponse)
async def get_index():
    if os.path.exists("Index.html"):
        with open("Index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Index.html File Not Found."

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)