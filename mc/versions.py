import logging
import re

_log = logging.getLogger(__name__)

_version_re = re.compile(r"\d+(?:\.\d+)+")


def parse_version(version: str | None) -> tuple[int, ...] | None:
    """
    Parse a dotted numeric version (e.g. "1.26.52.3") into a tuple of ints that sorts numerically.

    Returns None for anything that is not a plain dotted version, e.g. "1.26.52.3_inprogress", "junk" or "".

    """
    if not isinstance(version, str):
        return None
    version = version.strip()
    if not _version_re.fullmatch(version):
        return None
    return tuple(int(part) for part in version.split("."))


def is_newer(candidate: str | None, baseline: str | None) -> bool:
    """
    True if candidate is numerically greater than baseline.

    An unparseable candidate is never newer. If there is no baseline, or the baseline is unparseable (so cannot be
    compared), any valid candidate counts as newer.

    """
    candidate_t = parse_version(candidate)
    if candidate_t is None:
        return False
    if baseline is None:
        return True
    baseline_t = parse_version(baseline)
    if baseline_t is None:
        _log.warning(f"Cannot compare against unparseable version '{baseline}', treating {candidate} as newer")
        return True
    return candidate_t > baseline_t
