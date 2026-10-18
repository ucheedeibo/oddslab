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
        raise HTTPException(503, "Server is not configured.")
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
    return {"status": "ok", "version": VERSION}


@app.post("/oa/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(ip):
        raise HTTPException(429, "Too many attempts.")
    
    expected_token = settings.app_api_token
    if not tokens_match(body.token, expected_token):
        raise HTTPException(401, "Invalid token.")
        
    response.set_cookie(
        COOKIE,
        make_session(expected_token),
        max_age=SESSION_TTL,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@app.post("/oa/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/oa/session", dependencies=[Depends(require_auth)])
def session():
    return {"authenticated": True}


@app.get("/oa/fixtures", dependencies=[Depends(require_auth)])
async def fixtures(day: date = Query(default_factory=date.today)):
    raw = await get_fixtures(day.isoformat())
    out = []
    for item in raw:
        f, t, lg = item.get("fixture", {}), item.get("teams", {}), item.get("league", {})
        out.append({
            "fixture_id": f.get("id"),
            "kickoff": f.get("date"),
            "status": (f.get("status") or {}).get("short"),
            "home_team": (t.get("home") or {}).get("name"),
            "away_team": (t.get("away") or {}).get("name"),
            "league_id": lg.get("id"),
            "league": COMPETITIONS.get(lg.get("id"), lg.get("name")),
        })
    return {"date": day.isoformat(), "count": len(out), "fixtures": out}


@app.get("/oa/slips", dependencies=[Depends(require_auth)])
async def slips_for_day(day: date = Query(default_factory=date.today), target: float = Query(3.0, ge=1.5, le=10.0)):
    return await service.build_day(day, target)


@app.get("/oa/stats/{day_str}", dependencies=[Depends(require_auth)])
async def slip_stats(day_str: str):
    return await settle_daily_slips(day_str)


@app.post("/oa/refresh-cache", dependencies=[Depends(require_auth)])
async def refresh_daily_cache(day: date = Query(default_factory=date.today)):
    data = await service.build_day(day)
    return {"status": "success", "date": day.isoformat(), "slips_generated": len(data.get("slips", []))}


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
