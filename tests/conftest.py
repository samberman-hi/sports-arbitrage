from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def book(key, prices, market="h2h", updated=NOW, points=None, title=None):
    """One bookmaker entry. `prices` maps outcome name -> price."""
    outcomes = [{"name": n, "price": p} for n, p in prices.items()]
    if points:
        for oc in outcomes:
            oc["point"] = points[oc["name"]]
    return {"key": key, "title": title or key, "last_update": _iso(updated),
            "markets": [{"key": market, "last_update": _iso(updated), "outcomes": outcomes}]}


def event(bookmakers, sport="soccer_epl", home="Home", away="Away", starts_in=timedelta(days=2), eid="e1"):
    return {"id": eid, "sport_key": sport, "sport_title": sport, "commence_time": _iso(NOW + starts_in),
            "home_team": home, "away_team": away, "bookmakers": bookmakers}


@pytest.fixture
def now():
    return NOW
