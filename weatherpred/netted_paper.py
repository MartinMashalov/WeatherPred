"""Same-contract offsetting for a new, separately registered paper cohort.

No cross-contract collateral return is assumed. Opening-order cash reservations
and gross cost risk caps remain conservative; only completed opposite fills net.
The original E009/E015 reducer and registrations remain unchanged.
"""

from decimal import Decimal

from weatherpred.archive import canonical
from weatherpred.paper import empty_state, reduce_event, reserved
from weatherpred.timeutil import iso, utcnow

D = Decimal


def reduce_netted(state, event):
    result = reduce_event(state, event)
    result.setdefault("nettings", [])
    if event["type"] != "fill":
        return result
    order = result["orders"][event["data"]["order_id"]]
    prefix = order["account"] + ":" + order["ticker"] + ":"
    positions = {s: result["positions"].get(prefix + s) for s in ("yes", "no")}
    if not all(positions.values()):
        return result
    paired = min(D(p["quantity"]) for p in positions.values())
    matched_cost = D(0)
    legs = {}
    for side, p in positions.items():
        quantity, cost = D(p["quantity"]), D(p["cost"])
        allocated = cost * paired / quantity
        matched_cost += allocated
        legs[side] = {
            "quantity_before": str(quantity),
            "cost_before": str(cost),
            "allocated_cost": str(allocated),
        }
        if quantity == paired:
            del result["positions"][prefix + side]
        else:
            p["quantity"] = str(quantity - paired)
            p["cost"] = str(cost - allocated)
    a = result["accounts"][order["account"]]
    a["cash"] = str(D(a["cash"]) + paired)
    a["realized_pnl"] = str(D(a["realized_pnl"]) + paired - matched_cost)
    result["nettings"].append(
        {
            "at": event["at"],
            "account": order["account"],
            "ticker": order["ticker"],
            "order_id": order["id"],
            "fill_id": event["data"]["fill_id"],
            "source_record_id": event["data"]["source_record_id"],
            "quantity": str(paired),
            "cash_returned": str(paired),
            "matched_cost": str(matched_cost),
            "realized_pnl": str(paired - matched_cost),
            "legs": legs,
        }
    )
    if D(a["cash"]) < reserved(result, order["account"]):
        raise ValueError("Netting cash/reservation invariant failed")
    return result


class NettedPaperLedger:
    """A fill and its netting cash change commit together in one journal event."""

    def __init__(self, archive, run_id):
        self.archive, self.run_id, self.state = archive, str(run_id), empty_state()
        for row in archive.db.execute(
            "SELECT * FROM records WHERE kind='netted_paper_event' AND key=? ORDER BY id", (self.run_id,)
        ):
            self.state = reduce_netted(self.state, archive.json(row))

    def emit(self, kind, data, at=None):
        timestamp = at or utcnow()
        event = {"type": kind, "data": data, "at": iso(timestamp)}
        next_state = reduce_netted(self.state, event)
        record = self.archive.append(
            "netted_paper_event", self.run_id, timestamp, {}, canonical(event).encode()
        )
        self.state = next_state
        return record
