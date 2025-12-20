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


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "ibkr_grok_pipeline.py")

    print("Bot de trading MasounIA - Execution toutes les heures")
    print("Appuyez sur Ctrl+C pour arreter")
    print("-" * 60)

    try:
        while True:
            now = datetime.now()
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Lancement du bot...")

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
