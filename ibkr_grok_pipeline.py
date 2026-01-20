#!/usr/bin/env python3
"""Pipeline runner.

Runs a full cycle: export IBKR positions -> call Grok -> write orders.json ->
optionally qualify/submit orders -> send notification -> write audit logs.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ibkr_shared import get_open_markets, load_dotenv, read_json, utc_now_iso, write_json
from notifications import alert_execution_summary


def script_path(name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, name)


def run_command(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export IBKR positions, call Grok, write orders.json."
    )
    parser.add_argument(
        "--query",
        default=(
            "Analyse les news des dernieres 48-72h et propose des trades bases sur les catalyseurs actuels."
        ),
        help="User task or question for Grok (default: analyze recent news).",
    )
    parser.add_argument("--out", default="orders.json", help="Output JSON path.")
    parser.add_argument("--positions-out", help="Optional path to save exported positions JSON.")
    parser.add_argument(
        "--model",
        default="grok-4-1-fast-reasoning",
        help="Model name (default: grok-4-1-fast-reasoning).",
    )
    parser.add_argument(
        "--base-url",
        default="https://api.x.ai/v1",
        help="xAI API base URL.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Request timeout in seconds (default: 3600s = 1h for reasoning models).",
    )
    parser.add_argument("--raw", action="store_true", help="Print Grok output.")
    parser.add_argument(
        "--audit-dir",
        default=os.getenv("IBKR_AUDIT_DIR", "audit"),
        help="Directory to store audit logs (default: audit).",
    )
    parser.add_argument("--no-audit", action="store_true", help="Disable audit logs.")
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
    return parser


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

    # Email disabled: errors are logged in audit/ only.


def init_audit(args):
    if args.no_audit:
        return None, None
    audit_dir = make_audit_dir(args.audit_dir)
    audit_payload = {
        "run_id": os.path.basename(audit_dir),
        "started_at": utc_now_iso(),
        "query": args.query,
        "args": vars(args),
        "status": "running",
    }
    return audit_dir, audit_payload


def ensure_markets_open(audit_payload):
    now = datetime.now(timezone.utc)
    open_markets = get_open_markets(now)

    if audit_payload is not None:
        audit_payload["markets_open"] = open_markets
        audit_payload["checked_markets_at"] = now.isoformat()

    if open_markets:
        return True

    msg = "Tous les marches sont fermes (week-end ou jour ferie) - pipeline arrete."
    print(msg)
    if audit_payload is not None:
        audit_payload["status"] = "skipped_markets_closed"
        audit_payload["reason"] = msg
    return False


def ensure_positions_path(positions_out):
    temp_path = None
    positions_path = positions_out
    if not positions_path:
        fd, temp_path = tempfile.mkstemp(prefix="ibkr_positions_", suffix=".json")
        os.close(fd)
        positions_path = temp_path
    return positions_path, temp_path


def build_export_cmd(args, positions_path):
    cmd = [
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
        "--budget-tag",
        args.budget_tag,
        "--budget-currency",
        args.budget_currency,
    ]
    if args.account:
        cmd.extend(["--account", args.account])
    return cmd


def build_grok_cmd(args, positions_path, messages_path=None):
    cmd = [
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
    if messages_path:
        cmd.extend(["--dump-messages", messages_path])
    return cmd


def build_place_cmd(args, orders_path, positions_path, enriched_out=None):
    cmd = [
        sys.executable,
        script_path("ibkr_place_orders.py"),
        orders_path,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--client-id",
        str(args.client_id),
        "--positions",
        positions_path,
        "--limit-buffer-bps",
        str(args.limit_buffer_bps),
        "--md-wait",
        str(args.md_wait),
    ]
    if enriched_out:
        cmd.extend(["--enriched-out", enriched_out])
    if args.account:
        cmd.extend(["--account", args.account])
    if args.submit:
        cmd.append("--submit")
    else:
        cmd.append("--check")
    return cmd


def read_positions(positions_path, audit_dir=None, audit_payload=None, args=None):
    positions_data = read_json(positions_path)
    if audit_payload is not None and audit_dir and args is not None:
        audit_payload["positions_path"] = positions_path
        audit_payload["positions"] = positions_data
        write_json(positions_data, os.path.join(audit_dir, "positions.json"))
        write_json(vars(args), os.path.join(audit_dir, "pipeline_args.json"))
    return positions_data


def log_margin_warning(positions_data, audit_payload=None):
    if not isinstance(positions_data, dict):
        return
    using_margin = positions_data.get("using_margin", False)
    total_cash = positions_data.get("total_cash")
    if not (using_margin or (total_cash is not None and total_cash < 0)):
        return

    print("=" * 60, file=sys.stderr)
    print("ALERTE MARGE - Cash negatif detecte", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if total_cash is not None:
        print(f"Cash actuel: {total_cash:,.2f} EUR (NEGATIF!)", file=sys.stderr)
        print(f"Montant a recuperer: {abs(total_cash):,.2f} EUR", file=sys.stderr)
    print("", file=sys.stderr)
    print("Le bot va proposer des VENTES pour corriger la situation.", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    if audit_payload is not None:
        audit_payload["margin_call_mode"] = True
        audit_payload["margin_amount"] = abs(total_cash) if total_cash else 0


def write_audit_json(audit_dir, audit_payload):
    if audit_payload is None or not audit_dir:
        return
    audit_payload["ended_at"] = utc_now_iso()
    try:
        write_json(audit_payload, os.path.join(audit_dir, "audit.json"))
    except Exception as exc:
        print(f"Failed to write audit.json: {exc}", file=sys.stderr)


def main():
    load_dotenv(".env")

    args = build_arg_parser().parse_args()
    audit_dir, audit_payload = init_audit(args)

    if not ensure_markets_open(audit_payload):
        write_audit_json(audit_dir, audit_payload)
        return 0

    positions_path, temp_path = ensure_positions_path(args.positions_out)

    try:

        export_cmd = build_export_cmd(args, positions_path)
        export_result = run_command(export_cmd)
        record_cmd(audit_payload, "export", export_result)
        if export_result.returncode != 0:
            if export_result.stdout.strip():
                print(export_result.stdout.strip())
            if export_result.stderr.strip():
                print(export_result.stderr.strip(), file=sys.stderr)
            record_error(audit_payload, "export_positions_failed")
            return export_result.returncode or 2

        positions_data = read_positions(
            positions_path,
            audit_dir=audit_dir,
            audit_payload=audit_payload,
            args=args,
        )
        log_margin_warning(positions_data, audit_payload=audit_payload)

        messages_path = os.path.join(audit_dir, "grok_messages.json") if audit_dir else None
        grok_cmd = build_grok_cmd(args, positions_path, messages_path=messages_path if audit_dir else None)
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

        if audit_payload is not None and audit_dir:
            audit_payload["grok_output_raw"] = output
            with open(
                os.path.join(audit_dir, "grok_output_raw.json"),
                "w",
                encoding="utf-8",
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
        if audit_payload is not None and audit_dir:
            audit_payload["orders_path"] = args.out
            audit_payload["grok_output_parsed"] = parsed
            write_json(parsed, os.path.join(audit_dir, "orders.json"))

        if args.check or args.submit:
            enriched_out = (
                os.path.join(audit_dir, "orders_enriched.json") if audit_dir else None
            )
            place_cmd = build_place_cmd(
                args,
                orders_path=args.out,
                positions_path=positions_path,
                enriched_out=enriched_out,
            )
            place_result = run_command(place_cmd)
            record_cmd(audit_payload, "place", place_result)
            if place_result.stdout.strip():
                print(place_result.stdout.strip())
            if place_result.stderr.strip():
                print(place_result.stderr.strip(), file=sys.stderr)
            if place_result.returncode != 0:
                record_error(audit_payload, "place_orders_failed")
                return place_result.returncode or 2

            if audit_payload is not None and audit_dir and enriched_out:
                if os.path.isfile(enriched_out):
                    audit_payload["orders_enriched"] = read_json(enriched_out)

        orders_placed_count = len(parsed.get("orders", [])) if args.submit else None
        alert_execution_summary(
            parsed,
            positions_data,
            orders_placed=orders_placed_count,
        )

        if audit_payload is not None:
            audit_payload["status"] = "ok"
        return 0
    finally:
        write_audit_json(audit_dir, audit_payload)
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    raise SystemExit(main())
