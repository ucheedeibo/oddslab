"""Offline tests (no network, no API token). Uses the real strength model on synthetic data."""
import asyncio
import math
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np

from app import evaluation, evidence, provider, service
from app.core.strength import Match

DAY = date(2026, 10, 20)


def run(coro):
    return asyncio.run(coro)


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload


def fake_client(resp):
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return resp
    return C


def api_match(mid, home, away, comp=2021, day="2026-10-17", crest="http://c/x.png"):
    return {"id": mid, "utcDate": f"{day}T15:00:00Z", "status": "TIMED",
            "competition": {"id": comp, "name": "X"},
            "homeTeam": {"name": home, "crest": crest}, "awayTeam": {"name": away, "crest": crest},
            "score": {"fullTime": {"home": None, "away": None}}}


def synthetic_league(teams, seed=1, spread=0.6, start=date(2025, 8, 9), end=date(2026, 10, 18), prefix=""):
    """Weekly rounds with known true strengths. Returns [(date, home, away, hg, ag)]."""
    rng = np.random.RandomState(seed)
    names = [f"{prefix}T{i}" for i in range(teams)]
    att = rng.normal(0, spread, teams)
    dfn = rng.normal(0, spread, teams)
    rows, d = [], start
    while d <= end:
        order = rng.permutation(teams)
        for k in range(0, teams - 1, 2):
            h, a = order[k], order[k + 1]
            lh = math.exp(0.25 + 0.25 + att[h] - dfn[a])
            la = math.exp(0.25 + att[a] - dfn[h])
            rows.append((d, names[h], names[a], int(rng.poisson(lh)), int(rng.poisson(la))))
        d += timedelta(days=7)
    return rows


def rows_for_pair(all_rows, pair):
    lo, hi = (date(2026, 8, 1), date(2027, 7, 31)) if pair[1] == 2026 else (date(2025, 8, 1), date(2026, 7, 31))
    return [{"date": r[0].isoformat(), "home": r[1], "away": r[2], "hg": r[3], "ag": r[4]}
            for r in all_rows if lo <= r[0] <= hi]


def api_fixture(i, home, away, league=2021, status="TIMED"):
    return {"fixture": {"id": i, "date": "2026-10-20T19:00:00Z", "status": {"short": status}},
            "teams": {"home": {"name": home, "logo": "https://l"}, "away": {"name": away, "logo": "https://l"}},
            "league": {"id": league, "name": "X"}, "goals": {}}


class GridShapeTests(unittest.TestCase):
    def test_accepts_nested_list_grid_like_strength_model(self):
        m = service._grid_matrix([[0.1, 0.2], [0.3, 0.4]])
        self.assertAlmostEqual(sum(m.values()), 1.0)
        self.assertAlmostEqual(m[(1, 0)], 0.3)

    def test_accepts_numpy_dict_and_matrix_attribute(self):
        self.assertAlmostEqual(sum(service._grid_matrix(np.array([[1.0, 1.0], [1.0, 1.0]])).values()), 1.0)
        class G: matrix = {(0, 0): 1.0, (1, 0): 3.0}
        self.assertAlmostEqual(service._grid_matrix(G())[(1, 0)], 0.75)

    def test_rejects_empty_or_garbage(self):
        for bad in (None, [], "abc"):
            self.assertIsNone(service._grid_matrix(bad))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        provider._calls.clear()
        provider._finished_cache.clear()
        provider._failed.clear()

    def test_missing_token_does_not_crash(self):
        with patch.dict(os.environ, {}, clear=True):
            fx, err = run(provider.get_fixtures_checked("2026-10-17"))
            self.assertEqual((fx, err["code"]), ([], "missing_token"))
            self.assertEqual(run(provider.get_fixtures("2026-10-17")), [])

    def test_rejected_token_and_rate_limit(self):
        for status, code in ((400, "auth"), (403, "auth"), (429, "rate_limited"), (500, "upstream")):
            with patch.dict(os.environ, {"FOOTBALL_DATA_TOKEN": "t"}), \
                 patch.object(provider.httpx, "AsyncClient", fake_client(FakeResp(status, text="x"))):
                provider._calls.clear()
                fx, err = run(provider.get_fixtures_checked("2026-10-17"))
                self.assertEqual((fx, err["code"]), ([], code))

    def test_normalises_logos_and_filters(self):
        payload = {"matches": [api_match(1, "A", "B"), api_match(2, "C", "D", comp=9999), api_match(3, "E", "F", day="2026-10-18")]}
        with patch.dict(os.environ, {"FOOTBALL_DATA_TOKEN": "t"}), \
             patch.object(provider.httpx, "AsyncClient", fake_client(FakeResp(200, payload))):
            fx, err = run(provider.get_fixtures_checked("2026-10-17"))
        self.assertIsNone(err)
        self.assertEqual(len(fx), 1)
        flat = provider.flatten_fixture(fx[0])
        self.assertTrue(flat["home_logo"].startswith("https://"))

    def test_throttle_gives_up_instead_of_blocking_requests(self):
        provider._calls.extend([provider.time.monotonic()] * provider.MAX_PER_MINUTE)
        with self.assertRaises(provider.ProviderError) as cm:
            run(provider._throttle(max_wait=0.2))
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_cached_history_and_failure_memory(self):
        payload = {"matches": [{"utcDate": "2026-01-01T12:00:00Z", "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
                                "score": {"fullTime": {"home": 1, "away": 0}}}]}
        with patch.dict(os.environ, {"FOOTBALL_DATA_TOKEN": "t"}), \
             patch.object(provider.httpx, "AsyncClient", fake_client(FakeResp(200, payload))):
            self.assertEqual(len(run(provider.get_finished(2021, 2025))), 1)
        self.assertEqual(len(provider.cached_finished(2021, 2025)), 1)
        with patch.dict(os.environ, {"FOOTBALL_DATA_TOKEN": "t"}), \
             patch.object(provider.httpx, "AsyncClient", fake_client(FakeResp(403, text="no"))):
            with self.assertRaises(provider.ProviderError):
                run(provider.get_finished(2014, 2025, strict=True))
        self.assertTrue(provider.recently_failed((2014, 2025)))


def leg(fixture, odds, conf="Medium", market="1X2", lift=1.2):
    p = round(1 / odds, 2)
    return {"fixture_name": fixture, "home_team": fixture, "away_team": "x", "market": market,
            "selection": f"{fixture} {odds}", "probability": p, "odds": odds, "edge": 0.0,
            "score": p, "confidence": conf, "lift": lift, "base_rate": round(p / lift, 2),
            "xG_home": 1, "xG_away": 1, "top_scores": []}


class SlipTests(unittest.TestCase):
    def legs(self):
        out = []
        for i in range(16):
            for o in (1.25, 1.3, 1.4, 1.5, 1.8, 2.2, 2.6):
                out.append(leg(f"M{i}", o, market=f"mk{o}"))
        return out

    def test_each_target_is_reached_without_repeating_matches(self):
        for target in (2.0, 3.0, 5.0):
            slips = service.build_slips(self.legs(), target)
            self.assertTrue(slips, target)
            max_legs, floor = service.tier_rules(target)
            used = []
            self.assertTrue(slips[0]["meets_target"])
            for s in slips:
                self.assertTrue(s["meets_target"])
                self.assertTrue(target * 0.9 <= s["combined_odds"] <= target * 1.12, (target, s["combined_odds"]))
                self.assertLessEqual(len(s["legs"]), max_legs)
                self.assertTrue(all(l["probability"] >= floor for l in s["legs"]))
                used += [l["fixture_name"] for l in s["legs"]]
            self.assertEqual(len(used), len(set(used)))  # a match is never in two slips of a tier

    def test_tier_caps(self):
        self.assertEqual([service.tier_rules(t)[0] for t in (2.0, 3.0, 5.0)], [3, 5, 7])

    def test_more_legs_are_used_to_keep_every_leg_likelier(self):
        legs = []
        for i in range(3):
            legs.append(leg(f"S{i}", 1.26, market=f"s{i}"))  # strong legs
        for i in range(2):
            legs.append(leg(f"W{i}", 1.42, market=f"w{i}"))  # weaker pair also reaches ~2.0
        slip = service.build_slips(legs, 2.0)[0]
        self.assertEqual(len(slip["legs"]), 3)
        self.assertTrue(all(l["fixture_name"].startswith("S") for l in slip["legs"]))

    def test_coin_flip_legs_are_left_out_of_every_tier(self):
        legs = [leg(f"M{i}", 2.17) for i in range(10)]  # about 46% each
        for target in (2.0, 3.0, 5.0):
            self.assertEqual(service.build_slips(legs, target), [])

    def test_low_confidence_matches_never_enter_slips(self):
        legs = [leg(f"M{i}", o, conf="Low") for i in range(6) for o in (1.3, 1.5)]
        self.assertEqual(service.build_slips(legs, 3.0), [])

    def test_legs_below_league_average_are_excluded(self):
        legs = [leg(f"M{i}", o, lift=0.8) for i in range(6) for o in (1.3, 1.5)]
        self.assertEqual(service.build_slips(legs, 3.0), [])

    def test_higher_lift_legs_are_preferred(self):
        legs = []
        for i in range(3):
            legs += [leg(f"M{i}", 1.4, market="1X2", lift=1.05), leg(f"M{i}", 1.4, market=f"hi{i}", lift=1.6)]
        slip = service.build_slips(legs, 2.9)[0]
        self.assertTrue(all(l["lift"] == 1.6 for l in slip["legs"]))

    def test_same_market_is_capped_per_slip(self):
        legs = [leg(f"M{i}", 1.45, market="1X2") for i in range(6)]
        for slip in service.build_slips(legs, 3.0):
            self.assertLessEqual(len(slip["legs"]), service.MAX_SAME_MARKET)

    def test_unreachable_target_is_labelled_closest(self):
        slips = service.build_slips([leg("M0", 1.5), leg("M0", 1.3, market="b")], 5.0)
        self.assertEqual(len(slips), 1)
        self.assertFalse(slips[0]["meets_target"])
        self.assertIn("Closest", slips[0]["note"])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        service._analysis_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.cache_file = os.path.join(self.tmp, "c.json")

    def _run(self, fixtures, cached, error=None, prefetch=None, get_finished=None):
        async def fake_fixtures(d): return fixtures, error
        async def default_finished(lg, season, strict=False, max_wait=0, reserve=0):
            raise provider.ProviderError("rate_limited", "busy")
        queued = prefetch if prefetch is not None else []
        with patch.dict(os.environ, {"SLIPS_DIR": self.tmp}), \
             patch.object(provider, "get_fixtures_checked", fake_fixtures), \
             patch.object(provider, "cached_finished", lambda lg, s: cached.get((lg, s))), \
             patch.object(provider, "get_finished", get_finished or default_finished), \
             patch.object(provider, "prefetch_background", lambda pairs: queued.extend(pairs) or len(pairs)):
            return run(service.build_predictions(DAY, use_cache=False)), run(service.build_day(DAY, 3.0)), queued

    def test_every_rated_match_gets_a_prediction_with_confidence(self):
        rows = synthetic_league(20)
        cached = {(2021, 2026): rows_for_pair(rows, (2021, 2026)), (2021, 2025): rows_for_pair(rows, (2021, 2025))}
        fixtures = [api_fixture(i, f"T{2 * i}", f"T{2 * i + 1}") for i in range(6)]
        fixtures.append(api_fixture(99, "T0", "NoSuchTeam"))
        preds, slips, _ = self._run(fixtures, cached)
        self.assertEqual(preds["status"], "ok")
        self.assertEqual(preds["count"], 6)
        for f in preds["predictions"]:
            p = f["prediction"]
            self.assertAlmostEqual(p["p_home"] + p["p_draw"] + p["p_away"], 100, delta=0.5)
            self.assertIn(f["confidence"], ("Medium", "Low"))
            self.assertIn("why", f)
            self.assertTrue(f["why"]["rows"])
            self.assertTrue(all(r["sample"] for r in f["why"]["rows"]))
            self.assertTrue(f["home_logo"])
        self.assertEqual(len(preds["skipped"]), 1)
        self.assertNotIn("match_predictions", slips)
        self.assertTrue(slips["slips"])
        self.assertTrue(os.path.exists(self.cache_file))

    def test_slip_legs_carry_crests_and_their_own_evidence(self):
        rows = synthetic_league(20)
        cached = {(2021, 2026): rows_for_pair(rows, (2021, 2026)), (2021, 2025): rows_for_pair(rows, (2021, 2025))}
        fixtures = [api_fixture(i, f"T{2 * i}", f"T{2 * i + 1}") for i in range(7)]
        _, slips, _ = self._run(fixtures, cached)
        self.assertTrue(slips["slips"])
        for slip in slips["slips"]:
            for l in slip["legs"]:
                self.assertEqual(l["home_logo"], "https://l")
                self.assertIn("fixture_id", l)
                if l["why"]:
                    self.assertTrue(l["why"]["rows"])
                    self.assertEqual(len(l["why"]["samples"]), 2)

    def test_champions_league_uses_pooled_history_and_flags_thin_data(self):
        league_rows = {}
        cached = {}
        for lg, seed in ((2021, 1), (2014, 2), (2019, 3), (2002, 4), (2015, 5), (2003, 6), (2017, 7)):
            league_rows[lg] = synthetic_league(14, seed=seed, prefix=f"L{lg}-")
        cl_rows = []
        rng = np.random.RandomState(9)
        for d in (date(2025, 9, 20), date(2025, 10, 20), date(2025, 11, 20), date(2026, 2, 20), date(2026, 3, 11)):
            for lg_a, lg_b in ((2021, 2014), (2019, 2002), (2015, 2003)):
                cl_rows.append((d, f"L{lg_a}-T1", f"L{lg_b}-T2", int(rng.poisson(1.6)), int(rng.poisson(1.2))))
        cl_rows.append((date(2026, 10, 1), "Newcomer FC", "L2021-T1", 0, 3))
        cl_rows.append((date(2026, 10, 15), "L2014-T2", "Newcomer FC", 2, 0))
        for lg in (2021, 2014, 2019, 2002, 2015, 2003, 2017):
            for season in (2026, 2025):
                cached[(lg, season)] = rows_for_pair(league_rows[lg], (lg, season))
        for season in (2026, 2025):
            cached[(2001, season)] = rows_for_pair(cl_rows, (2001, season))
        fixtures = [api_fixture(1, "L2021-T1", "L2014-T2", league=2001),
                    api_fixture(2, "Newcomer FC", "L2019-T2", league=2001)]
        preds, _, _ = self._run(fixtures, cached)
        self.assertEqual(preds["status"], "ok", preds["notes"])
        by_home = {f["home_team"]: f for f in preds["predictions"]}
        self.assertEqual(by_home["L2021-T1"]["confidence"], "Medium")  # High is not used until backtests justify it
        self.assertEqual(by_home["Newcomer FC"]["confidence"], "Low")
        why = by_home["Newcomer FC"].get("why")
        if why:
            self.assertTrue(any(r["thin"] for r in why["rows"]))
            self.assertIn("low", why["verdict"].lower())

    def test_missing_history_reports_loading_and_queues_background_fetch(self):
        queued = []
        fixtures = [api_fixture(1, "A", "B", league=2001)]
        preds, _, queued = self._run(fixtures, {}, prefetch=queued)
        self.assertEqual(preds["status"], "loading")
        self.assertEqual(preds["count"], 0)
        self.assertTrue(queued)
        self.assertIn("background", preds["skipped"][0]["reason"])

    def test_provider_error_and_no_fixtures(self):
        preds, slips, _ = self._run([], {}, error={"code": "auth", "message": "token rejected"})
        self.assertEqual((preds["status"], preds["message"]), ("provider_error", "token rejected"))
        preds, slips, _ = self._run([], {})
        self.assertEqual(preds["status"], "no_fixtures")
        self.assertIn("Tuesday", preds["message"])
        self.assertFalse(os.path.exists(self.cache_file))


class EvidenceTests(unittest.TestCase):
    ROWS = ([{"date": f"2026-09-{d:02d}", "home": "Alpha", "away": "Beta", "hg": hg, "ag": ag}
             for d, (hg, ag) in enumerate([(1, 0), (2, 2), (0, 0), (3, 1), (1, 1)], start=1)]
            + [{"date": "2026-10-10", "home": "Gamma", "away": "Alpha", "hg": 0, "ag": 2}])

    def test_facts_are_counted_and_leak_free(self):
        idx = evidence.index_history(self.ROWS + self.ROWS)  # duplicates ignored
        f = evidence.team_facts(idx, "Alpha", "2026-10-11")
        self.assertEqual((f["n"], f["wins"], f["draws"], f["losses"]), (6, 3, 3, 0))
        self.assertEqual(f["scored_in"], 5)
        self.assertEqual(evidence.team_facts(idx, "Alpha", "2026-09-03")["n"], 2)  # only matches before the date

    def test_thin_samples_are_flagged_and_limit_the_verdict(self):
        idx = evidence.index_history(self.ROWS)
        fh, fa = evidence.team_facts(idx, "Alpha", "2026-10-11"), evidence.team_facts(idx, "Beta", "2026-10-11")
        w = evidence.build_why("btts", "Alpha", "Beta", fh, fa, 0.6, "Low", None)
        self.assertTrue(any(r["thin"] for r in w["rows"]))
        self.assertIn("held back by", w["verdict"])
        self.assertIn("low", w["verdict"].lower())
        self.assertEqual(w["fair_odds"], round(1 / 0.6, 2))

    def test_every_market_builds_rows_from_counts_only(self):
        idx = evidence.index_history(self.ROWS)
        fh, fa = evidence.team_facts(idx, "Alpha", "2026-10-11"), evidence.team_facts(idx, "Beta", "2026-10-11")
        for market in ("btts", "over25", "home_win", "away_win", "draw"):
            w = evidence.build_why(market, "Alpha", "Beta", fh, fa, 0.5, "Medium", None)
            self.assertTrue(w["rows"], market)
            for r in w["rows"]:
                self.assertIn(r["tag"], ("supports", "caution", "neutral"))
        self.assertIsNone(evidence.build_why("nonsense", "A", "B", fh, fa, 0.5, "Low", None))

    def test_league_base_rates_and_lift_row(self):
        rows = [{"date": "2026-01-01", "home": "A", "away": "B", "hg": h, "ag": a}
                for h, a in [(1, 0), (2, 2), (0, 0), (3, 1), (1, 1), (0, 1)] * 6]
        rates = evidence.base_rates(rows)
        self.assertAlmostEqual(rates["home"] + rates["draw"] + rates["away"], 1.0)
        self.assertIsNone(evidence.base_rates(rows[:5]))
        self.assertAlmostEqual(evidence.base_for("dc1x", rates), rates["home"] + rates["draw"])
        idx = evidence.index_history(rows)
        f = evidence.team_facts(idx, "A", "2026-12-01")
        w = evidence.build_why("over25", "A", "B", f, f, rates["over25"] * 1.3, "Medium", rates)
        lift_row = w["rows"][-1]
        self.assertEqual((lift_row["title"], lift_row["tag"]), ("Versus league average", "supports"))

    def test_lean_market_mapping(self):
        self.assertEqual(evidence.lean_market("Napoli Win", "Napoli", "Bodo"), "home_win")
        self.assertEqual(evidence.lean_market("Bodo Win", "Napoli", "Bodo"), "away_win")
        self.assertEqual(evidence.lean_market("BTTS Yes", "Napoli", "Bodo"), "btts")
        self.assertIsNone(evidence.lean_market("Something else", "Napoli", "Bodo"))


class EvaluationTests(unittest.TestCase):
    def test_model_beats_baseline_on_synthetic_league(self):
        rows = synthetic_league(20, seed=3, spread=0.6)
        cached = {(2021, 2026): rows_for_pair(rows, (2021, 2026)), (2021, 2025): rows_for_pair(rows, (2021, 2025))}
        with patch.object(provider, "cached_finished", lambda lg, s: cached.get((lg, s))):
            out = run(evaluation.evaluate(2021, cuts=6, day=DAY))
        self.assertEqual(out["status"], "ok")
        self.assertGreater(out["matches_scored"], 100)
        self.assertTrue(out["better_than_baseline"], out)
        self.assertTrue(out["calibration"])
        self.assertEqual(set(out["markets"]), {"over25", "btts", "over15"})
        for m in out["markets"].values():
            self.assertGreater(m["matches_scored"], 100)
            self.assertIn("brier", m["model"])
            self.assertTrue(m["calibration"])

    def test_reports_loading_when_history_missing(self):
        with patch.object(provider, "cached_finished", lambda lg, s: None), \
             patch.object(provider, "prefetch_background", lambda pairs: 0), \
             patch.object(provider, "recently_failed", lambda p: False):
            async def busy(*a, **k): raise provider.ProviderError("rate_limited", "busy")
            with patch.object(provider, "get_finished", busy):
                out = run(evaluation.evaluate(2021, day=DAY))
        self.assertEqual(out["status"], "loading")


if __name__ == "__main__":
    unittest.main()