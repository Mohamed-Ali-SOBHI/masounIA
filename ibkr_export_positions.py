#!/usr/bin/env python3
import argparse
import math
import os
import sys
from datetime import datetime, timezone

from ibkr_shared import load_dotenv, write_json


def to_number(value):
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def iso_utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_account_summary(ib, account):
    items = ib.accountSummary(account) if account else ib.accountSummary()
    summary = []
    for item in items:
        if account and item.account != account:
            continue
        summary.append(
            {
                "account": item.account,
                "tag": item.tag,
                "value": to_number(item.value),
                "currency": item.currency,
            }
        )
    return summary


def select_budget(summary, tag, currency):
    for entry in summary:
        if entry["tag"] == tag and entry["currency"] == currency:
            return entry
    return None


def main():
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Export IBKR portfolio positions to JSON for Grok."
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
        "--out",
        default="-",
        help="Output path for JSON (default stdout).",
    )
    args = parser.parse_args()

    try:
        from ib_insync import IB
    except Exception:
        print("Missing ib_insync. Install with: pip install ib_insync", file=sys.stderr)
        return 2

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id)

    account = args.account
    if not account:
        accounts = ib.managedAccounts()
        if len(accounts) == 1:
            account = accounts[0]
        elif accounts:
            print("Multiple accounts found, use --account.", file=sys.stderr)
            ib.disconnect()
            return 2

    summary = read_account_summary(ib, account)
    budget_entry = select_budget(summary, args.budget_tag, args.budget_currency)
    if not budget_entry or budget_entry["value"] is None:
        print(
            f"Budget not found for tag {args.budget_tag} currency {args.budget_currency}.",
            file=sys.stderr,
        )
        ib.disconnect()
        return 2

    ib.sleep(args.wait)
    portfolio_items = ib.portfolio()

    if not portfolio_items:
        print(
            "Warning: Portfolio is empty. Increase --wait if you have positions.",
            file=sys.stderr,
        )

    positions = []
    for item in portfolio_items:
        if account and item.account != account:
            continue
        contract = item.contract
        positions.append(
            {
                "account": item.account,
                "conid": contract.conId,
                "symbol": contract.symbol,
                "local_symbol": contract.localSymbol,
                "security_type": contract.secType,
                "exchange": contract.exchange,
                "currency": contract.currency,
                "position": to_number(item.position),
                "avg_cost": to_number(item.averageCost),
                "market_price": to_number(item.marketPrice),
                "market_value": to_number(item.marketValue),
                "unrealized_pnl": to_number(item.unrealizedPNL),
                "realized_pnl": to_number(item.realizedPNL),
            }
        )

    output = {
        "account": account or "",
        "as_of": iso_utc_now(),
        "budget_eur": budget_entry["value"],
        "budget_currency": budget_entry["currency"],
        "budget_tag": budget_entry["tag"],
        "positions": positions,
    }

    write_json(output, args.out)
    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
