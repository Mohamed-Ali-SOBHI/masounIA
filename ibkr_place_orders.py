#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys

from ibkr_shared import load_dotenv, read_json, write_json


def normalize_forex_symbol(symbol):
    compact = symbol.upper().replace("/", "").replace(".", "")
    if len(compact) == 6:
        return compact
    return symbol.upper()


def is_valid_number(value):
    if value is None:
        return False
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    return not (math.isnan(num) or math.isinf(num))


def first_valid(*values):
    for value in values:
        if is_valid_number(value):
            return float(value)
    return None


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text.upper()


ALLOWED_SEC_TYPES = {"STK", "ETF"}
ALLOWED_EXCHANGES = {"", "SMART"}
ALLOWED_ORDER_TYPES = {"LMT", "LIMIT"}  # uniquement des ordres limit
BANNED_US_ETF_TICKERS = {
    # ETF US classiques à bloquer (non UCITS)
    "SPY",
    "QQQ",
    "VOO",
    "IWM",
    "DIA",
    "XLK",
    "XLF",
    "XLY",
    "XLP",
    "XLI",
    "XLE",
    "XLV",
    "XLU",
    "XLC",
    "XLB",
}


def find_position(spec, positions):
    symbol = normalize_text(spec.get("symbol"))
    currency = normalize_text(spec.get("currency"))
    sec_type = normalize_text(spec.get("security_type"))
    exchange = normalize_text(spec.get("exchange"))

    candidates = [p for p in positions if normalize_text(p.get("symbol")) == symbol]
    if currency:
        candidates = [p for p in candidates if normalize_text(p.get("currency")) == currency]
    if sec_type:
        candidates = [p for p in candidates if normalize_text(p.get("security_type")) == sec_type]
    # Only filter by exchange if the position has a non-empty exchange
    if exchange:
        candidates = [p for p in candidates if not normalize_text(p.get("exchange")) or normalize_text(p.get("exchange")) == exchange]

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    exchanges = sorted({normalize_text(p.get("exchange")) for p in candidates if p.get("exchange")})
    raise ValueError(
        f"Ambiguous position for {symbol}. Specify exchange. Matches: {', '.join(exchanges)}"
    )


def validate_sell_quantity(spec, positions, pending_sells=None):
    if normalize_text(spec.get("action")) != "SELL":
        return
    if positions is None:
        raise ValueError("SELL order requires positions data.")
    position = find_position(spec, positions)
    if position is None:
        raise ValueError(f"SELL position not found for {spec.get('symbol')}.")
    held = position.get("position")
    if not is_valid_number(held) or float(held) <= 0:
        raise ValueError(f"No held quantity for {spec.get('symbol')}.")
    quantity = float(spec.get("quantity", 0))
    # Retirer les ventes deja en attente pour eviter de passer en short
    pending_qty = 0.0
    if pending_sells is not None:
        key = (
            normalize_text(spec.get("symbol")),
            normalize_text(spec.get("currency")),
            normalize_text(spec.get("security_type") or "STK"),
            normalize_text(spec.get("exchange")) or "SMART",
        )
        pending_qty = pending_sells.get(key, 0.0)
    available = float(held) - float(pending_qty)
    if available < 0:
        available = 0
    if quantity > float(held) + 1e-9:
        raise ValueError(
            f"SELL quantity {quantity} exceeds held {held} for {spec.get('symbol')}."
        )
    if quantity > available + 1e-9:
        raise ValueError(
            f"SELL quantity {quantity} exceeds available after pending sells "
            f"({available} <= held {held} - pending {pending_qty}) for {spec.get('symbol')}."
        )


def get_reference_price(ib, contract, wait_seconds):
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(wait_seconds)
    price = first_valid(
        ticker.last,
        ticker.close,
        ticker.marketPrice(),
        ticker.bid,
        ticker.ask,
    )
    ib.cancelMktData(contract)
    return price


def get_fx_rate(ib, currency, wait_seconds, cache):
    ccy = normalize_text(currency)
    if not ccy or ccy == "EUR":
        return 1.0
    if ccy in cache:
        return cache[ccy]

    from ib_insync import Forex

    contract = Forex(f"EUR{ccy}")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        return None
    price = get_reference_price(ib, qualified[0], wait_seconds)
    cache[ccy] = price
    return price


def convert_to_eur(ib, amount, currency, wait_seconds, cache):
    rate = get_fx_rate(ib, currency, wait_seconds, cache)
    if rate is None:
        return None
    if rate == 0:
        return None
    return float(amount) / float(rate)


def validate_order_spec(spec):
    missing = []
    for key in ("symbol", "action", "quantity", "order_type", "currency"):
        if key not in spec or spec[key] in (None, ""):
            missing.append(key)
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")
    try:
        quantity = float(spec.get("quantity"))
    except (TypeError, ValueError):
        raise ValueError("Invalid quantity")
    if quantity <= 0:
        raise ValueError("Quantity must be > 0")

    order_type = normalize_text(spec.get("order_type"))
    if order_type not in ALLOWED_ORDER_TYPES:
        allowed = ", ".join(sorted(ALLOWED_ORDER_TYPES))
        raise ValueError(f"order_type {order_type or 'UNKNOWN'} not allowed. Use: {allowed}")


def build_pending_sells_map(positions_data):
    pending_sells = {}
    if not isinstance(positions_data, dict):
        return pending_sells
    for order in positions_data.get("pending_orders", []) or []:
        if normalize_text(order.get("action")) != "SELL":
            continue
        key = (
            normalize_text(order.get("symbol")),
            normalize_text(order.get("currency")),
            normalize_text(order.get("security_type") or "STK"),
            normalize_text(order.get("exchange")) or "SMART",
        )
        qty = order.get("quantity", 0) or 0
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            continue
        pending_sells[key] = pending_sells.get(key, 0.0) + qty
    return pending_sells


def validate_instrument(spec):
    sec_type = str(spec.get("security_type") or "STK").upper()
    if sec_type not in ALLOWED_SEC_TYPES:
        raise ValueError(
            f"security_type {sec_type} not allowed. Allowed: {', '.join(sorted(ALLOWED_SEC_TYPES))}"
        )
    exchange = normalize_text(spec.get("exchange"))
    if exchange and exchange not in ALLOWED_EXCHANGES:
        raise ValueError(
            f"exchange {exchange} not allowed. Use SMART or leave empty for SMART routing."
        )

    # ETF: blocage explicite des tickers US non-UCITS
    if sec_type == "ETF":
        symbol = normalize_text(spec.get("symbol"))
        # Liste blanche optionnelle via env (UCITS_ETF_WHITELIST=VEUR,CSND,...)
        whitelist_env = os.getenv("UCITS_ETF_WHITELIST", "")
        whitelist = {s.strip().upper() for s in whitelist_env.split(",") if s.strip()}
        if whitelist and symbol not in whitelist:
            raise ValueError(
                f"ETF {symbol} not in UCITS whitelist (env UCITS_ETF_WHITELIST). Add it explicitly to proceed."
            )
        if symbol in BANNED_US_ETF_TICKERS:
            raise ValueError(f"ETF {symbol} is blocked (US ETF, non-UCITS).")


def build_contract(spec):
    from ib_insync import CFD, Contract, Crypto, Forex, Stock

    sec_type = str(spec.get("security_type") or "STK").upper()
    symbol = str(spec["symbol"]).upper()
    exchange = spec.get("exchange")
    currency = spec.get("currency")

    # Enforce whitelist for instruments/exchange
    validate_instrument(spec)

    safe_exchange = exchange or "SMART"
    safe_currency = currency or "USD"

    if sec_type == "STK":
        return Stock(symbol, safe_exchange, safe_currency)
    if sec_type == "ETF":
        # ETFs need explicit Contract with secType="ETF"
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "ETF"
        contract.exchange = safe_exchange
        contract.currency = safe_currency
        return contract

    # Should not reach here due to whitelist, keep fallback defensive
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.exchange = safe_exchange
    contract.currency = safe_currency
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
        "--positions",
        help="Path to IBKR positions JSON for sell validation.",
    )
    parser.add_argument(
        "--budget-eur",
        type=float,
        help="Override budget in EUR for buy total checks.",
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
        "--enriched-out",
        help="Write enriched orders JSON with computed prices.",
    )
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
    positions_data = None
    positions_list = None
    budget_eur = args.budget_eur
    pending_sells_map = {}
    if args.positions:
        positions_data = read_json(args.positions)
        if isinstance(positions_data, dict):
            positions_list = positions_data.get("positions")
            pending_sells_map = build_pending_sells_map(positions_data)
            if budget_eur is None and is_valid_number(positions_data.get("budget_eur")):
                budget_eur = float(positions_data.get("budget_eur"))
        if not isinstance(positions_list, list):
            print("Positions JSON missing positions list.", file=sys.stderr)
            return 2
    if budget_eur is None and is_valid_number(data.get("budget_eur")):
        budget_eur = float(data.get("budget_eur"))

    orders = data.get("orders", [])
    if not isinstance(orders, list) or not orders:
        print("No orders found in JSON.", file=sys.stderr)
        return 2

    for spec in orders:
        validate_order_spec(spec)
        try:
            validate_instrument(spec)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if normalize_text(spec.get("action")) == "SELL" and positions_list is None:
            print("SELL orders require --positions for validation.", file=sys.stderr)
            return 2
        if positions_list is not None:
            try:
                validate_sell_quantity(spec, positions_list, pending_sells_map)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2

    needs_prices = any(
        normalize_text(spec.get("order_type")) in ("LMT", "LIMIT")
        and spec.get("limit_price") is None
        for spec in orders
    )

    do_connect = args.check or args.submit
    if not do_connect:
        if needs_prices:
            print(
                "Limit orders without limit_price require --check or --submit.",
                file=sys.stderr,
            )
            return 2
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
    fx_cache = {}
    total_buy_eur = 0.0
    qualified_orders = []

    for idx, spec in enumerate(orders, start=1):
        contract = build_contract(spec)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"Order {idx}: contract not qualified", file=sys.stderr)
            ib.disconnect()
            return 2

        qualified_contract = qualified[0]
        order_type = normalize_text(spec.get("order_type"))
        action = normalize_text(spec.get("action"))
        limit_price = spec.get("limit_price")
        ref_price = None

        if order_type in ("LMT", "LIMIT") and limit_price is None:
            ref_price = get_reference_price(ib, qualified_contract, args.md_wait)
            if ref_price is None:
                print(f"Order {idx}: no market data for {spec['symbol']}", file=sys.stderr)
                ib.disconnect()
                return 2
            buffer = args.limit_buffer_bps / 10000.0
            if action == "BUY":
                limit_price = ref_price * (1 + buffer)
            else:
                limit_price = ref_price * (1 - buffer)
            spec["limit_price"] = round(limit_price, 6)

        if action == "BUY":
            price_for_value = spec.get("limit_price")
            if price_for_value is None:
                if ref_price is None:
                    ref_price = get_reference_price(ib, qualified_contract, args.md_wait)
                price_for_value = ref_price
            if price_for_value is None:
                print(f"Order {idx}: unable to price {spec['symbol']}", file=sys.stderr)
                ib.disconnect()
                return 2
            order_value = float(spec["quantity"]) * float(price_for_value)
            eur_value = convert_to_eur(
                ib, order_value, spec.get("currency"), args.md_wait, fx_cache
            )
            if eur_value is None:
                print(
                    f"Order {idx}: missing FX rate for {spec.get('currency')}",
                    file=sys.stderr,
                )
                ib.disconnect()
                return 2
            total_buy_eur += eur_value

        qualified_orders.append((idx, spec, qualified_contract))

    if budget_eur is not None:
        budget_eur = float(budget_eur)
        budget_cap = budget_eur * 0.80
        data["budget_eur"] = budget_eur
        data["estimated_total_eur"] = round(total_buy_eur, 2)
        data["budget_cap_eur"] = round(budget_cap, 2)
        if total_buy_eur > budget_cap + 0.01:
            print(
                f"Total BUY {total_buy_eur:.2f} EUR exceeds 80% budget cap {budget_cap:.2f} EUR "
                f"(budget {budget_eur:.2f} EUR).",
                file=sys.stderr,
            )
            ib.disconnect()
            return 2

    if args.enriched_out:
        data["orders"] = orders
        write_json(data, args.enriched_out)

    for idx, spec, qualified_contract in qualified_orders:
        ib_order = build_order(spec, args.account)
        if args.submit:
            trade = ib.placeOrder(qualified_contract, ib_order)
            trades.append(trade)
            print(f"Order {idx}: submitted {spec['action']} {spec['symbol']}")
        else:
            print(f"Order {idx}: qualified {spec['symbol']}")

    if args.submit:
        ib.sleep(3)
        for idx, trade in enumerate(trades, start=1):
            status = trade.orderStatus.status
            order_id = trade.order.orderId
            print(f"Order {idx}: id={order_id} status={status}")

    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
