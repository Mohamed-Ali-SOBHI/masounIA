#!/usr/bin/env python3
"""Export IBKR positions and a conservative EUR budget.

This script connects to IBKR, exports current portfolio positions, and computes
a conservative trading budget (in EUR) that also accounts for pending BUY
orders.
"""

import argparse
import math
import os
import sys
from datetime import timezone

from ibkr_shared import load_dotenv, resolve_ibkr_account, utc_now_iso, write_json


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


def calculate_pnl_percent(market_price, avg_cost):
    """
    Calculate percentage profit/loss.

    Returns:
        float or None: Percentage P&L, or None if cannot calculate
    """
    if market_price is None or avg_cost is None:
        return None
    if avg_cost == 0:
        return None  # Division par zero

    pnl_percent = ((market_price - avg_cost) / avg_cost) * 100
    return round(pnl_percent, 2)  # Arrondi a 2 decimales


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


def get_account_value(summary, tag, currency):
    """Get a specific account value by tag and currency."""
    for entry in summary:
        if entry["tag"] == tag and entry["currency"] == currency:
            return entry.get("value")
    return None


PENDING_ORDER_STATUSES = {
    "PreSubmitted",
    "Submitted",
    "PendingSubmit",
    "PendingCancel",
}


def build_arg_parser() -> argparse.ArgumentParser:
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
    return parser


def compute_fx_rate_usd_to_eur(summary):
    cash_usd = get_account_value(summary, "CashBalance", "USD")
    cash_eur = get_account_value(summary, "CashBalance", "EUR")
    if cash_usd and cash_usd != 0 and cash_eur and cash_eur != 0:
        return cash_eur / cash_usd if cash_usd != 0 else None
    return None


def collect_positions(portfolio_items, account):
    positions = []
    has_short_positions = False
    for item in portfolio_items:
        if account and item.account != account:
            continue
        contract = item.contract

        avg_cost = to_number(item.averageCost)
        market_price = to_number(item.marketPrice)
        pnl_percent = calculate_pnl_percent(market_price, avg_cost)

        position_qty = to_number(item.position)
        if position_qty is not None and position_qty < 0:
            has_short_positions = True

        positions.append(
            {
                "account": item.account,
                "conid": contract.conId,
                "symbol": contract.symbol,
                "local_symbol": contract.localSymbol,
                "security_type": contract.secType,
                "exchange": contract.exchange,
                "primary_exchange": getattr(contract, "primaryExchange", "") or "",
                "currency": contract.currency,
                "position": position_qty,
                "avg_cost": avg_cost,
                "market_price": market_price,
                "market_value": to_number(item.marketValue),
                "unrealized_pnl": to_number(item.unrealizedPNL),
                "realized_pnl": to_number(item.realizedPNL),
                "pnl_percent": pnl_percent,
            }
        )
    return positions, has_short_positions


def collect_pending_orders(ib, currency, fx_rate_usd_to_eur):
    """Collect pending orders and estimate total pending BUY value in EUR."""
    pending_value_eur = 0.0
    pending_value_by_currency = {}
    pending_orders = []

    open_trades = ib.openTrades()
    for trade in open_trades:
        order_status = trade.orderStatus.status
        if order_status not in PENDING_ORDER_STATUSES:
            continue

        quantity = trade.order.totalQuantity
        contract = trade.contract
        action = trade.order.action

        if hasattr(trade.order, "lmtPrice") and trade.order.lmtPrice:
            price = trade.order.lmtPrice
        else:
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract)
            ib.sleep(1.0)
            price = ticker.marketPrice() if ticker.marketPrice() else ticker.last
            ib.cancelMktData(contract)

        pending_orders.append(
            {
                "symbol": contract.symbol,
                "action": action,
                "quantity": int(quantity),
                "status": order_status,
                "limit_price": to_number(trade.order.lmtPrice)
                if hasattr(trade.order, "lmtPrice")
                else None,
                "order_type": trade.order.orderType,
                "currency": contract.currency,
                "exchange": getattr(contract, "exchange", "") or "",
                "primary_exchange": getattr(contract, "primaryExchange", "") or "",
            }
        )

        if action == "BUY" and price and price > 0:
            order_value = quantity * price
            order_currency = contract.currency
            pending_value_by_currency[order_currency] = (
                pending_value_by_currency.get(order_currency, 0.0) + order_value
            )

    for order_currency, value in pending_value_by_currency.items():
        if order_currency == currency:
            pending_value_eur += value
        elif order_currency == "USD" and fx_rate_usd_to_eur:
            pending_value_eur += value * fx_rate_usd_to_eur
        elif order_currency == "USD":
            pending_value_eur += value / 1.05

    return pending_value_eur, pending_orders


def compute_budget_safe(total_cash, available_funds, pending_value_eur):
    """Compute a conservative EUR budget and subtract pending BUY orders."""
    budget_safe = 0.0
    if total_cash is not None and total_cash > 0:
        if available_funds is not None and available_funds > 0:
            budget_safe = min(total_cash, available_funds)
        else:
            budget_safe = total_cash
    elif available_funds is not None and available_funds > 0:
        if total_cash is None or total_cash >= 0:
            budget_safe = available_funds
        else:
            budget_safe = 0.0

    return max(0.0, budget_safe - pending_value_eur)


def main():
    load_dotenv(".env")
    args = build_arg_parser().parse_args()

    try:
        from ib_insync import IB
    except Exception:
        print("Missing ib_insync. Install with: pip install ib_insync", file=sys.stderr)
        return 2

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id)

    account, account_error = resolve_ibkr_account(ib, args.account)
    if account_error:
        print(account_error, file=sys.stderr)
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

    # Récupérer les valeurs importantes du compte
    currency = args.budget_currency
    net_liquidation = get_account_value(summary, "NetLiquidation", currency)
    total_cash = get_account_value(summary, "TotalCashValue", currency)
    available_funds = budget_entry["value"]

    # Alerter si le cash est négatif (situation de marge)
    if total_cash is not None and total_cash < 0:
        print(
            f"WARNING: Vous utilisez de la marge! Cash: {total_cash:,.2f} {currency}",
            file=sys.stderr,
        )
        print(
            f"         Vous devez vendre ~{abs(total_cash):,.2f} {currency} de positions pour être cash-only.",
            file=sys.stderr,
        )

    ib.sleep(args.wait)
    portfolio_items = ib.portfolio()
    if not portfolio_items:
        print(
            "Warning: Portfolio is empty. Increase --wait if you have positions.",
            file=sys.stderr,
        )

    positions, has_short_positions = collect_positions(portfolio_items, account)

    # Alerter si positions short détectées
    if has_short_positions:
        print(
            f"WARNING: Positions SHORT detectees dans le portfolio!",
            file=sys.stderr,
        )
        print(
            f"         Le bot est configure LONG ONLY et sera bloque.",
            file=sys.stderr,
        )

    fx_rate_usd_to_eur = compute_fx_rate_usd_to_eur(summary)
    pending_value_eur, pending_orders = collect_pending_orders(
        ib,
        currency=currency,
        fx_rate_usd_to_eur=fx_rate_usd_to_eur,
    )

    if pending_value_eur > 0:
        print(f"INFO: Ordres en attente detectes - Valeur totale: {pending_value_eur:.2f} EUR", file=sys.stderr)

    budget_safe = compute_budget_safe(total_cash, available_funds, pending_value_eur)

    if pending_value_eur > 0:
        print(f"INFO: Budget ajuste - Budget safe apres ordres en attente: {budget_safe:.2f} EUR", file=sys.stderr)

    output = {
        "account": account or "",
        "as_of": utc_now_iso(),
        "net_liquidation": net_liquidation,  # NAV - Valeur totale du compte
        "total_cash": total_cash,  # Cash disponible (peut être négatif si marge)
        "available_funds": available_funds,  # Fonds disponibles pour trader
        "pending_orders_value": pending_value_eur,  # Valeur des ordres en attente (EUR)
        "pending_orders": pending_orders,  # Liste détaillée des ordres en attente pour Grok
        "budget_safe": budget_safe,  # Budget conservateur (min entre cash et available, 0 si marge) - MOINS ordres en attente
        "using_margin": (total_cash is not None and total_cash < 0) or has_short_positions,  # Flag pour marge ou short
        "currency": currency,
        # Ancien format pour compatibilité - utiliser budget_safe
        "budget_eur": budget_safe,
        "budget_currency": budget_entry["currency"],
        "budget_tag": "SafeBudget(min(TotalCash,AvailableFunds)-PendingOrders)",
        "positions": positions,
    }

    write_json(output, args.out)
    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
