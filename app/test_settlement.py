"""Offline tests for saving and settling slips (no network, no token)."""
import asyncio
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app import provider, settlement, slipstore

DAY = (date.today() - timedelta(days=1)).isoformat()


def run(coro):
    return asyncio.run(coro)


def leg(fid, key, odds=1.5, **kw):
    return {"fixture_id": fid, "fixture_name": f"H{fid} vs A{fid}", "home_team": f"H{fid}",
            "away_team": f"A{fid}", "selection": f"{key} pick", "base_key": key, "odds": odds, **kw}


def item(fid, hg, ag, status="FINISHED"):
    return {"fixture": {"id": fid, "status": {"short": status}},
            "teams": {"home": {"name": f"H{fid}"}, "away": {"name": f"A{fid}"}},
            "goals": {"home": hg, "away": ag}}


def slip(*legs):
    return {"combined_odds": 2.0, "probability": 0.5, "legs": list(legs)}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = patch.dict(os.environ, {"SLIPS_DIR": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def settle(self, items, day=DAY):
        calls = []

        async def fake(d):
            calls.append(d)
            if isinstance(items, Exception):
                raise items
            return items

        with patch.object(provider, "get_fixtures", fake):
            out = run(settlement.settle_daily_slips(day))
        return out, calls


class LegTests(unittest.TestCase):
    def test_every_market_key(self):
        cases = [("home", 2, 1, "WON"), ("home", 1, 1, "LOST"), ("away", 0, 1, "WON"),
                 ("dc1x", 1, 1, "WON"), ("dc1x", 0, 1, "LOST"), ("dcx2", 1, 1, "WON"), ("dcx2", 1, 0, "LOST"),
                 ("over15", 1, 1, "WON"), ("over15", 1, 0, "LOST"), ("over25", 2, 1, "WON"), ("over25", 1, 1, "LOST"),
                 ("btts", 1, 1, "WON"), ("btts", 1, 0, "LOST"),
                 ("dnb_home", 1, 1, "VOID"), ("dnb_home", 2, 0, "WON"), ("dnb_home", 0, 1, "LOST"),
                 ("dnb_away", 1, 1, "VOID"), ("dnb_away", 0, 2, "WON"), ("dnb_away", 1, 0, "LOST")]
        for key, h, a, want in cases:
            self.assertEqual(settlement.evaluate_leg({"base_key": key}, h, a), want, (key, h, a))

    def test_old_slips_without_a_base_key_still_settle(self):
        old = {"selection": "Liverpool FC Win", "home_team": "Liverpool FC", "away_team": "Villarreal CF"}
        self.assertEqual(settlement.evaluate_leg(old, 2, 0), "WON")
        self.assertEqual(settlement.evaluate_leg(old, 0, 1), "LOST")


class StoreTests(Base):
    def test_every_tier_is_kept_and_never_overwritten(self):
        self.assertTrue(slipstore.save_once(DAY, 2.0, [slip(leg(1, "home"))]))
        self.assertTrue(slipstore.save_once(DAY, 3.0, [slip(leg(2, "btts"))]))
        self.assertFalse(slipstore.save_once(DAY, 2.0, [slip(leg(3, "away"))]))
        doc = slipstore.load(DAY)
        self.assertEqual(set(doc["tiers"]), {"2.00", "3.00"})
        self.assertEqual(doc["tiers"]["2.00"]["slips"][0]["legs"][0]["fixture_id"], 1)

    def test_bulky_fields_are_not_saved(self):
        slipstore.save_once(DAY, 2.0, [slip(leg(1, "home", why={"rows": list(range(500))}))])
        self.assertNotIn("why", slipstore.load(DAY)["tiers"]["2.00"]["slips"][0]["legs"][0])


class SettleTests(Base):
    def test_finished_matches_settle_and_report_the_names_main_reads(self):
        slipstore.save_once(DAY, 3.0, [slip(leg(1, "home", 1.5), leg(2, "over15", 1.4))])
        out, _ = self.settle([item(1, 2, 0), item(2, 1, 1)])
        self.assertEqual(out["status"], "success")
        self.assertEqual((out["total"], out["won"], out["staked"], out["returned"]), (1, 1, 1.0, 2.1))
        self.assertEqual((out["win_rate_pct"], out["roi_pct"]), (100.0, 110.0))
        self.assertEqual(out["tiers"]["3.00"]["won_count"], 1)

    def test_one_lost_leg_loses_the_slip_even_with_a_pending_leg(self):
        slipstore.save_once(DAY, 3.0, [slip(leg(1, "home"), leg(2, "btts"))])
        out, _ = self.settle([item(2, 1, 0)])
        self.assertEqual(out["slips"][0]["status"], "LOST")

    def test_void_leg_drops_out_of_the_odds(self):
        slipstore.save_once(DAY, 3.0, [slip(leg(1, "home", 1.5), leg(2, "dnb_home", 1.6))])
        out, _ = self.settle([item(1, 2, 0), item(2, 1, 1)])
        self.assertEqual(out["slips"][0]["status"], "WON")
        self.assertEqual(out["returned"], 1.5)

    def test_all_void_slip_is_not_counted(self):
        slipstore.save_once(DAY, 2.0, [slip(leg(1, "dnb_home"))])
        out, _ = self.settle([item(1, 0, 0)])
        self.assertEqual((out["void_count"], out["settled_count"], out["roi_pct"]), (1, 0, 0.0))

    def test_finished_day_is_never_fetched_again(self):
        slipstore.save_once(DAY, 2.0, [slip(leg(1, "home"))])
        _, first = self.settle([item(1, 2, 0)])
        out, second = self.settle([item(1, 2, 0)])
        self.assertEqual((len(first), len(second)), (1, 0))
        self.assertEqual(out["won_count"], 1)

    def test_open_day_waits_before_checking_again(self):
        slipstore.save_once(DAY, 2.0, [slip(leg(1, "home"), leg(2, "home"))])
        _, first = self.settle([item(1, 2, 0)])
        out, second = self.settle([item(1, 2, 0), item(2, 1, 0)])
        self.assertEqual((len(first), len(second)), (1, 0))
        self.assertEqual(out["pending_count"], 1)

    def test_failed_lookup_keeps_earlier_grades(self):
        slipstore.save_once(DAY, 2.0, [slip(leg(1, "home"), leg(2, "home"))])
        self.settle([item(1, 2, 0)])
        doc = slipstore.load(DAY)
        doc["checked_ts"] = 0
        slipstore.write(DAY, doc)
        out, calls = self.settle(RuntimeError("down"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["slips"][0]["legs"][0]["result"], "WON")

    def test_no_saved_slips_and_future_days_do_not_call_the_api(self):
        out, calls = self.settle([item(1, 1, 0)])
        self.assertEqual((out["status"], len(calls)), ("no_slips", 0))
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        slipstore.save_once(tomorrow, 2.0, [slip(leg(1, "home"))])
        out, calls = self.settle([item(1, 1, 0)], day=tomorrow)
        self.assertEqual((len(calls), out["pending_count"]), (0, 1))


if __name__ == "__main__":
    unittest.main()