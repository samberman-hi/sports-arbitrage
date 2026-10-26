"""Arbitrage maths on decimal odds (total return per 1 staked).

Two structures are covered:

* Cross-book ("dutch") arb: back every outcome of a market, each at the best price
  available anywhere. Exists when sum(1 / odds) < 1. Staking each leg in proportion to
  1 / odds pays the same amount whichever outcome wins.
* Back-lay arb: back an outcome at a bookmaker and lay the same outcome on an exchange.
  Exists when the back price beats the lay price after commission.

Exchange commission is charged on net winnings, so a back bet at odds o with
commission c behaves like a commission-free bet at 1 + (o - 1)(1 - c).
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass


def effective_back_odds(odds: float, commission: float = 0.0) -> float:
    """Odds after commission on winnings, i.e. what a winning 1-unit back bet really returns."""
    return 1.0 + (odds - 1.0) * (1.0 - commission)


def book_sum(odds: list[float]) -> float:
    """Sum of implied probabilities. Below 1 means an arbitrage exists."""
    return sum(1.0 / o for o in odds)


def arb_roi(odds: list[float]) -> float:
    """Guaranteed return on total outlay of a perfectly staked cross-book arb (0.02 = 2%)."""
    return 1.0 / book_sum(odds) - 1.0


def round_to(x: float, increment: float) -> float:
    if increment <= 0:
        return x
    return round(round(x / increment) * increment, 10)


@dataclass(frozen=True)
class DutchStakes:
    stakes: list[float]   # stake per leg, after rounding
    outlay: float         # total staked
    payouts: list[float]  # total return if leg i wins
    min_profit: float     # worst case over outcomes, the guaranteed profit
    max_profit: float

    @property
    def roi(self) -> float:
        return self.min_profit / self.outlay if self.outlay else 0.0


def dutch_stakes(odds: list[float], total: float, increment: float = 0.0) -> DutchStakes:
    """Split `total` across legs so every outcome pays (about) the same.

    `odds` should already be commission-adjusted. With `increment` > 0 the stakes are
    rounded to that unit (e.g. 1.0 for whole euros), which makes the payouts slightly
    unequal; the guaranteed profit is then the smallest of them minus the outlay. Each
    leg is rounded up or down, whichever combination keeps the best guaranteed return
    without going more than one increment over `total`.
    """
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    exact = [total * i / s for i in inv]

    def build(stakes):
        outlay = sum(stakes)
        payouts = [st * o for st, o in zip(stakes, odds)]
        return DutchStakes(list(stakes), outlay, payouts, min(payouts) - outlay, max(payouts) - outlay)

    if increment <= 0:
        return build(exact)
    if len(odds) > 6:  # 2^n combinations; plain rounding is fine for very wide markets
        return build([round_to(x, increment) for x in exact])
    options = [sorted({round_to(math.floor(x / increment) * increment, increment),
                       round_to(math.ceil(x / increment) * increment, increment)}) for x in exact]
    best = None
    for combo in itertools.product(*options):
        if sum(combo) > total + increment + 1e-9 or min(combo) <= 0:
            continue
        d = build(combo)
        if best is None or d.roi > best.roi + 1e-12:
            best = d
    return best or build([round_to(x, increment) for x in exact])


@dataclass(frozen=True)
class BackLay:
    back_stake: float
    lay_stake: float      # backer's stake you accept on the exchange
    liability: float      # what you lose on the exchange if the selection wins
    profit_if_wins: float
    profit_if_loses: float

    @property
    def capital(self) -> float:
        """Cash that has to sit in the two accounts: back stake plus lay liability."""
        return self.back_stake + self.liability

    @property
    def min_profit(self) -> float:
        return min(self.profit_if_wins, self.profit_if_loses)

    @property
    def roi(self) -> float:
        return self.min_profit / self.capital if self.capital else 0.0


def back_lay_edge(back: float, lay: float, back_commission: float = 0.0,
                  lay_commission: float = 0.0) -> float:
    """Guaranteed profit per 1 of back stake when the lay stake is set to equalise outcomes.

    Positive means an arbitrage. With b' the commission-adjusted back odds, the equalising
    lay stake is b' / (lay - c_lay) and the profit is b'(1 - c_lay) / (lay - c_lay) - 1.
    """
    b = effective_back_odds(back, back_commission)
    return b * (1.0 - lay_commission) / (lay - lay_commission) - 1.0


def back_lay_stakes(back: float, lay: float, back_stake: float, back_commission: float = 0.0,
                    lay_commission: float = 0.0, increment: float = 0.0) -> BackLay:
    """Stakes for backing at `back` and laying at `lay` so both outcomes pay the same.

    `increment` rounds the bookmaker stake only; exchanges take stakes to the cent, so
    the lay stake is rounded to 0.01 after the back stake is fixed.
    """
    b = effective_back_odds(back, back_commission)
    s = round_to(back_stake, increment)
    lay_stake = round(s * b / (lay - lay_commission), 2)
    liability = lay_stake * (lay - 1.0)
    wins = s * (b - 1.0) - liability
    loses = lay_stake * (1.0 - lay_commission) - s
    return BackLay(s, lay_stake, liability, wins, loses)


def back_stake_for_capital(back: float, lay: float, capital: float, back_commission: float = 0.0,
                           lay_commission: float = 0.0) -> float:
    """Back stake whose stake + lay liability uses up `capital`."""
    b = effective_back_odds(back, back_commission)
    return capital / (1.0 + b * (lay - 1.0) / (lay - lay_commission))


def is_finite_price(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 1.0
