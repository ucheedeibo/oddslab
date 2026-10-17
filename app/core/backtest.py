"""Chronological walk-forward backtest.

For each window starting at t: models are fitted ONLY on matches dated before t (expanding, or rolling
if window_days is set); calibration is fitted ONLY on predictions made in earlier windows whose
outcomes were already known by t; predictions for [t, t+step) are then recorded and scored.
"""
from __future__ import annotations
from datetime import date, timedelta
import numpy as np
from app.core import strength, elo, metrics
from app.core.calibration import fit_calibrator
from app.core.strength import Match, InsufficientData

OUT = {"H": 0, "D": 1, "A": 2}


def outcome(m: Match) -> int:
    return 0 if m.hg > m.ag else 1 if m.hg == m.ag else 2


def _freq_baseline(train: list[Match]) -> list[float]:
    y = np.array([outcome(m) for m in train])
    return [float((y == k).mean()) for k in range(3)]


def walk_forward(matches: list[Match], start: date, step_days: int = 14, window_days: int | None = None,
                 min_train: int = 60, min_team_matches: int = 5, calib_min: int = 300, xi: float = 0.0019,
                 ridge: float = 2.0) -> dict:
    done = sorted([m for m in matches if m.hg is not None and m.ag is not None], key=lambda m: m.date)
    if not done:
        raise ValueError("No completed matches supplied.")
    end = done[-1].date
    rows: list[dict] = []
    skipped = 0
    t = start
    while t <= end:
        t_next = t + timedelta(days=step_days)
        window = [m for m in done if t <= m.date < t_next]
        if window:
            try:
                dc = strength.fit(done, t, xi=xi, ridge=ridge, min_total=min_train, window_days=window_days)
                el = elo.fit(done, t, min_total=min_train, window_days=window_days)
            except InsufficientData:
                dc = el = None
            if dc is not None:
                train = [m for m in done if m.date < t and (window_days is None or (t - m.date).days <= window_days)]
                freq = _freq_baseline(train)
                past = [r for r in rows if r["date"] < t and r["model_ok"]]  # outcomes known before t
                cal = fit_calibrator([r["dc_raw"] for r in past], [r["y"] for r in past], min_samples=calib_min)
                for m in window:
                    try:
                        p = dc.predict(m.home, m.away, min_team_matches)
                        e = el.predict(m.home, m.away, min_team_matches)
                    except InsufficientData:
                        skipped += 1
                        continue
                    raw = [p["home_win"], p["draw"], p["away_win"]]
                    rows.append({"id": m.id, "date": m.date, "home": m.home, "away": m.away, "y": outcome(m),
                                 "dc_raw": raw, "dc_cal": cal.transform([raw])[0].tolist(),
                                 "calibrated": cal.fitted, "elo": [e["home_win"], e["draw"], e["away_win"]],
                                 "freq": freq, "model_ok": True})
            else:
                skipped += len(window)
        t = t_next
    return {"rows": rows, "skipped": skipped, "summary": summarize_rows(rows)}


def summarize_rows(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "note": "No predictions could be made (insufficient history)."}
    y = [r["y"] for r in rows]
    out = {"n": len(rows), "models": {}}
    for key, label in (("freq", "frequency_baseline"), ("elo", "elo"), ("dc_raw", "dc_poisson_raw"), ("dc_cal", "dc_poisson_calibrated")):
        out["models"][label] = metrics.summarize([r[key] for r in rows], y)
    n_cal = sum(r["calibrated"] for r in rows)
    out["calibrated_rows"] = n_cal
    out["calibration_note"] = ("Calibration applied to %d of %d rows; earlier rows used the raw model because "
                               "too few prior out-of-sample predictions existed." % (n_cal, len(rows)))
    base = metrics.per_match_brier([r["freq"] for r in rows], y)
    mod = metrics.per_match_brier([r["dc_raw"] for r in rows], y)
    lo, hi = metrics.bootstrap_ci(base - mod)
    out["dc_vs_baseline_brier_gain"] = {"mean": float((base - mod).mean()), "ci95": [lo, hi],
                                        "note": "Positive = model beats frequency baseline. CI spanning 0 = not distinguishable."}
    return out
