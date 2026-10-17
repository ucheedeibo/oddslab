import pytest
from app.security import tokens_match, make_session, verify_session, RateLimiter

TOKEN = "x" * 40

def test_token_match():
    assert tokens_match(TOKEN, TOKEN)
    assert not tokens_match("wrong", TOKEN)
    assert not tokens_match(None, TOKEN)
    assert not tokens_match("", "")          # unconfigured server never authenticates

def test_session_valid_expired_tampered_and_rotated():
    s = make_session(TOKEN, now=1000, ttl=60)
    assert verify_session(s, TOKEN, now=1030)
    assert not verify_session(s, TOKEN, now=1100)                 # expired
    assert not verify_session(s[:-2] + "AA", TOKEN, now=1030)     # tampered signature
    assert not verify_session("9999999999." + s.split(".")[1], TOKEN, now=1030)  # forged expiry
    assert not verify_session(s, "y" * 40, now=1030)              # token rotated
    assert not verify_session(None, TOKEN) and not verify_session("junk", TOKEN)

def test_rate_limiter():
    r = RateLimiter(3, 60)
    assert all(r.allow("ip", now=i) for i in range(3))
    assert not r.allow("ip", now=4)
    assert r.allow("other", now=4)
    assert r.allow("ip", now=100)
