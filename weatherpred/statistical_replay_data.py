"""Quote-only model views, separately released labels and a post-prediction broker.

Pure functions: importing this module opens no dataset and computes no scores.
Historical clocks/depth/fees remain declared assumptions, never live receipts.
"""

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from weatherpred.archive import canonical
from weatherpred.bankroll_replay import aggregate_cash
from weatherpred.timeutil import parse_time

HOUR = 3600
FIRST = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
LAST = datetime(2026, 9, 6, tzinfo=UTC).timestamp()
SERIES = ("KXHIGHAUS", "KXHIGHCHI", "KXHIGHDEN", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHNY", "KXHIGHPHIL")
FEATURE_NAMES = (
    "market_logit",
    "spread",
    "mid_change_1h",
    "mid_change_3h",
    "observed_panel_mid_mass",
    "panel_quote_fraction",
    "bracket_rank",
    "season_sin",
    "season_cos",
    *tuple("series_" + s for s in SERIES[1:]),
)
SIDES = ("yes", "no")
LIMIT_SLIPPAGE = Decimal("0.01")
HASH = re.compile(r"[0-9a-f]{64}")


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite numeric UTC timestamp")
    return value


def money(value):
    if isinstance(value, bool):
        raise TypeError("Boolean money is invalid")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite monetary value")
    return result


def provenance(parts, assumed_at):
    if not parts:
        raise ValueError("Missing source provenance")
    allowed = (
        "source_record_id",
        "body_sha256",
        "record_sha256",
        "available_at",
        "received_at",
        "request_started_at",
        "url",
        "source_row_index",
        "quote_status",
    )
    safe = [{key: part[key] for key in allowed if key in part} for part in parts]
    for source in safe:
        if type(source.get("source_record_id")) is not int or source["source_record_id"] <= 0:
            raise ValueError("A source needs its original positive record ID")
        for key in ("body_sha256", "record_sha256"):
            if not isinstance(source.get(key), str) or HASH.fullmatch(source[key]) is None:
                raise ValueError("Missing original source hash")
    return {
        "available_ts": max(parse_time(part["available_at"]).timestamp() for part in safe),
        "availability_verified": False,
        "assumed_available_ts": stamp(assumed_at),
        "source_parts": safe,
        "availability_basis": "retrospective_conditional_endpoint_clock_actual_receipts_preserved",
    }


def exact_quote(market, when, cutoff):
    """No later/previous replacement; gate endpoint before consulting price fields."""
    when, cutoff = stamp(when), stamp(cutoff)
    if not FIRST < when < min(cutoff, LAST) or when % HOUR:
        return None, None
    quotes = market["quotes"]
    row = quotes.get(when) if when in quotes else quotes.get(str(int(when)))
    if row is None or row.get("bid") is None or row.get("ask") is None:
        return None, None
    bid, ask = money(row["bid"]), money(row["ask"])
    if not 0 < bid < ask < 1:
        return None, None
    sources = market["quote_provenance"]
    source = sources[when] if when in sources else sources[str(int(when))]
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}, source


def make_private_lookup(dataset):
    """Evaluator-only index. Never pass this or the original dataset to models."""
    if dataset.get("complete_event_census") is not True:
        raise ValueError("Incomplete dataset census")
    result = {}
    for market in dataset["markets"]:
        ticker = market["metadata"]["ticker"]
        if ticker in result:
            raise ValueError("Duplicate contract identity")
        result[ticker] = market
    if dataset.get("contracts") != len(result):
        raise ValueError("Contract count changed")
    return result


def rank_key(metadata):
    kind = metadata["strike_type"]
    if kind == "less":
        return Decimal("-Infinity"), money(metadata["cap_strike"]), metadata["ticker"]
    if kind == "between":
        low, high = money(metadata["floor_strike"]), money(metadata["cap_strike"])
        if low > high:
            raise ValueError("Inverted bracket")
        return low, high, metadata["ticker"]
    if kind == "greater":
        return money(metadata["floor_strike"]), Decimal("Infinity"), metadata["ticker"]
    raise ValueError("Unregistered bracket predicate")


def build_opportunities(dataset):
    """Full supported contract census at source_period_end−12h, with missing flags."""
    lookup = make_private_lookup(dataset)
    grouped, exclusions, event_source_states = defaultdict(list), [], defaultdict(set)
    for ticker, market in sorted(lookup.items()):
        meta = market["metadata"]
        identity = {key: meta[key] for key in ("ticker", "event", "day", "series")}
        if meta["series"] not in SERIES or not "2026-01-01" <= meta["day"] < "2026-09-06":
            raise ValueError("Foreign series or protected event day")
        event_source_states[meta["event"]].add((meta["source_window_status"], meta["source_period_end"]))
        if meta["source_window_status"] != "nws_standard_time_mapping" or meta["source_period_end"] is None:
            exclusions.append({**identity, "reason": "unsupported_source_window"})
        else:
            grouped[meta["event"]].append(market)
    if any(len(states) != 1 for states in event_source_states.values()):
        raise ValueError("An event cannot silently lose members through mixed source-window support")
    opportunities = []
    for event, members in sorted(grouped.items()):
        periods = {m["metadata"]["source_period_end"] for m in members}
        if len(periods) != 1:
            raise ValueError("Inconsistent event source window")
        decision = int(parse_time(next(iter(periods))).timestamp()) - 12 * HOUR
        if not FIRST <= decision < LAST or decision % HOUR:
            raise ValueError("Fixed event decision is outside the registered hourly calendar")
        # Event-wide features may only inspect contracts active at this decision.
        # Retain later-opened/closed contracts in the census as abstentions, but
        # never let their eventual strikes, count or receipts change active rows.
        active = [m for m in members if m["metadata"]["open_ts"] <= decision < m["metadata"]["close_ts"]]
        ranked = sorted(active, key=lambda m: rank_key(m["metadata"]))
        ranks = {m["metadata"]["ticker"]: i / max(1, len(ranked) - 1) for i, m in enumerate(ranked)}
        current, panel_sources = {}, []
        for member in members:
            meta = member["metadata"]
            is_active = meta["open_ts"] <= decision < meta["close_ts"]
            quote, source = exact_quote(member, decision, decision + 1) if is_active else (None, None)
            current[meta["ticker"]] = quote
            if is_active:
                panel_sources.append(meta["metadata_provenance"])
            if source is not None:
                panel_sources.append(source)
        valid_current = [q for q in current.values() if q is not None]
        mass = sum((q["mid"] for q in valid_current), Decimal(0))
        quote_fraction = len(valid_current) / len(active) if active else 0.0
        for market in sorted(members, key=lambda m: m["metadata"]["ticker"]):
            meta, reasons = market["metadata"], []
            ticker, quote = meta["ticker"], current[meta["ticker"]]
            is_active = meta["open_ts"] <= decision < meta["close_ts"]
            sources = list(panel_sources) if is_active else [meta["metadata_provenance"]]
            past = {}
            for lag in (1, 3):
                when = decision - lag * HOUR
                prior, source = (
                    exact_quote(market, when, decision + 1)
                    if is_active and meta["open_ts"] <= when < meta["close_ts"]
                    else (None, None)
                )
                past[lag] = prior
                if source is not None:
                    sources.append(source)
            phase = 2 * math.pi * (datetime.fromisoformat(meta["day"]).timetuple().tm_yday - 1) / 365
            features = [
                math.log(float(quote["mid"] / (1 - quote["mid"]))) if quote else None,
                float(quote["ask"] - quote["bid"]) if quote else None,
                float(quote["mid"] - past[1]["mid"]) if quote and past[1] else None,
                float(quote["mid"] - past[3]["mid"]) if quote and past[3] else None,
                float(mass),
                quote_fraction,
                ranks.get(ticker),
                math.sin(phase),
                math.cos(phase),
                *(float(meta["series"] == series) for series in SERIES[1:]),
            ]
            if not meta["open_ts"] <= decision < meta["close_ts"]:
                reasons.append("outside_market_hours")
            reasons.extend(
                "missing_" + name
                for name, value in zip(FEATURE_NAMES, features, strict=True)
                if value is None
            )
            row = {
                "opportunity_id": f"{ticker}:{decision}",
                "ticker": ticker,
                "event": event,
                "day": meta["day"],
                "series": meta["series"],
                "decision_ts": decision,
                "open_ts": meta["open_ts"],
                "close_ts": meta["close_ts"],
                "yes_bid": str(quote["bid"]) if quote else None,
                "yes_ask": str(quote["ask"]) if quote else None,
                "no_ask": str(1 - quote["bid"]) if quote else None,
                "feature_vector": features,
                "feature_mask": [v is not None for v in features],
                "feature_names": list(FEATURE_NAMES),
                "feature_complete": not reasons,
                "missing_reasons": reasons,
                "signal_provenance": provenance(sources, decision),
                "historical_availability_verified": False,
            }
            row["opportunity_sha256"] = digest(row)
            opportunities.append(row)
    return {
        "opportunities": opportunities,
        "exclusions": exclusions,
        "feature_names": list(FEATURE_NAMES),
        "census": {
            "input_contracts": len(lookup),
            "opportunities": len(opportunities),
            "complete_features": sum(row["feature_complete"] for row in opportunities),
            "excluded": len(exclusions),
            "events": len(grouped),
        },
    }


def checked_opportunity(row, lookup):
    if digest({k: v for k, v in row.items() if k != "opportunity_sha256"}) != row["opportunity_sha256"]:
        raise ValueError("Immutable opportunity changed")
    meta = lookup[row["ticker"]]["metadata"]
    if any(meta[key] != row[key] for key in ("ticker", "event", "day", "series", "open_ts", "close_ts")):
        raise ValueError("Opportunity/private contract identity changed")
    expected = int(parse_time(meta["source_period_end"]).timestamp()) - 12 * HOUR
    if row["decision_ts"] != expected or row["opportunity_id"] != f"{row['ticker']}:{expected}":
        raise ValueError("Fixed decision identity changed")
    return lookup[row["ticker"]]


def scenario_values(scenario):
    delay, slip, coefficient = (
        scenario["entry_delay_hours"],
        money(scenario["slippage"]),
        money(scenario["entry_coefficient"]),
    )
    if (
        type(delay) is not int
        or delay not in (1, 2)
        or slip not in (Decimal(".01"), Decimal(".02"))
        or coefficient != Decimal(".07")
    ):
        raise ValueError("Unregistered initial-lab execution scenario")
    if (delay, slip) not in ((1, Decimal(".01")), (2, Decimal(".02"))):
        raise ValueError("Entry delay and slippage scenario are coupled")
    return delay, slip, coefficient


def side_limit(opportunity, side):
    if side not in SIDES:
        raise ValueError("Unknown contract side")
    ask = opportunity[side + "_ask"]
    return min(Decimal(".99"), money(ask) + LIMIT_SLIPPAGE) if ask is not None else None


def attempt(opportunity, market, side, scenario, cutoff, limit):
    delay, slip, coefficient = scenario_values(scenario)
    entry = opportunity["decision_ts"] + delay * HOUR
    result = {
        "entry_ts": entry,
        "entry_price": None,
        "entry_price_provenance": None,
        "return_known": False,
        "net_return": 0.0,
        "return_release_ts": None,
        "terminal_cash_per_contract": None,
        "exit_ts": None,
        "terminal_provenance": None,
    }
    if entry >= cutoff:
        return {**result, "reason": "entry_not_released"}
    if limit is None or not opportunity["open_ts"] <= entry < opportunity["close_ts"]:
        return {**result, "reason": "no_admissible_attempt"}
    quote, source = exact_quote(market, entry, cutoff)
    if quote is None:
        return {**result, "reason": "unknown_missing_entry_endpoint"}
    price = (quote["ask"] if side == "yes" else 1 - quote["bid"]) + slip
    result.update(entry_price=str(price), entry_price_provenance=provenance([source], entry))
    if not 0 < price < 1 or price > limit:
        return {**result, "reason": "known_limit_rejection", "return_known": True, "return_release_ts": entry}
    settled = market["metadata"].get("settled_ts")
    if settled is None or stamp(settled) >= cutoff:
        return {**result, "reason": "filled_unreleased_settlement"}
    if settled <= entry:
        raise ValueError("Settlement is not strictly after a proposed valid entry")
    # Outcome and source receipt are consulted only after the release-time gate.
    outcome = market["metadata"].get("outcome")
    if type(outcome) is not int or outcome not in (0, 1):
        return {**result, "reason": "unknown_nonbinary_settlement"}
    payout = outcome if side == "yes" else 1 - outcome
    cash = aggregate_cash(price, 1, coefficient)
    released = max(entry, settled)
    result.update(
        reason="settled_conditional_fill",
        return_known=True,
        net_return=float(Decimal(payout) + cash["cash_change"]),
        return_release_ts=released,
        terminal_cash_per_contract=str(payout),
        exit_ts=settled,
        terminal_provenance=provenance([market["metadata"]["metadata_provenance"]], settled),
    )
    return result


def label_rows_before(opportunities, private_lookup, fit_cutoff, scenario):
    """Targets are masks plus zero placeholders; UNKNOWN is never a zero outcome."""
    cutoff = stamp(fit_cutoff)
    scenario_values(scenario)
    labels = []
    for opportunity in opportunities:
        market = checked_opportunity(opportunity, private_lookup)
        label = {
            "opportunity_id": opportunity["opportunity_id"],
            "probability_label": 0,
            "probability_known": False,
            "probability_release_ts": None,
            "probability_provenance": None,
            "net_return_labels": [0.0, 0.0],
            "net_return_known": [False, False],
            "net_return_release_ts": [None, None],
            "side_reasons": ["decision_not_before_fit", "decision_not_before_fit"],
            "side_provenance": [None, None],
            "historical_availability_verified": False,
        }
        if opportunity["decision_ts"] >= cutoff:
            labels.append(label)
            continue
        settled = market["metadata"].get("settled_ts")
        if settled is not None and opportunity["decision_ts"] < stamp(settled) < cutoff:
            outcome = market["metadata"].get("outcome")
            if type(outcome) is int and outcome in (0, 1):
                label.update(
                    probability_label=outcome,
                    probability_known=True,
                    probability_release_ts=settled,
                    probability_provenance=provenance([market["metadata"]["metadata_provenance"]], settled),
                )
        for i, side in enumerate(SIDES):
            result = attempt(opportunity, market, side, scenario, cutoff, side_limit(opportunity, side))
            label["net_return_labels"][i] = result["net_return"]
            label["net_return_known"][i] = result["return_known"]
            label["net_return_release_ts"][i] = result["return_release_ts"]
            label["side_reasons"][i] = result["reason"]
            label["side_provenance"][i] = {
                "entry": result["entry_price_provenance"],
                "terminal": result["terminal_provenance"],
            }
        labels.append(label)
    return labels


def resolve_intents(decisions, private_lookup, scenario, evaluation_end, *, prediction_artifact):
    """Resolve only a previously archived exact decision batch; no model invocation.

    Each decision contains opportunity (the sealed public row), policy_id, side,
    and original limit_price. Caller must verify the archive record referenced by
    prediction_artifact; this pure boundary checks its positive ID/hash fields
    and exact decisions_sha256 before opening any future endpoint or outcome.
    """
    if (
        type(prediction_artifact.get("record_id")) is not int
        or prediction_artifact["record_id"] <= 0
        or any(
            not isinstance(prediction_artifact.get(key), str)
            or HASH.fullmatch(prediction_artifact[key]) is None
            for key in ("body_sha256", "record_sha256")
        )
        or prediction_artifact.get("decisions_sha256") != digest(decisions)
    ):
        raise ValueError("Immutable archived prediction/decision binding is required first")
    cutoff = stamp(evaluation_end)
    scenario_values(scenario)
    orders, statuses, seen = [], [], set()
    for decision in decisions:
        opportunity, side, policy = decision["opportunity"], decision["side"], decision["policy_id"]
        market = checked_opportunity(opportunity, private_lookup)
        identity = opportunity["opportunity_id"]
        if identity in seen or side not in SIDES or not isinstance(policy, str) or not policy:
            raise ValueError("Duplicate/invalid immutable intent")
        seen.add(identity)
        limit = side_limit(opportunity, side)
        if limit is None or money(decision["limit_price"]) != limit:
            raise ValueError("Original decision ask+1cent limit changed across scenarios")
        result = attempt(opportunity, market, side, scenario, cutoff, limit)
        trade_id = f"{policy}:{identity}:{side}"
        orders.append(
            {
                "trade_id": trade_id,
                "policy_id": policy,
                "event": opportunity["event"],
                "cluster": "all_weather",
                "side": side,
                "decision_ts": opportunity["decision_ts"],
                "entry_ts": result["entry_ts"],
                "limit_price": str(limit),
                "signal_provenance": opportunity["signal_provenance"],
                "entry_price": result["entry_price"],
                "available_quantity": None,
                "entry_price_provenance": result["entry_price_provenance"],
                "exit_ts": result["exit_ts"],
                "terminal_cash_per_contract": result["terminal_cash_per_contract"],
                "terminal_kind": "settlement" if result["exit_ts"] is not None else None,
                "outcome_available_ts": result["exit_ts"],
                "terminal_provenance": result["terminal_provenance"],
                "prediction_record_id": prediction_artifact["record_id"],
                "prediction_body_sha256": prediction_artifact["body_sha256"],
            }
        )
        statuses.append({"opportunity_id": identity, "trade_id": trade_id, "reason": result["reason"]})
    return {
        "orders": orders,
        "statuses": statuses,
        "status_counts": dict(Counter(row["reason"] for row in statuses)),
        "availability_basis": "conditional_historical_replay_no_depth_or_actual_fill_claim",
    }
