import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

import accounts
import auth
import chat
import documents
import libraries
import models
from telemetry import FAILURES

app = FastAPI(title="NabuCite")
app.mount("/metrics", make_asgi_app())
web_port = int(os.getenv("NABUCITE_WEB_PORT", "5173"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{web_port}", f"http://127.0.0.1:{web_port}"],
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=["Authorization", "Content-Type", "X-Filename"],
)
app.include_router(accounts.router)
app.include_router(models.router)
app.include_router(documents.router)
app.include_router(auth.router)
app.include_router(libraries.router)
app.include_router(chat.router)


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    if response.status_code >= 400:
        FAILURES.labels("http_" + str(response.status_code // 100) + "xx").inc()
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store, no-transform"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # 验证错误的 input 可能包含登录密码。
    if request.url.path == "/api/auth/login":
        return JSONResponse({"detail": "认证失败，请重新登录。"}, status_code=401)
    return JSONResponse({"detail": "请求字段或格式不正确。"}, status_code=422)
