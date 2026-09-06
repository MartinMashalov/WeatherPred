"""Audit every preliminary-bound violation, without refitting the E014 model."""

import hashlib
import json
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from weatherpred.archive import Archive, canonical
from weatherpred.intraday_bounds import partial_high
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    a = Archive()
    try:
        parent = a.json(a.latest("experiment_report", "E014_intraday_bounds"))
        registration = a.db.execute(
            "SELECT * FROM records WHERE id=?", (parent["protocol_record_id"],)
        ).fetchone()
        config = a.json(registration)["config"]
        cutoff = parse_time(registration["available_at"])
        label_source = a.latest("research_dataset", "E003_nws_labels", cutoff)
        labels = {}
        for line in a.body(label_source).decode().splitlines():
            r = json.loads(line)
            if not config["development_start"] <= r["day"] < config["validation_end_exclusive"]:
                raise ValueError("Unregistered date")
            labels[r["series"], r["day"]] = r
        products = json.loads(Path("config/e003_nws_labels.json").read_text())["products"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        violations, errors, coverage = [], [], defaultdict(set)
        for series, product in products.items():
            raw = a.latest("e003_nws_zip", product, cutoff)
            with ZipFile(BytesIO(a.body(raw))) as zipped:
                for index, entry in enumerate(zipped.infolist()):
                    body = zipped.read(entry)
                    try:
                        r = partial_high(
                            body.decode("ascii"),
                            entry.filename,
                            product,
                            config["development_start"],
                            config["validation_end_exclusive"],
                            windows[series]["standard_utc_offset_hours"],
                        )
                        if r is None or (series, r["day"]) not in labels:
                            continue
                        label = labels[series, r["day"]]
                        split = "train" if r["day"] < config["train_end_exclusive"] else "validation"
                        coverage[split].add(label["event"])
                        if r["maximum_so_far_f"] > label["exchange_value_f"]:
                            violations.append(
                                {
                                    "event": label["event"],
                                    "split": split,
                                    "preliminary_maximum_f": r["maximum_so_far_f"],
                                    "exchange_maximum_f": label["exchange_value_f"],
                                    "difference_f": r["maximum_so_far_f"] - label["exchange_value_f"],
                                    "issued_at": r["issued_at"],
                                    "raw_record_id": raw["id"],
                                    "entry_index": index,
                                    "raw_sha256": hashlib.sha256(body).hexdigest(),
                                }
                            )
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append({"source": raw["id"], "index": index, "error": str(exc)})
        result = {
            "generated_at": iso(utcnow()),
            "parent_protocol_record_id": parent["protocol_record_id"],
            "model_record_id": parent["model_record_id"],
            "event_coverage": {k: len(v) for k, v in coverage.items()},
            "violations": violations,
            "parse_errors": errors,
            "violation_events_by_split": dict(
                Counter(split for split, event in {(r["split"], r["event"]) for r in violations})
            ),
            "maximum_discrepancy_f": max((r["difference_f"] for r in violations), default=0),
            "refits": 0,
            "network_requests": 0,
            "holdout_accessed": False,
            "profitability_proven": False,
        }
        a.append("experiment_report", "E014_source_diagnostics", utcnow(), {}, canonical(result).encode())
        Path("reports/E014_source_diagnostics.json").write_text(json.dumps(result, indent=2))
        print(
            json.dumps({k: v for k, v in result.items() if k not in ("violations", "parse_errors")}, indent=2)
        )
    finally:
        a.close()


if __name__ == "__main__":
    main()
