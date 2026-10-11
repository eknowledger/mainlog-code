"""How the registry compares a plugin's declared API version with the host's."""

from itertools import takewhile


def _leading_number(part: str):
    """The number a dotted part starts with ("0rc1" -> 0), or None if it doesn't."""
    digits = "".join(takewhile(str.isdecimal, part.strip()))
    return int(digits) if digits else None


def parse_version_prefix(version: str) -> tuple[int, ...]:
    """Leading numbers of the dotted parts, up to the first part that has none.

    "1.4" -> (1, 4)    "2.0rc1" -> (2, 0)    "1.0.12345" -> (1, 0, 12345)    "1.4.0" -> (1, 4, 0)
    """
    numbers = [_leading_number(part) for part in version.split(".")]
    if None in numbers:
        numbers = numbers[: numbers.index(None)]
    return tuple(numbers)
