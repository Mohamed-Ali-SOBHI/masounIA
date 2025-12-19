#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile

from ibkr_shared import load_dotenv, write_json


def script_path(name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, name)


def run_command(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def main():
    load_dotenv(".env")

    parser = argparse.ArgumentParser(
        description="Export IBKR positions, call Grok, write orders.json."
    )
    parser.add_argument("query", help="User task or question for Grok.")
    parser.add_argument("--out", default="orders.json", help="Output JSON path.")
    parser.add_argument(
        "--positions-out",
        help="Optional path to save exported positions JSON.",
    )
    parser.add_argument(
        "--model", default="grok-4-1-fast", help="Model name (default: grok-4-1-fast)."
    )
    parser.add_argument(
        "--base-url", default="https://api.x.ai/v1", help="xAI API base URL."
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="Request timeout in seconds."
    )
    parser.add_argument("--raw", action="store_true", help="Print Grok output.")
    parser.add_argument("--host", default=os.getenv("IBKR_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("IBKR_PORT", "7497")),
        help="TWS paper default 7497, live default 7496, IB Gateway paper 4002.",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=int(os.getenv("IBKR_CLIENT_ID", "3")),
    )
    parser.add_argument("--account", default=os.getenv("IBKR_ACCOUNT"))
    parser.add_argument(
        "--wait",
        type=float,
        default=1.0,
        help="Seconds to wait for account updates.",
    )
    parser.add_argument(
        "--budget-tag",
        default=os.getenv("IBKR_BUDGET_TAG", "AvailableFunds"),
        help="Account summary tag to use as budget (default: AvailableFunds).",
    )
    parser.add_argument(
        "--budget-currency",
        default=os.getenv("IBKR_BUDGET_CURRENCY", "EUR"),
        help="Budget currency to select (default: EUR).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Qualify orders via IBKR without placing them.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Place orders in IBKR after Grok (implies --check).",
    )
    args = parser.parse_args()

    temp_path = None
    positions_path = args.positions_out
    if not positions_path:
        fd, temp_path = tempfile.mkstemp(prefix="ibkr_positions_", suffix=".json")
        os.close(fd)
        positions_path = temp_path

    try:
        export_cmd = [
            sys.executable,
            script_path("ibkr_export_positions.py"),
            "--out",
            positions_path,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--client-id",
            str(args.client_id),
            "--wait",
            str(args.wait),
        ]
        export_cmd.extend(["--budget-tag", args.budget_tag])
        export_cmd.extend(["--budget-currency", args.budget_currency])
        if args.account:
            export_cmd.extend(["--account", args.account])

        result = run_command(export_cmd)
        if result.returncode != 0:
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            return result.returncode or 2

        grok_cmd = [
            sys.executable,
            script_path("grok41_fast_search.py"),
            "--positions",
            positions_path,
            "--model",
            args.model,
            "--base-url",
            args.base_url,
            "--timeout",
            str(args.timeout),
            args.query,
        ]
        grok_result = run_command(grok_cmd)
        if grok_result.returncode != 0:
            if grok_result.stdout.strip():
                print(grok_result.stdout.strip())
            if grok_result.stderr.strip():
                print(grok_result.stderr.strip(), file=sys.stderr)
            return grok_result.returncode or 2

        output = grok_result.stdout.strip()
        if not output:
            print("Grok output is empty.", file=sys.stderr)
            return 1
        if args.raw:
            print(output)

        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            print("Grok output is not valid JSON.", file=sys.stderr)
            return 1

        write_json(parsed, args.out)
        print(f"Wrote {args.out}")
        if args.check or args.submit:
            place_cmd = [
                sys.executable,
                script_path("ibkr_place_orders.py"),
                args.out,
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--client-id",
                str(args.client_id),
            ]
            if args.account:
                place_cmd.extend(["--account", args.account])
            if args.submit:
                place_cmd.append("--submit")
            else:
                place_cmd.append("--check")

            place_result = run_command(place_cmd)
            if place_result.stdout.strip():
                print(place_result.stdout.strip())
            if place_result.stderr.strip():
                print(place_result.stderr.strip(), file=sys.stderr)
            if place_result.returncode != 0:
                return place_result.returncode or 2
        return 0
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    raise SystemExit(main())
