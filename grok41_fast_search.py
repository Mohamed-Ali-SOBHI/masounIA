#!/usr/bin/env python3
import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request

from ibkr_shared import load_dotenv, read_json


def post_json(url, headers, payload, timeout):
    try:
        import requests  # type: ignore
    except Exception:
        return post_json_urllib(url, headers, payload, timeout)

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def post_json_urllib(url, headers, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    return json.loads(body)


def extract_budget_eur(positions):
    if not isinstance(positions, dict):
        return None
    value = positions.get("budget_eur")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_schema():
    return {
        "name": "ibkr_order_plan",
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "budget_eur": {"type": "number"},
                "estimated_total_eur": {"type": "number"},
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "security_type": {"type": "string"},
                            "action": {"type": "string"},
                            "quantity": {"type": "number"},
                            "order_type": {"type": "string"},
                            "limit_price": {"type": "number"},
                            "currency": {"type": "string"},
                            "exchange": {"type": "string"},
                            "time_in_force": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": [
                            "symbol",
                            "action",
                            "quantity",
                            "order_type",
                            "currency",
                        ],
                        "additionalProperties": False,
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                        },
                        "required": ["title", "url"],
                        "additionalProperties": False,
                    },
                },
                "disclaimer": {"type": "string"},
            },
            "required": [
                "summary",
                "key_points",
                "budget_eur",
                "estimated_total_eur",
                "orders",
                "sources",
                "disclaimer",
            ],
            "additionalProperties": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Call xAI Grok 4.1 Fast for long-term trading research "
            "with web_search + x_search tools and JSON schema output."
        )
    )
    parser.add_argument("query", nargs="?", help="User question or task.")
    parser.add_argument(
        "--model", default="grok-4-1-fast", help="Model name (default: grok-4-1-fast)."
    )
    parser.add_argument(
        "--base-url", default="https://api.x.ai/v1", help="xAI API base URL."
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="Request timeout in seconds."
    )
    parser.add_argument("--raw", action="store_true", help="Print raw model output.")
    parser.add_argument(
        "--positions",
        help="Path to IBKR positions JSON (from ibkr_export_positions.py).",
    )
    parser.add_argument(
        "--budget-eur",
        type=float,
        help="Override budget in EUR (defaults to positions JSON if provided).",
    )
    args = parser.parse_args()

    load_dotenv(".env")
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("Missing XAI_API_KEY env var. Put it in .env or export it.", file=sys.stderr)
        return 2

    if not args.query:
        print("Provide a query, for example:", file=sys.stderr)
        print(
            '  python grok41_fast_search.py "Propose une these long terme et des ordres IBKR"',
            file=sys.stderr,
        )
        return 2

    positions = None
    positions_json = None
    budget_eur = args.budget_eur
    if args.positions:
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
        if budget_eur is None:
            budget_from_positions = extract_budget_eur(positions)
            if budget_from_positions is None:
                print("Positions JSON missing budget_eur.", file=sys.stderr)
                return 2
            budget_eur = budget_from_positions

    if budget_eur is None:
        budget_eur = 200.0

    messages = [
        {
            "role": "system",
            "content": textwrap.dedent(
                f"""\
                You are an investment research assistant for long-term decisions
                (months/years), not a financial advisor. The user trades via IBKR.
                Budget: {budget_eur} EUR.

                Output rules:
                - Respond in French (ASCII) and output JSON only, no extra text.
                - The JSON must match the provided schema exactly.

                Objective:
                - Use current positions + budget + recent information to propose a
                  long-term action plan (buy/sell/hold) aimed at risk-adjusted return.
                - You may select instruments. Do not promise profitability or certainty.

                Data and tools:
                - Use web_search/x_search for key claims, catalysts, and current prices.
                - For every instrument in orders, include at least one source.
                - Add macro/sector sources if used.

                Portfolio context:
                - Analyze concentration, overlap, and currency exposure.
                - SELL only positions that exist and never exceed current quantity.

                Trading constraints:
                - Prefer liquid, diversified exposures (broad ETFs or large caps)
                  unless the thesis strongly justifies otherwise.
                - Limit new BUYs to 3 max unless strongly justified.

                Order formatting:
                - action is BUY or SELL; security_type is STK, ETF, FX, CRYPTO, or CFD when known.
                - Prefer LIMIT; include limit_price for limit orders. Market only if justified.
                - Use time_in_force=GTC for long-term unless user says otherwise.
                - Use exchange=SMART if unsure; set correct currency.
                - estimated_total_eur is sum of BUY orders in EUR and <= budget_eur.
                - budget_eur must equal {budget_eur}.
                - If information is insufficient, set orders to [] and explain why.

                Output content:
                - summary: concise plan and main rationale.
                - key_points: horizon, thesis, risks, catalysts, portfolio impact,
                  assumptions, and data sources used.
                - disclaimer: educational only, verify before execution.
                """
            ),
        }
    ]

    if args.positions:
        messages.append(
            {
                "role": "user",
                "content": f"Positions IBKR (JSON): {positions_json}",
            }
        )

    messages.append({"role": "user", "content": args.query})

    payload = {
        "model": args.model,
        "messages": messages,
        "tools": [{"type": "web_search"}, {"type": "x_search"}],
        "tool_choice": "auto",
        "response_format": {"type": "json_schema", "json_schema": build_schema()},
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{args.base_url.rstrip('/')}/chat/completions"
    data = post_json(url, headers, payload, args.timeout)

    content = data["choices"][0]["message"]["content"]
    if args.raw:
        print(content)
        return 0

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        print(content)
        return 1

    print(json.dumps(parsed, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
