"""Deterministic SIC division mapping (U.S. SIC Manual, 1987).

The SEC assigns each registrant a four-digit SIC code. The first two digits are
the major group, and major groups roll up to eleven divisions. This is a coarse,
public classification and is labelled as SIC wherever it is displayed; it is not
GICS or ICB.
"""

_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (1, 9, "Agriculture, Forestry & Fishing"),
    (10, 14, "Mining"),
    (15, 17, "Construction"),
    (20, 39, "Manufacturing"),
    (40, 49, "Transportation, Communications & Utilities"),
    (50, 51, "Wholesale Trade"),
    (52, 59, "Retail Trade"),
    (60, 67, "Finance, Insurance & Real Estate"),
    (70, 89, "Services"),
    (91, 97, "Public Administration"),
    (99, 99, "Nonclassifiable"),
)


def sic_division(sic_code: str | None) -> str | None:
    if not sic_code or len(sic_code) != 4 or not sic_code.isdigit():
        return None
    major_group = int(sic_code[:2])
    for low, high, name in _DIVISIONS:
        if low <= major_group <= high:
            return name
    return None
