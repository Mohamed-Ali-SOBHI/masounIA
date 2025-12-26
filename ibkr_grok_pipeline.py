#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ibkr_shared import load_dotenv, read_json, write_json


def script_path(name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, name)


def run_command(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_audit_dir(base_dir):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(base_dir, stamp)
    os.makedirs(path, exist_ok=True)
    return path


def record_cmd(audit_payload, name, result):
    if audit_payload is None:
        return
    audit_payload[name] = {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def record_error(audit_payload, message):
    if audit_payload is None:
        return
    audit_payload["status"] = "error"
    audit_payload["error"] = message


def main():
    load_dotenv(".env")

    parser = argparse.ArgumentParser(
        description="Export IBKR positions, call Grok, write orders.json."
    )
    parser.add_argument(
        "--query",
        default="Analyse les news des dernieres 48-72h et propose des trades bases sur les catalyseurs actuels.",
        help="User task or question for Grok (default: analyze recent news).",
    )
    parser.add_argument("--out", default="orders.json", help="Output JSON path.")
    parser.add_argument(
        "--positions-out",
        help="Optional path to save exported positions JSON.",
    )
    parser.add_argument(
        "--model", default="grok-4-1-fast-reasoning", help="Model name (default: grok-4-1-fast-reasoning)."
    )
    parser.add_argument(
        "--base-url", default="https://api.x.ai/v1", help="xAI API base URL."
    )
    parser.add_argument(
        "--timeout", type=int, default=3600, help="Request timeout in seconds (default: 3600s = 1h for reasoning models)."
    )
    parser.add_argument("--raw", action="store_true", help="Print Grok output.")
    parser.add_argument(
        "--audit-dir",
        default=os.getenv("IBKR_AUDIT_DIR", "audit"),
        help="Directory to store audit logs (default: audit).",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Disable audit logs.",
    )
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
        default=int(os.getenv("IBKR_CLIENT_ID", "1")),
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
        "--limit-buffer-bps",
        type=float,
        default=float(os.getenv("IBKR_LIMIT_BUFFER_BPS", "25")),
        help="Limit price buffer in bps (default: 25).",
    )
    parser.add_argument(
        "--md-wait",
        type=float,
        default=float(os.getenv("IBKR_MD_WAIT", "1.5")),
        help="Seconds to wait for market data (default: 1.5).",
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

    audit_dir = None
    audit_payload = None
    if not args.no_audit:
        audit_dir = make_audit_dir(args.audit_dir)
        audit_payload = {
            "run_id": os.path.basename(audit_dir),
            "started_at": utc_now_iso(),
            "query": args.query,
            "args": vars(args),
            "status": "running",
        }

    temp_path = None
    positions_path = args.positions_out
    if not positions_path:
        fd, temp_path = tempfile.mkstemp(prefix="ibkr_positions_", suffix=".json")
        os.close(fd)
        positions_path = temp_path

    positions_data = None

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
        record_cmd(audit_payload, "export", result)
        if result.returncode != 0:
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            record_error(audit_payload, "export_positions_failed")
            return result.returncode or 2

        if audit_dir:
            positions_data = read_json(positions_path)
            audit_payload["positions_path"] = positions_path
            audit_payload["positions"] = positions_data
            write_json(positions_data, os.path.join(audit_dir, "positions.json"))
            write_json(vars(args), os.path.join(audit_dir, "pipeline_args.json"))

        # Vérifier si le compte utilise de la marge
        if positions_data is None:
            positions_data = read_json(positions_path)

        if isinstance(positions_data, dict):
            using_margin = positions_data.get("using_margin", False)
            total_cash = positions_data.get("total_cash")

            if using_margin or (total_cash is not None and total_cash < 0):
                print("=" * 60, file=sys.stderr)
                print("ALERTE MARGE - Cash negatif detecte", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
                if total_cash is not None:
                    print(f"Cash actuel: {total_cash:,.2f} EUR (NEGATIF!)", file=sys.stderr)
                    print(f"Montant a recuperer: {abs(total_cash):,.2f} EUR", file=sys.stderr)
                print("", file=sys.stderr)
                print("Le bot va proposer des VENTES pour corriger la situation.", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
                if audit_payload:
                    audit_payload["margin_call_mode"] = True
                    audit_payload["margin_amount"] = abs(total_cash) if total_cash else 0

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
        messages_path = None
        if audit_dir:
            messages_path = os.path.join(audit_dir, "grok_messages.json")
            grok_cmd.extend(["--dump-messages", messages_path])
        grok_result = run_command(grok_cmd)
        record_cmd(audit_payload, "grok", grok_result)
        if grok_result.returncode != 0:
            if grok_result.stdout.strip():
                print(grok_result.stdout.strip())
            if grok_result.stderr.strip():
                print(grok_result.stderr.strip(), file=sys.stderr)
            record_error(audit_payload, "grok_failed")
            return grok_result.returncode or 2

        output = grok_result.stdout.strip()
        if not output:
            print("Grok output is empty.", file=sys.stderr)
            record_error(audit_payload, "grok_output_empty")
            return 1
        if args.raw:
            print(output)
        if audit_dir:
            audit_payload["grok_output_raw"] = output
            with open(
                os.path.join(audit_dir, "grok_output_raw.json"), "w", encoding="utf-8"
            ) as handle:
                handle.write(output + "\n")
            if messages_path and os.path.isfile(messages_path):
                audit_payload["grok_messages"] = read_json(messages_path)

        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            print("Grok output is not valid JSON.", file=sys.stderr)
            record_error(audit_payload, "grok_output_invalid_json")
            return 1

        write_json(parsed, args.out)
        print(f"Wrote {args.out}")
        if audit_dir:
            audit_payload["orders_path"] = args.out
            audit_payload["grok_output_parsed"] = parsed
            write_json(parsed, os.path.join(audit_dir, "orders.json"))
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
            place_cmd.extend(["--positions", positions_path])
            place_cmd.extend(["--limit-buffer-bps", str(args.limit_buffer_bps)])
            place_cmd.extend(["--md-wait", str(args.md_wait)])
            if audit_dir:
                place_cmd.extend(
                    ["--enriched-out", os.path.join(audit_dir, "orders_enriched.json")]
                )
            if args.account:
                place_cmd.extend(["--account", args.account])
            if args.submit:
                place_cmd.append("--submit")
            else:
                place_cmd.append("--check")

            place_result = run_command(place_cmd)
            record_cmd(audit_payload, "place", place_result)
            if place_result.stdout.strip():
                print(place_result.stdout.strip())
            if place_result.stderr.strip():
                print(place_result.stderr.strip(), file=sys.stderr)
            if place_result.returncode != 0:
                record_error(audit_payload, "place_orders_failed")
                return place_result.returncode or 2
            if audit_dir:
                enriched_path = os.path.join(audit_dir, "orders_enriched.json")
                if os.path.isfile(enriched_path):
                    audit_payload["orders_enriched"] = read_json(enriched_path)
        if audit_payload is not None:
            audit_payload["status"] = "ok"
        return 0
    finally:
        if audit_payload is not None and audit_dir:
            audit_payload["ended_at"] = utc_now_iso()
            try:
                write_json(audit_payload, os.path.join(audit_dir, "audit.json"))
            except Exception as exc:
                print(f"Failed to write audit.json: {exc}", file=sys.stderr)
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    raise SystemExit(main())
