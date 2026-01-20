#!/usr/bin/env python3
"""MasounIA runner (Europe session only).

Goal: avoid burning Grok API credits at night.

Behavior:
- Sleep until shortly before Europe open.
- Run periodically during the Europe session.
- Sleep after the session.

Config via env:
- EU_SESSION_START (default: 08:45)
- EU_SESSION_END (default: 17:45)
- EU_RUN_INTERVAL_MIN (default: 60)
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ibkr_shared import is_europe_market_open, load_dotenv


EU_TZ = ZoneInfo("Europe/Paris")


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        parts = str(value).strip().split(":")
        if len(parts) != 2:
            return default
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return default


def _local_dt_for(date_local, hour: int, minute: int) -> datetime:
    return datetime(
        date_local.year,
        date_local.month,
        date_local.day,
        hour,
        minute,
        tzinfo=EU_TZ,
    )


def _is_trading_day(date_local) -> bool:
    # Check the day using a stable local time (noon).
    probe_local = _local_dt_for(date_local, 12, 0)
    return is_europe_market_open(probe_local.astimezone(timezone.utc))


def _next_trading_day_start_utc(now_utc: datetime, start_hour: int, start_minute: int) -> datetime:
    now_local = now_utc.astimezone(EU_TZ)
    for delta_days in range(0, 14):
        candidate_date = now_local.date() + timedelta(days=delta_days)
        if not _is_trading_day(candidate_date):
            continue
        start_local = _local_dt_for(candidate_date, start_hour, start_minute)
        start_utc = start_local.astimezone(timezone.utc)
        if start_utc > now_utc:
            return start_utc
    return now_utc + timedelta(days=1)


def _sleep_until(target_utc: datetime):
    now_utc = datetime.now(timezone.utc)
    seconds = (target_utc - now_utc).total_seconds()
    if seconds <= 0:
        return
    time.sleep(seconds)


def main():
    load_dotenv(".env")

    start_h, start_m = _parse_hhmm(os.getenv("EU_SESSION_START", "08:35"), (8, 35))
    end_h, end_m = _parse_hhmm(os.getenv("EU_SESSION_END", "17:40"), (17, 40))
    interval_min = int(os.getenv("EU_RUN_INTERVAL_MIN", "60"))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "ibkr_grok_pipeline.py")

    print("MasounIA - Europe session only")
    print(f"Session: {start_h:02d}:{start_m:02d} -> {end_h:02d}:{end_m:02d} ({EU_TZ.key})")
    print(f"Interval: {interval_min} min")
    print("Ctrl+C pour arreter")

    try:
        while True:
            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone(EU_TZ)

            if not _is_trading_day(now_local.date()):
                next_start = _next_trading_day_start_utc(now_utc, start_h, start_m)
                print(
                    f"[{now_local.strftime('%Y-%m-%d %H:%M')}] Europe ferme (jour non-boursier). "
                    f"Prochain reveil: {next_start.astimezone(EU_TZ).strftime('%Y-%m-%d %H:%M')}"
                )
                _sleep_until(next_start)
                continue

            session_start_local = _local_dt_for(now_local.date(), start_h, start_m)
            session_end_local = _local_dt_for(now_local.date(), end_h, end_m)
            session_start_utc = session_start_local.astimezone(timezone.utc)
            session_end_utc = session_end_local.astimezone(timezone.utc)

            if now_utc < session_start_utc:
                print(
                    f"[{now_local.strftime('%Y-%m-%d %H:%M')}] Avant session. "
                    f"Reveil: {session_start_local.strftime('%H:%M')}"
                )
                _sleep_until(session_start_utc)
                continue

            if now_utc >= session_end_utc:
                next_start = _next_trading_day_start_utc(
                    now_utc + timedelta(minutes=1),
                    start_h,
                    start_m,
                )
                print(
                    f"[{now_local.strftime('%Y-%m-%d %H:%M')}] Fin session. "
                    f"Dodo jusqu'a: {next_start.astimezone(EU_TZ).strftime('%Y-%m-%d %H:%M')}"
                )
                _sleep_until(next_start)
                continue

            print(f"[{now_local.strftime('%Y-%m-%d %H:%M')}] Run pipeline...")
            cmd = [sys.executable, pipeline, "--submit"]
            result = subprocess.run(cmd)
            if result.returncode == 0:
                print(f"[{datetime.now(EU_TZ).strftime('%H:%M:%S')}] OK")
            else:
                print(
                    f"[{datetime.now(EU_TZ).strftime('%H:%M:%S')}] Erreur (code {result.returncode})"
                )

            next_run_utc = datetime.now(timezone.utc) + timedelta(minutes=interval_min)
            if next_run_utc < session_end_utc:
                next_local = next_run_utc.astimezone(EU_TZ)
                print(f"Prochain run: {next_local.strftime('%H:%M')} ({EU_TZ.key})")
                _sleep_until(next_run_utc)
            else:
                next_start = _next_trading_day_start_utc(next_run_utc, start_h, start_m)
                print(
                    f"Prochain run hors session. Dodo jusqu'a: {next_start.astimezone(EU_TZ).strftime('%Y-%m-%d %H:%M')}"
                )
                _sleep_until(next_start)

    except KeyboardInterrupt:
        print("\nArret demande (Ctrl+C)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
