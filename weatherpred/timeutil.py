from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Naive timestamp has no defensible information-availability time")
    return dt.astimezone(UTC)


def iso(value: str | datetime) -> str:
    return parse_time(value).isoformat(timespec="microseconds")
