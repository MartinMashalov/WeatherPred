"""Bounded public source-feasibility receipts; no model or trading evaluation.

Imported explicitly by the scout. The persistent manifest counts physical GET
attempts, including redirects and PublicClient retries, across invocations.
"""

import hashlib
import json
import time
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, utcnow

MANIFEST = Path("reports/innovation_source_requests.json")
HOSTS = {
    "external-api.kalshi.com",
    "aviationweather.gov",
    "docs.synopticdata.com",
    "api.synopticdata.com",
    "api.weather.gov",
    "www.weather.gov",
    "www.ncei.noaa.gov",
    "mesonet.agron.iastate.edu",
}


def fetch(requests):
    """Archive a supplied small request list, respecting the round's total cap."""
    archive = Archive()
    state = (
        json.loads(MANIFEST.read_text())
        if MANIFEST.exists()
        else {
            "round": "innovation-source-feasibility-20260906",
            "max_physical_gets": 20,
            "minimum_spacing_seconds": 1,
            "physical_gets": [],
            "results": [],
            "strategy_scores": 0,
            "weather_scores": 0,
            "protected_data": False,
        }
    )

    def save():
        MANIFEST.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    if "protocol_record_id" not in state:
        state["protocol_record_id"] = archive.append(
            "innovation_source_protocol",
            state["round"],
            utcnow(),
            {},
            canonical(
                {**state, "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            ).encode(),
        )
        save()
    client = PublicClient(archive, interval=1)
    last_request = time.monotonic()

    def before(request):
        nonlocal last_request
        if request.method != "GET" or request.url.host not in HOSTS or request.url.scheme != "https":
            raise ValueError("Request outside the public source scope")
        if len(state["physical_gets"]) >= state["max_physical_gets"]:
            raise RuntimeError("This round's twenty physical GET budget is exhausted")
        time.sleep(max(0, last_request + 1 - time.monotonic()))
        last_request = time.monotonic()
        state["physical_gets"].append({"url": str(request.url), "request_started_at": iso(utcnow())})
        save()

    client.client.event_hooks["request"] = [before]
    try:
        for key, url, params in requests:
            if any(row["key"] == key for row in state["results"]):
                raise ValueError("Request key already attempted; preserve the existing evidence")
            before_id = archive.db.execute("SELECT coalesce(max(id),0) FROM records").fetchone()[0]
            result = {"key": key, "requested_url": url, "params": params}
            try:
                response, identifier = client.get(url, params=params, kind="innovation_source_http", key=key)
                result.update(record_id=identifier, status=response.status_code, bytes=len(response.content))
            except httpx.HTTPError as error:
                result["error"] = f"{type(error).__name__}: {error}"
            rows = archive.db.execute(
                "SELECT id,kind,key,available_at,body_sha256,metadata FROM records WHERE id>? AND key=? ORDER BY id",
                (before_id, key),
            ).fetchall()
            result["receipts"] = [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]
            state["results"].append(result)
            save()
            print(json.dumps({k: v for k, v in result.items() if k != "receipts"}), flush=True)
    finally:
        client.close()
        archive.close()
    return state
