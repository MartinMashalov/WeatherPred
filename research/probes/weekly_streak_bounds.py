"""Replay a received TWC week and fresh books; conditional discovery, not orders."""

import argparse
import itertools
import json
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book, purchase_cost
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal


def longest(values):
    run = best = 0
    for value in values:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def possible_streaks(flags):
    unknown = [i for i, value in enumerate(flags) if value is None]
    values = []
    for possibility in itertools.product((False, True), repeat=len(unknown)):
        row = list(flags)
        for i, value in zip(unknown, possibility, strict=True):
            row[i] = value
        values.append(longest(row))
    return sorted(set(values))


def station_days(station, dates, received, threshold, margin):
    """Past missing/unfinished days stay unknown; current/future days are unknown.

    The margin perturbs the average in either direction before whole-degree
    rounding. It is sensitivity analysis, not a bound on future source revisions.
    """
    local_today = received.astimezone(ZoneInfo(station["timezone"])).date().isoformat()
    rows = []
    for day in dates:
        observations = [o for o in station["observations"] if o["localDate"] == day]
        if len({o["localHour"] for o in observations}) != len(observations):
            raise ValueError("Duplicated local hours; DST needs an explicit rule")
        values = [
            D(str(o["tempF"]))
            for o in observations
            if o["status"] == "settled" and parse_time(o["reportTimeUTC"]) <= received
        ]
        row = {"date": day, "settled_hours": len(values), "qualifies": None}
        if day < local_today and len(values) >= 18:
            avg = sum(values) / len(values)
            low = (avg - margin).quantize(D(1), rounding=ROUND_HALF_UP)
            high = (avg + margin).quantize(D(1), rounding=ROUND_HALF_UP)
            if low > threshold:
                row["qualifies"] = True
            elif high <= threshold:
                row["qualifies"] = False
            row.update(
                mean_f=str(avg),
                rounded_mean_f=str(avg.quantize(D(1), rounding=ROUND_HALF_UP)),
                published_daily_average_f=station["dailyAverages"].get(day, {}).get("avgF"),
            )
        rows.append(row)
    return rows


def run(archive, source_id, expansion_path, output):
    source = archive.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
    weather = archive.json(source)
    received = parse_time(source["available_at"])
    expansion = json.loads(Path(expansion_path).read_text())
    stations = {s["icaoId"]: s for s in weather["stations"]}
    rows, errors, refs = [], [], {source_id, expansion["record_id"]}
    for market in expansion["markets"]:
        if not market["ticker"].startswith("KXAVGTK"):
            continue
        try:
            rule = re.fullmatch(
                r"If there is at least (\d+) or more consecutive days within (.+?) through (.+?) on which the daily average temperature at (K[A-Z0-9]+) in (.+?) is strictly greater than (\d+(?:\.\d+)?) degrees Fahrenheit, then the market resolves to Yes\.",
                market["rules_primary"],
            )
            if (
                not rule
                or market["strike_type"] != "greater_or_equal"
                or D(rule[1]) != D(str(market["floor_strike"]))
            ):
                raise ValueError("Unsupported heat-streak rule")
            if (
                "A day with fewer than 18 reported hourly values does not satisfy the condition and breaks a streak."
                not in market["rules_secondary"]
            ):
                raise ValueError("Missing explicit hour-count convention")
            if "rounded to the nearest whole degree" not in market["rules_secondary"]:
                raise ValueError("Missing explicit rounding convention")
            if [
                datetime.strptime(rule[k], "%B %d, %Y").replace(tzinfo=UTC).date().isoformat() for k in (2, 3)
            ] != [
                weather["weekStart"],
                weather["weekEnd"],
            ]:
                raise ValueError("Weather and market weeks differ")
            station = stations[rule[4]]
            threshold = D(rule[6])
            if "book" not in market:
                raise ValueError("No verified fresh book")
            book_id = market["book"]["record_id"]
            raw_book = archive.db.execute("SELECT * FROM records WHERE id=?", (book_id,)).fetchone()
            book_time = parse_time(raw_book["available_at"])
            if received > book_time:
                raise ValueError("Weather information arrived after the book")
            data = archive.json(raw_book)
            book = parse_book(next(b for b in data["orderbooks"] if b["ticker"] == market["ticker"]))
            series = archive.latest("expansion_series", market["ticker"].split("-")[0], book_time)
            fee = archive.latest("expansion_fees", market["event_ticker"], book_time)
            if archive.json(fee).get("cursor"):
                raise ValueError("Fee history spans multiple pages; replay must be extended")
            schedule = resolve_current_fee(
                archive.json(series)["series"], archive.json(fee)["event_fee_changes"], book_time
            )
            refs.update((book_id, series["id"], fee["id"]))
            for margin in (D(0), D("0.5"), D(1)):
                days = station_days(station, weather["dates"], received, threshold, margin)
                possibilities = possible_streaks([d["qualifies"] for d in days])
                payouts = [int(x >= int(rule[1])) for x in possibilities]
                row = {
                    "ticker": market["ticker"],
                    "station": rule[4],
                    "mean_sensitivity_f": str(margin),
                    "days": days,
                    "possible_longest_streaks": possibilities,
                    "conditional_yes_payout_min": min(payouts),
                    "conditional_yes_payout_max": max(payouts),
                    "book_record_id": book_id,
                    "scenarios": [],
                }
                if min(payouts) == max(payouts):
                    side = "yes" if payouts[0] else "no"
                    row["conditional_known_side"] = side
                    for qty in (1, 5, 10):
                        for label, retained, slip in (
                            ("displayed", "1", "0"),
                            ("half_depth_1c", ".5", ".01"),
                            ("quarter_depth_2c", ".25", ".02"),
                        ):
                            cost = purchase_cost(book, side, qty, schedule, retained, slip)
                            scenario = {
                                "name": label,
                                "quantity": qty,
                                "full_size_available": cost is not None,
                            }
                            if cost is not None:
                                scenario.update(
                                    total_cost=str(cost.total),
                                    fees=str(cost.fees),
                                    conditional_profit=str(D(qty) - cost.total),
                                )
                            row["scenarios"].append(scenario)
                rows.append(row)
        except (KeyError, ValueError, TypeError, StopIteration) as exc:
            errors.append({"ticker": market["ticker"], "error": str(exc)})
    result = {
        "generated_at": iso(utcnow()),
        "weather_received_at": iso(received),
        "raw_source_ids": sorted(refs),
        "rows": rows,
        "errors": errors,
        "source_observations": weather["totalObservations"],
        "source_stations": len(stations),
        "limitations": [
            "All payout conclusions are conditional on the currently published completed-day data remaining materially unchanged at contractual expiration.",
            "The source labels hourly rows settled, but contract terms use the version at expiration and ignore later revisions; those are different finality rules.",
            "Data were retrieved now. Historical receipt times and subsequent fills are not known, and no order was submitted.",
            "The degree perturbations are robustness diagnostics, not probabilities or a guarantee against corrections.",
        ],
        "profitability_proven": False,
        "orders_submitted": 0,
    }
    rec = archive.append("discovery_report", "weekly_streak_bounds", utcnow(), {}, canonical(result).encode())
    Path(output).write_text(json.dumps({"record_id": rec, **result}, indent=2))
    positive = [
        r
        for r in rows
        if r["mean_sensitivity_f"] == "1"
        and any(
            s.get("name") == "half_depth_1c" and D(s.get("conditional_profit", "-1")) > 0
            for s in r["scenarios"]
        )
    ]
    return {
        "record_id": rec,
        "contracts": len({r["ticker"] for r in rows}),
        "rows": len(rows),
        "errors": errors,
        "positive_contracts_under_1f_mean_perturbation_and_half_depth_1c": len(positive),
        "positive_examples": [
            {k: r[k] for k in ("ticker", "conditional_known_side", "possible_longest_streaks", "scenarios")}
            for r in positive
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, default=78077)
    parser.add_argument("--expansion", default="reports/market_expansion.json")
    parser.add_argument("--output", default="reports/weekly_streak_bounds.json")
    args = parser.parse_args()
    arc = Archive()
    try:
        print(json.dumps(run(arc, args.source_id, args.expansion, args.output), indent=2))
    finally:
        arc.close()
