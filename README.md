# OddsLab — Milestone 1b

Private football analytics app. Estimates only; no guaranteed profit or accuracy.

## Verified in this build (26 tests, run offline)
- Poisson + Dixon-Coles score grid and markets (1X2, over/under, BTTS, exact scores)
- Time-weighted, regularised attack/defence model; Elo with draw model; frequency baseline
- Multiclass Brier, log loss, accuracy, reliability, ECE, bootstrap CIs
- Temperature/vector calibration (identity + flagged "uncalibrated" below 300 samples)
- Walk-forward backtest with leakage tests (editing future results does not change past predictions)
- Odds/margin removal, EV, conservative EV, value flags, flat-stake simulation with look-ahead guard
- Accumulator search near a target price (never two legs from one fixture)
- Session-cookie auth helpers (signed, expiring, token-rotation invalidates sessions, rate limiter)

## Written but NOT yet run (no FastAPI in the build sandbox)
- app/main.py (login, fixtures, stateless predict/backtest), app/provider.py, static UI.
  They compile; first real test is your deploy. See "Verify".

## Not built yet
PostgreSQL + migrations, historical ingestion, stored versioned predictions, odds snapshots,
scheduler, performance dashboard, value/combination screens, PWA manifest.

## Deploy (Replit, from iPhone)
1. Upload the ZIP, extract to project root.
2. Secrets: API_FOOTBALL_KEY, APP_API_TOKEN (32+ random chars), DISPLAY_TIMEZONE.
3. Shell: `pip install -r requirements.txt`
4. Run: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
5. Rotate a secret: change it in Secrets and restart (rotating APP_API_TOKEN signs you out).

## Verify
1. `pytest` in the Shell: expect 26 passed.
2. Open `/health`: expect status ok.
3. Open `/`, enter the token, tap Load fixtures.

## Troubleshooting
- 503 "not configured": a secret is missing.
- Cookie not sticking on plain http: set COOKIE_SECURE=false for local testing only.
- 502 on fixtures: provider error or plan limit; check the API-Football dashboard.
