"""Selected quarter and its three calendar predecessors, with explicit zeroes."""


def _quarter_index(quarter: str) -> int:
    try:
        year_text, number_text = quarter.split('-Q')
        year, number = int(year_text), int(number_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('quarter must be formatted like 2026-Q2') from exc
    if number not in (1, 2, 3, 4):
        raise ValueError('quarter number must be one of 1, 2, 3, 4')
    return year * 4 + number - 1


def _quarter_name(index: int) -> str:
    year, offset = divmod(index, 4)
    return f'{year:04d}-Q{offset + 1}'


def normalize_quarterly_trends(rows, target_quarter: str) -> list[dict]:
    """Return the approved four-quarter window ending at the selected quarter.

    ``rows`` is the SQL GROUP BY result (or the equivalent cached rows), with
    ``quarter``, ``helmet`` and ``vest`` keys. Future rows never affect the
    range. No events yields four zero-count quarters. Percentage changes are
    not calculated here, including the undefined previous-zero case.
    """
    target = _quarter_index(target_quarter)
    counts = {}
    for row in rows:
        index = _quarter_index(row['quarter'])
        if target - 3 <= index <= target:
            counts[index] = {'helmet': row['helmet'] or 0, 'vest': row['vest'] or 0}
    return [
        {'quarter': _quarter_name(index), **counts.get(index, {'helmet': 0, 'vest': 0})}
        for index in range(target - 3, target + 1)
    ]
