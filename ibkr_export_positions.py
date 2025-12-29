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


def get_account_value(summary, tag, currency):
    """Get a specific account value by tag and currency."""
    for entry in summary:
        if entry["tag"] == tag and entry["currency"] == currency:
            return entry.get("value")
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
        from ib_insync import IB, Forex
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

    positions = []
    has_short_positions = False
    for item in portfolio_items:
        if account and item.account != account:
            continue
        contract = item.contract

        # Calculer le pourcentage P&L
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

    # Récupérer le taux de change EUR/USD implicite utilisé par IBKR
    # en comparant les valeurs CashBalance en EUR et USD
    fx_rate_usd_to_eur = None
    cash_usd = get_account_value(summary, "CashBalance", "USD")
    cash_eur_from_usd = get_account_value(summary, "CashBalance", "EUR")

    # Si on a des valeurs EUR et USD, calculer le taux implicite
    if cash_usd and cash_usd != 0 and cash_eur_from_usd and cash_eur_from_usd != 0:
        # Le taux est approximé par la proportion des cash balances
        # Note: Ceci est une approximation car CashBalance peut inclure plusieurs devises
        fx_rate_usd_to_eur = cash_eur_from_usd / cash_usd if cash_usd != 0 else None

    # Récupérer les ordres en attente (submitted mais pas filled)
    open_trades = ib.openTrades()
    pending_value_eur = 0.0
    pending_value_by_currency = {}

    for trade in open_trades:
        order_status = trade.orderStatus.status
        # Statuts considérés comme "en attente" (pas encore remplis)
        pending_statuses = ['PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel']

        if order_status in pending_statuses and trade.order.action == 'BUY':
            # Calculer la valeur estimée de l'ordre en attente
            quantity = trade.order.totalQuantity
            contract = trade.contract

            # Essayer d'obtenir le prix de l'ordre (limit price ou market price)
            if hasattr(trade.order, 'lmtPrice') and trade.order.lmtPrice:
                price = trade.order.lmtPrice
            else:
                # Si pas de limit price, récupérer le market price actuel
                ib.qualifyContracts(contract)
                ticker = ib.reqMktData(contract)
                ib.sleep(1.0)
                price = ticker.marketPrice() if ticker.marketPrice() else ticker.last
                ib.cancelMktData(contract)

            if price and price > 0:
                order_value = quantity * price
                order_currency = contract.currency

                # Accumuler par devise
                if order_currency not in pending_value_by_currency:
                    pending_value_by_currency[order_currency] = 0.0
                pending_value_by_currency[order_currency] += order_value

    # Convertir les ordres en attente vers EUR
    for order_currency, value in pending_value_by_currency.items():
        if order_currency == currency:  # currency = EUR (budget_currency)
            pending_value_eur += value
        elif order_currency == 'USD' and fx_rate_usd_to_eur:
            # Utiliser le taux implicite IBKR
            pending_value_eur += value * fx_rate_usd_to_eur
        elif order_currency == 'USD':
            # Fallback: approximation standard si pas de taux disponible
            pending_value_eur += value / 1.05
        # Ajouter d'autres devises si nécessaire

    if pending_value_eur > 0:
        print(f"INFO: Ordres en attente detectes - Valeur totale: {pending_value_eur:.2f} EUR", file=sys.stderr)

    # Calculer le budget safe (le plus conservateur entre TotalCash et AvailableFunds)
    # PUIS soustraire la valeur des ordres en attente
    # Si TotalCash est négatif, on est en marge - budget = 0
    # Sinon, on prend le minimum entre TotalCash et AvailableFunds
    budget_safe = 0.0
    if total_cash is not None and total_cash > 0:
        if available_funds is not None and available_funds > 0:
            budget_safe = min(total_cash, available_funds)
        else:
            budget_safe = total_cash
    elif available_funds is not None and available_funds > 0:
        # Cas où total_cash est négatif ou None mais AvailableFunds est positif
        # On reste conservateur et on met budget à 0 si cash négatif
        if total_cash is None or total_cash >= 0:
            budget_safe = available_funds
        else:
            budget_safe = 0.0

    # Soustraire la valeur des ordres en attente du budget safe
    budget_safe = max(0.0, budget_safe - pending_value_eur)

    if pending_value_eur > 0:
        print(f"INFO: Budget ajuste - Budget safe apres ordres en attente: {budget_safe:.2f} EUR", file=sys.stderr)

    output = {
        "account": account or "",
        "as_of": iso_utc_now(),
        "net_liquidation": net_liquidation,  # NAV - Valeur totale du compte
        "total_cash": total_cash,  # Cash disponible (peut être négatif si marge)
        "available_funds": available_funds,  # Fonds disponibles pour trader
        "pending_orders_value": pending_value_eur,  # Valeur des ordres en attente (EUR)
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
