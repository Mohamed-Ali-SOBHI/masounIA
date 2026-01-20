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


def is_europe_market_open(dt: datetime) -> bool:
    """Return True if it's a Europe trading day (simplified).

    This intentionally ignores the intra-day hours so the bot can prepare/submit
    orders outside regular hours (IBKR will keep them pending as needed).
    """
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

    return True
def get_open_markets(dt: datetime) -> list[str]:
    """Return a list of open markets labels.

    MasounIA is configured to trade Europe only.
    """
    return ["Europe"] if is_europe_market_open(dt) else []
