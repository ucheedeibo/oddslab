import logging
from datetime import date
from pathlib import Path
from typing import Any
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.config import settings
from app.core import backtest, strength
from app.core.strength import InsufficientData, Match
from app.provider import COMPETITIONS, get_fixtures
from app import service
from app.security import RateLimiter, SESSION_TTL, make_session, tokens_match, verify_session

log = logging.getLogger("oddslab")   # never log tokens or keys
STATIC = Path(__file__).parent / "static"
VERSION = "0.3.0"
COOKIE = "oddslab_session"
login_limiter = RateLimiter(limit=5, per_seconds=300)

app = FastAPI(title="OddsLab", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                         "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                         "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'"})
    return resp


def require_auth(request: Request, authorization: str | None = Header(default=None),
                 session: str | None = Cookie(default=None, alias=COOKIE)):
    token = settings.app_api_token
    if not token:
        raise HTTPException(503, "Server is not configured (APP_API_TOKEN missing).")
    if verify_session(session, token):
        # Cookie-authenticated state-changing requests must carry a custom header (CSRF defence, plus SameSite=Strict).
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
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION, "provider_key_configured": bool(settings.api_football_key),
            "database": "not yet connected (Milestone 1b)"}


@app.post("/oa/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(ip):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    if not tokens_match(body.token, settings.app_api_token):
        raise HTTPException(401, "Invalid token.")
    response.set_cookie(COOKIE, make_session(settings.app_api_token), max_age=SESSION_TTL, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/")
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
    try:
        raw = await get_fixtures(day.isoformat())
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception:
        log.exception("provider request failed")
        raise HTTPException(502, "Football data provider request failed.")
    out = []
    for item in raw:
        f, t, lg = item.get("fixture", {}), item.get("teams", {}), item.get("league", {})
        out.append({"fixture_id": f.get("id"), "kickoff": f.get("date"), "status": (f.get("status") or {}).get("short"),
                    "home_team": (t.get("home") or {}).get("name"), "away_team": (t.get("away") or {}).get("name"),
                    "league_id": lg.get("id"), "league": COMPETITIONS.get(lg.get("id"), lg.get("name"))})
    return {"date": day.isoformat(), "count": len(out), "fixtures": out, "source": "API-Football",
            "note": "Predictions need stored history (next step); none are shown for these fixtures yet."}


def _matches(rows: Any) -> list[Match]:
    if not isinstance(rows, list) or len(rows) > 5000:
        raise HTTPException(422, "matches must be a list of at most 5000 rows.")
    try:
        return [Match(date.fromisoformat(str(r["date"])[:10]), str(r["home_team"]), str(r["away_team"]),
                      int(r["home_goals"]), int(r["away_goals"])) for r in rows]
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, "Each match needs date, home_team, away_team, home_goals, away_goals.")


@app.get("/oa/slips", dependencies=[Depends(require_auth)])
async def slips_for_day(day: date = Query(default_factory=date.today), target: float = Query(3.0, ge=1.5, le=10.0)):
    try:
        return await service.build_day(day, target)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception:
        log.exception("slip building failed")
        raise HTTPException(502, "Could not build slips (provider or model problem). Check the server log.")


@app.post("/oa/predict", dependencies=[Depends(require_auth)])
def predict(payload: dict[str, Any]):
    """Stateless until the database milestone: history is supplied by the caller. Fitted only on past data."""
    hist = _matches(payload.get("history"))
    home, away = payload.get("home_team"), payload.get("away_team")
    if not home or not away:
        raise HTTPException(422, "home_team and away_team are required.")
    as_of = date.fromisoformat(payload["as_of"]) if payload.get("as_of") else date.today()
    try:
        out = strength.fit(hist, as_of).predict(str(home), str(away))
    except InsufficientData as exc:
        raise HTTPException(422, f"Not enough data for a reliable estimate: {exc}")
    out["warning"] = "Uncalibrated until enough out-of-sample predictions exist. Not betting advice."
    return out


@app.post("/oa/backtest/run", dependencies=[Depends(require_auth)])
def run_backtest(payload: dict[str, Any]):
    ms = _matches(payload.get("matches"))
    try:
        start = date.fromisoformat(payload["start"])
        res = backtest.walk_forward(ms, start, int(payload.get("step_days", 14)),
                                    payload.get("window_days"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc))
    res["rows"] = [{**r, "date": r["date"].isoformat()} for r in res["rows"]][:2000]
    return res
