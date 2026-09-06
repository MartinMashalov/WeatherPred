"""As-of NBM updates, retaining older forecasts where new cards omit early hours."""

from datetime import date, datetime, timedelta

from weatherpred.daily_forecasts import daily_features
from weatherpred.timeutil import parse_time


def updated_features(day, offset, station, sources, decision):
    eligible = []
    for source in sources:
        if source["day"] != day:
            raise ValueError("Updated forecast source day mismatch")
        card = source["cards"][station]
        runtime = parse_time(card["runtime"])
        stored = parse_time(source["object"]["last_modified"])
        if card["station"] != station or runtime.date().isoformat() != day or runtime > stored:
            raise ValueError("Invalid updated station, runtime or storage ordering")
        if runtime.hour not in (1, 7, 13):
            raise ValueError("Unregistered updated model cycle")
        if stored <= decision:
            eligible.append((runtime, source, card))
    eligible.sort(key=lambda item: item[0])
    if len({runtime for runtime, _, _ in eligible}) != len(eligible):
        raise ValueError("Duplicate updated forecast runtime")
    early = next((s for runtime, s, _ in eligible if runtime.hour == 1), None)
    if early is None:
        raise ValueError("Missing common early-cycle control")
    baseline = daily_features(day, offset, early["cards"][station], early["object"]["last_modified"])
    if decision < parse_time(baseline["source_period_start"]):
        raise ValueError("Decision precedes registered source day")
    selected = {}
    for runtime, source, card in eligible:
        by_time = {int(parse_time(r["valid_at"]).timestamp()): r for r in card["rows"]}
        if len(by_time) != len(card["rows"]):
            raise ValueError("Duplicate updated forecast valid time")
        for valid in baseline["grid_valid_times"]:
            row = by_time.get(valid)
            if row is not None and row["tmp"] is not None:
                selected[valid] = {
                    "tmp": row["tmp"],
                    "source_record_id": source["record_id"],
                    "runtime": card["runtime"],
                    "object_last_modified": source["object"]["last_modified"],
                }
    if set(selected) != set(baseline["grid_valid_times"]):
        raise ValueError("Updated feature grid lost an original valid time")
    proxy_date = date.fromisoformat(day) + timedelta(days=1)
    proxy = None
    for runtime, source, card in eligible:
        target = datetime.combine(proxy_date, datetime.min.time(), tzinfo=runtime.tzinfo)
        row = next((r for r in card["rows"] if parse_time(r["valid_at"]) == target), None)
        if row is not None and row["txn"] is not None and row["xnd"] is not None and row["xnd"] >= 0:
            proxy = {
                "txn_18h": float(row["txn"]),
                "xnd_18h": float(row["xnd"]),
                "source_record_id": source["record_id"],
                "runtime": card["runtime"],
                "object_last_modified": source["object"]["last_modified"],
                "version": card["version"],
            }
    if proxy is None:
        raise ValueError("No eligible updated extrema proxy")
    result = {
        **baseline,
        "grid_max": float(max(r["tmp"] for r in selected.values())),
        "txn_18h": proxy["txn_18h"],
        "xnd_18h": proxy["xnd_18h"],
        "runtime": proxy["runtime"],
        "object_last_modified": proxy["object_last_modified"],
        "version": proxy["version"],
        "grid_source_lineage": {str(k): v for k, v in selected.items()},
        "proxy_source_lineage": proxy,
        "eligible_source_record_ids": [source["record_id"] for _, source, _ in eligible],
        "decision_at": decision.isoformat(),
    }
    return result
