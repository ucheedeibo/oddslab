"""Evidence behind a pick, counted from past results (adapted from the handoff's sample_logic.py).

Everything shown is a counted fact with its sample size. Nothing is free-form.
It reads the match history the app has already downloaded, so it needs no extra API calls.
Thin samples (fewer than THIN matches) are flagged, and confidence follows the thinner team.
"""
WANTED = 10   # matches looked at per team
THIN = 8      # fewer than this counts as a thin sample
MIN_BASE = 30  # league matches needed before a base rate is trusted

BASE_KEYS = {"home_win": "home", "away_win": "away", "draw": "draw", "over25": "over25", "btts": "btts"}


def index_history(rows) -> dict:
    """{team: [(iso_date, goals_for, goals_against), ...]} newest first, duplicates removed."""
    seen, by_team = set(), {}
    for r in rows:
        key = (r["date"], r["home"], r["away"])
        if key in seen:
            continue
        seen.add(key)
        by_team.setdefault(r["home"], []).append((r["date"], r["hg"], r["ag"]))
        by_team.setdefault(r["away"], []).append((r["date"], r["ag"], r["hg"]))
    for games in by_team.values():
        games.sort(reverse=True)
    return by_team


def team_facts(index: dict, team: str, before: str, n: int = WANTED) -> dict:
    games = [g for g in index.get(team, []) if g[0] < before][:n]
    k = len(games)
    return {
        "n": k,
        "scored_in": sum(gf > 0 for _, gf, _ in games),
        "conceded_in": sum(ga > 0 for _, _, ga in games),
        "btts_in": sum(gf > 0 and ga > 0 for _, gf, ga in games),
        "over25_in": sum(gf + ga >= 3 for _, gf, ga in games),
        "wins": sum(gf > ga for _, gf, ga in games),
        "draws": sum(gf == ga for _, gf, ga in games),
        "losses": sum(gf < ga for _, gf, ga in games),
        "unbeaten": sum(gf >= ga for _, gf, ga in games),
        "tg_avg": sum(gf + ga for _, gf, ga in games) / k if k else None,
    }


def base_rates(rows):
    """How often each market lands in this competition. None when the sample is too small."""
    n = len(rows)
    if n < MIN_BASE:
        return None
    c = lambda f: sum(1 for r in rows if f(r["hg"], r["ag"])) / n
    return {
        "n": n,
        "home": c(lambda h, a: h > a),
        "draw": c(lambda h, a: h == a),
        "away": c(lambda h, a: h < a),
        "over15": c(lambda h, a: h + a >= 2),
        "over25": c(lambda h, a: h + a >= 3),
        "btts": c(lambda h, a: h > 0 and a > 0),
    }


def base_for(key: str, rates):
    """Base rate for a market key, or None."""
    if not rates:
        return None
    if key in ("home", "draw", "away", "over15", "over25", "btts"):
        return rates[key]
    if key == "dc1x":
        return rates["home"] + rates["draw"]
    if key == "dcx2":
        return rates["away"] + rates["draw"]
    if key == "dnb_home":
        return rates["home"] / max(rates["home"] + rates["away"], 1e-9)
    if key == "dnb_away":
        return rates["away"] / max(rates["home"] + rates["away"], 1e-9)
    return None


def sample_label(n: int) -> str:
    return f"last {WANTED}" if n >= WANTED else f"{n} of {WANTED}"


def _tag(k, n, hi, lo):
    if n == 0:
        return "neutral"
    r = k / n
    return "supports" if r >= hi else "caution" if r <= lo else "neutral"


def _row(title, verb, k, n, hi=0.7, lo=0.4):
    if n == 0:
        return {"title": title, "text": "No matches found.", "tag": "neutral", "sample": f"0 of {WANTED}", "thin": True}
    return {"title": title, "text": f"{verb} {k} of {n} matches.", "tag": _tag(k, n, hi, lo),
            "sample": sample_label(n), "thin": n < THIN}


def _thin_row(name, n):
    return {"title": f"{name} sample is small", "tag": "caution", "sample": f"{n} of {WANTED}", "thin": True,
            "text": f"Only {n} matches found, so read their numbers with care."}


def _lift_row(model_p, base, n_base):
    if base is None:
        return {"title": "League average", "tag": "neutral", "sample": "no data", "thin": True,
                "text": "Not enough league matches to compare with."}
    lift = model_p / base if base else 1.0
    tag = "supports" if lift >= 1.10 else "caution" if lift <= 0.90 else "neutral"
    return {"title": "Versus league average", "tag": tag, "sample": f"{n_base} league matches", "thin": False,
            "text": f"Model gives {model_p * 100:.0f}%; this lands in {base * 100:.0f}% of matches in this competition."}


def _btts_rows(hn, fh, an, fa):
    rows = []
    for name, f in ((hn, fh), (an, fa)):
        n = f["n"]
        rows.append(_row(f"{name} scoring", "Scored in", f["scored_in"], n))
        rows.append(_row(f"{name} defence", "Conceded in", f["conceded_in"], n))
        rows.append(_row(f"BTTS in {name} games", "Both teams scored in", f["btts_in"], n, hi=0.6, lo=0.4))
    return rows


def _over_rows(hn, fh, an, fa):
    rows = []
    for name, f in ((hn, fh), (an, fa)):
        n = f["n"]
        rows.append(_row(f"Over 2.5 in {name} games", "Over 2.5 goals in", f["over25_in"], n, hi=0.6, lo=0.4))
        if n:
            tg = f["tg_avg"]
            tag = "supports" if tg >= 2.9 else "caution" if tg <= 2.2 else "neutral"
            rows.append({"title": f"{name} total goals", "tag": tag, "sample": sample_label(n), "thin": n < THIN,
                         "text": f"{tg:.1f} goals per match, both teams combined."})
    return rows


def _win_rows(pick, fp, opp, fo):
    rows = [_row(f"{pick} wins", "Won", fp["wins"], fp["n"], hi=0.6, lo=0.3),
            _row(f"{pick} unbeaten", "Did not lose in", fp["unbeaten"], fp["n"], hi=0.8, lo=0.5)]
    n = fo["n"]
    if n == 0:
        rows.append(_row(f"{opp} losses", "Lost", 0, 0))
    else:
        tag = "supports" if fo["losses"] / n >= 0.5 else "neutral"
        rows.append({"title": f"{opp} losses", "tag": tag, "sample": sample_label(n), "thin": n < THIN,
                     "text": f"Lost {fo['losses']} of {n} matches."})
        if fo["wins"] / n >= 0.6:
            rows.append({"title": f"{opp} in form", "tag": "caution", "sample": sample_label(n), "thin": n < THIN,
                         "text": f"Won {fo['wins']} of {n} matches."})
    return rows


def _draw_rows(hn, fh, an, fa):
    return [_row(f"Draws in {name} games", "Drew", f["draws"], f["n"], hi=0.35, lo=0.15)
            for name, f in ((hn, fh), (an, fa))]


def summary(rows) -> dict:
    return {t: sum(r["tag"] == t for r in rows) for t in ("supports", "caution", "neutral")}


def verdict(rows, conf, thin_names) -> str:
    s = summary(rows)
    text = f"{s['supports']} of {len(rows)} checks support this pick"
    if s["caution"]:
        text += f" and {s['caution']} urge caution"
    text += f". Data confidence is {conf.lower()}"
    if thin_names:
        text += f", held back by {', '.join(thin_names)}"
    return text + "."


def lean_market(selection: str, home: str, away: str):
    """Maps the model's lean (e.g. 'Napoli Win') to an evidence market, or None."""
    if selection == f"{home} Win":
        return "home_win"
    if selection == f"{away} Win":
        return "away_win"
    if selection == "Draw":
        return "draw"
    if selection == "Over 2.5 Goals":
        return "over25"
    if selection == "BTTS Yes":
        return "btts"
    return None


def build_why(market: str, home: str, away: str, fh: dict, fa: dict, model_p: float, conf: str, rates):
    """Evidence panel for one pick. Returns a JSON-ready dict, or None for an unknown market."""
    if market == "btts":
        rows = _btts_rows(home, fh, away, fa)
    elif market == "over25":
        rows = _over_rows(home, fh, away, fa)
    elif market == "home_win":
        rows = _win_rows(home, fh, away, fa)
    elif market == "away_win":
        rows = _win_rows(away, fa, home, fh)
    elif market == "draw":
        rows = _draw_rows(home, fh, away, fa)
    else:
        return None
    thin = [name for name, f in ((home, fh), (away, fa)) if f["n"] < THIN]
    for name, f in ((home, fh), (away, fa)):
        if f["n"] < THIN:
            rows.append(_thin_row(name, f["n"]))
    base = base_for(BASE_KEYS[market], rates)
    rows.append(_lift_row(model_p, base, rates["n"] if rates else 0))
    return {
        "market": market,
        "model_p": round(model_p, 3),
        "fair_odds": round(1 / max(model_p, 0.001), 2),
        "confidence": conf,
        "samples": [{"name": home, "n": fh["n"]}, {"name": away, "n": fa["n"]}],
        "summary": summary(rows),
        "verdict": verdict(rows, conf, thin),
        "rows": rows,
    }
