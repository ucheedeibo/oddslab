import math
from datetime import date, datetime, timedelta
import numpy as np
import pytest
from app.core import poisson, strength, elo, metrics, calibration, backtest, odds, combos
from app.core.strength import Match, InsufficientData
from tests.synth import make_league


# ---- Poisson / markets
def test_score_matrix_sums_to_one_with_and_without_dc():
    for rho in (0.0, -0.1, 0.05):
        m = poisson.score_matrix(1.4, 1.1, rho)
        assert m.sum() == pytest.approx(1.0)
        assert (m >= 0).all()

def test_hda_sums_to_one_and_symmetry():
    mk = poisson.markets(poisson.score_matrix(1.3, 1.3))
    assert mk["home_win"] + mk["draw"] + mk["away_win"] == pytest.approx(1.0)
    assert mk["home_win"] == pytest.approx(mk["away_win"])
    assert mk["over_2_5"] + mk["under_2_5"] == pytest.approx(1.0)

def test_dc_negative_rho_raises_draws_00_11():
    base = poisson.score_matrix(1.2, 1.0, 0.0); dc = poisson.score_matrix(1.2, 1.0, -0.1)
    assert dc[0, 0] > base[0, 0] and dc[1, 1] > base[1, 1]

def test_invalid_inputs():
    with pytest.raises(ValueError): poisson.score_matrix(0, 1)
    with pytest.raises(ValueError): poisson.score_matrix(3.0, 3.0, 0.5)


# ---- strength model & leakage
def test_fit_recovers_home_advantage_and_ranking():
    ms, att, dfn = make_league(seasons=4, seed=3)
    model = strength.fit(ms, date(2100, 1, 1), xi=0.0, ridge=1.0)
    assert 0.1 < model.home_adv < 0.4
    best, worst = max(att, key=att.get), min(att, key=att.get)
    assert model.attack[best] > model.attack[worst]

def test_fit_ignores_future_matches_exactly():
    ms, _, _ = make_league(seed=2)
    cut = ms[len(ms) // 2].date
    a = strength.fit(ms, cut)
    future_noise = [Match(cut + timedelta(days=5), "T00", "T01", 9, 9)] + [m for m in ms]
    b = strength.fit(future_noise, cut)
    assert a.n_matches == b.n_matches
    assert a.attack["T00"] == pytest.approx(b.attack["T00"])

def test_insufficient_data_is_explicit():
    ms, _, _ = make_league()
    with pytest.raises(InsufficientData): strength.fit(ms[:10], date(2100, 1, 1))
    model = strength.fit(ms, date(2100, 1, 1))
    with pytest.raises(InsufficientData): model.predict("T00", "NEWTEAM")

def test_predict_output_valid():
    ms, _, _ = make_league()
    p = strength.fit(ms, date(2100, 1, 1)).predict("T00", "T01")
    assert p["home_win"] + p["draw"] + p["away_win"] == pytest.approx(1.0)
    assert p["expected_goals"]["home"] > 0

def test_elo_probabilities_valid_and_ordered():
    ms, att, dfn = make_league(seasons=3)
    m = elo.fit(ms, date(2100, 1, 1))
    p = m.predict("T00", "T01")
    assert p["home_win"] + p["draw"] + p["away_win"] == pytest.approx(1.0)
    with pytest.raises(InsufficientData): elo.fit(ms[:5], date(2100, 1, 1))


# ---- metrics
def test_metrics_known_values():
    P = [[1, 0, 0], [0, 1, 0]]
    assert metrics.brier(P, [0, 1]) == pytest.approx(0.0)
    assert metrics.brier([[0.5, 0.25, 0.25]], [0]) == pytest.approx(0.25 + 0.0625 + 0.0625)
    assert metrics.log_loss([[0.5, 0.25, 0.25]], [0]) == pytest.approx(-math.log(0.5))
    with pytest.raises(ValueError): metrics.brier([[0.5, 0.5, 0.5]], [0])

def test_uniform_forecast_brier_is_two_thirds():
    P = np.full((300, 3), 1 / 3); y = np.arange(300) % 3
    assert metrics.brier(P, y) == pytest.approx(2 / 3)


# ---- calibration
def test_calibrator_identity_when_too_small():
    c = calibration.fit_calibrator([[0.5, 0.3, 0.2]] * 10, [0] * 10)
    assert not c.fitted
    assert c.transform([[0.5, 0.3, 0.2]])[0].sum() == pytest.approx(1.0)

def test_temperature_scaling_fixes_overconfidence_and_keeps_distribution():
    rng = np.random.default_rng(0)
    n = 4000
    true = rng.dirichlet([4, 3, 3], n)
    y = np.array([rng.choice(3, p=p) for p in true])
    over = true ** 2.5; over /= over.sum(1, keepdims=True)   # deliberately overconfident
    c = calibration.fit_calibrator(over[:2000], y[:2000], vector_samples=10**9)
    assert c.fitted and c.temp > 1.5
    cal = c.transform(over[2000:])
    assert np.allclose(cal.sum(1), 1.0)
    assert metrics.log_loss(cal, y[2000:]) < metrics.log_loss(over[2000:], y[2000:])


# ---- walk-forward backtest
def test_walk_forward_never_uses_future_and_is_chronological():
    ms, _, _ = make_league(seasons=3, seed=5)
    start = ms[len(ms) // 3].date
    res = backtest.walk_forward(ms, start, step_days=21, min_train=60)
    assert res["rows"], "should produce predictions"
    assert all(r["date"] >= start for r in res["rows"])
    assert [r["date"] for r in res["rows"]] == sorted(r["date"] for r in res["rows"])
    for r in res["rows"]:
        assert sum(r["dc_cal"]) == pytest.approx(1.0)

def test_backtest_is_unchanged_by_editing_future_results():
    """Leakage test: scrambling results AFTER a window must not change that window's predictions."""
    ms, _, _ = make_league(seasons=3, seed=7)
    start = ms[len(ms) // 3].date
    base = backtest.walk_forward(ms, start, step_days=21)
    cutoff = start + timedelta(days=21)
    tampered = [Match(m.date, m.home, m.away, 7, 0, m.id) if m.date >= cutoff else m for m in ms]
    alt = backtest.walk_forward(tampered, start, step_days=21)
    first = [r for r in base["rows"] if r["date"] < cutoff]
    first_alt = [r for r in alt["rows"] if r["date"] < cutoff]
    assert first and len(first) == len(first_alt)
    for a, b in zip(first, first_alt):
        assert a["dc_raw"] == pytest.approx(b["dc_raw"]) and a["elo"] == pytest.approx(b["elo"])

def test_model_beats_frequency_baseline_on_structured_data():
    ms, _, _ = make_league(n_teams=14, seasons=4, seed=11)
    res = backtest.walk_forward(ms, ms[len(ms) // 3].date, step_days=21)
    s = res["summary"]
    assert s["models"]["dc_poisson_raw"]["brier"] < s["models"]["frequency_baseline"]["brier"]


# ---- odds
def test_implied_and_margin():
    assert odds.implied(2.0) == pytest.approx(0.5)
    o = [2.0, 3.5, 4.0]
    assert odds.overround(o) == pytest.approx(0.5 + 1/3.5 + 0.25 - 1)
    for method in ("proportional", "power"):
        assert sum(odds.remove_margin(o, method)) == pytest.approx(1.0)
    with pytest.raises(ValueError): odds.implied(1.0)

def test_expected_value():
    assert odds.expected_value(0.5, 2.2) == pytest.approx(0.1)
    assert odds.expected_value(0.4, 2.0) == pytest.approx(-0.2)
    assert odds.conservative_ev(0.5, 0.05, 2.2) < odds.expected_value(0.5, 2.2)

def test_value_flag_blocks_stale_odds_and_uncalibrated():
    now = datetime(2026, 1, 1, 12)
    ok, why = odds.is_flagged(0.6, 0.02, 2.0, now - timedelta(minutes=5), now, calibrated=True)
    assert ok and not why
    ok, why = odds.is_flagged(0.6, 0.02, 2.0, now - timedelta(hours=5), now, calibrated=True)
    assert not ok and any("old" in w for w in why)
    ok, why = odds.is_flagged(0.6, 0.02, 2.0, now, now, calibrated=False)
    assert not ok

def test_simulation_rejects_lookahead_and_computes_drawdown():
    t = datetime(2026, 1, 1)
    bad = [{"pred_ts": t, "odds_ts": t + timedelta(hours=1), "odds": 2.0, "won": True, "date": t}]
    with pytest.raises(ValueError): odds.simulate_flat(bad)
    mk = lambda i, odds_, won: {"pred_ts": t, "odds_ts": t, "odds": odds_, "won": won, "date": t + timedelta(days=i)}
    r = odds.simulate_flat([mk(0, 2.0, True), mk(1, 2.0, False), mk(2, 2.0, False), mk(3, 3.0, True)])
    assert r["profit_units"] == pytest.approx(1 - 1 - 1 + 2)
    assert r["max_drawdown_units"] == pytest.approx(2.0)


# ---- combinations
def _legs():
    return [dict(fixture_id=i, market="home_win", selection="H", odds=o, p=p, p_se=0.03)
            for i, (o, p) in enumerate([(1.5, 0.70), (1.8, 0.58), (2.0, 0.52), (1.4, 0.74), (1.7, 0.60)])]

def test_combined_odds_product_and_target_window():
    assert combos.combined_odds([1.5, 2.0]) == pytest.approx(3.0)
    res = combos.search(_legs(), target=3.0)
    assert res
    for c in res:
        assert 2.5 <= c["combined_odds"] <= 3.5
        assert c["probability"] == pytest.approx(math.prod(l["p"] for l in c["legs"]))
        assert c["probability_conservative"] <= c["probability"]

def test_combos_never_pair_same_fixture():
    legs = _legs() + [dict(fixture_id=0, market="over_2_5", selection="O", odds=2.0, p=0.5, p_se=0.03)]
    for c in combos.search(legs, target=3.0, max_legs=4, top=50):
        ids = [l["fixture_id"] for l in c["legs"]]
        assert len(ids) == len(set(ids))

def test_combo_flags_no_edge():
    legs = [dict(fixture_id=i, market="m", selection="s", odds=1.7, p=0.5, p_se=0.05) for i in range(4)]
    res = combos.search(legs, target=3.0)
    assert res and all(c["ev_conservative"] < 0 and "does not make this safe" in c["note"] for c in res)
