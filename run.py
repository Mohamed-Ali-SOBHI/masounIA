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
from datetime import datetime, timedelta

# Charger les variables d'environnement
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ibkr_shared import load_dotenv

load_dotenv(".env")


def is_us_market_open(dt):
    """Verifie si les marches US (NYSE, NASDAQ) sont ouverts."""
    # Week-end
    if dt.weekday() >= 5:  # 5=samedi, 6=dimanche
        return False

    year = dt.year
    month = dt.month
    day = dt.day

    # Jours feries fixes
    fixed_holidays = [
        (1, 1),   # Nouvel An
        (7, 4),   # Independence Day
        (12, 25), # Noel
    ]

    if (month, day) in fixed_holidays:
        return False

    # MLK Day - 3eme lundi de janvier
    if month == 1 and dt.weekday() == 0:  # Lundi
        if 15 <= day <= 21:
            return False

    # Presidents Day - 3eme lundi de fevrier
    if month == 2 and dt.weekday() == 0:  # Lundi
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
    if month == 5 and dt.weekday() == 0:  # Lundi
        if day >= 25:  # Dernier lundi est toujours >= 25
            return False

    # Labor Day - 1er lundi de septembre
    if month == 9 and dt.weekday() == 0:  # Lundi
        if day <= 7:  # Premier lundi est toujours <= 7
            return False

    # Thanksgiving - 4eme jeudi de novembre
    if month == 11 and dt.weekday() == 3:  # Jeudi
        if 22 <= day <= 28:  # 4eme jeudi est toujours dans cette plage
            return False

    return True


def is_europe_market_open(dt):
    """Verifie si les marches europeens (Euronext, Xetra, SIX) sont ouverts."""
    # Week-end
    if dt.weekday() >= 5:
        return False

    year = dt.year
    month = dt.month
    day = dt.day

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

    return True


def is_asia_market_open(dt):
    """Verifie si les marches asiatiques (Tokyo, Hong Kong) sont ouverts."""
    # Week-end
    if dt.weekday() >= 5:
        return False

    month = dt.month
    day = dt.day

    # Jours feries communs (simplifie)
    # Note: Chaque marche a ses propres feries, ceci est une approximation
    common_holidays = [
        (1, 1),   # Nouvel An
        (12, 25), # Noel (Hong Kong)
    ]

    if (month, day) in common_holidays:
        return False

    return True


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
            now = datetime.now()

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
