#!/usr/bin/env python3
"""
Script ultra-simple pour trading automatique avec Grok.
Lance: python run.py
Execute le bot toutes les heures en boucle.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Charger les variables d'environnement
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ibkr_shared import load_dotenv

load_dotenv(".env")


def _ensure_aware(dt):
    """Force un datetime timezone-aware (UTC par défaut si naïf)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _within_session(dt_local, start_hour, start_minute, end_hour, end_minute):
    """Vérifie que dt_local est dans la plage horaire [start, end]."""
    start = dt_local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = dt_local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= dt_local <= end


def is_us_market_open(dt):
    """Verifie si les marches US (NYSE, NASDAQ) sont ouverts."""
    dt_local = _ensure_aware(dt).astimezone(ZoneInfo("America/New_York"))

    # Week-end
    if dt_local.weekday() >= 5:  # 5=samedi, 6=dimanche
        return False

    year = dt_local.year
    month = dt_local.month
    day = dt_local.day

    # Jours feries fixes
    fixed_holidays = [
        (1, 1),   # Nouvel An
        (7, 4),   # Independence Day
        (12, 25), # Noel
    ]

    if (month, day) in fixed_holidays:
        return False

    # MLK Day - 3eme lundi de janvier
    if month == 1 and dt_local.weekday() == 0:  # Lundi
        if 15 <= day <= 21:
            return False

    # Presidents Day - 3eme lundi de fevrier
    if month == 2 and dt_local.weekday() == 0:  # Lundi
        if 15 <= day <= 21:
            return False

    # Good Friday - vendredi avant Paques (liste pour 2025-2030)
    good_fridays = {
        2025: (4, 18),
        2026: (4, 3),
        2027: (3, 26),
        2028: (4, 14),
        2029: (3, 30),
        2030: (4, 19),
    }
    if year in good_fridays and (month, day) == good_fridays[year]:
        return False

    # Memorial Day - dernier lundi de mai
    if month == 5 and dt_local.weekday() == 0:  # Lundi
        if day >= 25:  # Dernier lundi est toujours >= 25
            return False

    # Labor Day - 1er lundi de septembre
    if month == 9 and dt_local.weekday() == 0:  # Lundi
        if day <= 7:  # Premier lundi est toujours <= 7
            return False

    # Thanksgiving - 4eme jeudi de novembre
    if month == 11 and dt_local.weekday() == 3:  # Jeudi
        if 22 <= day <= 28:  # 4eme jeudi est toujours dans cette plage
            return False

    # Horaires réguliers: 09:30-16:00 America/New_York (hors pre/after-market)
    return _within_session(dt_local, 9, 30, 16, 0)


def is_europe_market_open(dt):
    """Verifie si les marches europeens (Euronext, Xetra, SIX) sont ouverts."""
    dt_local = _ensure_aware(dt).astimezone(ZoneInfo("Europe/Paris"))

    # Week-end
    if dt_local.weekday() >= 5:
        return False

    year = dt_local.year
    month = dt_local.month
    day = dt_local.day

    # Jours feries europeens communs
    # Note: LSE (Londres) a Boxing Day (26/12) mais Euronext/Xetra non
    common_holidays = [
        (1, 1),   # Nouvel An
        (12, 25), # Noel
    ]

    if (month, day) in common_holidays:
        return False

    # Lundi de Paques (liste pour 2025-2030)
    easter_mondays = {
        2025: (4, 21),
        2026: (4, 6),
        2027: (3, 29),
        2028: (4, 17),
        2029: (4, 2),
        2030: (4, 22),
    }
    if year in easter_mondays and (month, day) == easter_mondays[year]:
        return False

    # Vendredi Saint (la plupart des marches europeens)
    good_fridays = {
        2025: (4, 18),
        2026: (4, 3),
        2027: (3, 26),
        2028: (4, 14),
        2029: (3, 30),
        2030: (4, 19),
    }
    if year in good_fridays and (month, day) == good_fridays[year]:
        return False

    # 1er mai - Fete du Travail (Europe continentale)
    if month == 5 and day == 1:
        return False

    # Horaires réguliers: 09:00-17:30 Europe/Paris
    return _within_session(dt_local, 9, 0, 17, 30)


def is_asia_market_open(dt):
    """Verifie si les marches asiatiques (Tokyo, Hong Kong) sont ouverts."""
    dt_tokyo = _ensure_aware(dt).astimezone(ZoneInfo("Asia/Tokyo"))
    dt_hk = dt_tokyo.astimezone(ZoneInfo("Asia/Hong_Kong"))

    # Week-end (Tokyo ou HK)
    if dt_tokyo.weekday() >= 5:
        return False

    month = dt_tokyo.month
    day = dt_tokyo.day

    # Jours feries communs (simplifie)
    # Note: Chaque marche a ses propres feries, ceci est une approximation
    common_holidays = [
        (1, 1),   # Nouvel An
        (12, 25), # Noel (Hong Kong)
    ]

    if (month, day) in common_holidays:
        return False

    # Horaires approximatifs:
    # - Tokyo: 09:00-15:00 JST (sans pause dejeuner pour simplifier)
    # - Hong Kong: 09:30-16:00 HKT
    tokyo_open = _within_session(dt_tokyo, 9, 0, 15, 0)
    hk_open = _within_session(dt_hk, 9, 30, 16, 0)
    return tokyo_open or hk_open


def get_open_markets(dt):
    """Retourne la liste des marches ouverts a la date donnee."""
    open_markets = []

    if is_us_market_open(dt):
        open_markets.append("US")
    if is_europe_market_open(dt):
        open_markets.append("Europe")
    if is_asia_market_open(dt):
        open_markets.append("Asie")

    return open_markets


def is_market_open(dt):
    """
    Verifie si AU MOINS UN marche majeur est ouvert.
    Retourne True si US, Europe, ou Asie est ouvert.
    """
    open_markets = get_open_markets(dt)
    return len(open_markets) > 0


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "ibkr_grok_pipeline.py")

    print("Bot de trading MasounIA - Execution toutes les heures")
    print("Appuyez sur Ctrl+C pour arreter")
    print("-" * 60)

    try:
        while True:
            now = datetime.now(timezone.utc)

            # Verifie quels marches sont ouverts
            open_markets = get_open_markets(now)

            if not open_markets:
                print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Tous les marches fermes (week-end ou jour ferie)")
                print("Le bot ne sera pas execute")

                # Calcule la prochaine execution
                next_run = datetime.now() + timedelta(hours=1)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Prochaine verification: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"En attente pendant 1 heure...")

                # Attendre 1 heure
                time.sleep(3600)
                continue

            # Affiche quels marches sont ouverts
            markets_str = ", ".join(open_markets)
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Lancement du bot...")
            print(f"Marches ouverts: {markets_str}")

            # Lance le pipeline avec soumission automatique
            cmd = [sys.executable, pipeline, "--submit"]
            result = subprocess.run(cmd)

            if result.returncode == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Bot execute avec succes")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Bot termine avec erreur (code: {result.returncode})")

            # Calcule la prochaine execution
            next_run = datetime.now() + timedelta(hours=1)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Prochaine execution: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"En attente pendant 1 heure...")

            # Attendre 1 heure (3600 secondes)
            time.sleep(3600)

    except KeyboardInterrupt:
        print("\n\nArret du bot demande par l'utilisateur (Ctrl+C)")
        print("Au revoir!")

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
