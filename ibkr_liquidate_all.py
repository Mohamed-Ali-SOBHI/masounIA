#!/usr/bin/env python3
"""
Liquide toutes les positions du compte IBKR.
Usage: python ibkr_liquidate_all.py [--submit]
"""
import argparse
import os
import sys

from ibkr_shared import load_dotenv


def main():
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Liquide toutes les positions IBKR."
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
        help="Seconds to wait for portfolio data.",
    )
    parser.add_argument(
        "--md-wait",
        type=float,
        default=1.5,
        help="Seconds to wait for market data per position.",
    )
    parser.add_argument(
        "--limit-buffer-bps",
        type=float,
        default=25.0,
        help="Buffer in basis points below market for limit price (default: 25 bps).",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Actually submit orders (default: dry-run only).",
    )
    args = parser.parse_args()

    try:
        from ib_insync import IB, LimitOrder
    except Exception:
        print("Missing ib_insync. Install with: pip install ib_insync", file=sys.stderr)
        return 2

    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id)

        account = args.account
        if not account:
            accounts = ib.managedAccounts()
            if len(accounts) == 1:
                account = accounts[0]
            elif accounts:
                print("Multiple accounts found, use --account.", file=sys.stderr)
                return 2

        # Wait for portfolio to populate
        ib.sleep(args.wait)
        portfolio_items = ib.portfolio()

        if not portfolio_items:
            print("No positions found. Portfolio is empty.")
            return 0

        # Filter by account if specified
        if account:
            portfolio_items = [p for p in portfolio_items if p.account == account]

        if not portfolio_items:
            print(f"No positions found for account {account}.")
            return 0

        print(f"Found {len(portfolio_items)} position(s) to liquidate:")
        print("=" * 80)

        orders_placed = []
        total_value = 0.0

        for idx, item in enumerate(portfolio_items, 1):
            contract = item.contract
            position = item.position
            market_price = item.marketPrice
            market_value = item.marketValue

            if position == 0:
                print(f"[{idx}] Skipping {contract.symbol}: position=0")
                continue

            total_value += abs(market_value)

            # Determine if LONG or SHORT position
            is_short = position < 0
            action = "BUY" if is_short else "SELL"
            quantity = abs(position)

            print(f"[{idx}] {contract.symbol} ({contract.secType})")
            print(f"     Position: {position:,.0f} shares {'(SHORT)' if is_short else '(LONG)'}")
            print(f"     Action: {action} to close position")
            print(f"     Market Price: {market_price:.2f} {contract.currency}")
            print(f"     Market Value: {market_value:,.2f} {contract.currency}")

            # Set exchange to SMART for routing
            contract.exchange = "SMART"

            # Get fresh market data for limit price
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(args.md_wait)

            # Use appropriate price based on action
            reference_price = None
            if action == "BUY":
                # For BUY orders, use ask price if available (we're buying)
                if ticker.ask and ticker.ask > 0:
                    reference_price = ticker.ask
                elif ticker.last and ticker.last > 0:
                    reference_price = ticker.last
                elif ticker.close and ticker.close > 0:
                    reference_price = ticker.close
                else:
                    reference_price = market_price
            else:
                # For SELL orders, use bid price if available (we're selling)
                if ticker.bid and ticker.bid > 0:
                    reference_price = ticker.bid
                elif ticker.last and ticker.last > 0:
                    reference_price = ticker.last
                elif ticker.close and ticker.close > 0:
                    reference_price = ticker.close
                else:
                    reference_price = market_price

            # Calculate limit price with buffer
            if action == "BUY":
                # For BUY, slightly above market for quick fill (close short)
                limit_price = reference_price * (1.0 + args.limit_buffer_bps / 10000.0)
            else:
                # For SELL, slightly below market for quick fill (close long)
                limit_price = reference_price * (1.0 - args.limit_buffer_bps / 10000.0)
            limit_price = round(limit_price, 2)

            print(f"     Reference Price: {reference_price:.2f}")
            print(f"     Limit Price: {limit_price:.2f} (buffer: {args.limit_buffer_bps} bps)")

            # Create order
            order = LimitOrder(
                action=action,
                totalQuantity=quantity,
                lmtPrice=limit_price,
            )

            if args.submit:
                trade = ib.placeOrder(contract, order)
                ib.sleep(0.5)  # Let order submit
                print(f"     Status: {trade.orderStatus.status}")
                print(f"     Order ID: {trade.order.orderId}")
                orders_placed.append({
                    "symbol": contract.symbol,
                    "action": action,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "order_id": trade.order.orderId,
                    "status": trade.orderStatus.status,
                })
            else:
                print(f"     [DRY-RUN] Would {action} {quantity} @ {limit_price}")

            print()

        print("=" * 80)
        print(f"Total positions: {len(portfolio_items)}")
        print(f"Total market value: {total_value:,.2f}")

        if args.submit:
            print(f"Orders placed: {len(orders_placed)}")
            print("\nSummary:")
            for order_info in orders_placed:
                print(f"  {order_info['symbol']}: {order_info['action']} {order_info['quantity']} @ {order_info['limit_price']} - {order_info['status']} (ID: {order_info['order_id']})")
        else:
            print("\n*** DRY-RUN MODE ***")
            print("Use --submit to actually place orders.")

        return 0

    finally:
        ib.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
