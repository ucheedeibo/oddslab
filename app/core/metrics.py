"""Forecast-quality metrics for H/D/A probabilities. P rows = [home, draw, away]; y = 0/1/2."""
from __future__ import annotations
import numpy as np

EPS = 1e-12


def _check(P, y):
    P, y = np.asarray(P, float), np.asarray(y, int)
    if P.ndim != 2 or P.shape[1] != 3 or len(P) != len(y) or len(P) == 0:
        raise ValueError("P must be (n,3) and match y; n >= 1.")
    if not np.allclose(P.sum(axis=1), 1.0, atol=1e-6) or (P < -1e-12).any():
        raise ValueError("Each probability row must be non-negative and sum to 1.")
    return P, y


def brier(P, y) -> float:
    """Multiclass Brier: mean over matches of sum_k (p_k - o_k)^2. Range 0..2; lower is better."""
    P, y = _check(P, y)
    return float(((P - np.eye(3)[y]) ** 2).sum(axis=1).mean())


def log_loss(P, y) -> float:
    P, y = _check(P, y)
    return float(-np.log(np.clip(P[np.arange(len(y)), y], EPS, 1)).mean())


def accuracy(P, y) -> float:
    P, y = _check(P, y)
    return float((P.argmax(axis=1) == y).mean())


def reliability(p, o, bins: int = 10) -> list[dict]:
    """Binary reliability table for one event: predicted prob p vs outcome o (0/1)."""
    p, o = np.asarray(p, float), np.asarray(o, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        s = idx == b
        n = int(s.sum())
        if n == 0:
            continue
        obs = float(o[s].mean())
        se = float(np.sqrt(max(obs * (1 - obs), 1e-9) / n))
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b+1]:.1f}", "n": n, "mean_pred": float(p[s].mean()),
                     "observed": obs, "approx_se": se})
    return rows


def ece(P, y, bins: int = 10) -> float:
    """Class-wise expected calibration error, averaged over H/D/A.
    Limitation: biased upward in small samples and sensitive to binning; read with the sample size."""
    P, y = _check(P, y)
    total = 0.0
    for k in range(3):
        rows = reliability(P[:, k], (y == k).astype(float), bins)
        total += sum(r["n"] * abs(r["mean_pred"] - r["observed"]) for r in rows) / len(y)
    return float(total / 3)


def bootstrap_ci(values, stat=np.mean, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0):
    v = np.asarray(values, float)
    if len(v) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    s = [stat(v[rng.integers(0, len(v), len(v))]) for _ in range(n_boot)]
    return (float(np.quantile(s, alpha / 2)), float(np.quantile(s, 1 - alpha / 2)))


def per_match_brier(P, y):
    P, y = _check(P, y)
    return ((P - np.eye(3)[y]) ** 2).sum(axis=1)


def summarize(P, y) -> dict:
    P, y = _check(P, y)
    lo, hi = bootstrap_ci(per_match_brier(P, y))
    return {"n": len(y), "brier": brier(P, y), "brier_ci95": [lo, hi], "log_loss": log_loss(P, y),
            "accuracy": accuracy(P, y), "ece": ece(P, y)}
