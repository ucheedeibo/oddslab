import logging
from datetime import date
from pathlib import Path
from typing import Any
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.core import backtest, strength
from app.core.strength import InsufficientData, Match
from app.provider import COMPETITIONS, get_fixtures
from app import service
from app.settlement import settle_daily_slips
from app.security import RateLimiter, SESSION_TTL, make_session, tokens_match, verify_session

log = logging.getLogger("oddslab")
STATIC = Path(__file__).parent / "static"
VERSION = "0.3.0"
COOKIE = "oddslab_session"
login_limiter = RateLimiter(limit=5, per_seconds=300)

app = FastAPI(title="OddsLab", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'",
    })
    return resp


def require_auth(request: Request, authorization: str | None = Header(default=None),
                 session: str | None = Cookie(default=None, alias=COOKIE)):
    token = settings.app_api_token
    if not token:
        raise HTTPException(503, "Server is not configured (APP_API_TOKEN missing).")
    if verify_session(session, token):
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "oddslab":
            raise HTTPException(403, "Missing CSRF header.")
        return
    if authorization and authorization.startswith("Bearer ") and tokens_match(authorization[7:], token):
        return
    raise HTTPException(401, "Unauthorized")


class LoginBody(BaseModel):
    token: str


@app.get("/")
def home():
    index_file = STATIC / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>OddsLab Engine Active</h1>")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "provider_key_configured": bool(settings.api_football_key),
        "database": "not yet connected",
    }


@app.post("/oa/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(ip):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    if not tokens_match(body.token, settings.app_api
