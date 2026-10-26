"""Command line: scan, watch, sports, stake, backlay. Run `python -m arb -h`."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import config
from .maths import back_lay_stakes, back_stake_for_capital, dutch_stakes, effective_back_odds
from .odds_api import OddsAPI, OddsAPIError, Snapshot, call_cost, load_snapshots
from .report import format_opportunity, log_opportunities, write_latest
from .scanner import find_opportunities, parse_events


def _csv(s: str | None) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def _commissions(overrides: list[str]) -> dict[str, float]:
    rates = dict(config.EXCHANGE_COMMISSION)
    for item in overrides:
        book, _, rate = item.partition("=")
        rates[book.strip()] = float(rate)
    return rates


def _books(args) -> set[str] | None:
    return set(_csv(args.books)) or (None if args.all_books else config.MY_BOOKS) or None


def _excluded(args) -> set[str] | None:
    return set(_csv(args.exclude)) | (set() if args.all_books else config.DEFAULT_EXCLUDE) or None


def _client(args) -> OddsAPI:
    key = config.load_api_key()
    if not key:
        sys.exit("No API key. Get a free one at https://the-odds-api.com and put\n"
                 "ODDS_API_KEY=your_key in a .env file in the project folder (or export it).\n"
                 "Try `python -m arb scan --demo` to see the scanner on sample data first.")
    return OddsAPI(key, cache_ttl=args.cache_ttl)


def _pick_sports(api: OddsAPI, args) -> list[str]:
    active = {s["key"]: s for s in api.sports()}  # free call
    if args.sports:
        wanted = _csv(args.sports)
        off = [s for s in wanted if s not in active]
        if off:
            print(f"Not in season / unknown, skipped: {', '.join(off)}")
        return [s for s in wanted if s in active]
    if args.all_sports:
        return [k for k, s in active.items() if not s.get("has_outrights")]
    return [s for s in config.DEFAULT_SPORTS if s in active]


def _fetch(args) -> list[Snapshot]:
    if args.demo or args.from_file:
        return load_snapshots(args.from_file or config.SAMPLE_PATH)
    api = _client(args)
    sports = _pick_sports(api, args)
    per_call = call_cost(args.markets, args.regions, args.bookmakers)
    print(f"{len(sports)} sports x {per_call} credits = up to {len(sports) * per_call} credits "
          f"(cached results younger than {args.cache_ttl:.0f}s are free)")
    if args.dry_run:
        print("Sports:", ", ".join(sports))
        return []
    snaps = []
    for sport in sports:
        try:
            snaps.append(api.odds(sport, args.regions, args.markets, args.bookmakers))
        except OddsAPIError as e:
            print(f"  {sport}: {e}")
            if "401" in str(e) or "429" in str(e):
                break
    return snaps


def _scan_once(args, snaps: list[Snapshot]):
    commissions = _commissions(args.commission)
    markets, opps, now = [], [], datetime.now(timezone.utc)
    if args.demo or args.from_file:
        # Replayed data: judge "upcoming" and staleness as of when it was captured.
        now = min((s.fetched_at for s in snaps), default=now)
    for snap in snaps:
        ms = parse_events(snap.events, commissions)
        markets += ms
        opps += find_opportunities(ms, now=now, min_roi=-1.0, include_live=args.include_live,
                                   max_hours=args.max_hours, books=_books(args), exclude=_excluded(args),
                                   fetched_at=snap.fetched_at)
    opps.sort(key=lambda o: o.roi, reverse=True)
    arbs = [o for o in opps if o.roi > 0 and o.roi >= args.min_roi]
    return markets, arbs, opps, now


def _stats(snaps, markets) -> dict:
    quota = next((s.quota for s in reversed(snaps) if s.quota), {})
    return {"sports": sorted({s.sport for s in snaps}), "events": len({m.event_id for m in markets}),
            "markets": len(markets), "bookmakers": len({q.book for m in markets for q in m.quotes}),
            "credits_left": quota.get("remaining"),
            "odds_at": min(s.fetched_at for s in snaps).isoformat(timespec="seconds") if snaps else None}


def _save_latest(args, snaps, markets, arbs, opps) -> None:
    near = [o for o in opps if o not in arbs][:5]
    write_latest({**_stats(snaps, markets), "currency": args.currency}, arbs, near, args.budget, args.increment)


def _summary(snaps, markets, replay: bool = False) -> str:
    events = {m.event_id for m in markets}
    books = {q.book for m in markets for q in m.quotes}
    quota = next((s.quota for s in reversed(snaps) if s.quota), {})
    left = f" · {quota['remaining']:.0f} credits left" if "remaining" in quota else ""
    src = f" · replayed data captured {snaps[0].fetched_at:%d %b %H:%M} UTC" if replay and snaps else ""
    return f"{len(events)} events, {len(markets)} markets, {len(books)} bookmakers{left}{src}"


def cmd_scan(args) -> None:
    snaps = _fetch(args)
    if not snaps:
        return
    markets, arbs, opps, now = _scan_once(args, snaps)
    if not (args.demo or args.from_file):
        _save_latest(args, snaps, markets, arbs, opps)
    print(_summary(snaps, markets, replay=bool(args.demo or args.from_file)))
    print()
    if arbs:
        print(f"{len(arbs)} arbitrage{'s' if len(arbs) != 1 else ''} (budget {args.currency}{args.budget:,.0f} each):\n")
        for i, o in enumerate(arbs[:args.top], 1):
            print(format_opportunity(i, o, args.budget, args.currency, args.increment, now), end="\n\n")
        if not (args.demo or args.from_file or args.no_log):
            log_opportunities(arbs)
            print(f"Logged to {config.LOG_PATH}")
    else:
        print("No arbitrage right now. Closest markets:\n")
        for i, o in enumerate(opps[:5], 1):
            print(format_opportunity(i, o, args.budget, args.currency, args.increment, now), end="\n\n")


def _notify(title: str, text: str) -> None:
    if sys.platform == "darwin":
        script = ['-e', 'on run argv', '-e',
                  'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                  '-e', 'end run']
        subprocess.run(["osascript", *script, title, text], check=False)
    print("\a", end="", flush=True)


def cmd_watch(args) -> None:
    if args.demo or args.from_file:
        sys.exit("watch needs live data; use scan for --demo / --from-file.")
    api = _client(args)
    sports = _pick_sports(api, args)
    per_scan = len(sports) * call_cost(args.markets, args.regions, args.bookmakers)
    print(f"Watching {len(sports)} sports every {args.every:g} min, up to {per_scan} credits a scan. Ctrl-C to stop.")
    seen: dict[str, float] = {}
    args.cache_ttl = 0
    while True:
        snaps = []
        for sport in sports:
            try:
                snaps.append(api.odds(sport, args.regions, args.markets, args.bookmakers))
            except OddsAPIError as e:
                print(f"  {sport}: {e}")
        markets, arbs, opps, now = _scan_once(args, snaps)
        if snaps:
            _save_latest(args, snaps, markets, arbs, opps)
        fresh = [o for o in arbs if o.roi > seen.get(o.key, -1) + 0.0025]
        stamp = datetime.now().strftime("%H:%M")
        print(f"[{stamp}] {_summary(snaps, markets)} · {len(arbs)} arbs, {len(fresh)} new")
        for i, o in enumerate(fresh, 1):
            print(format_opportunity(i, o, args.budget, args.currency, args.increment, now), end="\n\n")
            seen[o.key] = o.roi
        if fresh:
            best = fresh[0]
            _notify(f"Arb {best.roi:+.2%}", f"{best.market.event} ({best.market.label})")
            if not args.no_log:
                log_opportunities(fresh)
        remaining = api.quota.get("remaining")
        if remaining is not None and remaining < per_scan + args.reserve:
            print(f"Stopping: {remaining:.0f} credits left, keeping a reserve of {args.reserve}.")
            return
        time.sleep(args.every * 60)


def cmd_sports(args) -> None:
    api = _client(args)
    for s in sorted(api.sports(include_all=args.all), key=lambda s: (s["group"], s["key"])):
        if s.get("has_outrights"):
            continue
        mark = "*" if s["key"] in config.DEFAULT_SPORTS else " "
        print(f"{mark} {s['key']:<40} {s['title']}")
    print("\n* = scanned by default")


def cmd_stake(args) -> None:
    comm = [float(c) for c in _csv(args.commission_each)] or [0.0] * len(args.odds)
    if len(comm) != len(args.odds):
        sys.exit("--commission-each needs one rate per price")
    eff = [effective_back_odds(o, c) for o, c in zip(args.odds, comm)]
    d = dutch_stakes(eff, args.budget, args.increment)
    print(f"Book sum {sum(1 / o for o in eff):.4f} → {'ARB' if d.min_profit > 0 else 'no arb'}")
    for o, e, s, pay in zip(args.odds, eff, d.stakes, d.payouts):
        note = f" (effective {e:.3f})" if e != o else ""
        print(f"  {o:>6.2f}{note}: stake {args.currency}{s:,.2f} → returns {args.currency}{pay:,.2f}")
    print(f"Outlay {args.currency}{d.outlay:,.2f}, guaranteed profit {args.currency}{d.min_profit:,.2f} ({d.roi:+.2%})")


def cmd_backlay(args) -> None:
    s = args.back_stake or back_stake_for_capital(args.back, args.lay, args.budget,
                                                  args.back_commission, args.lay_commission)
    bl = back_lay_stakes(args.back, args.lay, s, args.back_commission, args.lay_commission, args.increment)
    c = args.currency
    print(f"Back {c}{bl.back_stake:,.2f} @ {args.back}, lay {c}{bl.lay_stake:,.2f} @ {args.lay} "
          f"(liability {c}{bl.liability:,.2f})")
    signed = lambda x: f"{'+' if x >= 0 else '-'}{c}{abs(x):,.2f}"
    print(f"  selection wins:  {signed(bl.profit_if_wins)}")
    print(f"  selection loses: {signed(bl.profit_if_loses)}")
    print(f"Capital {c}{bl.capital:,.2f}, guaranteed {signed(bl.min_profit)} ({bl.roi:+.2%})"
          + ("" if bl.min_profit > 0 else " → not an arb"))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m arb", description="Find sports betting arbitrage across bookmakers and exchanges.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scan_opts(sp):
        sp.add_argument("--demo", action="store_true", help="use the bundled sample snapshot (no API key needed)")
        sp.add_argument("--from-file", help="replay a saved snapshot JSON")
        sp.add_argument("--sports", help="comma-separated sport keys (see `sports`)")
        sp.add_argument("--all-sports", action="store_true", help="every in-season sport (costs more credits)")
        sp.add_argument("--regions", default=config.DEFAULT_REGIONS, help="uk,eu,us,au (default %(default)s)")
        sp.add_argument("--markets", default=config.DEFAULT_MARKETS,
                        help="h2h,totals,spreads (default %(default)s; exchange lay prices come with h2h)")
        sp.add_argument("--bookmakers", help="fetch only these book keys instead of whole regions (cheaper if <=10)")
        sp.add_argument("--books", help="only use these book keys when looking for arbs (your accounts)")
        sp.add_argument("--exclude", help="ignore these book keys (on top of config.DEFAULT_EXCLUDE)")
        sp.add_argument("--all-books", action="store_true",
                        help="don't apply config.MY_BOOKS / DEFAULT_EXCLUDE")
        sp.add_argument("--commission", action="append", default=[], metavar="BOOK=RATE",
                        help="override an exchange's commission, e.g. betfair_ex_uk=0.02")
        sp.add_argument("--budget", type=float, default=100.0, help="capital per opportunity (default %(default)s)")
        sp.add_argument("--currency", default="€")
        sp.add_argument("--increment", type=float, default=config.STAKE_INCREMENT, help="round bookmaker stakes to this")
        sp.add_argument("--min-roi", type=float, default=config.MIN_ROI, help="e.g. 0.01 for 1%%")
        sp.add_argument("--max-hours", type=float, help="only events starting within this many hours")
        sp.add_argument("--include-live", action="store_true", help="include in-play events (risky: feed lags)")
        sp.add_argument("--top", type=int, default=20)
        sp.add_argument("--cache-ttl", type=float, default=60.0, help="reuse responses younger than this (s)")
        sp.add_argument("--no-log", action="store_true", help="don't append to data/opportunities.csv")

    sp = sub.add_parser("scan", help="one scan for arbs now")
    scan_opts(sp)
    sp.add_argument("--dry-run", action="store_true", help="show which sports and the credit cost, fetch nothing")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("watch", help="rescan on a timer and notify on new arbs")
    scan_opts(sp)
    sp.add_argument("--every", type=float, default=15.0, help="minutes between scans (default %(default)s)")
    sp.add_argument("--reserve", type=int, default=20, help="stop when credits would drop below this")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("sports", help="list in-season sport keys (free)")
    sp.add_argument("--all", action="store_true", help="include out-of-season sports")
    sp.add_argument("--cache-ttl", type=float, default=60.0)
    sp.set_defaults(func=cmd_sports)

    sp = sub.add_parser("stake", help="stake calculator for a set of back prices")
    sp.add_argument("odds", type=float, nargs="+")
    sp.add_argument("--budget", type=float, default=100.0)
    sp.add_argument("--commission-each", help="commission per price, e.g. 0,0,0.05")
    sp.add_argument("--increment", type=float, default=0.0)
    sp.add_argument("--currency", default="€")
    sp.set_defaults(func=cmd_stake)

    sp = sub.add_parser("backlay", help="back-lay calculator")
    sp.add_argument("--back", type=float, required=True)
    sp.add_argument("--lay", type=float, required=True)
    sp.add_argument("--back-stake", type=float, help="fix the back stake instead of a budget")
    sp.add_argument("--budget", type=float, default=100.0, help="stake + liability")
    sp.add_argument("--back-commission", type=float, default=0.0)
    sp.add_argument("--lay-commission", type=float, default=0.05)
    sp.add_argument("--increment", type=float, default=0.0)
    sp.add_argument("--currency", default="€")
    sp.set_defaults(func=cmd_backlay)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except OddsAPIError as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
