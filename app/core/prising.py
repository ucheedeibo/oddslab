"""Selection probabilities from a score grid. Only options the goals model can honestly price."""
from __future__ import annotations
import numpy as np

OU_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
TT_LINES = (0.5, 1.5, 2.5)


def selection_probs(m: np.ndarray) -> dict[tuple[str, str], float]:
    h, a = np.indices(m.shape)
    H, D, A = (float(m[h > a].sum()), float(m[h == a].sum()), float(m[h < a].sum()))
    out = {("1X2", "H"): H, ("1X2", "D"): D, ("1X2", "A"): A,
           ("DC", "HD"): H + D, ("DC", "HA"): H + A, ("DC", "DA"): D + A}
    y = float(m[(h > 0) & (a > 0)].sum())
    out[("BTTS", "Y")], out[("BTTS", "N")] = y, 1 - y
    tot = h + a
    for L in OU_LINES:
        o = float(m[tot > L].sum())
        out[("OU", f"O{L}")], out[("OU", f"U{L}")] = o, 1 - o
    for L in TT_LINES:
        for mk, g in (("TTH", h), ("TTA", a)):
            o = float(m[g > L].sum())
            out[(mk, f"O{L}")], out[(mk, f"U{L}")] = o, 1 - o
    return out


def label(market: str, sel: str, home: str, away: str) -> str:
    if market == "1X2":
        return {"H": f"{home} to win", "D": "Draw", "A": f"{away} to win"}[sel]
    if market == "DC":
        return {"HD": f"{home} or draw", "HA": f"{home} or {away} (no draw)", "DA": f"{away} or draw"}[sel]
    if market == "BTTS":
        return "Both teams to score: " + ("Yes" if sel == "Y" else "No")
    side, line = ("Over" if sel[0] == "O" else "Under"), sel[1:]
    if market == "OU":
        return f"{side} {line} goals"
    team = home if market == "TTH" else away
    return f"{team} {side.lower()} {line} goals"
