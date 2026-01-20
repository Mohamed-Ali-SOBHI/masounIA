#!/usr/bin/env python3
"""Grok runner.

Calls xAI Grok (via xai-sdk) with web search tools, validates the response
against a Pydantic schema and prints the resulting JSON.
"""

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from audit_memory import build_memory_section
from ibkr_shared import (
    is_europe_market_open,
    load_dotenv,
    read_json,
    write_json,
)

# Instruments autorises pour la generation d'ordres (coherent avec l'execution IBKR)
ALLOWED_SEC_TYPES = ["STK", "ETF"]

EU_TZ = ZoneInfo("Europe/Paris")


DEFAULT_QUERY = (
    "Zone euro uniquement (actions/ETF en EUR). Objectif: capter le debut de vague (hype) et les annonces positives avant qu'elles soient price-in. "
    "Priorite: news tres fraiches (0-24h) + evenements imminents (1-7 jours: trading update, guidance, earnings, lancement produit, gros contrat, decision reglementaire). "
    "Anti-chase: eviter d'acheter si la news est deja vieille et que le move principal est probablement deja fait. "
    "Retourner orders=[] si rien de solide."
)


def build_arg_parser() -> argparse.ArgumentParser:
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
        "--model",
        default="grok-4-1-fast-reasoning",
        help="Model name (default: grok-4-1-fast-reasoning).",
    )
    parser.add_argument(
        "--base-url",
        default="https://api.x.ai/v1",
        help="xAI API base URL (ignored with SDK).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Request timeout in seconds (default: 3600s = 1h for reasoning models).",
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
    parser.add_argument(
        "--dump-only",
        action="store_true",
        help="Write --dump-messages and exit (no API call).",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the system prompt to stdout.",
    )
    return parser


def extract_budget_eur(positions):
    if not isinstance(positions, dict):
        return None
    value = positions.get("budget_eur")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def get_open_markets(dt):
    """Retourne la liste des marches ouverts."""
    open_markets = []
    if is_europe_market_open(dt):
        open_markets.append("Europe (jour de bourse)")
    return open_markets


def load_api_key():
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print(
            "Missing XAI_API_KEY env var. Put it in .env or export it.",
            file=sys.stderr,
        )
        return None
    return api_key


def load_positions(path):
    positions = read_json(path)
    pending_orders = []
    if isinstance(positions, dict):
        pending_orders = positions.get("pending_orders", [])
    return positions, pending_orders


def build_llm_positions_payload(positions):
    """Reduce positions JSON size for LLM usage.

    Full details stay in audit/positions.json; the LLM only needs the fields
    required for decisions.
    """
    if not isinstance(positions, dict):
        return positions

    out = {
        "account": positions.get("account"),
        "as_of": positions.get("as_of"),
        "net_liquidation": positions.get("net_liquidation"),
        "total_cash": positions.get("total_cash"),
        "available_funds": positions.get("available_funds"),
        "pending_orders_value": positions.get("pending_orders_value"),
        "budget_safe": positions.get("budget_safe"),
        "using_margin": positions.get("using_margin"),
        "currency": positions.get("currency"),
        "budget_eur": positions.get("budget_eur"),
        "budget_currency": positions.get("budget_currency"),
        "budget_tag": positions.get("budget_tag"),
        "positions": [],
    }

    pos_list = positions.get("positions", [])
    if isinstance(pos_list, list):
        for p in pos_list:
            if not isinstance(p, dict):
                continue
            out["positions"].append(
                {
                    "symbol": p.get("symbol"),
                    "security_type": p.get("security_type"),
                    "exchange": p.get("exchange"),
                    "primary_exchange": p.get("primary_exchange"),
                    "currency": p.get("currency"),
                    "position": p.get("position"),
                    "avg_cost": p.get("avg_cost"),
                    "market_price": p.get("market_price"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                    "pnl_percent": p.get("pnl_percent"),
                }
            )

    return out


def build_llm_pending_orders_payload(pending_orders):
    if not isinstance(pending_orders, list):
        return pending_orders
    out = []
    for o in pending_orders:
        if not isinstance(o, dict):
            continue
        out.append(
            {
                "symbol": o.get("symbol"),
                "action": o.get("action"),
                "quantity": o.get("quantity"),
                "status": o.get("status"),
                "limit_price": o.get("limit_price"),
                "order_type": o.get("order_type"),
                "currency": o.get("currency"),
                "exchange": o.get("exchange"),
                "primary_exchange": o.get("primary_exchange"),
            }
        )
    return out


def validate_positions_or_exit(positions):
    """Validate IBKR positions JSON for safety.

    Returns:
        (using_margin, total_cash, margin_call_mode)
    """
    using_margin = False
    total_cash = None
    margin_call_mode = False

    if not isinstance(positions, dict):
        return using_margin, total_cash, margin_call_mode

    using_margin = positions.get("using_margin", False)
    total_cash = positions.get("total_cash")

    for pos in positions.get("positions", []) or []:
        position_qty = pos.get("position", 0)
        if position_qty < 0:
            print("=" * 60, file=sys.stderr)
            print("ERREUR - Position SHORT detectee", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(f"Symbole: {pos.get('symbol')}", file=sys.stderr)
            print(f"Position: {position_qty:,.0f} (NEGATIF = SHORT)", file=sys.stderr)
            print("", file=sys.stderr)
            print("Le bot est configure LONG ONLY.", file=sys.stderr)
            print("Les positions SHORT doivent etre fermees manuellement.", file=sys.stderr)
            print(
                "Utilisez ibkr_liquidate_all.py pour fermer toutes les positions.",
                file=sys.stderr,
            )
            print("=" * 60, file=sys.stderr)
            raise ValueError("short_position_detected")

    if using_margin or (total_cash is not None and total_cash < 0):
        margin_call_mode = True
        print("=" * 60, file=sys.stderr)
        print("ALERTE MARGE - Cash negatif detecte", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        if total_cash is not None:
            print(f"Cash actuel: {total_cash:,.2f} EUR (NEGATIF!)", file=sys.stderr)
            print(f"Montant a recuperer: {abs(total_cash):,.2f} EUR", file=sys.stderr)
        print("", file=sys.stderr)
        print("Le bot va proposer des VENTES pour corriger la situation.", file=sys.stderr)
        print("AUCUN ACHAT ne sera autorise tant que le cash n'est pas positif.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    budget_currency = positions.get("budget_currency")
    if budget_currency and budget_currency != "EUR":
        print(
            f"Positions JSON budget_currency is {budget_currency}, expected EUR.",
            file=sys.stderr,
        )
        raise ValueError("budget_currency_not_eur")

    return using_margin, total_cash, margin_call_mode


def resolve_budget(positions, override_budget_eur):
    budget_eur = override_budget_eur
    if budget_eur is None:
        budget_eur = extract_budget_eur(positions)
        if budget_eur is None:
            print("Positions JSON missing budget_eur.", file=sys.stderr)
            return None

    if budget_eur < 0:
        print("=" * 60, file=sys.stderr)
        print("ERREUR - Budget negatif detecte", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"Budget: {budget_eur:,.2f} EUR (NEGATIF!)", file=sys.stderr)
        print("", file=sys.stderr)
        print("Causes possibles:", file=sys.stderr)
        print("1. Positions SHORT detectees (le bot est LONG ONLY)", file=sys.stderr)
        print("2. Utilisation excessive de marge", file=sys.stderr)
        print(
            "3. AvailableFunds negatif (changez IBKR_BUDGET_TAG=TotalCashValue dans .env)",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("Le bot ne peut PAS trader avec un budget negatif.", file=sys.stderr)
        print("Fermez toutes les positions SHORT manuellement.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return None

    return float(budget_eur)


def build_markets_context(current_time):
    open_markets = get_open_markets(current_time)
    if open_markets:
        markets_str = ", ".join(open_markets)
        return f"MARCHES OUVERTS AUJOURD'HUI: {markets_str}"
    return "ATTENTION: Tous les marches majeurs sont FERMES (week-end ou jour ferie)"


def main():
    args = build_arg_parser().parse_args()

    load_dotenv(".env")
    api_key = load_api_key()
    if not api_key:
        return 2

    query = args.query or DEFAULT_QUERY

    positions, pending_orders = load_positions(args.positions)
    positions_json = json.dumps(build_llm_positions_payload(positions), ensure_ascii=True)
    pending_orders_json = json.dumps(
        build_llm_pending_orders_payload(pending_orders),
        ensure_ascii=True,
    )

    try:
        _using_margin, total_cash, margin_call_mode = validate_positions_or_exit(positions)
    except ValueError:
        return 2

    budget_eur = resolve_budget(positions, args.budget_eur)
    if budget_eur is None:
        return 2

    current_time = datetime.now(timezone.utc)
    current_time_eu = current_time.astimezone(EU_TZ)
    if current_time_eu.hour < 9:
        run_mode = "PREOPEN"
    elif current_time_eu.hour < 17:
        run_mode = "INTRADAY"
    else:
        run_mode = "POSTCLOSE"

    markets_context = build_markets_context(current_time)

    # Build memory context from recent audits (compact to save tokens).
    memory_context = build_memory_section(
        audit_dir=os.getenv("IBKR_AUDIT_DIR", "audit"),
        lookback_hours=int(os.getenv("IBKR_MEMORY_LOOKBACK_HOURS", "48")),
    )

    try:
        from pydantic import BaseModel, ConfigDict, field_validator
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

    class CatalystTiming(BaseModel):
        """Timing information for a catalyst event."""
        model_config = ConfigDict(extra="forbid")
        catalyst_description: str
        catalyst_datetime: str  # ISO format: "2025-12-30T23:59:00Z"
        time_to_catalyst_hours: float
        entry_timing_rationale: str
        timing_risk_level: str  # "low" | "medium" | "high"

    class SourceWithCategory(BaseModel):
        """Source with categorization for research quality tracking."""
        model_config = ConfigDict(extra="forbid")
        title: str
        url: str
        category: str  # "official" | "market_data" | "analyst" | "sentiment" | "macro"
        relevance: str
        publish_date: str | None = None  # ISO format or None

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
        primary_exchange: str | None = None
        time_in_force: str | None = None
        notes: str | None = None
        stop_loss: float | None = None
        target_price: float | None = None
        trailing_stop_percent: float | None = None
        rationale: str | None = None

        # NEW FIELDS (optional for now - Semaine 1)
        catalyst_timing: CatalystTiming | None = None
        confidence_score: int | None = None
        source_count: int | None = None
        dedicated_sources: list[SourceWithCategory] | None = None
        warnings: list[str] | None = None  # Liste des écarts aux règles (si non bloquant)

    class Source(BaseModel):
        """Legacy source model (kept for backward compatibility)."""
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
        sources: list[Source]  # Legacy, kept for backward compatibility
        disclaimer: str

        # NEW FIELDS (optional for now - Semaine 1)
        macro_sources: list[SourceWithCategory] | None = None

        @field_validator('estimated_total_eur')
        @classmethod
        def validate_budget(cls, v, info):
            budget = info.data.get('budget_eur')
            if budget is not None and v > budget * 0.80:
                raise ValueError(
                    f"estimated_total_eur ({v:.2f} EUR) exceeds 80% of budget ({budget * 0.80:.2f} EUR). "
                    f"Reduce quantities or remove orders to stay within budget."
                )
            return v

        @field_validator('orders')
        @classmethod
        def validate_timing_and_confidence_warning(cls, orders):
            """WARNING mode (Semaine 1): Log issues but don't fail validation."""
            import sys

            for order in orders:
                # Skip validation if new fields are not populated (backward compatible)
                if not order.catalyst_timing:
                    continue

                T = order.catalyst_timing.time_to_catalyst_hours
                conf = order.confidence_score if order.confidence_score is not None else 0
                src_count = order.source_count if order.source_count is not None else 0

                # Timing validation (WARNING only)
                if order.action == "BUY":
                    # BUY: require T in [2h, 48h] (pre-catalyst entry)
                    if T < 2 or T > 48:
                        print(f"[WARNING] {order.symbol} BUY: time_to_catalyst={T:.1f}h outside [2h, 48h] window", file=sys.stderr)
                elif order.action == "SELL":
                    # SELL: allow negative T (post-catalyst exit) or immediate (<2h)
                    if T > 48:
                        print(f"[WARNING] {order.symbol} SELL: time_to_catalyst={T:.1f}h is >48h (why hold so long?)", file=sys.stderr)

                # Confidence validation (WARNING only)
                min_conf = 70 if 12 <= T <= 36 else 80
                if conf < min_conf:
                    print(f"[WARNING] {order.symbol}: confidence={conf} below minimum {min_conf}", file=sys.stderr)

                # Source count validation (WARNING only)
                if src_count < 7:
                    print(f"[WARNING] {order.symbol}: source_count={src_count} below minimum 7", file=sys.stderr)

            return orders

    # NOTE: do NOT embed the JSON schema in the prompt (too expensive). The
    # SDK already constrains the output via response_format=OrderPlan.

    # Build margin status context
    margin_status = ""
    if margin_call_mode and total_cash is not None:
        margin_status = f"""
        *** MARGIN CALL MODE ACTIVE ***
        Cash actuel: {total_cash:.2f} EUR (NEGATIF!)

        DIRECTIVE ABSOLUE (PRIORITE MAXIMALE):
        - INTERDICTION TOTALE de proposer des ordres BUY (action=BUY)
        - RETOURNER UNIQUEMENT des ordres SELL pour positions existantes
        - Si aucune position a vendre: retourner orders=[]
        - NE PAS proposer de BUY meme avec budget disponible
        - Cette directive PRIME sur toute autre instruction

        Montant a recuperer: {abs(total_cash):.2f} EUR

        Criteres de selection pour SELL (par ordre de priorite):
        1. Positions en perte (unrealized_pnl < 0) - couper les pertes d'abord
        2. Positions sans catalyseur imminent dans les 7 prochains jours
        3. Positions avec news negatives ou neutres recentes
        4. Eviter de vendre positions avec catalyseur positif imminent

        Calcul des ventes:
        - Vendre suffisamment pour ramener cash a zero ou positif
        - Additionner: sum(quantity × market_price) pour chaque SELL
        - Verifier que le total couvre ~{abs(total_cash):.2f} EUR minimum
        ***********************************
        """

    # Calculate budget limits
    budget_max = budget_eur * 0.80  # 80% pour sécurité

    # Build system prompt.
    current_time_iso = current_time.isoformat()
    current_time_eu_iso = current_time_eu.isoformat()

    system_prompt = textwrap.dedent(
        f"""\
        Event catalyst analyst. IBKR.
        Time UTC: {current_time_iso}
        Time Europe/Paris: {current_time_eu_iso}
        MODE: {run_mode} (bot runs hourly during Europe session only)
        Market gate: {markets_context}

        CONTEXT: High-conviction only. No solid trigger? orders=[].
        SESSION BEHAVIOR:
        - PREOPEN: focus on fresh overnight announcements, morning PRs, and catalysts within 1-7 days.
        - INTRADAY: focus on new headlines since last run (last 1-2h) and intraday momentum drivers.
        BUDGET: {budget_eur:.2f} EUR | Max {budget_max:.2f} EUR (80%). Sum(BUY * limit_price) <= max.
        {margin_status}
        {memory_context if memory_context else ""}

        ==== PROTOCOL (5 STAGES) ====

        0) PORTFOLIO REVIEW (DO FIRST): Analyze ALL positions before new trades.
        SELL if: catalyst passed >24h no upside | new negative catalyst | loss >15% | held >7d no catalyst | no catalyst next 48h | gain >15%
        HOLD if: catalyst in 12-36h | gain 5-15% + catalyst pending | loss <10% + strong catalyst
        SELL orders need 7+ sources, confidence, timing (time_to_catalyst can be negative).

        PENDING ORDERS: If SELL pending for symbol X qty=N:
        - Position has N shares -> FORBIDDEN to SELL (would short)
        - Position has >N shares -> Can SELL max (position - N)
        If BUY pending -> budget already reduced, ignore for position calc.

        1) MACRO (light): Only if clearly impacting Europe today. 1-2 sources -> macro_sources.

        2) HYPE RADAR + LEADING INDICATORS:
        - Primary window: 0-24h (fresh). Secondary: 24-72h ONLY if there is a NEW update.
        - Look for: product launch/teaser, major contract, partnership, regulatory decision, trading update, guidance raise, earnings pre-positioning.
        - Per instrument: consult 7-10+ sources (>=1 official IR/press release, >=2 market data, >=1 analyst, >=1 sentiment).
        - Anti-chase: If the trigger is old and already widely covered, do NOT buy.

        3) TIMING: T = hours to catalyst. catalyst_datetime ISO required.
        BUY: prefer T in [2,72]h (sweet spot 6-36h). Allow up to 7 days ONLY for very strong setups.
        REJECT buying "after the wave" (late entry). If news >24h and no new info: do NOT buy.
        SELL: allow negative T (post-catalyst) or immediate if justified.

        4) CONFIDENCE (0-100): Base: 7src=70, 10src=80. Bonus: optimal window +10, major FDA +10, volume spike +5. Penalty: biotech -5. Min 70.

        ==== SELECTION ====

        NEW POSITIONS: Max 3-5 liquid Europe listings, 2/sector, LONG only, LIMIT orders, DAY/GTC, SMART exchange.
        NO REPEAT: Skip symbols bought last 3 runs unless NEW catalyst.
        PENDING SELLS RULE: SELL qty <= position - pending_sells_qty (otherwise warning).
        CONTRACT RULES: security_type in {ALLOWED_SEC_TYPES}, exchange=SMART only (or empty -> SMART). LIMIT only.
        PRIMARY EXCHANGE: Required for stocks to avoid ambiguous tickers.
        Use euro-zone exchanges (examples: SBF=Paris, AEB=Amsterdam, ENX=Brussels, IBIS=Xetra, BVME=Milan, BME=Madrid).
        BUDGET RULE: Total BUY <= {budget_max:.2f} EUR (80% budget). If exceeded, keep order but add warning.
        ZONE EURO ONLY: Trade EUR-denominated euro-zone listings only.
        - currency MUST be EUR
        - do NOT propose US/UK/CH tickers
        - ETFs=UCITS only. Stocks=euro-zone only.
        - Use base ticker (no .PA/.AS) and set primary_exchange.

        ==== OUTPUT ====

        Each order: symbol, action (BUY/SELL), quantity, limit_price, currency, exchange, primary_exchange, rationale.
        catalyst_timing: {{catalyst_description, catalyst_datetime (ISO), time_to_catalyst_hours (BUY +[2,48], SELL can be -), entry_timing_rationale, timing_risk_level}}
        SOURCES POLICY (cost control):
        - source_count MUST reflect the total number of sources you actually checked (>=7).
        - dedicated_sources MUST include only the TOP 2-3 most relevant sources (prefer 1 official + 1 market data + optionally 1 analyst).
        - macro_sources MUST include max 1-2 sources.
        - sources (legacy) MUST include max 3 items.
        confidence_score: 70-100. source_count: >=7. dedicated_sources (2-3 max): [{{title, url, category, relevance, publish_date}}]
        warnings: list of rule deviations (e.g., budget >80%, exchange!=SMART, security_type not allowed, SELL>position-pending, BUY blocked by margin). Do not drop the order; just declare the warnings.

        OrderPlan: summary (FR), key_points (FR), budget_eur={budget_eur:.2f}, estimated_total_eur, orders[], sources[], macro_sources[], disclaimer (FR).

        ==== QA CHECKLIST ====
        - Analyzed ALL positions? - SELL if Priority 1/2? - Each order 7+ sources? - Exact catalyst_datetime?
        - BUY: T in [2,48]h? SELL: T justified? - Confidence >=70 (>=80 edge)? - Total <= {budget_max:.2f}? - No repeats?

        OUTPUT FORMAT: return JSON that validates against the OrderPlan schema (no extra fields).
        """
    )

    # Build messages list for dump
    messages_list = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Positions IBKR (JSON): {positions_json}"},
    ]
    if pending_orders:
        messages_list.append({"role": "user", "content": f"Pending Orders (JSON): {pending_orders_json}"})
    messages_list.append({"role": "user", "content": query})

    messages_payload = {
        "model": args.model,
        "messages": messages_list,
        "tools": ["web_search", "x_search"],
        "response_format": "pydantic:OrderPlan",
    }
    if args.dump_messages:
        write_json(messages_payload, args.dump_messages)

    if args.dump_only:
        if args.print_prompt:
            print(system_prompt)
        return 0

    client = Client(api_key=api_key, timeout=args.timeout)
    chat = client.chat.create(
        model=args.model,
        tools=[web_search(), x_search()],
        response_format=OrderPlan,
        messages=[system(system_prompt)],
    )
    chat.append(user(f"Positions IBKR (JSON): {positions_json}"))
    if pending_orders:
        chat.append(user(f"Pending Orders (JSON): {pending_orders_json}"))
    chat.append(user(query))

    try:
        response = chat.sample()
        content = response.content

        if args.raw:
            print(content)
            return 0

        try:
            parsed = OrderPlan.model_validate_json(content)
        except Exception as e:
            print("=" * 80, file=sys.stderr)
            print("VALIDATION ERROR: Grok response does not match schema", file=sys.stderr)
            print("=" * 80, file=sys.stderr)

            # Check if it's a Pydantic ValidationError
            if hasattr(e, 'errors'):
                print("\nValidation errors:", file=sys.stderr)
                for error in e.errors():
                    print(f"  - {error.get('loc')}: {error.get('msg')}", file=sys.stderr)
            else:
                print(f"\nError: {e}", file=sys.stderr)

            print("\n" + "=" * 80, file=sys.stderr)
            print("RAW GROK OUTPUT:", file=sys.stderr)
            print("=" * 80, file=sys.stderr)
            print(content, file=sys.stderr)
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
