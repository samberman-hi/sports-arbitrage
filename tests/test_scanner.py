from datetime import timedelta

import pytest
from conftest import NOW, book, event

from arb import config
from arb.odds_api import load_snapshots
from arb.scanner import cross_book, find_opportunities, parse_events

FAIR = {"Home": 2.40, "Draw": 3.30, "Away": 3.00}  # book sum 1.053, no arb


def _scan(events, **kw):
    return find_opportunities(parse_events(events), now=NOW, **kw)


def test_no_arb_in_a_normal_market():
    ev = event([book("a", FAIR), book("b", {"Home": 2.38, "Draw": 3.35, "Away": 2.95})])
    assert _scan([ev]) == []


def test_cross_book_arb_takes_the_best_price_per_outcome():
    ev = event([book("a", {"Home": 2.62, "Draw": 3.30, "Away": 3.00}),
                book("b", {"Home": 2.40, "Draw": 3.45, "Away": 3.30})])
    [opp] = _scan([ev])
    assert opp.kind == "cross-book"
    assert {(l.outcome, l.book) for l in opp.legs} == {("Home", "a"), ("Draw", "b"), ("Away", "b")}
    assert opp.roi == pytest.approx(1 / (1 / 2.62 + 1 / 3.45 + 1 / 3.30) - 1)


def test_exchange_back_price_is_judged_after_commission():
    # 3.40 on Betfair is 3.28 after 5% commission, which loses to 3.30 at a bookmaker.
    ev = event([book("a", {"Home": 2.62, "Draw": 3.45, "Away": 3.30}),
                book("betfair_ex_uk", {"Home": 2.40, "Draw": 3.30, "Away": 3.40})])
    [opp] = _scan([ev])
    away = next(l for l in opp.legs if l.outcome == "Away")
    assert away.book == "a"


def test_soccer_match_odds_need_the_draw():
    ev = event([book("a", {"Home": 2.5}), book("b", {"Away": 2.5})])
    assert cross_book(parse_events([ev])[0]) is None


def test_two_way_sports_do_not_need_a_draw():
    ev = event([book("a", {"Home": 2.1, "Away": 1.8}), book("b", {"Home": 1.8, "Away": 2.1})],
               sport="basketball_nba")
    [opp] = _scan([ev])
    assert opp.roi == pytest.approx(0.05)


def test_back_lay_arb_against_an_exchange_lay():
    ev = event([book("paddypower", {"Home": 2.24, "Draw": 3.3, "Away": 3.0}),
                book("betfair_ex_uk", {"Home": 2.10, "Draw": 3.4, "Away": 3.1}),
                book("betfair_ex_uk", {"Home": 2.12, "Draw": 3.5, "Away": 3.2}, market="h2h_lay")])
    [opp] = _scan([ev])
    assert opp.kind == "back-lay"
    back, lay = opp.legs
    assert (back.side, back.book, lay.side, lay.book) == ("back", "paddypower", "lay", "betfair_ex_uk")
    assert lay.commission == config.EXCHANGE_COMMISSION["betfair_ex_uk"]


def test_never_back_and_lay_at_the_same_exchange():
    ev = event([book("a", FAIR),
                book("smarkets", {"Home": 2.60, "Draw": 3.3, "Away": 3.0}),
                book("smarkets", {"Home": 2.50, "Draw": 3.4, "Away": 3.1}, market="h2h_lay")])
    assert all(o.kind != "back-lay" for o in _scan([ev]))


def test_totals_and_spreads_pair_complementary_lines():
    ev = event([
        book("a", {"Over": 2.10, "Under": 1.80}, market="totals", points={"Over": 2.5, "Under": 2.5}),
        book("b", {"Over": 1.80, "Under": 2.10}, market="totals", points={"Over": 2.5, "Under": 2.5}),
        book("a", {"Home": 2.10, "Away": 1.80}, market="spreads", points={"Home": -1.5, "Away": 1.5}),
        book("b", {"Home": 1.80, "Away": 2.10}, market="spreads", points={"Home": -1.5, "Away": 1.5}),
        # quarter lines settle half-win / half-push, so they are skipped
        book("c", {"Home": 3.0, "Away": 3.0}, market="spreads", points={"Home": -0.25, "Away": 0.25}),
    ])
    markets = parse_events([ev])
    assert sorted((m.market, m.line) for m in markets) == [("spreads", -1.5), ("totals", 2.5)]
    assert {round(o.roi, 4) for o in find_opportunities(markets, now=NOW)} == {0.05}


def test_filters_live_far_future_and_books():
    arb = [book("a", {"Home": 2.62, "Draw": 3.30, "Away": 3.00}),
           book("b", {"Home": 2.40, "Draw": 3.45, "Away": 3.30})]
    live = event(arb, starts_in=-timedelta(minutes=30), eid="live")
    later = event(arb, starts_in=timedelta(days=10), eid="later")
    assert [o.market.event_id for o in _scan([live, later])] == ["later"]
    assert {o.market.event_id for o in _scan([live, later], include_live=True)} == {"live", "later"}
    assert _scan([live, later], max_hours=48) == []
    assert _scan([later], books={"a"}) == []        # one book alone can't make this arb
    assert _scan([later], exclude={"b"}) == []


def test_flags_stale_and_suspicious_prices():
    ev = event([book("a", {"Home": 9.0, "Draw": 3.30, "Away": 3.00}, updated=NOW - timedelta(minutes=40)),
                book("b", {"Home": 2.40, "Draw": 3.45, "Away": 3.30})])
    [opp] = find_opportunities(parse_events([ev]), now=NOW, fetched_at=NOW)
    text = " ".join(opp.flags)
    assert "pricing error" in text and "stale" in text


def test_demo_snapshot_finds_the_planted_arbs():
    snaps = load_snapshots(config.SAMPLE_PATH)
    opps = []
    for s in snaps:
        opps += find_opportunities(parse_events(s.events), now=s.fetched_at, fetched_at=s.fetched_at)
    events = [o.market.event for o in opps]
    assert len(opps) == 5
    assert events[0] == "Newcastle United v Brighton and Hove Albion"
    assert "pricing error" in opps[0].flags[0]
    assert "Arsenal v Liverpool" not in events
    assert all(o.alternatives == [] or o.alternatives[0].roi <= o.roi for o in opps)
