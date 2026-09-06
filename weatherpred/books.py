"""Conservative displayed-liquidity cost calculations. These are NOT fills."""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from weatherpred.fees import FeeAccumulator, FeeSchedule

D = Decimal


def parse_book(data):
    if "orderbook_fp" not in data:
        raise ValueError("Missing fixed-point order book; do not interpret as empty liquidity")
    parsed = {}
    for side in ("yes", "no"):
        raw = data["orderbook_fp"].get(side + "_dollars")
        if raw is None:
            raise ValueError("Missing book side")
        levels = []
        seen = set()
        for pair in raw:
            p, q = D(str(pair[0])), D(str(pair[1]))
            if not (p.is_finite() and q.is_finite() and 0 < p < 1 and q > 0):
                raise ValueError("Invalid book level")
            if p in seen:
                raise ValueError("Duplicate price level")
            seen.add(p)
            levels.append((p, q))
        parsed[side] = sorted(levels, reverse=True)
    if parsed["yes"] and parsed["no"] and parsed["yes"][0][0] + parsed["no"][0][0] >= 1:
        raise ValueError("Locked/crossed book is not a reliable executable snapshot")
    return parsed


@dataclass(frozen=True)
class QuoteCost:
    quantity: Decimal
    principal: Decimal
    fees: Decimal
    worst_price: Decimal

    @property
    def total(self):
        return self.principal + self.fees


def purchase_cost(book, side, quantity, schedule: FeeSchedule, depth_retained="1", slippage="0"):
    """Walk opposite bids to buy this side. Return None when full size is unavailable.

    Slippage is charged once by moving each ask, not by adding the spread again.
    A later arrival snapshot is needed to model latency and actual execution.
    """
    if side not in ("yes", "no"):
        raise ValueError("Invalid outcome side")
    remaining, fraction, slip = D(str(quantity)), D(str(depth_retained)), D(str(slippage))
    if not (remaining > 0 and 0 < fraction <= 1 and 0 <= slip < 1):
        raise ValueError("Invalid execution scenario")
    principal, fees, worst = D(0), D(0), D(0)
    accumulator = FeeAccumulator(schedule)
    for bid, depth in book["no" if side == "yes" else "yes"]:
        ask = 1 - bid + slip
        if ask >= 1:
            continue
        # The research order uses whole contracts; venue fractional depth remains
        # usable but no unobserved quantity is invented or rounded upward.
        take = min(remaining, (depth * fraction).quantize(D("0.01"), rounding=ROUND_FLOOR))
        if take == 0:
            continue
        result = accumulator.fill(ask, take)
        principal += ask * take
        fees += result["net_fee"]
        worst = ask
        remaining -= take
        if remaining == 0:
            return QuoteCost(D(str(quantity)), principal, fees, worst)
    return None
