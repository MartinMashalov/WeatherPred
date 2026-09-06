"""Current fee arithmetic, from Kalshi's 2026-09-06 fee-rounding documentation.

Historical fee schedules must be supplied separately. No current schedule is
silently applied to old trades. An accumulator instance belongs to one order.
"""

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

D = Decimal


@dataclass(frozen=True)
class FeeSchedule:
    fee_type: str
    multiplier: Decimal
    balance_precision: Decimal = field(default_factory=lambda: D("0.0001"))

    def model_fee(self, price, quantity, maker=False):
        p, q = D(str(price)), D(str(quantity))
        if not (p.is_finite() and q.is_finite() and 0 <= p <= 1 and q > 0):
            raise ValueError("Invalid fill price or quantity")
        if self.fee_type not in ("quadratic", "quadratic_with_maker_fees"):
            raise ValueError(f"Unimplemented fee model: {self.fee_type}")
        if self.multiplier < 0 or not self.multiplier.is_finite():
            raise ValueError("Invalid fee multiplier")
        coefficient = D("0.07")
        if maker:
            coefficient = D("0.0175") if self.fee_type == "quadratic_with_maker_fees" else D(0)
        return coefficient * self.multiplier * q * p * (1 - p)


@dataclass
class FeeAccumulator:
    schedule: FeeSchedule
    carried: Decimal = field(default_factory=lambda: D(0))

    def fill(self, price, quantity, maker=False, sell=False):
        precision = self.schedule.balance_precision
        if precision not in (D("0.01"), D("0.0001")):
            raise ValueError("Unsupported balance precision")
        p, q = D(str(price)), D(str(quantity))
        model = self.schedule.model_fee(p, q, maker)
        trade_fee = model.quantize(D("0.000001"), rounding=ROUND_CEILING)
        revenue = p * q * (1 if sell else -1)
        aligned_change = (revenue - trade_fee).quantize(precision, rounding=ROUND_FLOOR)
        rounding_fee = revenue - trade_fee - aligned_change
        self.carried += rounding_fee
        rebate = min(
            self.carried.quantize(precision, rounding=ROUND_FLOOR),
            (trade_fee + rounding_fee).quantize(precision, rounding=ROUND_FLOOR),
        )
        self.carried -= rebate
        return {
            "trade_fee": trade_fee,
            "rounding_fee": rounding_fee,
            "rebate": rebate,
            "net_fee": trade_fee + rounding_fee - rebate,
            "balance_change": aligned_change + rebate,
        }
