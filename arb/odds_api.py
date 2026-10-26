"""Minimal client for The Odds API v4 (https://the-odds-api.com).

Costs: /sports is free. /sports/{sport}/odds costs (markets x regions) credits per call,
or (markets x ceil(bookmakers / 10)) when you name bookmakers instead of regions. The free
plan gives 500 credits a month, so responses are cached and the cost is shown up front.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config


class OddsAPIError(RuntimeError):
    pass


@dataclass
class Snapshot:
    """One sport's odds as fetched (or replayed from disk)."""
    sport: str
    fetched_at: datetime
    events: list[dict]
    quota: dict = field(default_factory=dict)
    from_cache: bool = False

    def to_json(self) -> dict:
        return {"sport": self.sport, "fetched_at": self.fetched_at.isoformat(),
                "quota": self.quota, "events": self.events}

    @classmethod
    def from_json(cls, d: dict, from_cache: bool = True) -> "Snapshot":
        return cls(d["sport"], datetime.fromisoformat(d["fetched_at"]), d["events"],
                   d.get("quota", {}), from_cache)


def request_markets(markets: str) -> str:
    """Lay markets are returned alongside h2h automatically, so they are never requested."""
    return ",".join(m for m in markets.split(",") if m and not m.endswith("_lay"))


def call_cost(markets: str, regions: str | None, bookmakers: str | None) -> int:
    n_markets = len(request_markets(markets).split(","))
    if bookmakers:
        return n_markets * math.ceil(len([b for b in bookmakers.split(",") if b]) / 10)
    return n_markets * len([r for r in (regions or "").split(",") if r])


class OddsAPI:
    def __init__(self, api_key: str, cache_dir: Path = config.CACHE_DIR, cache_ttl: float = 60.0):
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.quota: dict = {}

    def _get(self, path: str, **params) -> list | dict:
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{config.API_BASE}{path}?" + urllib.parse.urlencode({"apiKey": self.api_key, **params})
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                for h in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
                    if resp.headers.get(h) is not None:
                        self.quota[h.removeprefix("x-requests-")] = float(resp.headers[h])
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 401:
                raise OddsAPIError("API key rejected (401). Check ODDS_API_KEY.") from e
            if e.code == 429:
                raise OddsAPIError("Rate limited or out of credits (429). " + detail) from e
            raise OddsAPIError(f"HTTP {e.code} from {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise OddsAPIError(f"Could not reach The Odds API: {e.reason}") from e
        return json.loads(body)

    def sports(self, include_all: bool = False) -> list[dict]:
        """Sports and leagues; only in-season ones unless include_all. Free."""
        return self._get("/sports", all="true" if include_all else None)

    def odds(self, sport: str, regions: str | None = config.DEFAULT_REGIONS,
             markets: str = config.DEFAULT_MARKETS, bookmakers: str | None = None) -> Snapshot:
        """Current pre-match and live odds for one sport, from cache if fresh enough."""
        markets = request_markets(markets)
        tag = f"{sport}__{markets}__{bookmakers or regions}".replace(",", "-")
        path = self.cache_dir / f"{tag}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl:
            return Snapshot.from_json(json.loads(path.read_text()))
        events = self._get(f"/sports/{sport}/odds", markets=markets, oddsFormat="decimal",
                           dateFormat="iso", regions=None if bookmakers else regions,
                           bookmakers=bookmakers)
        snap = Snapshot(sport, datetime.now(timezone.utc), events, dict(self.quota))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap.to_json()))
        return snap


def load_snapshots(path: Path) -> list[Snapshot]:
    """Replay saved data: one snapshot file, or a {"snapshots": [...]} bundle like the sample."""
    d = json.loads(Path(path).read_text())
    if "snapshots" in d:
        return [Snapshot.from_json(s) for s in d["snapshots"]]
    return [Snapshot.from_json(d)]
