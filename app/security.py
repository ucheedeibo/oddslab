"""Single-user auth: exchange APP_API_TOKEN once for a short-lived signed, HttpOnly session cookie."""
from __future__ import annotations
import base64, hashlib, hmac, time
from collections import defaultdict, deque

SESSION_TTL = 12 * 3600


def tokens_match(supplied: str | None, expected: str) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _key(token: str) -> bytes:
    # Derived from the token: rotating APP_API_TOKEN invalidates every existing session.
    return hashlib.sha256(b"oddslab-session-v1:" + token.encode()).digest()


def make_session(token: str, now: float | None = None, ttl: int = SESSION_TTL) -> str:
    exp = str(int((now if now is not None else time.time()) + ttl))
    sig = hmac.new(_key(token), exp.encode(), hashlib.sha256).digest()
    return exp + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def verify_session(cookie: str | None, token: str, now: float | None = None) -> bool:
    if not cookie or not token or "." not in cookie:
        return False
    exp, _, sig = cookie.partition(".")
    try:
        if int(exp) < (now if now is not None else time.time()):
            return False
    except ValueError:
        return False
    good = base64.urlsafe_b64encode(hmac.new(_key(token), exp.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return hmac.compare_digest(sig, good)


class RateLimiter:
    """In-memory sliding window (fine for one server process; use a shared store if you scale out)."""
    def __init__(self, limit: int, per_seconds: int):
        self.limit, self.per, self.hits = limit, per_seconds, defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        q = self.hits[key]
        while q and q[0] <= now - self.per:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
