import collections
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from weatherpred.archive import canonical
from weatherpred.timeutil import iso, utcnow

LOG = logging.getLogger(__name__)


def family(series):
    """Discovery tags only, never substitutes for parsed contract rules."""
    title = " ".join(series["title"].lower().split())
    text = title + " " + series.get("contract_terms_url", "").lower()
    if re.search(r"\b(low(?:est)?|min(?:imum)?)\b.*\b(temp|temperature)\b", title):
        return "temperature_min"
    if re.search(r"\b(high(?:est)?|max(?:imum)?)\b.*\b(temp|temperature)\b", title):
        return "temperature_max"
    for name, words in [
        ("nonweather_geophysical", ("earthquake", "volcano", "solar flare")),
        ("snow", ("snow", "blizzard")),
        ("precipitation", ("rain", "precipitation")),
        ("hurricane_storm", ("hurricane", "tornado", "cyclone", "storm", "landfall")),
        ("temperature_hourly_or_index", ("hourly", "hourtemperature", "weatherindex")),
        ("temperature_min", ("lowest", "low temp", "minimum", "min temp")),
        ("temperature_max", ("highest", "high temp", "maximum", "max temp")),
        ("temperature_other", ("temperature", "hottest", "coldest", "warmest", "degrees")),
        ("climate_environment", ("climate", "drought", "ice", "aqi", "carbon", "co2", "wildfire")),
    ]:
        if any(word in text for word in words):
            return name
    return "unclassified_review_required"


def discover(client, output="reports"):
    data, record_id = client.json(
        "/series", {"include_product_metadata": "true"}, kind="series_catalog", key="all"
    )
    all_series = data["series"]
    climate = [s for s in all_series if s.get("category") == "Climate and Weather"]
    # Also retain title matches outside the primary category for manual coverage audit.
    outside = [
        s
        for s in all_series
        if s.get("category") != "Climate and Weather"
        and any(
            w in s["title"].lower() for w in ("temperature", "rainfall", "snowfall", "hurricane", "weather")
        )
    ]
    for s in climate:
        client.archive.append(
            "series", s["ticker"], utcnow(), {"raw_record_id": record_id}, canonical(s).encode()
        )
    report = {
        "generated_at": iso(utcnow()),
        "catalog_record_id": record_id,
        "all_series_count": len(all_series),
        "climate_series_count": len(climate),
        "families": dict(collections.Counter(family(s) for s in climate)),
        "source_names": dict(
            collections.Counter(x["name"] for s in climate for x in (s.get("settlement_sources") or []))
        ),
        "outside_category_candidates": outside,
        "series": [dict(s, discovery_family=family(s)) for s in sorted(climate, key=lambda x: x["ticker"])],
    }
    Path(output).mkdir(parents=True, exist_ok=True)
    Path(output, "universe.json").write_text(json.dumps(report, indent=2))
    LOG.info(
        "Universe: %s total series; %s climate/weather; %s outside-category candidates",
        len(all_series),
        len(climate),
        len(outside),
    )
    return report


def collect_markets(client, series, status="open", output="reports"):
    rows, failures = [], []
    for index, s in enumerate(series):
        try:
            for page, record_id in client.pages(
                "/markets",
                "markets",
                {"series_ticker": s["ticker"], "status": status, "limit": 1000},
                kind="market_page",
                key=s["ticker"] + ":" + status,
            ):
                for market in page:
                    client.archive.append(
                        "market",
                        market["ticker"],
                        utcnow(),
                        {"raw_record_id": record_id, "series_ticker": s["ticker"]},
                        canonical(market).encode(),
                    )
                    rows.append(dict(market, series_ticker=s["ticker"], discovery_family=family(s)))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            failures.append({"series_ticker": s["ticker"], "error": str(exc)})
            LOG.warning("Series collection failed %s: %s", s["ticker"], exc)
        if index % 25 == 0 or index == len(series) - 1:
            LOG.info(
                "Market census %s/%s series; %s markets; %s failures",
                index + 1,
                len(series),
                len(rows),
                len(failures),
            )
    report = {
        "generated_at": iso(utcnow()),
        "status": status,
        "markets": rows,
        "failures": failures,
        "complete": not failures,
    }
    Path(output).mkdir(parents=True, exist_ok=True)
    Path(output, f"markets_{status}.json").write_text(json.dumps(report, indent=2))
    return report


def archive_rules(client, series, output="reports/rules"):
    from io import BytesIO

    from pypdf import PdfReader

    Path(output).mkdir(parents=True, exist_ok=True)
    urls = sorted({s["contract_terms_url"] for s in series if s.get("contract_terms_url")})
    results = []
    for index, url in enumerate(urls):
        try:
            response, record_id = client.get(url, kind="contract_pdf", key=url)
            text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(response.content)).pages)
            name = Path(urlparse(url).path).name
            Path(output, name + ".txt").write_text(text)
            results.append({"url": url, "record_id": record_id, "file": name + ".txt"})
        except (httpx.HTTPError, ValueError, OSError) as exc:
            results.append({"url": url, "error": str(exc)})
        if index % 20 == 0 or index == len(urls) - 1:
            LOG.info("Contract templates %s/%s", index + 1, len(urls))
    Path(output, "manifest.json").write_text(json.dumps(results, indent=2))
    return results
