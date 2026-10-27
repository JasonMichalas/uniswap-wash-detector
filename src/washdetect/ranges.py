from __future__ import annotations


def merge_intervals(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[list[int]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 1:  # touching inclusive ranges are one span
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def missing_intervals(
    have: list[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    if start > end:
        return []
    clipped: list[tuple[int, int]] = []
    for left, right in merge_intervals(have):
        lo, hi = max(left, start), min(right, end)
        if lo <= hi:
            clipped.append((lo, hi))
    clipped = merge_intervals(clipped)
    missing: list[tuple[int, int]] = []
    cursor = start
    for left, right in clipped:
        if cursor < left:
            missing.append((cursor, left - 1))
        cursor = max(cursor, right + 1)
    if cursor <= end:
        missing.append((cursor, end))
    return missing


def covers(have: list[tuple[int, int]], start: int, end: int) -> bool:
    return not missing_intervals(have, start, end)


def intersect_intervals(
    left: list[tuple[int, int]], right: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    a = merge_intervals(left)
    b = merge_intervals(right)
    found: list[tuple[int, int]] = []
    i = 0
    j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            found.append((lo, hi))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return found
