# Sports Arbitrage Scanner

A live scanner that looks for **betting arbitrage**: prices across bookmakers and betting
exchanges that, staked correctly, return a profit whatever the result. It pulls current
odds for upcoming events from [The Odds API](https://the-odds-api.com) (about 30 UK and
EU bookmakers usable from Ireland, plus the Betfair, Smarkets and Matchbook exchanges). It finds every arb and
sizes the stakes for your budget. It can also keep watching and send a macOS notification
when a new arb appears.

No third-party packages are needed. The scanner is plain Python 3.11+.

## Quick start

```bash
git clone https://github.com/samberman-hi/sports-arbitrage.git
cd sports-arbitrage
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[test]"                             # only pytest; the scanner itself has no dependencies

python -m arb scan --demo        # sample data, no key needed
python -m pytest                 # tests
```

To use live odds:

1. Get a free API key at <https://the-odds-api.com> (500 credits a month).
2. `cp .env.example .env` and paste the key after `ODDS_API_KEY=`.
3. Run:

```bash
python -m arb sports             # in-season leagues (free)
python -m arb scan --dry-run     # which leagues would be scanned and the credit cost
python -m arb scan               # one scan now
python -m arb watch --every 20   # rescan every 20 min, notify on new arbs
```

Useful options:

| Option | Effect |
|---|---|
| `--books paddypower,boylesports,betfair_ex_uk` | only show arbs you can place (or set `MY_BOOKS` in `arb/config.py`) |
| `--budget 250` | capital per arb (stakes plus any lay liability) |
| `--min-roi 0.01` | only arbs of 1% or more |
| `--max-hours 48` | only events starting in the next 48 hours |
| `--sports soccer_epl,basketball_nba` / `--all-sports` | choose leagues |
| `--markets h2h,totals,spreads` | add over/under and handicap markets (each one costs extra) |
| `--commission betfair_ex_uk=0.02` | your real exchange commission rate |
| `--bookmakers a,b,c` | fetch only these books, which costs 1 region's worth per 10 books |

Every arb found live is appended to `data/opportunities.csv`, so the log builds up a
record of when and where arbs appear. Each live scan also writes `data/latest.json` (the
arbs with their stakes, warnings and the closest near misses) for dashboards to read. Calculators work offline:

```bash
python -m arb stake 2.62 3.45 3.30 --budget 100 --increment 1
python -m arb backlay --back 2.24 --lay 2.12 --budget 100 --lay-commission 0.05
```

## Example output (demo data)

```
 3. ARB +2.61%  Brentford v Everton  (EPL)
    Match odds · cross-book · Sat 31 Oct 15:00 (in 4d 21h)
    BACK Brentford    2.62  @ BoyleSports  stake €39.00
    BACK Draw         3.45  @ Sky Bet      stake €30.00
    BACK Everton      3.30  @ Bet Victor   stake €31.00
    Capital €100.00 → profit €2.18 to €3.50 whatever the result
    or: back-lay +0.92%: back Brentford 2.62 @ BoyleSports, lay Brentford 2.52 @ Smarkets
```

The headline ROI is for exact stakes. The profit line uses stakes rounded to whole euros,
so it is a little lower. The demo prices are made up; see `data/sample/odds_sample.json`.

## How it works

Decimal odds `o` imply a probability of `1/o`. Two structures are checked in every market:

**Cross-book (dutching).** Take the best price for each outcome across all books. If
`Σ 1/oᵢ < 1`, staking `sᵢ ∝ 1/oᵢ` pays the same whichever outcome wins. The return on
outlay is `1 / Σ 1/oᵢ − 1`.

**Back-lay.** Back an outcome at a bookmaker at `b`, and lay it on an exchange at `l` with
commission `c`. Setting the lay stake to `b/(l − c)` per unit of back stake equalises both
outcomes. The guaranteed profit per unit is `b(1 − c)/(l − c) − 1`, so it is an arb when
`b(1 − c) > l − c`.

Exchange **back** prices are compared after commission, `1 + (o − 1)(1 − c)`, because
commission is charged only on winnings. Stakes are rounded to your increment. Each leg is
rounded up or down, whichever combination keeps the best guaranteed return.

The scanner protects against false arbs in a few ways:

- Soccer match odds need all three outcomes, including the draw.
- Asian quarter lines are skipped because they settle as half win, half push.
- You never back and lay at the same exchange.
- In-play events are skipped by default.

Each arb is also flagged when:

- its edge is above 8%, which usually means a pricing error that gets voided,
- its legs were updated more than 10 minutes apart (stale),
- it kicks off within 15 minutes,
- every leg is at a single book.

If a market has several arbs, the best one is shown and the rest are listed as
alternatives. They usually exploit the same mispriced price.

## Is it really risk-free?

The maths is risk-free. Placing the bets is not. These are the real risks, roughly in
order of how often they bite:

1. **Prices move between legs.** The feed is snapshots, often a minute or more old, and
   you place legs one after another. Place the leg most likely to move first, which is
   usually the soft bookmaker's outlier price. Then fill the exchange leg, which is deep
   and shows its price live.
2. **Account restrictions.** Soft bookmakers profile customers and quickly cut stakes
   for anyone who only takes their outlier prices. This, not the maths, is what limits
   arbing in practice.
3. **Palpable-error voids.** A price far off the market can be voided after the event, so
   one leg is cancelled and the other runs unhedged. That is why big edges are flagged.
4. **Rule differences.** The same "match odds" can settle differently at two books.
   Examples are tennis retirements, extra time and overtime, and dead-heat rules. Check
   both books' rules for the market.
5. **Exchange liquidity and commission.** The feed shows the best lay price, not how much
   money is available at it. Your real Betfair commission may differ from 5%.
6. **Capital lock-up.** Lay bets tie up their liability until the event settles.

Arbitrage betting is legal, but bookmakers' terms let them limit or close accounts. Only
stake what you can afford to have stuck in an account.

## API credits

`/sports` is free. Each `/odds` call costs (markets × regions) credits, so the default
(h2h, `uk,eu`) is 2 credits per league. Exchange lay prices come back with h2h at no
extra cost. Eight leagues is therefore about 16 credits a scan, or about 30 scans a month
on the free plan. To stretch that:

- use `--bookmakers` with your 10 books (1 credit per league),
- use fewer `--sports`,
- use a longer `watch --every`.

Responses are cached for 60 s, and `watch` stops before your credits run out.

## Layout

```
arb/maths.py      odds, commission, dutch and back-lay staking, stake rounding
arb/scanner.py    API events → markets; finds and flags arbs
arb/odds_api.py   The Odds API client, response cache, credit cost
arb/report.py     stakes for a budget, terminal output, CSV log
arb/cli.py        scan / watch / sports / stake / backlay
arb/config.py     exchanges and commissions, default leagues, thresholds
tests/            pytest suite (python -m pytest)
```
