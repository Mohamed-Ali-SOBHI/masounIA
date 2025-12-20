#!/usr/bin/env python3
import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone

from ibkr_shared import load_dotenv, read_json, write_json


def extract_budget_eur(positions):
    if not isinstance(positions, dict):
        return None
    value = positions.get("budget_eur")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Call xAI Grok 4.1 Fast with web_search + x_search tools and JSON schema output."
        )
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="User question or task (default: analyze recent news and propose trades).",
    )
    parser.add_argument(
        "--model", default="grok-4-1-fast-reasoning", help="Model name (default: grok-4-1-fast-reasoning)."
    )
    parser.add_argument(
        "--base-url", default="https://api.x.ai/v1", help="xAI API base URL (ignored with SDK)."
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="Request timeout in seconds."
    )
    parser.add_argument("--raw", action="store_true", help="Print raw model output.")
    parser.add_argument(
        "--positions",
        required=True,
        help="Path to IBKR positions JSON (from ibkr_export_positions.py).",
    )
    parser.add_argument(
        "--budget-eur",
        type=float,
        help="Override budget in EUR (defaults to positions JSON if provided).",
    )
    parser.add_argument(
        "--dump-messages",
        help="Write the model messages payload to a JSON file.",
    )
    args = parser.parse_args()

    load_dotenv(".env")
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("Missing XAI_API_KEY env var. Put it in .env or export it.", file=sys.stderr)
        return 2

    if not args.query:
        args.query = "Analyse les news des dernieres 48-72h et propose des trades bases sur les catalyseurs actuels."

    positions = read_json(args.positions)
    positions_json = json.dumps(positions, ensure_ascii=True)

    if isinstance(positions, dict):
        budget_currency = positions.get("budget_currency")
        if budget_currency and budget_currency != "EUR":
            print(
                f"Positions JSON budget_currency is {budget_currency}, expected EUR.",
                file=sys.stderr,
            )
            return 2

    budget_eur = args.budget_eur
    if budget_eur is None:
        budget_from_positions = extract_budget_eur(positions)
        if budget_from_positions is None:
            print("Positions JSON missing budget_eur.", file=sys.stderr)
            return 2
        budget_eur = budget_from_positions

    if budget_eur is None:
        print("Budget must be provided via positions JSON or --budget-eur.", file=sys.stderr)
        return 2

    current_time = datetime.now(timezone.utc)
    current_date_str = current_time.strftime("%A %d %B %Y, %H:%M UTC")

    try:
        from pydantic import BaseModel, ConfigDict
    except ImportError:
        print("Missing pydantic. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    try:
        from xai_sdk import Client
        from xai_sdk.chat import system, user
        from xai_sdk.tools import web_search, x_search
    except ImportError:
        print("xai-sdk not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    class Order(BaseModel):
        model_config = ConfigDict(extra="forbid")
        symbol: str
        security_type: str | None = None
        action: str
        quantity: float
        order_type: str
        limit_price: float | None = None
        currency: str
        exchange: str | None = None
        time_in_force: str | None = None
        notes: str | None = None
        stop_loss: float | None = None
        target_price: float | None = None
        trailing_stop_percent: float | None = None
        rationale: str | None = None

    class Source(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: str
        url: str

    class OrderPlan(BaseModel):
        model_config = ConfigDict(extra="forbid")
        summary: str
        key_points: list[str]
        budget_eur: float
        estimated_total_eur: float
        orders: list[Order]
        sources: list[Source]
        disclaimer: str

    schema_json = OrderPlan.model_json_schema()

    # Build system prompt
    system_prompt = textwrap.dedent(
        f"""\
        You are a proactive investment research analyst focused on news-driven trading
        strategies. You trade via IBKR. Budget: {budget_eur} EUR.

        EXECUTION CONTEXT: This bot runs AUTOMATICALLY EVERY HOUR.
        - Your previous execution was ~1 hour ago
        - You will run again in 1 hour to reassess the situation
        - Existing positions may be from your previous recommendations
        - You can adjust/close positions in subsequent hourly runs based on new developments
        - Avoid over-trading: prioritize HIGH-CONVICTION opportunities only
        - If no major news/catalysts, it's OK to return empty orders [] and wait

        CURRENT DATE AND TIME: {current_date_str}
        Use this as reference for "recent news" (last 24-72h means since {(current_time.replace(hour=0, minute=0, second=0) - timedelta(days=3)).strftime('%d %B %Y')}).

        CRITICAL: You MUST use web_search and x_search tools extensively before proposing orders.

        Research methodology MANDATORY:
        1. Scan recent news 24-72h for market-moving events:
           - Earnings reports, product launches, regulatory changes
           - Geopolitical events, central bank decisions, economic data
           - Sector trends, technological breakthroughs, M&A activity

        2. Use x_search to gauge market sentiment on X:
           - Market sentiment and trending topics
           - Institutional analyst opinions
           - Breaking news and rumors
           - Retail investor sentiment shifts

        3. Cross-reference multiple sources:
           - Verify claims with at least 2-3 independent sources
           - Check current prices and volume trends
           - Identify potential catalysts (upcoming events, earnings dates)

        4. Build a thesis:
           - Connect news to specific trading opportunities
           - Identify oversold/overbought reactions to news
           - Spot sector rotations or thematic trends
           - Consider contrarian positions when sentiment is extreme

        Strategy requirements:
        - Base your recommendations on RECENT NEWS and CURRENT TRENDS
        - For each order, cite specific news/events that justify it
        - Identify 2-5 day, weekly, or monthly catalysts (not just long-term)
        - Adapt strategy to market regime (risk-on vs risk-off)
        - Consider momentum, reversal, and event-driven opportunities

        Portfolio management:
        - Analyze current positions for news-related risks or opportunities
        - SELL positions with negative catalysts or deteriorating fundamentals
        - SELL only what exists, never exceed held quantity
        - Rebalance based on evolving market conditions
        - Manage concentration and correlation risk

        Trading execution:
        - Prefer liquid instruments (broad ETFs, large caps, major FX pairs)
        - Limit new BUYs to 3-5 positions max for diversification
        - Use LIMIT orders with prices based on current market data
        - Set time_in_force=GTC for multi-day positions, DAY for same-day
        - Use exchange=SMART unless specific routing needed
        - security_type: STK, ETF, FX, CRYPTO, or CFD

        Budget rules:
        - Total budget available: {budget_eur} EUR
        - estimated_total_eur is sum of BUY orders in EUR
        - MUST keep 10-20% buffer: use maximum {budget_eur * 0.85:.2f} EUR ({budget_eur} * 85%)
        - Buffer ensures flexibility for adjustments and prevents full allocation risk

        Sources (MANDATORY):
        - Include at least 1-2 sources per recommended instrument
        - Add macro/sector sources for context
        - Cite X posts if used for sentiment analysis
        - If insufficient data found, set orders to [] and explain why

        Output format (French, ASCII only, strict JSON):
        - summary: strategy overview with key news drivers
        - key_points: timeline (days/weeks), thesis, catalysts, risks,
          portfolio impact, sentiment analysis, sources used
        - orders: detailed with rationale field explaining the news/catalyst
        - disclaimer: educational only, not financial advice

        Output rules:
        - JSON only, no extra text before or after
        - Must match the provided schema exactly
        - Do not promise returns or certainty

        JSON SCHEMA REQUIRED:
        {json.dumps(schema_json, indent=2, ensure_ascii=True)}
        """
    )

    messages_payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Positions IBKR (JSON): {positions_json}"},
            {"role": "user", "content": args.query},
        ],
        "tools": ["web_search", "x_search"],
        "response_format": "pydantic:OrderPlan",
    }
    if args.dump_messages:
        write_json(messages_payload, args.dump_messages)

    client = Client(api_key=api_key, timeout=args.timeout)
    chat = client.chat.create(
        model=args.model,
        tools=[web_search(), x_search()],
        response_format=OrderPlan,
        messages=[system(system_prompt)],
    )
    chat.append(user(f"Positions IBKR (JSON): {positions_json}"))
    chat.append(user(args.query))

    try:
        response = chat.sample()
        content = response.content

        if args.raw:
            print(content)
            return 0

        try:
            parsed = OrderPlan.model_validate_json(content)
        except Exception:
            print(content)
            return 1

        print(json.dumps(parsed.model_dump(), indent=2, ensure_ascii=True))
        return 0

    except Exception as exc:
        print(f"Error calling xAI API: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
