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
        "--timeout", type=int, default=3600, help="Request timeout in seconds (default: 3600s = 1h for reasoning models)."
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
        News-driven trading analyst. IBKR. Budget: {budget_eur} EUR.

        CONTEXT: Bot runs hourly. Previous run: ~1h ago. Next run: in 1h.
        - Existing positions may be yours. Adjust/close based on new developments.
        - High-conviction only. No major catalysts? Return empty orders [].
        - {current_date_str}. Recent = last 24-72h ({(current_time.replace(hour=0, minute=0, second=0) - timedelta(days=3)).strftime('%d %b %Y')}-now).

        MANDATORY RESEARCH (web_search + x_search):
        1. Scan 24-72h news: earnings, regulation, central banks, geopolitics, sector trends, M&A
        2. X sentiment: market mood, analyst opinions, breaking news, retail shifts
        3. Verify: 2-3 sources, check prices/volume, find catalysts (earnings dates, events)
        4. Thesis: connect news to trades, spot over-reactions, sector rotations, contrarian plays

        STRATEGY:
        - Base on RECENT news/trends. Cite specific events per order (rationale field).
        - 2-5 day to monthly catalysts. Adapt to risk-on/off regime.
        - Review current positions: SELL on negative news. Manage concentration.
        - BUY: max 3-5 liquid positions. LIMIT orders, current prices.
        - Set appropriate time_in_force and exchange fields.
        - Use valid security_type for each instrument.

        EUROPEAN IBKR RESTRICTIONS:
        - ETFs: ONLY UCITS (European-domiciled). NO US ETFs.
        - Stocks: All markets tradeable (no restrictions).
        - Symbol: base ticker ONLY (NO exchange suffix). Exchange in separate field.
        - Currency: match instrument domicile.

        BUDGET: {budget_eur} EUR total. Max use: {budget_eur * 0.85:.2f} EUR (85%). Keep 15% buffer.

        SOURCES: 1-2 per instrument + macro context. Cite X posts if used. Insufficient data? orders=[].

        OUTPUT (French, ASCII, strict JSON):
        - summary: strategy + news drivers
        - key_points: timeline, thesis, catalysts, risks, portfolio impact, sentiment, sources
        - orders: with rationale (news/catalyst)
        - disclaimer: educational, not advice
        - JSON only, match schema, no promises.

        JSON SCHEMA:
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
