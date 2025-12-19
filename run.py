#!/usr/bin/env python3
"""
Script ultra-simple pour trading automatique avec Grok.
Lance: python run.py
"""
import os
import subprocess
import sys


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "ibkr_grok_pipeline.py")

    # Lance le pipeline avec soumission automatique
    cmd = [sys.executable, pipeline, "--submit"]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
