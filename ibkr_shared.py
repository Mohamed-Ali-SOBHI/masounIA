#!/usr/bin/env python3
"""Shared helpers for MasounIA.

This project is intentionally a set of standalone scripts. This file provides
small utilities shared by multiple scripts to avoid copy/paste.
"""

import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utc_now_iso() -> str:
    """UTC timestamp as ISO-8601 (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_ibkr_account(ib, requested_account):
    """Resolve the IBKR account to use.

    Returns:
        (account, error_message)
    """
    if requested_account:
        return str(requested_account).strip(), None

    accounts = ib.managedAccounts() or []
    if len(accounts) == 1:
        return accounts[0], None
    if accounts:
        return None, "Multiple accounts found, use --account."
    return None, "No IBKR accounts found."


def load_dotenv(path):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if value and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def read_json(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(data, path):
    output = json.dumps(data, indent=2, ensure_ascii=True)
    if path == "-":
        print(output)
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(output)


# ---------------------------------------------------------------------------
# Market hours (simplified)
#
# Centralized here to avoid duplicating the same logic across scripts.
# Used as a safety gate only (not a full exchange calendar).
# ---------------------------------------------------------------------------


GOOD_FRIDAYS = {
    2025: (4, 18),
    2026: (4, 3),
    2027: (3, 26),
    2028: (4, 14),
    2029: (3, 30),
    2030: (4, 19),
}


EASTER_MONDAYS = {
    2025: (4, 21),
    2026: (4, 6),
    2027: (3, 29),
    2028: (4, 17),
    2029: (4, 2),
    2030: (4, 22),
}


def _ensure_aware(dt: datetime) -> datetime:
    """Force timezone-aware datetime (UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _within_session(
    dt_local: datetime,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
) -> bool:
    start = dt_local.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
    )
    end = dt_local.replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
    )
    return start <= dt_local <= end


def is_us_market_open(dt: datetime) -> bool:
    """NYSE/NASDAQ regular session open (simplified)."""
    dt_local = _ensure_aware(dt).astimezone(ZoneInfo("America/New_York"))

    if dt_local.weekday() >= 5:
        return False

    year = dt_local.year
    month = dt_local.month
    day = dt_local.day

    if (month, day) in {(1, 1), (7, 4), (12, 25)}:
        return False

    # MLK Day - 3rd Monday of January
    if month == 1 and dt_local.weekday() == 0 and 15 <= day <= 21:
        return False

    # Presidents Day - 3rd Monday of February
    if month == 2 and dt_local.weekday() == 0 and 15 <= day <= 21:
        return False

    if year in GOOD_FRIDAYS and (month, day) == GOOD_FRIDAYS[year]:
        return False

    # Memorial Day - last Monday of May
    if month == 5 and dt_local.weekday() == 0 and day >= 25:
        return False

    # Labor Day - first Monday of September
    if month == 9 and dt_local.weekday() == 0 and day <= 7:
        return False

    # Thanksgiving - 4th Thursday of November
    if month == 11 and dt_local.weekday() == 3 and 22 <= day <= 28:
        return False

    return _within_session(dt_local, 9, 30, 16, 0)


def is_europe_market_open(dt: datetime) -> bool:
    """Europe markets regular session open (simplified)."""
    dt_local = _ensure_aware(dt).astimezone(ZoneInfo("Europe/Paris"))

    if dt_local.weekday() >= 5:
        return False

    year = dt_local.year
    month = dt_local.month
    day = dt_local.day

    if (month, day) in {(1, 1), (12, 25)}:
        return False

    if year in EASTER_MONDAYS and (month, day) == EASTER_MONDAYS[year]:
        return False

    if year in GOOD_FRIDAYS and (month, day) == GOOD_FRIDAYS[year]:
        return False

    if month == 5 and day == 1:
        return False

    return _within_session(dt_local, 9, 0, 17, 30)


def is_asia_market_open(dt: datetime) -> bool:
    """Tokyo/Hong Kong regular session open (very simplified)."""
    dt_tokyo = _ensure_aware(dt).astimezone(ZoneInfo("Asia/Tokyo"))
    dt_hk = dt_tokyo.astimezone(ZoneInfo("Asia/Hong_Kong"))

    if dt_tokyo.weekday() >= 5:
        return False

    month = dt_tokyo.month
    day = dt_tokyo.day

    if (month, day) in {(1, 1), (12, 25)}:
        return False

    tokyo_open = _within_session(dt_tokyo, 9, 0, 15, 0)
    hk_open = _within_session(dt_hk, 9, 30, 16, 0)
    return tokyo_open or hk_open


def get_open_markets(dt: datetime) -> list[str]:
    """Return a list of open markets labels."""
    markets: list[str] = []
    if is_us_market_open(dt):
        markets.append("US")
    if is_europe_market_open(dt):
        markets.append("Europe")
    if is_asia_market_open(dt):
        markets.append("Asie")
    return markets
