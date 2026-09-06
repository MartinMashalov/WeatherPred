"""One local process group with retained logs, wall deadline and sampled RSS cap."""

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def process_group_rss(group):
    result = subprocess.run(
        ["ps", "-ax", "-o", "pgid=,rss="], capture_output=True, text=True, check=True, timeout=2
    )
    return sum(
        int(parts[1]) * 1024
        for line in result.stdout.splitlines()
        if len(parts := line.split()) == 2 and int(parts[0]) == group
    )


def terminate_group(process):
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass
        if number == signal.SIGTERM:
            try:
                process.wait(timeout=0.15)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=2)


def supervise(command, cwd, directory, wall_seconds, rss_bytes, stop_file):
    """No shell, hidden retry or OS-sandbox claim; actual tiny children test this."""
    if not command or any(not isinstance(arg, str) for arg in command) or wall_seconds <= 0 or rss_bytes <= 0:
        raise ValueError("Invalid supervised command or finite resource budget")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = directory / "stdout.log", directory / "stderr.log"
    result = {
        "command": command,
        "cwd": str(cwd),
        "wall_seconds_limit": wall_seconds,
        "rss_bytes_limit": rss_bytes,
        "automatic_retry": False,
        "rss_monitor": "process-group sum sampled at approximately 0.2s; child also reports its peak",
        "status": "failed",
        "reason": None,
        "maximum_sampled_rss_bytes": 0,
    }
    (directory / "started.json").write_text(json.dumps(result, indent=2) + "\n")
    environment = dict(
        os.environ,
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="2",
        MKL_NUM_THREADS="2",
        NUMEXPR_NUM_THREADS="2",
        HF_HUB_OFFLINE="1",
        HF_DATASETS_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        WANDB_DISABLED="true",
        WANDB_MODE="disabled",
    )
    started, process = time.monotonic(), None
    old = {}

    def interrupted(number, _frame):
        raise InterruptedError(f"Supervisor received signal {number}")

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            old[number] = signal.signal(number, interrupted)
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            if Path(stop_file).exists():
                raise InterruptedError("Stop file present before child launch")
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                start_new_session=True,
            )
            result["pid"] = process.pid
            while True:
                if Path(stop_file).exists():
                    result["reason"] = "stop_file"
                    break
                if time.monotonic() - started >= wall_seconds:
                    result["reason"] = "wall_deadline"
                    break
                rss = process_group_rss(process.pid)
                result["maximum_sampled_rss_bytes"] = max(result["maximum_sampled_rss_bytes"], rss)
                if rss > rss_bytes:
                    result["reason"] = "rss_limit"
                    break
                code = process.poll()
                if code is not None:
                    result["status"] = "completed" if code == 0 else "failed"
                    result["reason"] = None if code == 0 else "nonzero_exit"
                    break
                time.sleep(min(0.2, max(0, wall_seconds - (time.monotonic() - started))))
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            terminate_group(process)
            result["returncode"] = process.returncode
            result["process_group_termination_sent"] = True
        for number, handler in old.items():
            signal.signal(number, handler)
        result["elapsed_seconds"] = time.monotonic() - started
        result["logs"] = {
            path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (stdout_path, stderr_path)
            if path.exists()
        }
        with (directory / "result.json").open("x") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
    return result
