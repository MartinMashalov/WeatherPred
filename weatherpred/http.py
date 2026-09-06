"""Public GET-only ingestion, with receipt-time provenance and bounded retries."""

import logging
import time

import httpx

from weatherpred.archive import Archive
from weatherpred.timeutil import iso, utcnow

LOG = logging.getLogger(__name__)
BASE = "https://external-api.kalshi.com/trade-api/v2"


class PublicClient:
    def __init__(self, archive: Archive, interval=0.35):
        self.archive = archive
        self.interval = interval
        self.next_request = 0.0
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "WeatherPred/0.1 (public weather research; local shadow mode)",
                "Cache-Control": "no-cache",
            },
        )

    def close(self):
        self.client.close()

    def get(self, path, params=None, kind="http", key=None, *, byte_range=None, expected_etag=None):
        url = path if path.startswith("https://") else BASE + path
        request_headers = {}
        if byte_range is not None:
            start, end = byte_range
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
                raise ValueError("Invalid inclusive byte range")
            request_headers["Range"] = f"bytes={start}-{end}"
        if expected_etag is not None:
            request_headers["If-Match"] = expected_etag
        request = self.client.build_request("GET", url, params=params, headers=request_headers)
        for attempt in range(3):
            time.sleep(max(0, self.next_request - time.monotonic()))
            started = utcnow()
            self.next_request = time.monotonic() + self.interval
            try:
                response = self.client.send(request)
            except httpx.TransportError as exc:
                self.archive.append(
                    "http_error",
                    key or str(request.url),
                    utcnow(),
                    {
                        "url": str(request.url),
                        "request_started_at": iso(started),
                        "error": str(exc),
                        "attempt": attempt,
                    },
                    b"",
                )
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
                continue
            received = utcnow()
            record_id = self.archive.append(
                kind if response.is_success else "http_error",
                key or str(request.url),
                received,
                {
                    "url": str(request.url),
                    "request_started_at": iso(started),
                    "received_at": iso(received),
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "conditional_request_headers": request_headers,
                    "attempt": attempt,
                },
                response.content,
            )
            LOG.debug("GET %s status=%s record=%s", request.url, response.status_code, record_id)
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                try:
                    pause = min(float(response.headers.get("retry-after", 2 ** (attempt + 1))), 30)
                except ValueError:
                    pause = 2 ** (attempt + 1)
                time.sleep(max(0, pause))
                continue
            response.raise_for_status()
            if expected_etag is not None and response.headers.get("etag") != expected_etag:
                raise ValueError("Object ETag changed between listing and acquisition")
            if byte_range is not None:
                content_range = response.headers.get("content-range", "")
                expected = f"bytes {start}-{end}/"
                if (
                    response.status_code != 206
                    or not content_range.startswith(expected)
                    or len(response.content) != end - start + 1
                ):
                    raise ValueError("Byte-range response does not match requested range")
            return response, record_id
        raise RuntimeError("Retry loop exhausted")

    def json(self, path, params=None, kind="http", key=None):
        response, record_id = self.get(path, params=params, kind=kind, key=key)
        return response.json(), record_id

    def pages(self, path, field, params=None, kind="http", key=None):
        params = dict(params or {})
        seen = set()
        while True:
            data, record_id = self.json(path, params=params, kind=kind, key=key)
            yield data[field], record_id
            cursor = data.get("cursor")
            if not cursor:
                return
            if cursor in seen:
                raise ValueError("API repeated pagination cursor; refusing incomplete census")
            seen.add(cursor)
            params["cursor"] = cursor
