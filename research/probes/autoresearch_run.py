"""Execute registered local research commands with a shared wall-time deadline.

This is process supervision, not an OS sandbox. Only run trusted, reviewed code.
No command is inferred, installed, retried, or submitted to an external service.
"""

import argparse
import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from research.probes.autoresearch_protocol import (
    TrialLedger,
    check_sources,
    digest,
    now,
    source_hashes,
    timestamp,
)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_execution(plan, candidate):
    runner = str(Path(__file__).resolve())
    if plan["evaluator_sources"].get(runner) != source_hashes([runner])[runner]:
        raise ValueError("The process runner must be pinned in evaluator_sources before registration")
    execution = plan["execution"]
    cwd = Path(execution["cwd"])
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError("Execution requires a registered absolute working directory")
    for command, pinned in (
        (candidate["command"], candidate["sources"]),
        (execution["evaluator_command"], plan["evaluator_sources"]),
    ):
        if (
            not isinstance(command, list)
            or len(command) < 2
            or any(not isinstance(arg, str) for arg in command)
        ):
            raise ValueError("Registered commands must contain an executable and a script entrypoint")
        if not Path(command[0]).is_absolute() or not os.access(command[0], os.X_OK):
            raise ValueError("The command executable must be an explicit executable absolute path")
        script = Path(command[1])
        if not script.is_absolute() or not script.is_file() or str(script) not in pinned:
            raise ValueError("The actual script entrypoint must be included in the registered source hashes")
        if source_hashes([script])[str(script)] != pinned[str(script)]:
            raise ValueError("The registered script entrypoint changed")
    return execution


def terminate_group(process):
    """Stop the isolated process group, including children of an exited leader."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit before a descendant; send KILL to the entire group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


@contextmanager
def capture_interrupts():
    def interrupt(signum, _frame):
        raise InterruptedError(f"Runner received signal {signum}")

    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, interrupt)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_trial(ledger_path, candidate_id):
    ledger = TrialLedger(ledger_path)
    state = ledger.status()
    plan = state["plan"]
    candidate = next((c for c in plan["candidates"] if c["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Unregistered candidate")
    execution = validate_execution(plan, candidate)
    check_sources(plan)
    started = ledger.start(candidate_id)
    remaining = timestamp(started["payload"]["deadline"]) - time.time()
    deadline = time.monotonic() + max(0, remaining)
    root = Path(ledger_path).resolve().with_suffix(".artifacts")
    trial_dir = root / digest(candidate_id)[:20]
    evidence = {
        "candidate_id": candidate_id,
        "registration_sha256": ledger.read()[0]["sha256"],
        "trial_start_sha256": started["sha256"],
        "artifact_directory": str(trial_dir),
        "runner_pid": os.getpid(),
        "phases": [],
        "execution_scope": "registered_local_process_without_sandbox",
        "automatic_retry": False,
    }
    artifacts, failure = {}, None
    process = None
    try:
        trial_dir.mkdir(parents=True, exist_ok=False)
        metadata = trial_dir / "execution.json"
        artifacts["execution"] = metadata
        write_json(metadata, evidence)
        environment = dict(os.environ)
        environment.update(
            WEATHERPRED_TRIAL_DIR=str(trial_dir),
            WEATHERPRED_CANDIDATE_ID=candidate_id,
            WEATHERPRED_CANDIDATE_JSON=json.dumps(candidate, sort_keys=True),
            WEATHERPRED_INPUT_MANIFEST=plan["input_manifest_path"],
        )
        with capture_interrupts():
            for phase, command in (
                ("candidate", candidate["command"]),
                ("evaluator", execution["evaluator_command"]),
            ):
                check_sources(plan)
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"Shared trial deadline expired before {phase}")
                # A candidate-created score must never substitute for evaluator output.
                report = trial_dir / "result.json"
                if phase == "evaluator" and report.exists():
                    report.rename(trial_dir / "candidate_untrusted_result.json")
                    artifacts["candidate_untrusted_result"] = trial_dir / "candidate_untrusted_result.json"
                output, errors = trial_dir / f"{phase}.stdout.log", trial_dir / f"{phase}.stderr.log"
                artifacts[f"{phase}_stdout"] = output
                artifacts[f"{phase}_stderr"] = errors
                step = {"phase": phase, "argv": command, "started_at": now(), "cwd": execution["cwd"]}
                evidence["phases"].append(step)
                with output.open("xb") as stdout, errors.open("xb") as stderr:
                    process = subprocess.Popen(
                        command,
                        cwd=execution["cwd"],
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        shell=False,
                        start_new_session=True,
                    )
                    step.update(pid=process.pid, process_group=process.pid)
                    write_json(metadata, evidence)
                    try:
                        process.wait(timeout=max(0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired as exc:
                        step["timed_out"] = True
                        raise TimeoutError(f"{phase} exceeded the shared trial deadline") from exc
                    finally:
                        terminate_group(process)
                        step.update(
                            returncode=process.returncode, finished_at=now(), group_termination_sent=True
                        )
                        process = None
                        write_json(metadata, evidence)
                if step["returncode"] != 0:
                    raise RuntimeError(f"{phase} exited with code {step['returncode']}")
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            terminate_group(process)
        if "execution" in artifacts:
            evidence.update(finished_at=now(), failure=failure)
            write_json(artifacts["execution"], evidence)
    # Missing evaluator output becomes a retained invalid trial in ledger.finish.
    if failure:
        terminal = ledger.finish(candidate_id, failure=failure, artifacts=artifacts)
    else:
        terminal = ledger.finish(candidate_id, report_path=trial_dir / "result.json", artifacts=artifacts)
    return {"terminal": terminal, "execution": evidence, "ranking": ledger.ranking()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("candidate_id")
    args = parser.parse_args()
    result = run_trial(args.ledger, args.candidate_id)
    print(json.dumps(result, indent=2))
    return 0 if result["terminal"]["payload"]["status"] == "scored" else 1


if __name__ == "__main__":
    raise SystemExit(main())
