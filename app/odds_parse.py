"""Parse and normalize API-Football odds structures into internal odds maps."""
from __future__ import annotations
from collections import defaultdict


def parse_odds(odds_response: dict) -> tuple[dict[tuple[str, str], dict], dict[str, int]]:
    odds_map = defaultdict(list)
    unmapped = defaultdict(int)

    bookmakers = odds_response.get("bookmakers", [])
    for bm in bookmakers:
        for bet in bm.get("bets", []):
            name = bet.get("name", "").strip()
            for val in bet.get("values", []):
                odd_val = val.get("odd")
                if odd_val is None:
                    continue
                try:
                    odd = float(odd_val)
                except (ValueError, TypeError):
                    continue

                raw_label = str(val.get("value", "")).strip()
                key = _map_market(name, raw_label)
                if key:
                    odds_map[key].append(odd)
                else:
                    unmapped[name] += 1

    final_odds = {}
    for k, odds_list in odds_map.items():
        if odds_list:
            odds_list.sort()
            mid = len(odds_list) // 2
            median_odd = odds_list[mid] if len(odds_list) % 2 != 0 else (odds_list[mid - 1] + odds_list[mid]) / 2
            final_odds[k] = {"odds": round(median_odd, 3), "n_books": len(odds_list)}

    return final_odds, dict(unmapped)


def _map_market(bet_name: str, label: str) -> tuple[str, str] | None:
    b = bet_name.lower()
    l = label.lower()

    if b in ("match winner", "1x2", "full time result"):
        if l in ("home", "1"): return ("1X2", "H")
        if l in ("draw", "x"): return ("1X2", "D")
        if l in ("away", "2"): return ("1X2", "A")

    if "double chance" in b:
        if "home/draw" in l or "1x" in l: return ("DC", "1X")
        if "home/away" in l or "12" in l: return ("DC", "12")
        if "draw/away" in l or "x2" in l: return ("DC", "X2")

    if "both teams to score" in b or b == "btts":
        if l in ("yes", "y"): return ("BTTS", "Y")
        if l in ("no", "n"): return ("BTTS", "N")

    if "goals over/under" in b or "over/under" in b:
        parts = label.split()
        if len(parts) >= 2:
            direction, line = parts[0].lower(), parts[1]
            if direction in ("over", "o"): return ("OU", f"O{line}")
            if direction in ("under", "u"): return ("OU", f"U{line}")

    if "draw no bet" in b or b == "dnb":
        if l in ("home", "1"): return ("DNB", "H")
        if l in ("away", "2"): return ("DNB", "A")

    return None
