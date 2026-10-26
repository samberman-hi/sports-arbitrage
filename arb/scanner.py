"""Turn The Odds API events into markets and find the arbitrages in them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config
from .maths import arb_roi, back_lay_edge, back_lay_stakes, effective_back_odds, is_finite_price


@dataclass(frozen=True)
class Quote:
    book: str           # The Odds API key, e.g. "paddypower"
    book_title: str     # display name, e.g. "Paddy Power"
    outcome: str
    price: float
    side: str           # "back" or "lay"
    updated: datetime
    commission: float   # on net winnings; 0 for ordinary bookmakers

    @property
    def effective(self) -> float:
        """Back odds after commission (only meaningful for back quotes)."""
        return effective_back_odds(self.price, self.commission)


@dataclass
class Market:
    event_id: str
    sport: str
    sport_title: str
    home: str
    away: str
    commence: datetime
    market: str          # "h2h", "totals" or "spreads"
    line: float | None   # total or home handicap; None for h2h
    quotes: list[Quote] = field(default_factory=list)

    @property
    def event(self) -> str:
        return f"{self.home} v {self.away}"

    @property
    def label(self) -> str:
        names = {"h2h": "Match odds", "totals": "Total", "spreads": "Handicap"}
        name = names.get(self.market, self.market)
        if self.line is None:
            return name
        return f"{name} {self.line:+g}" if self.market == "spreads" else f"{name} {self.line:g}"

    def outcomes(self, side: str = "back") -> list[str]:
        return sorted({q.outcome for q in self.quotes if q.side == side})


@dataclass(frozen=True)
class Leg:
    side: str
    outcome: str
    book: str
    book_title: str
    odds: float
    commission: float
    updated: datetime
    also_at: tuple[str, ...] = ()   # other books offering the same price


@dataclass
class Opportunity:
    kind: str            # "cross-book" or "back-lay"
    market: Market
    legs: list[Leg]
    roi: float           # guaranteed return on the capital tied up, before rounding
    flags: list[str] = field(default_factory=list)
    alternatives: list["Opportunity"] = field(default_factory=list)  # other trades on the same market

    @property
    def summary(self) -> str:
        legs = ", ".join(f"{l.side} {l.outcome} {l.odds:g} @ {l.book_title}" for l in self.legs)
        return f"{self.kind} {self.roi:+.2%}: {legs}"

    @property
    def key(self) -> str:
        legs = ";".join(sorted(f"{l.side}:{l.outcome}:{l.book}" for l in self.legs))
        return f"{self.market.event_id}|{self.market.market}|{self.market.line}|{legs}"


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _usable_line(point: float) -> bool:
    # Quarter lines (e.g. -0.25) settle as half win / half push, so backing both sides
    # does not pay a fixed amount. Whole and half lines are fine (a whole-line push
    # refunds every leg).
    return (point * 2) == int(point * 2)


def parse_events(events: list[dict], commissions: dict[str, float] | None = None) -> list[Market]:
    """Flatten API events into one Market per (event, market type, line)."""
    commissions = config.EXCHANGE_COMMISSION if commissions is None else commissions
    markets: dict[tuple, Market] = {}
    for ev in events:
        commence = _parse_time(ev["commence_time"])
        for bm in ev.get("bookmakers", []):
            book, title = bm["key"], bm.get("title", bm["key"])
            for mk in bm.get("markets", []):
                key = mk["key"]
                side = "lay" if key.endswith("_lay") else "back"
                base = key.removesuffix("_lay")
                if base not in ("h2h", "totals", "spreads"):
                    continue
                updated = _parse_time(mk.get("last_update") or bm.get("last_update"))
                for oc in mk.get("outcomes", []):
                    price, name, point = oc.get("price"), oc["name"], oc.get("point")
                    if not is_finite_price(price):
                        continue
                    line = None
                    if base == "totals":
                        if point is None or not _usable_line(point):
                            continue
                        line, name = float(point), f"{name} {point:g}"
                    elif base == "spreads":
                        if point is None or not _usable_line(point):
                            continue
                        line = float(point) if name == ev["home_team"] else -float(point)
                        name = f"{name} {point:+g}"
                    mkey = (ev["id"], base, line)
                    if mkey not in markets:
                        markets[mkey] = Market(ev["id"], ev["sport_key"], ev.get("sport_title", ""),
                                               ev["home_team"], ev["away_team"], commence, base, line)
                    markets[mkey].quotes.append(
                        Quote(book, title, name, float(price), side, updated, commissions.get(book, 0.0)))
    return list(markets.values())


def _complete(market: Market, outcomes: list[str]) -> bool:
    """Guard against treating an incomplete set of outcomes as an arb."""
    if len(outcomes) < 2:
        return False
    if market.market == "h2h" and market.sport.startswith("soccer"):
        return len(outcomes) == 3 and "Draw" in outcomes
    return len(outcomes) == 2 if market.market != "h2h" else True


def _leg(q: Quote, others: list[Quote]) -> Leg:
    also = tuple(sorted({o.book_title for o in others if o.book != q.book}))
    return Leg(q.side, q.outcome, q.book, q.book_title, q.price, q.commission, q.updated, also)


def cross_book(market: Market) -> Opportunity | None:
    """Back every outcome at its best commission-adjusted price."""
    outcomes = market.outcomes("back")
    if not _complete(market, outcomes):
        return None
    legs, odds = [], []
    for name in outcomes:
        quotes = [q for q in market.quotes if q.side == "back" and q.outcome == name]
        best = max(quotes, key=lambda q: q.effective)
        ties = [q for q in quotes if abs(q.effective - best.effective) < 1e-9]
        legs.append(_leg(best, ties))
        odds.append(best.effective)
    return Opportunity("cross-book", market, legs, arb_roi(odds))


def back_lay(market: Market) -> list[Opportunity]:
    """For each outcome, the best back price elsewhere against each exchange's lay price."""
    found = []
    for name in market.outcomes("lay"):
        backs = [q for q in market.quotes if q.side == "back" and q.outcome == name]
        lays = [q for q in market.quotes if q.side == "lay" and q.outcome == name]
        best = None
        for lay in lays:
            candidates = [b for b in backs if b.book != lay.book]
            if not candidates:
                continue
            back = max(candidates, key=lambda b: back_lay_edge(b.price, lay.price, b.commission, lay.commission))
            edge = back_lay_edge(back.price, lay.price, back.commission, lay.commission)
            if best is None or edge > best[0]:
                best = (edge, back, lay, backs, lays)
        if best is None:
            continue
        _, back, lay, backs, lays = best
        roi = back_lay_stakes(back.price, lay.price, 1.0, back.commission, lay.commission).roi
        back_ties = [b for b in backs if b.price == back.price and b.commission == back.commission]
        lay_ties = [l for l in lays if l.price == lay.price and l.commission == lay.commission]
        found.append(Opportunity("back-lay", market, [_leg(back, back_ties), _leg(lay, lay_ties)], roi))
    return found


def _flag(opp: Opportunity, now: datetime, fetched_at: datetime | None) -> None:
    m = opp.market
    if opp.roi >= config.SUSPICIOUS_ROI:
        opp.flags.append("edge this big is usually a pricing error; the bet may be voided as a palpable error")
    if len({l.book for l in opp.legs}) == 1:
        opp.flags.append("every leg is at one book, which is almost certainly bad data")
    times = [l.updated for l in opp.legs]
    gap = (max(times) - min(times)).total_seconds()
    if gap > config.MAX_QUOTE_AGE_GAP:
        opp.flags.append(f"stale: legs were last updated {gap / 60:.0f} min apart")
    if fetched_at is not None:
        oldest = (fetched_at - min(times)).total_seconds()
        if oldest > config.MAX_QUOTE_AGE_GAP:
            opp.flags.append(f"oldest price is {oldest / 60:.0f} min old")
    to_go = (m.commence - now).total_seconds()
    if to_go < 0:
        opp.flags.append("in play: live prices move faster than the feed")
    elif to_go < 900:
        opp.flags.append("kicks off within 15 min")


def find_opportunities(markets: list[Market], now: datetime | None = None, min_roi: float = config.MIN_ROI,
                       include_live: bool = False, max_hours: float | None = None,
                       books: set[str] | None = None, exclude: set[str] | None = None,
                       fetched_at: datetime | None = None) -> list[Opportunity]:
    """All arbs at or above `min_roi`, best first.

    `books` keeps only the bookmakers you can actually bet with; `exclude` drops some.
    """
    now = now or datetime.now(timezone.utc)
    found = []
    for m in markets:
        hours = (m.commence - now).total_seconds() / 3600
        if (hours < 0 and not include_live) or (max_hours is not None and hours > max_hours):
            continue
        if books or exclude:
            m = Market(**{**m.__dict__, "quotes": [
                q for q in m.quotes if (not books or q.book in books) and (not exclude or q.book not in exclude)]})
        # One entry per market: the best trade, with the others kept as alternatives.
        # They usually exploit the same mispriced price, so they are not extra profit.
        candidates = sorted((o for o in [cross_book(m), *back_lay(m)] if o is not None),
                            key=lambda o: o.roi, reverse=True)
        if candidates and candidates[0].roi >= min_roi:
            best = candidates[0]
            best.alternatives = [o for o in candidates[1:] if o.roi > 0]
            _flag(best, now, fetched_at)
            found.append(best)
    return sorted(found, key=lambda o: o.roi, reverse=True)

