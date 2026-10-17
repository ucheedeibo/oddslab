"""Parse and normalize API-Football odds structures into internal odds maps across all available markets."""
from __future__ import annotations
from collections import defaultdict


def parse_odds(odds_response: dict) -> tuple[dict[tuple[str, str], dict], dict[str, int]]:
    """
    Parses API-Football odds response for a fixture.
    Returns:
        odds_map: {(market_type, selection_key): {"odds": float, "n_books": int}}
        unmapped: {raw_bet_name: count}
    """
    odds_map = defaultdict(list)
    unmapped = defaultdict(int)

    bookmakers = odds_response.get("bookmakers", [])
    for bm in bookmakers:
        bets = bm.get("bets", [])
        for bet in bets:
            name = bet.get("name", "").strip()
            values = bet.get("values", [])

            for val in values:
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

    # Aggregate median odds across bookmakers
    final_odds = {}
    for k, odds_list in odds_map.items():
        if odds_list:
            odds_list.sort()
            mid = len(odds_list) // 2
            median_odd = odds_list[mid] if len(odds_list) % 2 != 0 else (odds_list[mid - 1] + odds_list[mid]) / 2
            final_odds[k] = {"odds": round(median_odd, 3), "n_books": len(odds_list)}

    return final_odds, dict(unmapped)


def _map_market(bet_name: str, label: str) -> tuple[str, str] | None:
    """Map external bookmaker market names and labels to internal representation."""
    b = bet_name.lower()
    l = label.lower()

    # --- 1. MATCH RESULT MARKETS ---
    if b in ("match winner", "1x2", "full time result", "fulltime result"):
        if l in ("home", "1"): return ("1X2", "H")
        if l in ("draw", "x"): return ("1X2", "D")
        if l in ("away", "2"): return ("1X2", "A")

    if b in ("first half winner", "1st half 1x2", "half time result"):
        if l in ("home", "1"): return ("HT_1X2", "H")
        if l in ("draw", "x"): return ("HT_1X2", "D")
        if l in ("away", "2"): return ("HT_1X2", "A")

    if b in ("second half winner", "2nd half 1x2"):
        if l in ("home", "1"): return ("2H_1X2", "H")
        if l in ("draw", "x"): return ("2H_1X2", "D")
        if l in ("away", "2"): return ("2H_1X2", "A")

    if "double chance" in b:
        if "first half" in b or "1st half" in b:
            if "home/draw" in l or "1x" in l: return ("HT_DC", "1X")
            if "home/away" in l or "12" in l: return ("HT_DC", "12")
            if "draw/away" in l or "x2" in l: return ("HT_DC", "X2")
        else:
            if "home/draw" in l or "1x" in l: return ("DC", "1X")
            if "home/away" in l or "12" in l: return ("DC", "12")
            if "draw/away" in l or "x2" in l: return ("DC", "X2")

    if "draw no bet" in b or b == "dnb":
        if "first half" in b or "1st half" in b:
            if l in ("home", "1"): return ("HT_DNB", "H")
            if l in ("away", "2"): return ("HT_DNB", "A")
        else:
            if l in ("home", "1"): return ("DNB", "H")
            if l in ("away", "2"): return ("DNB", "A")

    if "asian handicap" in b:
        return ("AH", f"{label}")

    if "handicap result" in b or "european handicap" in b or "three-way handicap" in b:
        return ("EH", f"{label}")

    if "half time / full time" in b or "ht/ft" in b:
        return ("HT_FT", f"{label.upper()}")

    if "win to nil" in b:
        if "home" in l or "1" in l: return ("WIN_TO_NIL", "H")
        if "away" in l or "2" in l: return ("WIN_TO_NIL", "A")

    if "to qualify" in b:
        if "home" in l or "1" in l: return ("QUALIFY", "H")
        if "away" in l or "2" in l: return ("QUALIFY", "A")

    if "win either half" in b:
        if "home" in l or "1" in l: return ("WIN_EITHER_HALF", "H")
        if "away" in l or "2" in l: return ("WIN_EITHER_HALF", "A")

    if "win both halves" in b:
        if "home" in l or "1" in l: return ("WIN_BOTH_HALVES", "H")
        if "away" in l or "2" in l: return ("WIN_BOTH_HALVES", "A")

    # --- 2. GOALS MARKETS ---
    if "both teams to score" in b or b == "btts":
        if "first half" in b or "1st half" in b:
            if l in ("yes", "y"): return ("HT_BTTS", "Y")
            if l in ("no", "n"): return ("HT_BTTS", "N")
        elif "second half" in b or "2nd half" in b:
            if l in ("yes", "y"): return ("2H_BTTS", "Y")
            if l in ("no", "n"): return ("2H_BTTS", "N")
        else:
            if l in ("yes", "y"): return ("BTTS", "Y")
            if l in ("no", "n"): return ("BTTS", "N")

    if "goals over/under" in b or "total goals" in b or b == "over/under":
        parts = label.split()
        if len(parts) >= 2:
            direction, line = parts[0].lower(), parts[1]
            prefix = "O" if direction in ("over", "o") else "U" if direction in ("under", "u") else ""
            if prefix:
                if "first half" in b or "1st half" in b: return ("HT_OU", f"{prefix}{line}")
                if "second half" in b or "2nd half" in b: return ("2H_OU", f"{prefix}{line}")
                return ("OU", f"{prefix}{line}")

    if "home team exact goals" in b or "home goals" in b or "home team total" in b:
        parts = label.split()
        if len(parts) >= 2:
            direction, line = parts[0].lower(), parts[1]
            if direction in ("over", "o"): return ("TTH", f"O{line}")
            if direction in ("under", "u"): return ("TTH", f"U{line}")

    if "away team exact goals" in b or "away goals" in b or "away team total" in b:
        parts = label.split()
        if len(parts) >= 2:
            direction, line = parts[0].lower(), parts[1]
            if direction in ("over", "o"): return ("TTA", f"O{line}")
            if direction in ("under", "u"): return ("TTA", f"U{line}")

    if "clean sheet" in b:
        if "home" in b or "home" in l:
            return ("CLEAN_SHEET_H", "Y" if l in ("yes", "y") else "N")
        if "away" in b or "away" in l:
            return ("CLEAN_SHEET_A", "Y" if l in ("yes", "y") else "N")

    if "correct score" in b:
        if "first half" in b or "1st half" in b: return ("HT_CS", label)
        if "second half" in b or "2nd half" in b: return ("2H_CS", label)
        return ("CS", label)

    if "exact total goals" in b or "exact goals" in b: return ("EXACT_GOALS", label)
    if "odd/even" in b and "corners" not in b and "cards" not in b: return ("GOALS_ODD_EVEN", label.upper())
    if "first team to score" in b: return ("FIRST_TEAM_SCORE", label)
    if "last team to score" in b: return ("LAST_TEAM_SCORE", label)

    # --- 3. CORNER MARKETS ---
    if "corner" in b:
        if "total" in b or "over/under" in b:
            parts = label.split()
            if len(parts) >= 2:
                direction, line = parts[0].lower(), parts[1]
                prefix = "O" if direction in ("over", "o") else "U"
                if "first half" in b or "1st half" in b: return ("HT_CORNERS_OU", f"{prefix}{line}")
                return ("CORNERS_OU", f"{prefix}{line}")
        if "handicap" in b: return ("CORNERS_AH", label)
        if "1x2" in b or "winner" in b: return ("CORNERS_1X2", label.upper())

    # --- 4. CARDS AND BOOKINGS ---
    if "card" in b or "booking" in b:
        if "total" in b or "over/under" in b:
            parts = label.split()
            if len(parts) >= 2:
                direction, line = parts[0].lower(), parts[1]
                prefix = "O" if direction in ("over", "o") else "U"
                return ("CARDS_OU", f"{prefix}{line}")
        if "red card" in b: return ("RED_CARD", "Y" if l in ("yes", "y") else "N")

    # --- 5. PLAYER MARKETS ---
    if "anytime goalscorer" in b: return ("ANYTIME_GOALSCORER", label)
    if "first goalscorer" in b: return ("FIRST_GOALSCORER", label)
    if "last goalscorer" in b: return ("LAST_GOALSCORER", label)
    if "player to be booked" in b or "card" in b and "player" in b: return ("PLAYER_CARD", label)
    if "player shots" in b: return ("PLAYER_SHOTS", label)

    # --- 6. SHOTS AND MATCH STATS ---
    if "shots on target" in b: return ("SHOTS_ON_TARGET", label)
    if "total shots" in b: return ("TOTAL_SHOTS", label)
    if "fouls" in b: return ("FOULS", label)
    if "offsides" in b: return ("OFFSIDES", label)

    # --- 7. COMBINED MARKETS ---
    if "result/both teams to score" in b or "match result and both teams to score" in b: return ("1X2_BTTS", label)
    if "result/total goals" in b or "match result and over/under" in b: return ("1X2_OU", label)
    if "double chance and over/under" in b or "double chance & total" in b: return ("DC_OU", label)

    # --- 8. OUTRIGHT MARKETS ---
    if "winner" in b or "outright" in b: return ("OUTRIGHT", label)

    return None
