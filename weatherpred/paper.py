"""Event-sourced paper accounts and conservative taker/maker fill mechanics.

All fills here are simulated. Market evidence is referenced, never mutated.
"""

import copy
from decimal import ROUND_FLOOR, Decimal

from weatherpred.archive import canonical
from weatherpred.fees import FeeAccumulator, FeeSchedule
from weatherpred.timeutil import parse_time, utcnow

D = Decimal
OPEN = {"pending", "resting"}


def schedule_for(order):
    fee = order["fee_schedule"]
    return FeeSchedule(fee["fee_type"], D(fee["multiplier"]), D(fee["balance_precision"]))


def reserve_unit(order):
    # Maximum quadratic taker fee plus one cent rounding allowance per contract.
    return D(order["limit"]) + D("0.0175") * schedule_for(order).multiplier + D("0.01")


def reserved(state, account, event=None):
    return sum(
        (
            reserve_unit(o) * D(o["remaining"])
            for o in state["orders"].values()
            if o["account"] == account and o["status"] in OPEN and (event is None or o["event"] == event)
        ),
        D(0),
    )


def exposure(state, account, event=None):
    return reserved(state, account, event) + sum(
        (
            D(p["cost"])
            for p in state["positions"].values()
            if p["account"] == account and (event is None or p["event"] == event)
        ),
        D(0),
    )


def reduce_event(state, event):
    """Validate an event on a copy, making failed journal writes atomic in memory."""
    state = copy.deepcopy(state)
    kind, value = event["type"], event["data"]
    if kind == "account":
        key = value["account"]
        if key in state["accounts"] or D(value["initial_cash"]) <= 0:
            raise ValueError("Duplicate or invalid paper account")
        state["accounts"][key] = {
            "initial_cash": value["initial_cash"],
            "cash": value["initial_cash"],
            "fees": "0",
            "realized_pnl": "0",
            "peak_equity": value["initial_cash"],
            "marked_equity": value["initial_cash"],
            "halted": False,
        }
    elif kind == "order":
        order = copy.deepcopy(value)
        if order["id"] in state["orders"] or order["account"] not in state["accounts"]:
            raise ValueError("Duplicate order or unknown paper account")
        if state["accounts"][order["account"]]["halted"]:
            raise ValueError("Paper account drawdown kill switch is active")
        quantity, limit = D(order["quantity"]), D(order["limit"])
        if (
            not quantity.is_finite()
            or quantity <= 0
            or quantity != quantity.to_integral_value()
            or not 0 < limit < 1
        ):
            raise ValueError("Invalid intended whole quantity or limit")
        if order["side"] not in ("yes", "no") or order["style"] not in ("taker", "maker"):
            raise ValueError("Invalid paper order side/style")
        schedule_for(order).model_fee("0.5", 1)
        if (
            not parse_time(order["submitted_at"])
            < parse_time(order["arrival_due_at"])
            < parse_time(order["expires_at"])
            <= parse_time(order["close_at"])
        ):
            raise ValueError("Order timing/latency violates its trading window")
        if parse_time(event["at"]) != parse_time(order["submitted_at"]):
            raise ValueError("Order timestamp differs from journal publication")
        cash = D(state["accounts"][order["account"]]["cash"])
        cost = reserve_unit(order) * quantity
        if cash - reserved(state, order["account"]) < cost:
            raise ValueError("Paper order exceeds available cash")
        # Only Miami in this first run: the entire portfolio is one weather cluster.
        equity = cash + exposure(state, order["account"]) - reserved(state, order["account"])
        if exposure(state, order["account"], order["event"]) + cost > equity * D("0.05"):
            raise ValueError("Paper event exposure exceeds 5 percent")
        if exposure(state, order["account"]) + cost > equity * D("0.10"):
            raise ValueError("Paper weather-cluster exposure exceeds 10 percent")
        order.update(
            status="pending",
            remaining=str(quantity),
            fee_carried="0",
            queue_ahead="0",
            seen_trades=[],
            fill_ids=[],
        )
        state["orders"][order["id"]] = order
    elif kind == "arrival":
        order = state["orders"][value["order_id"]]
        if order["status"] != "pending" or not parse_time(order["arrival_due_at"]) <= parse_time(
            event["at"]
        ) < parse_time(order["expires_at"]):
            raise ValueError("Invalid paper arrival time/status")
        if D(value["queue_ahead"]) < 0:
            raise ValueError("Negative queue ahead")
        order.update(
            status="resting",
            arrived_at=event["at"],
            queue_ahead=value["queue_ahead"],
            arrival_book_record_id=value["book_record_id"],
        )
    elif kind == "cancel":
        order = state["orders"][value["order_id"]]
        if order["status"] not in OPEN:
            raise ValueError("Only open paper orders can be cancelled")
        order.update(status="cancelled", cancelled_at=event["at"], cancel_reason=value["reason"])
    elif kind == "queue":
        order = state["orders"][value["order_id"]]
        if order["status"] != "resting" or order["style"] != "maker":
            raise ValueError("Queue update requires a resting maker order")
        if value["trade_id"] in order["seen_trades"] or not D(0) <= D(value["queue_ahead"]) <= D(
            order["queue_ahead"]
        ):
            raise ValueError("Duplicate trade or invalid queue advancement")
        order["seen_trades"].append(value["trade_id"])
        order["queue_ahead"] = value["queue_ahead"]
    elif kind == "fill":
        order = state["orders"][value["order_id"]]
        quantity, price = D(value["quantity"]), D(value["price"])
        if order["status"] != "resting" or value["fill_id"] in order["fill_ids"]:
            raise ValueError("Order is not fillable or fill already applied")
        if (
            not parse_time(order["arrived_at"])
            <= parse_time(value["evidence_at"])
            <= parse_time(event["at"])
            < parse_time(order["expires_at"])
        ):
            raise ValueError("Fill evidence is before arrival, future or after expiration")
        if (
            not D(0) < quantity <= D(order["remaining"])
            or quantity != quantity.quantize(D("0.01"))
            or not D(0) < price <= D(order["limit"])
        ):
            raise ValueError("Fill exceeds quantity/limit or has invalid precision")
        accumulator = FeeAccumulator(schedule_for(order), D(order["fee_carried"]))
        fee = accumulator.fill(price, quantity, maker=order["style"] == "maker")
        cost = -fee["balance_change"]
        account = state["accounts"][order["account"]]
        account["cash"] = str(D(account["cash"]) - cost)
        account["fees"] = str(D(account["fees"]) + fee["net_fee"])
        order["remaining"] = str(D(order["remaining"]) - quantity)
        order["fee_carried"] = str(accumulator.carried)
        order["fill_ids"].append(value["fill_id"])
        if D(order["remaining"]) == 0:
            order["status"] = "filled"
        key = order["account"] + ":" + order["ticker"] + ":" + order["side"]
        position = state["positions"].setdefault(
            key, {k: order[k] for k in ("account", "event", "ticker", "side", "close_at", "floor_strike")}
        )
        position["quantity"] = str(D(position.get("quantity", "0")) + quantity)
        position["cost"] = str(D(position.get("cost", "0")) + cost)
        state["fills"].append(
            {
                **value,
                "account": order["account"],
                "ticker": order["ticker"],
                "side": order["side"],
                "style": order["style"],
                "at": event["at"],
                "cost": str(cost),
                "fee": str(fee["net_fee"]),
                "simulated": True,
            }
        )
    elif kind == "settlement":
        if value["event"] in state["settled_events"]:
            raise ValueError("Event was already settled")
        if any(o["event"] == value["event"] and o["status"] in OPEN for o in state["orders"].values()):
            raise ValueError("Cancel open orders before settling")
        for key, position in list(state["positions"].items()):
            if position["event"] != value["event"]:
                continue
            label = value["labels"][position["ticker"]]
            if label not in (0, 1) or parse_time(event["at"]) < parse_time(position["close_at"]):
                raise ValueError("Invalid or premature settlement label")
            payout = D(label if position["side"] == "yes" else 1 - label) * D(position["quantity"])
            account = state["accounts"][position["account"]]
            account["cash"] = str(D(account["cash"]) + payout)
            account["realized_pnl"] = str(D(account["realized_pnl"]) + payout - D(position["cost"]))
            state["settlements"].append(
                {
                    **position,
                    "payout": str(payout),
                    "at": event["at"],
                    "source_record_id": value["source_record_id"],
                }
            )
            del state["positions"][key]
        state["settled_events"].append(value["event"])
    elif kind == "slot":
        if value["scheduled_at"] in state["slots"]:
            raise ValueError("Paper slot processed twice")
        state["slots"].append(value["scheduled_at"])
    elif kind == "mark":
        account = state["accounts"][value["account"]]
        equity = D(value["equity"])
        if not equity.is_finite() or equity < 0:
            raise ValueError("Invalid paper marked equity")
        peak = max(equity, D(account["peak_equity"]))
        account.update(marked_equity=str(equity), peak_equity=str(peak), marked_at=event["at"])
        if equity <= peak * D("0.8"):
            account["halted"] = True
    elif kind not in ("abstain", "error", "stopped"):
        raise ValueError("Unknown paper journal event")
    for key, account in state["accounts"].items():
        if D(account["cash"]) < 0 or D(account["cash"]) < reserved(state, key):
            raise ValueError("Paper cash/reserve invariant failed")
    return state


def empty_state():
    return {
        "accounts": {},
        "orders": {},
        "positions": {},
        "fills": [],
        "settlements": [],
        "settled_events": [],
        "slots": [],
    }


class PaperLedger:
    def __init__(self, archive, run_id):
        self.archive, self.run_id, self.state = archive, str(run_id), empty_state()
        for row in archive.db.execute(
            "SELECT * FROM records WHERE kind='paper_event' AND key=? ORDER BY id", (self.run_id,)
        ):
            self.state = reduce_event(self.state, archive.json(row))

    def emit(self, kind, data, at=None):
        timestamp = at or utcnow()
        event = {"type": kind, "data": data, "at": timestamp.isoformat(timespec="microseconds")}
        next_state = reduce_event(self.state, event)
        record = self.archive.append("paper_event", self.run_id, timestamp, {}, canonical(event).encode())
        self.state = next_state
        return record


def taker_slices(book, side, remaining, limit, retained, slippage):
    remaining, limit, fraction, slip = map(D, map(str, (remaining, limit, retained, slippage)))
    if side not in ("yes", "no") or remaining <= 0 or not 0 < fraction <= 1 or not 0 <= slip < 1:
        raise ValueError("Invalid taker scenario")
    fills = []
    for bid, size in book["no" if side == "yes" else "yes"]:
        price = 1 - bid + slip
        if price > limit or price >= 1:
            break
        quantity = min(remaining, (size * fraction).quantize(D("0.01"), rounding=ROUND_FLOOR))
        if quantity > 0:
            fills.append({"price": str(price), "quantity": str(quantity)})
            remaining -= quantity
        if remaining == 0:
            break
    return fills


def maker_trade(order, trade, received_at, participation="0.25"):
    """Queue burns only on opposite-taker prints strictly through our bid."""
    participation = D(participation)
    if not participation.is_finite() or not 0 < participation <= 1:
        raise ValueError("Invalid maker volume participation")
    if trade["trade_id"] in order["seen_trades"] or trade.get("is_block_trade") is not False:
        return None
    side = trade.get("taker_outcome_side")
    if side not in ("yes", "no") or trade.get("taker_book_side") != ("bid" if side == "yes" else "ask"):
        raise ValueError("Ambiguous public trade direction")
    if trade.get("taker_side", side) != side:
        raise ValueError("Conflicting public trade directions")
    when = parse_time(trade["created_time"])
    if not parse_time(order["arrived_at"]) < when <= received_at < parse_time(order["expires_at"]):
        return None
    if trade["ticker"] != order["ticker"] or side == order["side"]:
        return None
    yes, no, volume = (D(trade[k]) for k in ("yes_price_dollars", "no_price_dollars", "count_fp"))
    if not 0 < yes < 1 or not 0 < no < 1 or yes + no != 1 or not volume.is_finite() or volume <= 0:
        raise ValueError("Malformed public trade prices/quantity")
    price = yes if order["side"] == "yes" else no
    if price >= D(order["limit"]):
        return None
    ahead = D(order["queue_ahead"])
    excess = max(D(0), volume - ahead)
    queue = max(D(0), ahead - volume)
    quantity = min(D(order["remaining"]), (excess * participation).quantize(D("0.01"), rounding=ROUND_FLOOR))
    return {
        "queue_ahead": str(queue),
        "quantity": str(quantity),
        "evidence_at": trade["created_time"],
        "trade_id": trade["trade_id"],
    }
