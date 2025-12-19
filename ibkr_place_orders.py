#!/usr/bin/env python3
import argparse
import json
import os
import sys

from ibkr_shared import load_dotenv, read_json


def normalize_forex_symbol(symbol):
    compact = symbol.upper().replace("/", "").replace(".", "")
    if len(compact) == 6:
        return compact
    return symbol.upper()


def validate_order_spec(spec):
    missing = []
    for key in ("symbol", "action", "quantity", "order_type", "currency"):
        if key not in spec or spec[key] in (None, ""):
            missing.append(key)
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")
    order_type = str(spec.get("order_type", "")).upper()
    if order_type in ("LMT", "LIMIT") and spec.get("limit_price") is None:
        raise ValueError("limit_price required for limit orders")


def build_contract(spec):
    from ib_insync import CFD, Contract, Crypto, Forex, Stock

    sec_type = str(spec.get("security_type") or "STK").upper()
    symbol = str(spec["symbol"]).upper()
    exchange = spec.get("exchange")
    currency = spec.get("currency")

    if sec_type in ("STK", "ETF"):
        return Stock(symbol, exchange or "SMART", currency or "USD")
    if sec_type in ("CASH", "FX", "FOREX"):
        pair = normalize_forex_symbol(symbol)
        return Forex(pair)
    if sec_type in ("CRYPTO", "CRYPT"):
        return Crypto(symbol, exchange or "PAXOS", currency or "USD")
    if sec_type == "CFD":
        return CFD(symbol, exchange or "SMART", currency or "USD")

    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    if exchange:
        contract.exchange = exchange
    if currency:
        contract.currency = currency
    return contract


def build_order(spec, account):
    from ib_insync import LimitOrder, MarketOrder, Order

    action = str(spec["action"]).upper()
    quantity = float(spec["quantity"])
    order_type = str(spec["order_type"]).upper()

    if order_type in ("MKT", "MARKET"):
        ib_order = MarketOrder(action, quantity)
    elif order_type in ("LMT", "LIMIT"):
        limit_price = float(spec["limit_price"])
        ib_order = LimitOrder(action, quantity, limit_price)
    else:
        ib_order = Order()
        ib_order.action = action
        ib_order.orderType = order_type
        ib_order.totalQuantity = quantity
        if spec.get("limit_price") is not None:
            ib_order.lmtPrice = float(spec["limit_price"])

    tif = spec.get("time_in_force")
    if tif:
        ib_order.tif = str(tif)
    if account:
        ib_order.account = account
    return ib_order


def main():
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Place IBKR orders from Grok JSON output."
    )
    parser.add_argument(
        "json_path",
        help="Path to JSON file (or - for stdin).",
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
        "--check",
        action="store_true",
        help="Connect to IBKR to qualify contracts without placing orders.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Place orders in IBKR (implies --check).",
    )
    args = parser.parse_args()

    data = read_json(args.json_path)

    orders = data.get("orders", [])
    if not isinstance(orders, list) or not orders:
        print("No orders found in JSON.", file=sys.stderr)
        return 2

    for spec in orders:
        validate_order_spec(spec)

    do_connect = args.check or args.submit
    if not do_connect:
        print("Dry-run: not connecting to IBKR.")
        print(json.dumps(orders, indent=2, ensure_ascii=True))
        return 0

    try:
        from ib_insync import IB
    except Exception:
        print("Missing ib_insync. Install with: pip install ib_insync", file=sys.stderr)
        return 2

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id)

    trades = []
    for idx, spec in enumerate(orders, start=1):
        contract = build_contract(spec)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"Order {idx}: contract not qualified", file=sys.stderr)
            continue

        ib_order = build_order(spec, args.account)
        if args.submit:
            trade = ib.placeOrder(qualified[0], ib_order)
            trades.append(trade)
            print(f"Order {idx}: submitted {spec['action']} {spec['symbol']}")
        else:
            print(f"Order {idx}: qualified {spec['symbol']}")

    if args.submit:
        ib.sleep(1)
        for idx, trade in enumerate(trades, start=1):
            status = trade.orderStatus.status
            order_id = trade.order.orderId
            print(f"Order {idx}: id={order_id} status={status}")

    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
