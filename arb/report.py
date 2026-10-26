"""Stake an opportunity for a given budget, print it, and log it."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .maths import back_lay_stakes, back_stake_for_capital, dutch_stakes, effective_back_odds
from .scanner import Opportunity


@dataclass
class Plan:
    stakes: list[float]       # per leg: stake (back) or backer's stake accepted (lay)
    liabilities: list[float]  # per leg: what the leg can lose
    capital: float
    profits: dict[str, float]  # outcome -> profit
    increment: float           # stake rounding actually used


def plan(opp: Opportunity, budget: float, increment: float = config.STAKE_INCREMENT) -> Plan:
    """Concrete stakes using about `budget` of capital (stakes plus lay liability).

    On a thin edge, rounding to whole euros can turn the guaranteed profit negative, so
    for a real arb the rounding falls back to 0.10 and then to cents.
    """
    p = _plan(opp, budget, increment)
    for finer in (0.1, 0.01):
        if opp.roi <= 0 or min(p.profits.values()) > 0 or finer >= increment:
            break
        p = _plan(opp, budget, finer)
    return p


def _plan(opp: Opportunity, budget: float, increment: float) -> Plan:
    if opp.kind == "cross-book":
        odds = [effective_back_odds(l.odds, l.commission) for l in opp.legs]
        d = dutch_stakes(odds, budget, increment)
        profits = {l.outcome: p - d.outlay for l, p in zip(opp.legs, d.payouts)}
        return Plan(d.stakes, d.stakes, d.outlay, profits, increment)
    back, lay = opp.legs
    s = back_stake_for_capital(back.odds, lay.odds, budget, back.commission, lay.commission)
    bl = back_lay_stakes(back.odds, lay.odds, s, back.commission, lay.commission, increment)
    return Plan([bl.back_stake, bl.lay_stake], [bl.back_stake, bl.liability], bl.capital,
                {back.outcome: bl.profit_if_wins, f"not {back.outcome}": bl.profit_if_loses}, increment)


def _until(t: datetime, now: datetime) -> str:
    secs = (t - now).total_seconds()
    if secs < 0:
        return "in play"
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    return f"in {d}d {h}h" if d else f"in {h}h {rem // 60:02d}m"


def format_opportunity(i: int, opp: Opportunity, budget: float, cur: str = "€",
                       increment: float = config.STAKE_INCREMENT, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    m, p = opp.market, plan(opp, budget, increment)
    tag = "ARB" if opp.roi > 0 else "near miss"
    when = m.commence.astimezone().strftime("%a %d %b %H:%M")
    lines = [
        f"{i:>2}. {tag} {opp.roi:+.2%}  {m.event}  ({m.sport_title})",
        f"    {m.label} · {opp.kind} · {when} ({_until(m.commence, now)})",
    ]
    width = max(len(l.outcome) for l in opp.legs)
    bwidth = max(len(l.book_title) for l in opp.legs)
    for leg, stake, liab in zip(opp.legs, p.stakes, p.liabilities):
        extra = []
        if leg.side == "lay":
            extra.append(f"liability {cur}{liab:,.2f}")
        if leg.commission:
            extra.append(f"{leg.commission:.0%} commission")
        if leg.also_at:
            extra.append("also at " + ", ".join(leg.also_at[:3]) + ("…" if len(leg.also_at) > 3 else ""))
        what = "stake" if leg.side == "back" else "lay"
        lines.append(f"    {leg.side.upper():<4} {leg.outcome:<{width}}  {leg.odds:>6.2f}  @ {leg.book_title:<{bwidth}}"
                     f"  {what} {cur}{stake:,.2f}" + (f"  ({'; '.join(extra)})" if extra else ""))
    signed = lambda x: f"{'-' if x < 0 else ''}{cur}{abs(x):,.2f}"
    lo, hi = signed(min(p.profits.values())), signed(max(p.profits.values()))
    profit = lo if lo == hi else f"{lo} to {hi}"
    rounding = f" (stakes to {cur}{p.increment:g}: edge too thin for {cur}{increment:g})" if p.increment != increment else ""
    lines.append(f"    Capital {cur}{p.capital:,.2f} → profit {profit} whatever the result{rounding}")
    lines += [f"    ! {f}" for f in opp.flags]
    lines += [f"    or: {alt.summary}" for alt in opp.alternatives]
    return "\n".join(lines)


LOG_FIELDS = ["logged_at", "kind", "roi", "sport", "event", "commence", "market", "legs", "flags", "event_id"]


def log_opportunities(opps: list[Opportunity], path: Path = config.LOG_PATH) -> None:
    """Append to a CSV so the scanner builds up a record of what it has seen."""
    if not opps:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for o in opps:
            w.writerow({
                "logged_at": now, "kind": o.kind, "roi": f"{o.roi:.5f}", "sport": o.market.sport,
                "event": o.market.event, "commence": o.market.commence.isoformat(),
                "market": o.market.label, "event_id": o.market.event_id,
                "legs": " | ".join(f"{l.side} {l.outcome} {l.odds} @ {l.book}" for l in o.legs),
                "flags": " | ".join(o.flags),
            })
