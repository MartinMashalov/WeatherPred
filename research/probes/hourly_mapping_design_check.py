"""Offline design checks only: no collection, registration, labels or models.

Run with a Python interpreter that already provides jsonschema. This optional
design tool is separate from the project's base runtime and ordinary pytest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from datetime import datetime, timedelta
from fractions import Fraction as F
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
UTC = ZoneInfo("UTC")
DESIGN = ROOT / "config/kaus_hourly_mapping_design.json"
SCHEMA = ROOT / "config/kaus_hourly_mapping.schema.json"
DOCUMENT = ROOT / "research/HOURLY_SETTLEMENT_MAPPING.md"
REPORT = ROOT / "evidence/hourly_settlement_mapping_checks.json"


def timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def cross_fields(design: dict) -> None:
    cases = design["cases"]
    expected = [datetime(2026, 9, d, h, tzinfo=UTC) for d in (8, 9) for h in (0, 6, 12, 18)]
    assert [timestamp(c["target_utc"]) for c in cases] == expected
    assert len({c["case_id"] for c in cases}) == 8
    for case, target in zip(cases, expected, strict=True):
        local = target.astimezone(ZoneInfo("America/Chicago"))
        eastern = target.astimezone(ZoneInfo("America/New_York"))
        assert timestamp(case["decision_utc"]) == target - timedelta(hours=1)
        assert case["case_id"] == "KAUS:" + target.strftime("%Y%m%dT%H%M%SZ")
        assert case["station_local"] == local.isoformat()
        assert case["display_eastern"] == eastern.isoformat()
        assert case["station_local_date"] == local.date().isoformat()
        assert case["station_local_hour"] == local.hour
        assert case["station_local_fold"] == local.fold == 0
        assert case["expected_event_ticker"] == "KXTEMPAUSH-" + eastern.strftime("%y%b%d%H").upper()
        monday = local.date() - timedelta(days=local.weekday())
        assert case["requested_week_start"] == monday.isoformat()
    assert timestamp(design["registration_deadline_utc"]) < timestamp(cases[0]["decision_utc"])
    poll = design["polling_proposal"]
    offsets = poll["paired_twc_and_event_seconds_from_target"]
    assert offsets == sorted(set(offsets))
    nominal = len(cases) * (len(poll["kalshi_listing_seconds_from_target"]) + 2 * len(offsets))
    assert nominal == poll["nominal_requests"] == 264
    assert nominal * poll["maximum_attempts_per_request"] == poll["maximum_attempts_total"] == 792
    assert timestamp(poll["hard_stop_utc"]) == expected[-1] + timedelta(days=1, minutes=10)
    assert design["success_gates"]["latest_completion_utc"] == poll["hard_stop_utc"]
    for filename, digest in design["sources"]["source_file_sha256"].items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest, filename


def quantile_bounds(alphas: list[F], knots: list[F], x: F) -> tuple[F, F, F, F]:
    assert len(alphas) == len(knots) and len(alphas) > 0
    assert alphas == sorted(set(alphas)) and 0 < alphas[0] < alphas[-1] < 1
    assert knots == sorted(knots)
    lf = max([F(0)] + [a for a, q in zip(alphas, knots, strict=True) if q <= x])
    uf = min([F(1)] + [a for a, q in zip(alphas, knots, strict=True) if q > x])
    lg = max([F(0)] + [a for a, q in zip(alphas, knots, strict=True) if q < x])
    ug = min([F(1)] + [a for a, q in zip(alphas, knots, strict=True) if q >= x])
    return lf, uf, lg, ug


def synthetic_probability_checks() -> tuple[int, int]:
    alphas = [F(1, 100), F(1, 10), F(1, 2), F(9, 10), F(99, 100)]
    support = [F(x) for x in (-2, -1, 0, 1, 2)]
    rng = random.Random(20260906)
    weights = [[int(i == j) for i in range(5)] for j in range(5)]
    weights += [[rng.randint(0, 8) for _ in support] for _ in range(50)]
    grid = [F(i, 2) for i in range(-6, 7)]
    threshold_count = interval_count = 0
    for row in weights:
        assert sum(row) > 0
        probs = [F(w, sum(row)) for w in row]

        def cdf(x: F, strict: bool = False, probs: list[F] = probs) -> F:
            return sum(
                (p for v, p in zip(support, probs, strict=True) if v < x or (v == x and not strict)), F(0)
            )

        knots = [next(v for v in support if cdf(v) >= a) for a in alphas]
        for x in grid:
            lf, uf, lg, ug = quantile_bounds(alphas, knots, x)
            assert lf <= cdf(x) <= uf
            assert lg <= cdf(x, strict=True) <= ug
            assert 1 - uf <= 1 - cdf(x) <= 1 - lf
            threshold_count += 1
        for lower in grid:
            for upper in grid:
                if lower > upper:
                    continue
                lf, uf, _, _ = quantile_bounds(alphas, knots, upper)
                _, _, lg, ug = quantile_bounds(alphas, knots, lower)
                actual = cdf(upper) - cdf(lower, strict=True)
                assert max(F(0), lf - ug) <= actual <= min(F(1), uf - lg)
                interval_count += 1
    a = [F(1, 10), F(1, 2), F(9, 10)]
    q = [F(90), F(95), F(100)]
    lf, uf, lg, ug = quantile_bounds(a, q, F(95))
    assert (1 - uf, 1 - lf) == (F(1, 10), F(1, 2))
    lf2, uf2, _, _ = quantile_bounds(a, q, F(100))
    assert (max(F(0), lf2 - ug), min(F(1), uf2 - lg)) == (F(2, 5), F(9, 10))
    try:
        quantile_bounds(a, [F(95), F(90), F(100)], F(95))
    except AssertionError:
        pass
    else:
        raise AssertionError("Crossing quantiles did not fail")
    return threshold_count, interval_count


def main() -> None:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError

    design, schema = json.loads(DESIGN.read_text()), json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(design)
    cross_fields(design)
    checks = [
        "Draft 2020-12 schema valid; draft configuration validates with date-time formats.",
        "8 exact UTC targets, decisions, Austin clocks, Eastern tickers and weekly keys verified.",
        "264 nominal mapping requests; 792 maximum attempts; final revision window and hard stop verified.",
        f"{len(design['sources']['source_file_sha256'])} archived schema/document source hashes verified.",
    ]
    mutations = [
        ("orders", lambda x: x["scope"].update(trading_orders=1)),
        ("forecast", lambda x: x["scope"].update(model_forecasts=1)),
        ("semantic overclaim", lambda x: x["success_gates"].update(semantics_verified=True)),
        ("ninth case", lambda x: x["cases"].append(copy.deepcopy(x["cases"][0]))),
        ("unsafe retry budget", lambda x: x["polling_proposal"].update(maximum_attempts_total=793)),
        ("extra source", lambda x: x["sources"].update(unregistered_url="https://example.invalid/")),
        ("bad source hash", lambda x: x["sources"].update(rule_pdf_sha256="bad")),
        ("swapped decision", lambda x: x["cases"][0].update(decision_utc=x["cases"][1]["decision_utc"])),
        ("wrong local hour", lambda x: x["cases"][0].update(station_local_hour=20)),
    ]
    for label, mutate in mutations:
        bad = copy.deepcopy(design)
        mutate(bad)
        try:
            validator.validate(bad)
            cross_fields(bad)
        except (ValidationError, AssertionError):
            pass
        else:
            raise AssertionError(f"Unsafe design accepted: {label}")
    checks.append(f"{len(mutations)} negative configuration cases rejected without weakening gates.")
    thresholds, intervals = synthetic_probability_checks()
    checks.append(
        f"55 synthetic discrete distributions: {thresholds} threshold and {intervals} inclusive-interval bounds verified exactly, including point masses/ties."
    )
    checks.append("Published synthetic examples verified; crossing quantiles rejected.")
    local_links = re.findall(r"\]\(([^):]+(?:/[^)]*)?)\)", DOCUMENT.read_text())
    link_count = 0
    for link in local_links:
        assert (DOCUMENT.parent / link).resolve().exists(), link
        link_count += 1
    checks.append(f"{link_count} local documentation links resolve.")
    files = [DESIGN, SCHEMA, DOCUMENT, Path(__file__)]
    for path in files:
        assert all(line.rstrip() == line for line in path.read_text().splitlines()), path
    checks.append("No trailing whitespace in the four new design/check files.")
    report = {
        "kind": "offline_design_checks_not_a_settlement_audit",
        "checked_at": datetime.now(UTC).isoformat(),
        "command": "python3 research/probes/hourly_mapping_design_check.py",
        "validator": "jsonschema Draft202012Validator with FormatChecker; pre-existing system Python package",
        "checks": checks,
        "new_network_requests": 0,
        "new_weather_or_outcome_values_read": 0,
        "registrations": 0,
        "forecasts": 0,
        "orders": 0,
        "artifact_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        },
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for check in checks:
        print(check)
    print("No acquisition, registration, forecasts, outcomes or orders.")


if __name__ == "__main__":
    main()
