"""Append-only research trials; reported provenance gates are not a data sandbox.

The runner supplies permitted views and audits actual source lineage. This ledger
preserves real reported receipt clocks separately from conditional availability,
and never grants verified-history, profitability or trading promotion status.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
from bisect import bisect_right
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

HEX = re.compile(r"[0-9a-f]{64}\Z")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise TypeError("Times must be explicit timezone-aware ISO strings")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Naive time is forbidden")
    return result.astimezone(UTC)


def _now():
    return datetime.now(UTC)


def _hash(value):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise ValueError("Expected a lowercase SHA256 fingerprint")
    return value


def _finite_json(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)
    elif (
        value is None
        or isinstance(value, (str, bool, int))
        or isinstance(value, float)
        and math.isfinite(value)
    ):
        pass
    else:
        raise ValueError("Only finite JSON values are permitted")


def _pin_files(plan):
    pins = plan["pins"]
    if set(pins) != {"sources", "config", "dataset"} or not pins["sources"]:
        raise ValueError("Explicit source, config and dataset pins are required")
    result = dict(pins["sources"])
    for name in ("config", "dataset"):
        item = pins[name]
        if set(item) != {"path", "sha256"}:
            raise ValueError("Config/dataset pin requires exactly path and sha256")
        path, digest = item["path"], item["sha256"]
        if path in result and result[path] != digest:
            raise ValueError("Conflicting source/config/dataset fingerprints")
        result[path] = digest
    for path, digest in result.items():
        _hash(digest)
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError("Pins must name existing absolute file paths")
        if _sha(Path(path).read_bytes()) != digest:
            raise ValueError("Pinned source/config/dataset bytes changed")


def _validate_plan(plan, registered_at, check_files=True):
    _finite_json(plan)
    if (
        plan["schema_version"] != 1
        or not isinstance(plan["campaign_id"], str)
        or not plan["campaign_id"].strip()
    ):
        raise ValueError("Campaign requires a schema version and nonempty identity")
    if plan["phase"] not in ("development", "prospective") or plan["availability_mode"] not in (
        "conditional_historical",
        "receipt_verified",
    ):
        raise ValueError("Unknown development/prospective evidence mode")
    cutoffs = [_time(value) for value in plan["fit_cutoffs"]]
    start, end = _time(plan["evaluation_start"]), _time(plan["evaluation_end"])
    if (
        not cutoffs
        or cutoffs != sorted(set(cutoffs))
        or _time(plan["fit_cutoff"]) != cutoffs[0]
        or cutoffs[0] > start
        or start >= end
        or cutoffs[-1] >= end
    ):
        raise ValueError("Fixed fold cutoffs and evaluation window are inconsistent")
    if plan["phase"] == "prospective" and (
        plan["availability_mode"] != "receipt_verified" or registered_at >= start
    ):
        raise ValueError("Prospective trials require receipt mode and advance registration")
    candidates = plan["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("A bounded candidate list is required")
    ids = []
    for candidate in candidates:
        if (
            not isinstance(candidate["id"], str)
            or not candidate["id"].strip()
            or type(candidate["requires_fit"]) is not bool
        ):
            raise ValueError("Candidates require unique IDs and explicit requires_fit flags")
        ids.append(candidate["id"])
    if len(set(ids)) != len(ids) or type(plan["max_trials"]) is not int or not len(ids) <= plan["max_trials"]:
        raise ValueError("Duplicate candidate identity or insufficient fixed trial capacity")
    for key in ("max_trial_seconds", "max_total_seconds"):
        value = plan[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("Trial and total budgets must be finite and positive")
    if plan["max_trial_seconds"] > plan["max_total_seconds"]:
        raise ValueError("Trial budget exceeds campaign budget")
    if check_files:
        _pin_files(plan)


def _validate_predictions(value, candidate, plan, committed_at):
    _finite_json(value)
    if value["candidate_id"] != candidate["id"]:
        raise ValueError("Prediction artifact belongs to a different candidate")
    provenance = value["provenance"]
    if provenance["availability_mode"] != plan["availability_mode"]:
        raise ValueError("Prediction evidence mode changed")
    for name in ("model_sha256", "feature_view_sha256"):
        _hash(provenance[name])
    folds = provenance["folds"]
    if [item["fit_cutoff"] for item in folds] != plan["fit_cutoffs"]:
        raise ValueError("Every registered fit cutoff must appear exactly once in order")
    for fold in folds:
        cutoff = _time(fold["fit_cutoff"])
        _hash(fold["model_sha256"])
        if cutoff > committed_at:
            raise ValueError("A future fit is claimed completed")
        available, received = (
            fold["latest_training_label_available_at"],
            fold["latest_training_label_received_at"],
        )
        if candidate["requires_fit"]:
            _hash(fold["training_labels_sha256"])
            if (
                available is None
                or received is None
                or _time(available) >= cutoff
                or _time(received) > committed_at
            ):
                raise ValueError("Training labels are unavailable at their fold cutoff or actual commit")
            if plan["availability_mode"] == "receipt_verified" and _time(received) >= cutoff:
                raise ValueError("An actual training label receipt is after its fold cutoff")
        elif any(item is not None for item in (available, received, fold["training_labels_sha256"])):
            raise ValueError("A no-fit candidate must not claim fitted training labels")
    rows = value["predictions"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("The opportunity panel must retain predictions or abstentions")
    cutoffs = [_time(item) for item in plan["fit_cutoffs"]]
    seen = set()
    for row in rows:
        identity = row["opportunity_id"]
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Duplicate or empty opportunity identity")
        seen.add(identity)
        decision = _time(row["decision_at"])
        if not _time(plan["evaluation_start"]) <= decision < _time(plan["evaluation_end"]):
            raise ValueError("Prediction decision is outside the registered evaluation window")
        fold = bisect_right(cutoffs, decision) - 1
        if fold < 0 or _time(row["fold_fit_cutoff"]) != cutoffs[fold]:
            raise ValueError("Prediction uses the wrong chronological fitted model")
        if _time(row["feature_available_at"]) > decision or _time(row["feature_received_at"]) > committed_at:
            raise ValueError("Feature availability or actual receipt is after its permitted use")
        if plan["availability_mode"] == "receipt_verified" and _time(row["feature_received_at"]) > decision:
            raise ValueError("Actual feature receipt is after decision")
        if plan["phase"] == "prospective" and committed_at > decision:
            raise ValueError("Prospective predictions must be committed before their decisions")
    return len(rows)


class TrialRejected(ValueError):
    """An invalid commit/result has already been preserved as a terminal failure."""


class CampaignLedger:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.artifacts = self.path.with_suffix(self.path.suffix + ".artifacts")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self, check_pins=True, check_artifacts=True):
        if not self.path.exists():
            raise ValueError("Campaign is not registered")
        rows, previous, last_at = [], "0" * 64, None
        states = {}
        for line in self.path.read_text().splitlines():
            row = json.loads(line)
            payload = {key: value for key, value in row.items() if key != "sha256"}
            if (
                row["sequence"] != len(rows)
                or row["previous_sha256"] != previous
                or _sha(_json(payload).encode()) != row["sha256"]
            ):
                raise ValueError("Campaign hash chain changed")
            at = _time(row["at"])
            if last_at is not None and at < last_at:
                raise ValueError("Campaign timestamps moved backwards")
            if not rows:
                if row["kind"] != "registration":
                    raise ValueError("First campaign entry must be registration")
                plan = row["payload"]["plan"]
                _validate_plan(plan, at, check_files=check_pins)
                module = row["payload"]["ledger_source"]
                if check_pins and _sha(Path(module["path"]).read_bytes()) != module["sha256"]:
                    raise ValueError("Registered ledger implementation changed")
                allowed = {candidate["id"] for candidate in plan["candidates"]}
            else:
                identifier = row["payload"]["trial_id"]
                if identifier not in allowed:
                    raise ValueError("Unregistered trial entered the campaign")
                prior = states.get(identifier)
                kind = row["kind"]
                if kind == "trial_start" and prior is None:
                    if _time(row["payload"]["deadline"]) != at + timedelta(seconds=plan["max_trial_seconds"]):
                        raise ValueError("Stored trial deadline changed")
                    spent = sum(
                        s["terminal"]["payload"]["elapsed_seconds"] for s in states.values() if s["terminal"]
                    )
                    reserved = sum(not s["terminal"] for s in states.values()) * plan["max_trial_seconds"]
                    if (
                        len(states) >= plan["max_trials"]
                        or spent + reserved + plan["max_trial_seconds"] > plan["max_total_seconds"]
                    ):
                        raise ValueError("Stored trial start exceeded the registered budget")
                    states[identifier] = {"start": row, "commit": None, "terminal": None}
                elif (
                    kind == "predictions_committed"
                    and prior
                    and not prior["commit"]
                    and not prior["terminal"]
                ):
                    evidence = row["payload"]
                    if check_artifacts:
                        body = Path(evidence["artifact_path"]).read_bytes()
                        if _sha(body) != evidence["prediction_sha256"]:
                            raise ValueError("Committed prediction artifact changed")
                        candidate = next(c for c in plan["candidates"] if c["id"] == identifier)
                        value = json.loads(body)
                        count = _validate_predictions(value, candidate, plan, at)
                        if (
                            count != evidence["opportunities"]
                            or evidence["provenance"] != value["provenance"]
                        ):
                            raise ValueError("Committed prediction census/provenance changed")
                    prior["commit"] = row
                elif kind in ("result", "failure") and prior and not prior["terminal"]:
                    if kind == "result" and (
                        not prior["commit"]
                        or row["payload"]["result"]["prediction_sha256"]
                        != prior["commit"]["payload"]["prediction_sha256"]
                    ):
                        raise ValueError("Result requires its prior immutable prediction commitment")
                    elapsed = (at - _time(prior["start"]["at"])).total_seconds()
                    if row["payload"]["elapsed_seconds"] != elapsed or elapsed < 0:
                        raise ValueError("Trial resource accounting changed")
                    if kind == "result":
                        result = row["payload"]["result"]
                        _finite_json(result)
                        if row["payload"]["result_sha256"] != _sha(_json(result).encode()) or at > _time(
                            prior["start"]["payload"]["deadline"]
                        ):
                            raise ValueError("Stored result hash or deadline changed")
                    prior["terminal"] = row
                else:
                    raise ValueError("Duplicate trial or invalid campaign state transition")
            rows.append(row)
            previous, last_at = row["sha256"], at
        if not rows:
            raise ValueError("Empty campaign ledger")
        return rows, states

    def _append(self, rows, kind, payload, at=None):
        at = at or _now()
        if rows and at < _time(rows[-1]["at"]):
            raise ValueError("Clock moved backwards; cannot append campaign evidence")
        row = {
            "sequence": len(rows),
            "previous_sha256": rows[-1]["sha256"] if rows else "0" * 64,
            "at": at.isoformat(),
            "kind": kind,
            "payload": payload,
        }
        row["sha256"] = _sha(_json(row).encode())
        with self.path.open("a") as stream:
            stream.write(_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row

    def register(self, plan):
        with self._locked():
            if self.path.exists():
                raise ValueError("Campaign already registered; no overwrite")
            at = _now()
            _validate_plan(plan, at)
            source = Path(__file__).resolve()
            return self._append(
                [],
                "registration",
                {
                    "plan": plan,
                    "ledger_source": {"path": str(source), "sha256": _sha(source.read_bytes())},
                    "source_lineage_independently_verified": False,
                    "os_sandbox": False,
                    "historical_availability_verified": False,
                    "promotion_eligible": False,
                    "profitability_proven": False,
                },
                at,
            )

    def start_trial(self, identifier):
        with self._locked():
            rows, states = self._read()
            plan = rows[0]["payload"]["plan"]
            if identifier not in {c["id"] for c in plan["candidates"]} or identifier in states:
                raise ValueError("Unregistered or already attempted trial; no retry")
            spent = sum(
                state["terminal"]["payload"]["elapsed_seconds"]
                for state in states.values()
                if state["terminal"]
            )
            reserved = sum(not state["terminal"] for state in states.values()) * plan["max_trial_seconds"]
            if (
                len(states) >= plan["max_trials"]
                or spent + reserved + plan["max_trial_seconds"] > plan["max_total_seconds"]
            ):
                raise ValueError("Insufficient remaining fixed trial budget")
            at = _now()
            return self._append(
                rows,
                "trial_start",
                {
                    "trial_id": identifier,
                    "deadline": (at + timedelta(seconds=plan["max_trial_seconds"])).isoformat(),
                },
                at,
            )

    def _failure(self, rows, state, identifier, reason, at):
        return self._append(
            rows,
            "failure",
            {
                "trial_id": identifier,
                "reason": reason,
                "elapsed_seconds": (at - _time(state["start"]["at"])).total_seconds(),
                "automatic_retry": False,
            },
            at,
        )

    def commit_predictions(self, identifier, path):
        with self._locked():
            rows, states = self._read()
            state = states.get(identifier)
            if not state or state["terminal"] or state["commit"]:
                raise ValueError("Only a running uncommitted trial can commit predictions")
            at = _now()
            try:
                if at > _time(state["start"]["payload"]["deadline"]):
                    raise ValueError("Prediction commitment exceeded the trial deadline")
                plan = rows[0]["payload"]["plan"]
                candidate = next(c for c in plan["candidates"] if c["id"] == identifier)
                body = Path(path).read_bytes()
                count = _validate_predictions(json.loads(body), candidate, plan, at)
                digest = _sha(body)
                self.artifacts.mkdir(parents=True, exist_ok=True)
                retained = self.artifacts / f"{_sha(identifier.encode())[:16]}-{digest}.json"
                try:
                    with retained.open("xb") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                except FileExistsError:
                    if retained.read_bytes() != body:
                        raise ValueError("Existing immutable artifact content differs")
                # Account for validation/copy time; do not backdate a large commitment
                # to the beginning of this method or allow it to cross a decision.
                at = _now()
                if at > _time(state["start"]["payload"]["deadline"]):
                    raise ValueError("Prediction commitment exceeded the trial deadline")
                value = json.loads(body)
                if plan["phase"] == "prospective" and any(
                    at > _time(row["decision_at"]) for row in value["predictions"]
                ):
                    raise ValueError("Prospective predictions were not committed before their decisions")
                return self._append(
                    rows,
                    "predictions_committed",
                    {
                        "trial_id": identifier,
                        "artifact_path": str(retained),
                        "original_path": str(Path(path).resolve()),
                        "prediction_sha256": digest,
                        "opportunities": count,
                        "provenance": value["provenance"],
                    },
                    at,
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._failure(rows, state, identifier, f"Invalid prediction commitment: {error}", _now())
                raise TrialRejected(f"Prediction rejected; failure retained: {error}") from error

    def finish_trial(self, identifier, result):
        with self._locked():
            rows, states = self._read()
            state = states.get(identifier)
            if not state or state["terminal"]:
                raise ValueError("Only a running trial can finish")
            at = _now()
            try:
                _finite_json(result)
                if state["commit"] is None:
                    raise ValueError("A result requires a prior prediction commitment")
                if at > _time(state["start"]["payload"]["deadline"]):
                    raise ValueError("Trial exceeded its fixed deadline")
                if result["prediction_sha256"] != state["commit"]["payload"]["prediction_sha256"]:
                    raise ValueError("Result links different predictions")
                _hash(result["label_artifact_sha256"])
                if _time(result["evaluation_end"]) != _time(rows[0]["payload"]["plan"]["evaluation_end"]):
                    raise ValueError("Result changed its evaluation window")
                if not isinstance(result["metrics"], dict) or not result["metrics"]:
                    raise ValueError("A result requires finite metric fields")
                if any(
                    result.get(key, False) is not False
                    for key in (
                        "promotion_eligible",
                        "profitability_proven",
                        "historical_availability_verified",
                    )
                ):
                    raise ValueError("Campaign results cannot claim verified history or promotion")
                digest = _sha(_json(result).encode())
                at = _now()
                if at > _time(state["start"]["payload"]["deadline"]):
                    raise ValueError("Trial exceeded its fixed deadline")
                return self._append(
                    rows,
                    "result",
                    {
                        "trial_id": identifier,
                        "result": result,
                        "result_sha256": digest,
                        "elapsed_seconds": (at - _time(state["start"]["at"])).total_seconds(),
                        "promotion_eligible": False,
                        "profitability_proven": False,
                    },
                    at,
                )
            except (ValueError, KeyError, TypeError) as error:
                self._failure(rows, state, identifier, f"Invalid result: {error}", _now())
                raise TrialRejected(f"Result rejected; failure retained: {error}") from error

    def fail_trial(self, identifier, reason):
        with self._locked():
            # A runner must be able to retain failure after a pinned input changed.
            rows, states = self._read(check_pins=False, check_artifacts=False)
            state = states.get(identifier)
            if not state or state["terminal"] or not isinstance(reason, str) or not reason.strip():
                raise ValueError("Failure requires a running trial and a nonempty reason")
            return self._failure(rows, state, identifier, reason, _now())

    def verify(self):
        with self._locked():
            rows, states = self._read()
            return {
                "records_verified": len(rows),
                "trials": len(states),
                "chain_head": rows[-1]["sha256"],
                "source_lineage_independently_verified": False,
                "os_sandbox": False,
            }

    def summary(self):
        with self._locked():
            rows, states = self._read()
            plan = rows[0]["payload"]["plan"]
            terminal = [state["terminal"] for state in states.values() if state["terminal"]]
            spent = sum(row["payload"]["elapsed_seconds"] for row in terminal)
            running = [name for name, state in states.items() if not state["terminal"]]
            reserved = len(running) * plan["max_trial_seconds"]
            return {
                "campaign_id": plan["campaign_id"],
                "phase": plan["phase"],
                "availability_mode": plan["availability_mode"],
                "planned_trials": len(plan["candidates"]),
                "attempted_trials": len(states),
                "results": sum(row["kind"] == "result" for row in terminal),
                "failures": sum(row["kind"] == "failure" for row in terminal),
                "running": running,
                "committed_trials": sum(state["commit"] is not None for state in states.values()),
                "spent_seconds": spent,
                "reserved_seconds": reserved,
                "remaining_seconds": max(0, plan["max_total_seconds"] - spent - reserved),
                "promotion_eligible": False,
                "profitability_proven": False,
                "historical_availability_verified": False,
                "os_sandbox": False,
                "source_lineage_independently_verified": False,
            }
