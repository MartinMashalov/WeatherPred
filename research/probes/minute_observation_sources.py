"""Twelve-GET metadata/documentation scope; no measurement-data routes."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from research.probes import innovation_sources

MANIFEST = Path("reports/minute_observation_requests.json")
REQUESTS = {
    "iem-minute-backend-help": "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?help",
    "iem-minute-station-directory": "https://mesonet.agron.iastate.edu/geojson/network/ASOS1MIN.geojson",
    "iem-asos-overview": "https://mesonet.agron.iastate.edu/ASOS/",
    "noaa-madis-one-minute-docs": "https://madis.ncep.noaa.gov/madis_OMO.shtml",
    "ncei-page2-format": "https://www.ncei.noaa.gov/pub/data/asos-onemin/td6406.txt",
    "iem-backend-source": "https://raw.githubusercontent.com/akrherz/iem/main/htdocs/cgi-bin/request/asos1min.py",
    "iem-minute-processing-source": "https://raw.githubusercontent.com/akrherz/iem/main/scripts/asos/one_minute.py",
}


def fetch(keys):
    if not keys or len(keys) != len(set(keys)) or set(keys) - REQUESTS.keys():
        raise ValueError("Only distinct predeclared documentation/metadata routes are allowed")
    if not MANIFEST.exists():
        MANIFEST.write_text(
            json.dumps(
                {
                    "round": "minute-observation-metadata-20260906",
                    "max_physical_gets": 12,
                    "minimum_spacing_seconds": 1,
                    "physical_gets": [],
                    "results": [],
                    "strategy_scores": 0,
                    "weather_scores": 0,
                    "protected_data": False,
                    "measurement_bodies_authorized": False,
                    "routes": REQUESTS,
                    "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    innovation_sources.MANIFEST = MANIFEST
    innovation_sources.HOSTS = {urlparse(url).hostname for url in REQUESTS.values()}
    return innovation_sources.fetch([(key, REQUESTS[key], None) for key in keys])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keys", nargs="+", choices=sorted(REQUESTS))
    args = parser.parse_args()
    fetch(args.keys)
