from __future__ import annotations

from datetime import UTC, datetime, timedelta

from washdetect.config import MAX_WINDOW_DAYS
from washdetect.rpc import RpcClient


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _unix(dt: datetime) -> int:
    return int(_naive(dt).replace(tzinfo=UTC).timestamp())


def parse_day(value: str, *, end_of_day: bool) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"could not read the date {value!r}; use YYYY-MM-DD") from exc
    parsed = _naive(parsed)
    if len(text) == 10 and end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
    return parsed


def resolve_window(
    client: RpcClient,
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[int, int, datetime, datetime]:
    if days is None and not (start and end):
        raise ValueError(
            "Choose a window: pass days (1-90) or both start and end (YYYY-MM-DD). "
            "There is no default history length."
        )
    if days is not None and (start or end):
        raise ValueError("Pass either days or start/end, not both.")
    head = client.block_number()
    head_block = client.get_block(head)
    head_ts = datetime.fromtimestamp(int(head_block["timestamp"], 16), UTC)
    head_ts = _naive(head_ts)
    if days is not None:
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_WINDOW_DAYS:
            raise ValueError(f"days must be an integer from 1 to {MAX_WINDOW_DAYS}")
        end_ts = head_ts
        start_ts = end_ts - timedelta(days=days)
        end_block = head
    else:
        assert start and end
        start_ts = parse_day(start, end_of_day=False)
        end_ts = parse_day(end, end_of_day=True)
        if end_ts > head_ts:
            end_ts = head_ts
        span = (end_ts - start_ts).total_seconds() / 86400.0
        if span <= 0:
            raise ValueError("The window ends before it starts.")
        if span > MAX_WINDOW_DAYS + 0.01:
            raise ValueError(f"A window can be at most {MAX_WINDOW_DAYS} days (this one is {span:.1f}).")
        end_block = client.find_block_by_timestamp(_unix(end_ts), 1, head)
    start_block = client.find_block_by_timestamp(_unix(start_ts), 1, end_block)
    if start_block > end_block:
        raise ValueError("The window does not cover any blocks.")
    return start_block, end_block, start_ts, end_ts
