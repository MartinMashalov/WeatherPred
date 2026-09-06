"""Restore the exact public case-identity fixture required by frozen tests.

The manifest contains case IDs, clocks, eligibility and source references, not
temperature values. Existing local research artifacts are never overwritten.
"""

import gzip
import hashlib
from pathlib import Path


def pytest_sessionstart(session):
    root = Path(__file__).resolve().parents[1]
    expected = "ccbcbb142cbb7d6954d3ea0d89000427003b27834f9b9f8bb28477d46339b741"
    destination = root / "reports/E022_manifest.json"
    if destination.exists():
        body = destination.read_bytes()
    else:
        body = gzip.decompress((root / "tests/fixtures/E022_manifest.json.gz").read_bytes())
    if hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("Frozen E022 case manifest differs from its registered hash")
    if not destination.exists():
        destination.parent.mkdir(exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(body)
