import pytest

from arb.maths import (arb_roi, back_lay_edge, back_lay_stakes, back_stake_for_capital, book_sum,
                       dutch_stakes, effective_back_odds)


def test_effective_odds_charges_commission_on_winnings_only():
    assert effective_back_odds(3.0, 0.05) == pytest.approx(2.9)
    assert effective_back_odds(3.0, 0.0) == 3.0


def test_two_way_arb_roi():
    # 1/2.1 + 1/2.1 = 0.952..., so 5% on outlay
    assert book_sum([2.1, 2.1]) == pytest.approx(0.95238, abs=1e-5)
    assert arb_roi([2.1, 2.1]) == pytest.approx(0.05)
    assert arb_roi([1.9, 1.9]) < 0


def test_dutch_stakes_pay_the_same_whatever_wins():
    d = dutch_stakes([2.62, 3.45, 3.30], 100)
    assert d.outlay == pytest.approx(100)
    assert max(d.payouts) - min(d.payouts) == pytest.approx(0, abs=1e-9)
    assert d.roi == pytest.approx(arb_roi([2.62, 3.45, 3.30]))


def test_rounded_stakes_stay_within_budget_and_never_beat_exact():
    exact = dutch_stakes([2.62, 3.45, 3.30], 100)
    rounded = dutch_stakes([2.62, 3.45, 3.30], 100, increment=1.0)
    assert all(s == int(s) for s in rounded.stakes)
    assert rounded.outlay <= 101
    assert rounded.roi <= exact.roi + 1e-12
    assert rounded.min_profit > 0


def test_rounding_picks_the_best_floor_ceil_combination():
    odds = [2.62, 3.45, 3.30]
    best = dutch_stakes(odds, 100, increment=1.0)
    naive = [round(100 * (1 / o) / sum(1 / x for x in odds)) for o in odds]
    naive_min = min(s * o for s, o in zip(naive, odds)) - sum(naive)
    assert best.roi >= naive_min / sum(naive) - 1e-12


def test_back_lay_equalises_both_outcomes():
    bl = back_lay_stakes(2.24, 2.14, 50, lay_commission=0.05)
    assert bl.profit_if_wins == pytest.approx(bl.profit_if_loses, abs=0.02)
    # per unit of back stake the profit matches the closed form
    assert bl.min_profit / 50 == pytest.approx(back_lay_edge(2.24, 2.14, 0, 0.05), abs=5e-4)


def test_back_lay_needs_back_to_beat_lay_after_commission():
    assert back_lay_edge(2.0, 2.0, 0, 0.05) < 0          # same price loses the commission
    assert back_lay_edge(2.10, 2.06, 0, 0.05) < 0         # 2.10 * 0.95 = 1.995 < 2.06 - 0.05
    assert back_lay_edge(2.10, 2.04, 0, 0.05) > 0         # 1.995 > 2.04 - 0.05
    # threshold: back * (1 - c) > lay - c
    assert back_lay_edge(2.1, 2.1 * 0.95 + 0.05, 0, 0.05) == pytest.approx(0, abs=1e-12)


def test_back_stake_for_capital_uses_the_budget():
    s = back_stake_for_capital(2.24, 2.14, 100, 0, 0.02)
    bl = back_lay_stakes(2.24, 2.14, s, 0, 0.02)
    assert bl.capital == pytest.approx(100, abs=0.02)
