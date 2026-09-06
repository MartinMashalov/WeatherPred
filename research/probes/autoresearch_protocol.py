"""Finite research scheduling with immutable inputs and explicit evidence gates.

This is a local trial ledger, not a sandbox, process supervisor or trading bot.
The caller runs a fixed evaluator and must enforce the deadline returned by start.
No result from this module authorizes live trading or opens a final holdout.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("All timestamps must include a timezone")
    return parsed.timestamp()


def now():
    return datetime.now(UTC).isoformat()


def source_hashes(paths):
    return {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def check_sources(plan):
    expected = dict(plan["evaluator_sources"])
    expected[plan["input_manifest_path"]] = plan["input_manifest_sha256"]
    for candidate in plan["candidates"]:
        for path, sha in candidate["sources"].items():
            if path in expected and expected[path] != sha:
                raise ValueError("Conflicting source fingerprints")
            expected[path] = sha
    if source_hashes(expected) != expected:
        raise ValueError("A frozen evaluator or candidate source changed")


def validate_plan(plan, registered_at):
    if plan["phase"] not in {"development", "prospective"}:
        raise ValueError("Only development or prospective evaluation is allowed")
    start, cutoff, end = map(timestamp, (plan["training_start"], plan["fit_cutoff"], plan["evaluation_end"]))
    evaluation_start = timestamp(plan["evaluation_start"])
    registration = timestamp(registered_at)
    if not start < cutoff <= evaluation_start < end or cutoff > registration:
        raise ValueError("Training and evaluation chronology is invalid")
    if plan["phase"] == "prospective" and registration >= evaluation_start:
        raise ValueError("Prospective registration must precede evaluation")
    for interval in plan["sealed_periods"]:
        a, b = timestamp(interval["start"]), timestamp(interval["end"])
        if a >= b or max(start, a) < min(cutoff, b) or max(evaluation_start, a) < min(end, b):
            raise ValueError("Training or evaluation touches a sealed period")
    candidates = plan["candidates"]
    if not candidates or len(candidates) > plan["max_trials"]:
        raise ValueError("Candidate count exceeds the registered trial budget")
    if len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate identity")
    identities = [digest({k: v for k, v in c.items() if k != "id"}) for c in candidates]
    if len(set(identities)) != len(identities):
        raise ValueError("Renaming an identical candidate does not create a new experiment")
    if candidates[0]["id"] != plan["baseline_id"]:
        raise ValueError("The first candidate must be the registered baseline")
    if (
        not math.isfinite(plan["max_trial_seconds"])
        or not math.isfinite(plan["max_total_seconds"])
        or not 0 < plan["max_trial_seconds"] <= plan["max_total_seconds"]
        or not isinstance(plan["minimum_evaluation_days"], int)
        or plan["minimum_evaluation_days"] < 1
        or plan["prior_comparisons"] < 0
        or plan["metric"]["direction"] not in {"min", "max"}
        or not math.isfinite(plan["metric"]["minimum_improvement"])
        or plan["metric"]["minimum_improvement"] < 0
    ):
        raise ValueError("Invalid resource, metric or evidence budget")
    if len(plan["input_manifest_sha256"]) != 64:
        raise ValueError("A frozen input-manifest fingerprint is required")
    if not plan["evaluator_sources"] or any(not c["sources"] for c in candidates):
        raise ValueError("Evaluator and candidate sources must be pinned")
    check_sources(plan)


def check_information_boundaries(plan, report, *, registered_at=None, assessed_at=None):
    """Check evaluator-supplied lineage; source-byte verification remains separate."""
    cutoff = timestamp(plan["fit_cutoff"])
    assessed = timestamp(assessed_at or now())
    if not report["training_rows"] or not report["evaluation_rows"]:
        raise ValueError("Empty training or evaluation evidence")
    if report["input_manifest_sha256"] != plan["input_manifest_sha256"]:
        raise ValueError("Evaluator used an unregistered input manifest")
    days = set()
    for phase in ("training", "evaluation"):
        identities = set()
        for row in report[f"{phase}_rows"]:
            identity = (row["event_id"], row["decision_at"])
            if identity in identities:
                raise ValueError("Duplicate event/decision observation")
            identities.add(identity)
            decision = timestamp(row["decision_at"])
            target = timestamp(row["target_at"])
            feature_receipt = timestamp(row["feature_received_at"])
            feature_observation = timestamp(row["feature_observed_at"])
            label_receipt = timestamp(row["label_received_at"])
            if not feature_observation <= feature_receipt <= decision < target <= label_receipt <= assessed:
                raise ValueError("Feature receipt or label chronology is invalid")
            if phase == "training":
                if not timestamp(plan["training_start"]) <= target < cutoff or label_receipt >= cutoff:
                    raise ValueError("A training outcome was unavailable at the fit cutoff")
            else:
                if not cutoff <= decision or not timestamp(plan["evaluation_start"]) <= target < timestamp(
                    plan["evaluation_end"]
                ):
                    raise ValueError("Evaluation observation falls outside the registered fold")
                if plan["phase"] == "prospective" and (
                    registered_at is None
                    or not timestamp(registered_at) <= timestamp(row["prediction_committed_at"]) <= decision
                ):
                    raise ValueError("Prospective prediction was not committed within its registered window")
                days.add(datetime.fromtimestamp(target, UTC).date().isoformat())
    return len(days)


def evaluation_identity(report):
    return digest(
        sorted((r["event_id"], r["decision_at"], r["target_at"]) for r in report["evaluation_rows"])
    )


class TrialLedger:
    """A locked hash chain; failed and interrupted attempts retain their trial slot."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self):
        rows, previous = [], "0" * 64
        if not self.path.exists():
            return rows
        for line in self.path.read_text().splitlines():
            row = json.loads(line)
            payload = {k: v for k, v in row.items() if k != "sha256"}
            if (
                row["sequence"] != len(rows)
                or row["previous"] != previous
                or digest(payload) != row["sha256"]
            ):
                raise ValueError("Trial ledger hash chain mismatch")
            rows.append(row)
            previous = row["sha256"]
        return rows

    def append(self, rows, kind, payload):
        row = {
            "sequence": len(rows),
            "previous": rows[-1]["sha256"] if rows else "0" * 64,
            "at": now(),
            "kind": kind,
            "payload": payload,
        }
        row["sha256"] = digest(row)
        with self.path.open("a") as target:
            target.write(canonical(row) + "\n")
            target.flush()
            os.fsync(target.fileno())
        return row

    def register(self, plan):
        with self.locked():
            if self.path.exists():
                raise ValueError("A registration cannot be overwritten")
            registered_at = now()
            validate_plan(plan, registered_at)
            return self.append(
                [],
                "registration",
                {
                    "plan": plan,
                    "registered_at": registered_at,
                    "protocol_sources": source_hashes([__file__]),
                },
            )

    def status(self):
        rows = self.read()
        if not rows or rows[0]["kind"] != "registration":
            raise ValueError("Missing registration")
        protocol = rows[0]["payload"]["protocol_sources"]
        if source_hashes(protocol) != protocol:
            raise ValueError("The registered trial protocol changed")
        plan, starts, terminal = rows[0]["payload"]["plan"], {}, {}
        for row in rows[1:]:
            candidate = row["payload"]["candidate_id"]
            if row["kind"] == "started" and candidate not in starts:
                starts[candidate] = row
            elif row["kind"] == "finished" and candidate in starts and candidate not in terminal:
                terminal[candidate] = row
            else:
                raise ValueError("Invalid trial state transition")
        spent = sum(r["payload"]["elapsed_seconds"] for r in terminal.values())
        running = [c for c in starts if c not in terminal]
        reserved = len(running) * plan["max_trial_seconds"]
        return {
            "plan": plan,
            "starts": starts,
            "terminal": terminal,
            "running": running,
            "spent_seconds": spent,
            "reserved_seconds": reserved,
            "remaining_seconds": max(0, plan["max_total_seconds"] - spent - reserved),
            "planned_comparisons": len(plan["candidates"]),
            "attempted_trials": len(starts),
            "prior_comparisons": plan["prior_comparisons"],
            "promotion_eligible": False,
        }

    def start(self, candidate_id):
        with self.locked():
            state = self.status()
            plan = state["plan"]
            check_sources(plan)
            candidates = {c["id"] for c in plan["candidates"]}
            if candidate_id not in candidates or candidate_id in state["starts"]:
                raise ValueError("Unregistered or already attempted candidate")
            if state["running"]:
                raise ValueError("A trial is still running; inspect its actual process before recovery")
            baseline = state["terminal"].get(plan["baseline_id"], {}).get("payload", {})
            if candidate_id != plan["baseline_id"] and baseline.get("status") != "scored":
                raise ValueError("The fixed baseline must first complete successfully")
            if state["remaining_seconds"] < plan["max_trial_seconds"]:
                raise ValueError("Insufficient remaining resource budget")
            start = now()
            deadline = datetime.fromtimestamp(timestamp(start) + plan["max_trial_seconds"], UTC).isoformat()
            return self.append(
                self.read(),
                "started",
                {"candidate_id": candidate_id, "started_at": start, "deadline": deadline},
            )

    def finish(self, candidate_id, *, report_path=None, failure=None, artifacts=None):
        with self.locked():
            state = self.status()
            if candidate_id not in state["running"]:
                raise ValueError("Only a running trial can finish")
            if (report_path is None) == (failure is None):
                raise ValueError("Provide exactly one result artifact or failure reason")
            plan = state["plan"]
            started = state["starts"][candidate_id]["payload"]
            elapsed = timestamp(now()) - timestamp(started["started_at"])
            if elapsed < 0:
                raise ValueError("System clock moved backwards; cannot account for budget")
            result = {
                "candidate_id": candidate_id,
                "elapsed_seconds": elapsed,
                "status": "failed" if failure else "scored",
                "failure": failure,
                "metric": None,
                "promotion_eligible": False,
            }
            try:
                if artifacts:
                    result["artifacts"] = {
                        name: {
                            "path": str(Path(path).resolve()),
                            "sha256": source_hashes([path])[str(Path(path).resolve())],
                        }
                        for name, path in artifacts.items()
                    }
                raw = None
                if report_path is not None:
                    raw = Path(report_path).read_bytes()
                    result["report_sha256"] = hashlib.sha256(raw).hexdigest()
                    result["report_path"] = str(Path(report_path).resolve())
                check_sources(plan)
                if elapsed > plan["max_trial_seconds"]:
                    raise ValueError("Trial exceeded its registered wall-clock budget")
                if raw is not None:
                    report = json.loads(raw)
                    days = check_information_boundaries(
                        plan, report, registered_at=self.read()[0]["payload"]["registered_at"]
                    )
                    if days < plan["minimum_evaluation_days"]:
                        raise ValueError("Insufficient distinct evaluation days")
                    identity = evaluation_identity(report)
                    if (
                        candidate_id != plan["baseline_id"]
                        and identity
                        != state["terminal"][plan["baseline_id"]]["payload"]["evaluation_identity"]
                    ):
                        raise ValueError("Candidate evaluation panel differs from the fixed baseline")
                    metric = report["metrics"][plan["metric"]["name"]]
                    if (
                        isinstance(metric, bool)
                        or not isinstance(metric, (int, float))
                        or not math.isfinite(metric)
                    ):
                        raise ValueError("Metric must be finite")
                    result.update(metric=metric, evaluation_days=days, evaluation_identity=identity)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                result.update(status="invalid", failure=str(exc), metric=None)
            return self.append(self.read(), "finished", result)

    def ranking(self):
        state = self.status()
        plan = state["plan"]
        scored = [r["payload"] for r in state["terminal"].values() if r["payload"]["status"] == "scored"]
        sign = 1 if plan["metric"]["direction"] == "min" else -1
        scored.sort(key=lambda r: (sign * r["metric"], r["candidate_id"]))
        baseline = next((r for r in scored if r["candidate_id"] == plan["baseline_id"]), None)
        winner = scored[0] if scored else None
        improvement = sign * (baseline["metric"] - winner["metric"]) if baseline and winner else None
        return {
            "best_research_candidate": winner["candidate_id"] if winner else None,
            "improvement_over_baseline": improvement,
            "keep_for_research": improvement is not None
            and improvement > plan["metric"]["minimum_improvement"],
            "evaluation_kind": plan["phase"],
            "planned_comparisons": state["planned_comparisons"],
            "attempted_trials": state["attempted_trials"],
            "failed_trials": sum(r["payload"]["status"] != "scored" for r in state["terminal"].values()),
            "promotion_eligible": False,
            "limitation": "Selection among trials is exploratory; independent future execution evidence is required.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    register = sub.add_parser("register")
    register.add_argument("plan", type=Path)
    start = sub.add_parser("start")
    start.add_argument("candidate_id")
    finish = sub.add_parser("finish")
    finish.add_argument("candidate_id")
    finish.add_argument("--report", type=Path)
    finish.add_argument("--failure")
    sub.add_parser("status")
    sub.add_parser("ranking")
    args = parser.parse_args()
    ledger = TrialLedger(args.ledger)
    if args.action == "register":
        result = ledger.register(json.loads(args.plan.read_text()))
    elif args.action == "start":
        result = ledger.start(args.candidate_id)
    elif args.action == "finish":
        result = ledger.finish(args.candidate_id, report_path=args.report, failure=args.failure)
    else:
        result = getattr(ledger, args.action)()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
