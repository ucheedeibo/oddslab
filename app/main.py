import logging
from datetime import date
from pathlib import Path
from typing import Any
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.provider import COMPETITIONS, get_board
from app import service
from app.settlement import settle_daily_slips
from app.security import (
    NAME_RE,
    RateLimiter,
    SESSION_TTL,
    check_user,
    make_session,
    pretty_code,
    tokens_match,
    user_code,
    verify_session,
)

log = logging.getLogger("oddslab")
STATIC = Path(__file__).parent / "static"
VERSION = "0.4.0"
COOKIE = "oddslab_session"

login_limiter = RateLimiter(limit=8, per_seconds=300)
slips_limiter = RateLimiter(limit=10, per_seconds=3600)

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


def _revoked() -> set:
    return {n.strip().lower() for n in settings.revoked_users.split(",") if n.strip()}


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    session: str | None = Cookie(default=None, alias=COOKIE),
) -> dict:
    token = settings.app_api_token
    if not token:
        raise HTTPException(503, "Server is not configured (APP_API_TOKEN missing).")
    who = verify_session(session, token)
    if who:
        role, name = who
        if role == "user" and name in _revoked():
            raise HTTPException(401, "Access has been switched off.")
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "oddslab":
            raise HTTPException(403, "Missing CSRF header.")
        return {"role": role, "name": name}
    if authorization and authorization.startswith("Bearer ") and tokens_match(authorization[7:], token):
        return {"role": "owner", "name": "owner"}
    raise HTTPException(401, "Unauthorized")


def require_owner(who: dict = Depends(require_auth)) -> dict:
    if who["role"] != "owner":
        raise HTTPException(403, "Owner only.")
    return who


class LoginBody(BaseModel):
    token: str
    name: str = ""


@app.get("/")
def home():
    index_file = STATIC / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>OddsLab / MatchMind Engine Active</h1>")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "data_token_configured": bool(settings.football_data_token),
        "database": "none (invite-only codes, no stored accounts)",
    }


@app.post("/oa/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(ip):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    name = body.name.strip().lower()
    if name:
        if not check_user(settings.app_api_token, name, body.token, _revoked()):
            raise HTTPException(401, "Invalid name or code.")
        cookie = make_session(settings.app_api_token, "user", name)
    else:
        if not tokens_match(body.token, settings.app_api_token):
            raise HTTPException(401, "Invalid token.")
        cookie = make_session(settings.app_api_token, "owner", "owner")
    response.set_cookie(
        COOKIE,
        cookie,
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


@app.get("/oa/session")
def session(who: dict = Depends(require_auth)):
    return {"authenticated": True, **who}


@app.get("/oa/admin/code")
def make_code(name: str = Query(...), who: dict = Depends(require_owner)):
    name = name.strip().lower()
    if not NAME_RE.fullmatch(name) or name == "owner":
        raise HTTPException(422, "Use 2-20 letters, numbers or underscores (not 'owner').")
    return {
        "name": name,
        "code": pretty_code(user_code(settings.app_api_token, name)),
        "note": "Give them this name and code. To switch them off, add the name to REVOKED_USERS in Render.",
    }


@app.get("/oa/live")
async def live(day: date = Query(default_factory=date.today), who: dict = Depends(require_auth)):
    try:
        matches = await get_board(day.isoformat())
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception:
        log.exception("scoreboard failed")
        raise HTTPException(502, "Football data provider request failed.")
    return {
        "date": day.isoformat(),
        "count": len(matches),
        "matches": matches,
        "source": "football-data.org",
        "note": "Scores on the free plan can be delayed; this is not a true live feed.",
    }


@app.get("/oa/slips")
async def slips_for_day(
    day: date = Query(default_factory=date.today),
    target: float = Query(3.0, ge=1.5, le=10.0),
    who: dict = Depends(require_auth),
):
    if who["role"] != "owner" and not slips_limiter.allow(who["name"]):
        raise HTTPException(429, "Slip limit reached (10 per hour). Try again later.")
    try:
        return await service.build_day_cached(day, target)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception:
        log.exception("slip building failed")
        raise HTTPException(502, "Could not build slips (provider or model problem). Check the server log.")


@app.get("/oa/stats/{day_str}", dependencies=[Depends(require_auth)])
async def slip_stats(day_str: str):
    """Returns settled slip statuses and performance analytics (Win Rate & ROI)."""
    try:
        return await settle_daily_slips(day_str)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception:
     log.exception("stats calculation failed")
        raise HTTPException(502, "Could not compute slip statistics.")
