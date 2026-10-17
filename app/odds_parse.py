"""Turn an API-Football /odds response into {(market, selection): {odds, n_books}}.
Only names we recognise are used; everything else is reported as unmapped, never guessed.
NOTE: response shape (bookmakers -> bets -> values[{value, odd}]) must be confirmed against your plan;
the app reports what it could and could not read so you can see it."""
from __future__ import annotations
import math, re, statistics
from app.core.pricing import OU_LINES, TT_LINES

NAME_MAP = {"match winner": "1X2", "double chance": "DC", "both teams score": "BTTS",
            "both teams to score": "BTTS", "goals over/under": "OU",
            "total - home": "TTH", "total - away": "TTA"}
_1X2 = {"home": "H", "draw": "D", "away": "A"}
_DC = {"home/draw": "HD", "home/away": "HA", "draw/away": "DA"}
_YN = {"yes": "Y", "no": "N"}
_LINE = re.compile(r"^(over|under)\s+(\d+(?:\.\d+)?)$")


def _sel(market: str, value: str):
    v = str(value).strip().lower()
    if market == "1X2": return _1X2.get(v)
    if market == "DC": return _DC.get(v)
    if market == "BTTS": return _YN.get(v)
    m = _LINE.match(v)
    if not m: return None
    line = float(m.group(2))
    allowed = OU_LINES if market == "OU" else TT_LINES
    return f"{'O' if m.group(1) == 'over' else 'U'}{line}" if line in allowed else None


def parse_odds(response: list[dict]) -> tuple[dict, dict]:
    found: dict[tuple[str, str], list[float]] = {}
    unmapped: dict[str, int] = {}
    for item in response or []:
        for bk in item.get("bookmakers") or []:
            for bet in bk.get("bets") or []:
                name = str(bet.get("name", "")).strip().lower()
                market = NAME_MAP.get(name)
                if market is None:
                    unmapped[bet.get("name", "?")] = unmapped.get(bet.get("name", "?"), 0) + 1
                    continue
                for v in bet.get("values") or []:
                    sel = _sel(market, v.get("value", ""))
                    try:
                        odd = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if sel and math.isfinite(odd) and odd > 1.0:
                        found.setdefault((market, sel), []).append(odd)
    odds = {k: {"odds": round(statistics.median(v), 3), "n_books": len(v)} for k, v in found.items()}
    return odds, unmapped
