"""Accuracy check: how well would the model have predicted recent matches?

For each cut-off date the model is fitted ONLY on matches before that date
(fit() enforces this), then scored on the matches played in the following
window. Results are compared with a naive baseline that just uses the league's
long-run home / draw / away frequencies. Lower RPS, log-loss and Brier are better.
"""
import asyncio
import math
from datetime import date, datetime, timedelta, timezone

from app import provider, service
from app.core import strength
from app.core.strength import InsufficientData

BINS = [(0.0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]
BIN_BINS = [(0.0, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]
MARKETS = (("over25", "Over 2.5 goals"), ("btts", "Both teams score"), ("over15", "Over 1.5 goals"))


def _outcome(m):
    return 0 if m.hg > m.ag else (1 if m.hg == m.ag else 2)


def _scores(p, o):
    onehot = [1.0 if i == o else 0.0 for i in range(3)]
    rps = ((p[0] - onehot[0]) ** 2 + ((p[0] + p[1]) - (onehot[0] + onehot[1])) ** 2) / 2
    brier = sum((p[i] - onehot[i]) ** 2 for i in range(3))
    logloss = -math.log(max(p[o], 1e-9))
    return rps, logloss, brier


def _baseline(train):
    n = len(train)
    if n < 20:
        return [0.45, 0.27, 0.28]
    counts = [0, 0, 0]
    for m in train:
        counts[_outcome(m)] += 1
    return [c / n for c in counts]


def _bin_scores(p, y):
    return (p - y) ** 2, -math.log(max(p if y else 1 - p, 1e-9))


def _bin_summary(rows):
    n = len(rows)
    return {"brier": round(sum(r[0] for r in rows) / n, 4), "log_loss": round(sum(r[1] for r in rows) / n, 4)}


def _bin_rate(train, test_fn, default=0.5):
    if len(train) < 20:
        return default
    return sum(1 for m in train if test_fn(m)) / len(train)


def _hit(market, m):
    total = m.hg + m.ag
    if market == "over25":
        return total >= 3
    if market == "over15":
        return total >= 2
    return m.hg > 0 and m.ag > 0


def _summary(rows):
    n = len(rows)
    if not n:
        return None
    return {
        "rps": round(sum(r[0] for r in rows) / n, 4),
        "log_loss": round(sum(r[1] for r in rows) / n, 4),
        "brier": round(sum(r[2] for r in rows) / n, 4),
    }


async def evaluate(league_id: int, pooled: bool = False, cuts: int = 6, step_days: int = 14,
                   day: date = None) -> dict:
    day = day or datetime.now(timezone.utc).date()
    if league_id not in provider.MODELLED:
        return {"status": "error", "message": "Unsupported competition."}
    if pooled and league_id != service.CL_ID:
        return {"status": "error", "message": "Pooled mode is only used for the Champions League."}
    cuts = max(1, min(cuts, 8))

    pairs = service.history_pairs(day, service.CL_ID if pooled else league_id)
    notes = []
    rows, pending = await service._load_history(pairs, notes, allow_fetch=False)
    if pending:
        return {"status": "loading", "message": "League history is still loading in the background. Try again in a minute or two.",
                "notes": notes}

    all_matches = service._to_matches(rows.values())
    own_pairs = {p for p in service.history_pairs(day, league_id) if p[0] == league_id}
    own = service._to_matches(rows[p] for p in own_pairs if p in rows)

    model_rows, base_rows, picks = [], [], []
    mk_model = {k: [] for k, _ in MARKETS}
    mk_base = {k: [] for k, _ in MARKETS}
    mk_pairs = {k: [] for k, _ in MARKETS}
    for k in range(1, cuts + 1):
        as_of = day - timedelta(days=step_days * k)
        window = [m for m in own if as_of <= m.date < as_of + timedelta(days=step_days)]
        if not window:
            continue
        try:
            model = await asyncio.to_thread(strength.fit, all_matches, as_of)
        except (InsufficientData, RuntimeError):
            continue
        train_own = [m for m in own if m.date < as_of]
        base = _baseline(train_own)
        base_rate = {k: _bin_rate(train_own, lambda m, k=k: _hit(k, m)) for k, _ in MARKETS}
        for m in window:
            try:
                grid = model.grid(m.home, m.away, min_matches=1)
            except InsufficientData:
                continue
            matrix = service._grid_matrix(grid)
            if matrix is None:
                continue
            s = service._summarise(matrix, 1)
            p = [s["home"], s["draw"], s["away"]]
            o = _outcome(m)
            model_rows.append(_scores(p, o))
            base_rows.append(_scores(base, o))
            top = max(range(3), key=lambda i: p[i])
            picks.append((p[top], top == o))
            probs = {"over25": s["overs"][2.5], "btts": s["btts"], "over15": s["overs"][1.5]}
            for k, _ in MARKETS:
                y = 1 if _hit(k, m) else 0
                mk_model[k].append(_bin_scores(probs[k], y))
                mk_base[k].append(_bin_scores(base_rate[k], y))
                mk_pairs[k].append((probs[k], y))

    n = len(model_rows)
    if n == 0:
        return {"status": "no_data", "message": "No past matches could be scored for this setup.", "notes": notes}

    calibration = []
    for lo, hi in BINS:
        sel = [x for x in picks if lo <= x[0] < hi]
        if sel:
            calibration.append({
                "predicted": f"{round(sum(x[0] for x in sel) / len(sel) * 100)}%",
                "actual": f"{round(sum(1 for x in sel if x[1]) / len(sel) * 100)}%",
                "matches": len(sel),
            })

    m_sum, b_sum = _summary(model_rows), _summary(base_rows)
    markets = {}
    for k, label in MARKETS:
        pairs = mk_pairs[k]
        cal = []
        for lo, hi in BIN_BINS:
            sel = [x for x in pairs if lo <= x[0] < hi]
            if sel:
                cal.append({"predicted": f"{round(sum(x[0] for x in sel) / len(sel) * 100)}%",
                            "actual": f"{round(sum(x[1] for x in sel) / len(sel) * 100)}%", "matches": len(sel)})
        mm, bb = _bin_summary(mk_model[k]), _bin_summary(mk_base[k])
        markets[k] = {"label": label, "matches_scored": len(pairs), "model": mm, "baseline": bb,
                      "better_than_baseline": mm["brier"] < bb["brier"], "calibration": cal}
    return {
        "status": "ok",
        "competition": provider.COMPETITIONS.get(league_id),
        "mode": "pooled" if pooled else "single competition",
        "matches_scored": n,
        "top_pick_accuracy": f"{round(sum(1 for x in picks if x[1]) / n * 100)}%",
        "model": m_sum,
        "baseline": b_sum,
        "better_than_baseline": m_sum["rps"] < b_sum["rps"],
        "calibration": calibration,
        "markets": markets,
        "how_to_read": ("Lower rps, log_loss and brier are better. The model only has value if it beats the baseline. "
                        "In calibration, 'predicted' should be close to 'actual'; a much higher predicted value means the model is overconfident."),
        "notes": notes,
    }
