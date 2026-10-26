import csv
import json
import os
import time

import pytest
from conftest import NOW, book, event

from arb.cli import main
from arb.odds_api import OddsAPI, Snapshot, call_cost
from arb.report import format_opportunity, log_opportunities, plan
from arb.scanner import find_opportunities, parse_events

ARB = event([book("a", {"Home": 2.62, "Draw": 3.30, "Away": 3.00}, title="Book A"),
             book("b", {"Home": 2.40, "Draw": 3.45, "Away": 3.30}, title="Book B")])
BACK_LAY = event([book("paddypower", {"Home": 2.24, "Draw": 3.3, "Away": 3.0}),
                  book("betfair_ex_uk", {"Home": 2.12, "Draw": 3.5, "Away": 3.2}, market="h2h_lay")])


def _opp(ev):
    [opp] = find_opportunities(parse_events([ev]), now=NOW)
    return opp


def test_plan_spends_about_the_budget_and_profits_whatever_happens():
    for ev in (ARB, BACK_LAY):
        p = plan(_opp(ev), 100, increment=1.0)
        assert p.capital == pytest.approx(100, abs=3)
        assert min(p.profits.values()) > 0


def test_format_shows_every_leg_and_the_profit():
    text = format_opportunity(1, _opp(ARB), 100, now=NOW)
    assert "Book A" in text and "Book B" in text and "profit €" in text
    text = format_opportunity(1, _opp(BACK_LAY), 100, now=NOW)
    assert "LAY" in text and "liability" in text and "5% commission" in text


def test_log_appends_rows(tmp_path):
    path = tmp_path / "log.csv"
    log_opportunities([_opp(ARB)], path)
    log_opportunities([_opp(BACK_LAY)], path)
    rows = list(csv.DictReader(path.open()))
    assert [r["kind"] for r in rows] == ["cross-book", "back-lay"]


def test_call_cost():
    assert call_cost("h2h", "uk,eu", None) == 2
    assert call_cost("h2h,h2h_lay,totals", "uk,eu", None) == 4  # lay prices are free with h2h
    assert call_cost("h2h", "uk,eu", "paddypower,betfair_ex_uk") == 1
    assert call_cost("h2h", None, ",".join(f"b{i}" for i in range(11))) == 2


def test_fresh_cache_is_reused_without_a_request(tmp_path):
    api = OddsAPI("key", cache_dir=tmp_path, cache_ttl=60)
    calls = []
    api._get = lambda path, **kw: calls.append(path) or [ARB]
    first = api.odds("soccer_epl")
    second = api.odds("soccer_epl")
    assert len(calls) == 1 and second.from_cache and second.events == first.events
    for f in tmp_path.iterdir():  # age the cache past the TTL
        os.utime(f, (time.time() - 120, time.time() - 120))
    api.odds("soccer_epl")
    assert len(calls) == 2


def test_snapshot_round_trip():
    s = Snapshot("soccer_epl", NOW, [ARB], {"remaining": 480.0})
    back = Snapshot.from_json(json.loads(json.dumps(s.to_json())))
    assert (back.sport, back.fetched_at, back.events, back.quota) == (s.sport, s.fetched_at, s.events, s.quota)


def test_cli_calculators_and_demo(capsys):
    main(["stake", "2.62", "3.45", "3.30", "--budget", "100"])
    assert "ARB" in capsys.readouterr().out
    main(["backlay", "--back", "2.0", "--lay", "2.0"])
    assert "not an arb" in capsys.readouterr().out
    main(["scan", "--demo"])
    out = capsys.readouterr().out
    assert "5 arbitrages" in out and "replayed data" in out


def test_thin_edge_falls_back_to_finer_rounding():
    thin = event([book("a", {"Home": 1.14, "Draw": 11.0, "Away": 32.04}),
                  book("b", {"Home": 1.10, "Draw": 11.7, "Away": 25.0})])
    opp = _opp(thin)
    assert 0 < opp.roi < 0.01
    p = plan(opp, 100, increment=1.0)
    assert p.increment < 1.0 and min(p.profits.values()) > 0
