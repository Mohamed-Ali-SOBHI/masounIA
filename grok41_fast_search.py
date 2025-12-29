#!/usr/bin/env python3
import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone

from audit_memory import build_memory_section
from ibkr_shared import load_dotenv, read_json, write_json


def extract_budget_eur(positions):
    if not isinstance(positions, dict):
        return None
    value = positions.get("budget_eur")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def is_us_market_open(dt):
    """Verifie si les marches US (NYSE, NASDAQ) sont ouverts."""
    if dt.weekday() >= 5:
        return False
    year, month, day = dt.year, dt.month, dt.day
    fixed_holidays = [(1, 1), (7, 4), (12, 25)]
    if (month, day) in fixed_holidays:
        return False
    if month == 1 and dt.weekday() == 0 and 15 <= day <= 21:
        return False
    if month == 2 and dt.weekday() == 0 and 15 <= day <= 21:
        return False
    good_fridays = {2025: (4, 18), 2026: (4, 3), 2027: (3, 26), 2028: (4, 14), 2029: (3, 30), 2030: (4, 19)}
    if year in good_fridays and (month, day) == good_fridays[year]:
        return False
    if month == 5 and dt.weekday() == 0 and day >= 25:
        return False
    if month == 9 and dt.weekday() == 0 and day <= 7:
        return False
    if month == 11 and dt.weekday() == 3 and 22 <= day <= 28:
        return False
    return True


def is_europe_market_open(dt):
    """Verifie si les marches europeens (Euronext, Xetra, SIX) sont ouverts."""
    if dt.weekday() >= 5:
        return False
    year, month, day = dt.year, dt.month, dt.day
    common_holidays = [(1, 1), (12, 25)]
    if (month, day) in common_holidays:
        return False
    easter_mondays = {2025: (4, 21), 2026: (4, 6), 2027: (3, 29), 2028: (4, 17), 2029: (4, 2), 2030: (4, 22)}
    if year in easter_mondays and (month, day) == easter_mondays[year]:
        return False
    good_fridays = {2025: (4, 18), 2026: (4, 3), 2027: (3, 26), 2028: (4, 14), 2029: (3, 30), 2030: (4, 19)}
    if year in good_fridays and (month, day) == good_fridays[year]:
        return False
    if month == 5 and day == 1:
        return False
    return True


def is_asia_market_open(dt):
    """Verifie si les marches asiatiques (Tokyo, Hong Kong) sont ouverts."""
    if dt.weekday() >= 5:
        return False
    month, day = dt.month, dt.day
    common_holidays = [(1, 1), (12, 25)]
    if (month, day) in common_holidays:
        return False
    return True


def get_open_markets(dt):
    """Retourne la liste des marches ouverts."""
    open_markets = []
    if is_us_market_open(dt):
        open_markets.append("US (NYSE, NASDAQ)")
    if is_europe_market_open(dt):
        open_markets.append("Europe (Euronext, Xetra, SIX)")
    if is_asia_market_open(dt):
        open_markets.append("Asie (Tokyo, Hong Kong)")
    return open_markets


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

    # Vérifier si le compte utilise de la marge et détecter les positions short
    using_margin = False
    total_cash = None
    margin_call_mode = False
    has_short_positions = False

    if isinstance(positions, dict):
        using_margin = positions.get("using_margin", False)
        total_cash = positions.get("total_cash")

        # Détecter les positions short (position négative)
        pos_list = positions.get("positions", [])
        for pos in pos_list:
            position_qty = pos.get("position", 0)
            if position_qty < 0:
                has_short_positions = True
                print("=" * 60, file=sys.stderr)
                print("ERREUR - Position SHORT detectee", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
                print(f"Symbole: {pos.get('symbol')}", file=sys.stderr)
                print(f"Position: {position_qty:,.0f} (NEGATIF = SHORT)", file=sys.stderr)
                print("", file=sys.stderr)
                print("Le bot est configure LONG ONLY.", file=sys.stderr)
                print("Les positions SHORT doivent etre fermees manuellement.", file=sys.stderr)
                print("Utilisez ibkr_liquidate_all.py pour fermer toutes les positions.", file=sys.stderr)
                print("=" * 60, file=sys.stderr)

        if has_short_positions:
            return 2

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

    # Bloquer si budget negatif (situation anormale - positions short ou probleme)
    if budget_eur < 0:
        print("=" * 60, file=sys.stderr)
        print("ERREUR - Budget negatif detecte", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"Budget: {budget_eur:,.2f} EUR (NEGATIF!)", file=sys.stderr)
        print("", file=sys.stderr)
        print("Causes possibles:", file=sys.stderr)
        print("1. Positions SHORT detectees (le bot est LONG ONLY)", file=sys.stderr)
        print("2. Utilisation excessive de marge", file=sys.stderr)
        print("3. AvailableFunds negatif (changez IBKR_BUDGET_TAG=TotalCashValue dans .env)", file=sys.stderr)
        print("", file=sys.stderr)
        print("Le bot ne peut PAS trader avec un budget negatif.", file=sys.stderr)
        print("Fermez toutes les positions SHORT manuellement.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 2

    current_time = datetime.now(timezone.utc)

    # Detecter quels marches sont ouverts
    open_markets = get_open_markets(current_time)
    if open_markets:
        markets_str = ", ".join(open_markets)
        markets_context = f"MARCHES OUVERTS AUJOURD'HUI: {markets_str}"
    else:
        markets_context = "ATTENTION: Tous les marches majeurs sont FERMES (week-end ou jour ferie)"

    # Build memory context from recent audits
    memory_context = build_memory_section(
        audit_dir=os.getenv("IBKR_AUDIT_DIR", "audit"),
        lookback_hours=72
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

    schema_json = OrderPlan.model_json_schema()

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

    # Build NEW system prompt (refonte complete pour timing precis)
    current_time_iso = current_time.isoformat()

    system_prompt = textwrap.dedent(
        f"""\
        Event catalyst analyst. IBKR. Time: {current_time_iso}. {markets_context}

        CONTEXT: Bot runs hourly. High-conviction only. No catalysts? orders=[].
        BUDGET: {budget_eur:.2f} EUR → Max {budget_max:.2f} EUR (80%). Sum(BUY × limit_price) ≤ max.
        {margin_status}
        {memory_context if memory_context else ""}

        ═══ PROTOCOL (5 STAGES) ═══

        0️⃣ PORTFOLIO REVIEW (DO FIRST): Analyze ALL positions before new trades.
        For each position: search last 12h developments → apply SELL matrix:
        SELL if: ✗ Catalyst passed >24h no upside ✗ New negative catalyst ✗ Loss >15% ✗ Held >7d no catalyst ✗ No catalyst next 48h ✗ Gain >15%
        HOLD if: ✓ Catalyst in 12-36h ✓ Gain 5-15% + catalyst pending ✓ Loss <10% + strong catalyst
        → SELL orders need 7+ sources, confidence, timing (time_to_catalyst can be negative)

        1️⃣ MACRO: web_search Fed/ECB/geopolitics/VIX. 2-3 sources → macro_sources.

        2️⃣ CATALYSTS (24-48h only): Scan earnings/FDA/M&A/partnerships.
        Per instrument: 7-10+ sources (2-3 official, 2-3 market data, 1-2 analyst, 1-2 sentiment, 1 macro link).

        3️⃣ TIMING: T = hours to catalyst. catalyst_datetime ISO required.
        BUY if: T∈[2,12]h + conf≥80 | T∈(12,36)h + conf≥70 | T∈[36,48]h + conf≥80
        REJECT: T<2h | T>48h | vague timing

        4️⃣ CONFIDENCE (0-100): Base: 7src=70, 10src=80. Bonus: optimal window +10, major FDA +10, volume spike +5. Penalty: biotech -5. Min 70.

        ═══ SELECTION ═══

        NEW POSITIONS: Max 3-5 liquid (vol>500K/d), 2/sector, LONG only, LIMIT orders, DAY/GTC, SMART exchange.
        NO REPEAT: Skip symbols bought last 3 runs unless NEW catalyst.
        IBKR EU: ETFs=UCITS only (no SPY/QQQ). Stocks=all OK. Ticker=base only (no .AS/.PA). Currency=match domicile.

        ═══ OUTPUT ═══

        Each order: symbol, action (BUY/SELL), quantity, limit_price, currency, exchange, rationale.
        catalyst_timing: {{catalyst_description, catalyst_datetime (ISO), time_to_catalyst_hours (BUY: +[2,48], SELL: can be -), entry_timing_rationale, timing_risk_level}}
        confidence_score: 70-100. source_count: ≥7. dedicated_sources: [{{title, url, category, relevance, publish_date}}]

        OrderPlan: summary (FR), key_points (FR), budget_eur={budget_eur:.2f}, estimated_total_eur, orders[], sources[], macro_sources[], disclaimer (FR).

        ═══ QA CHECKLIST ═══
        □ Analyzed ALL positions? □ SELL if Priority 1/2? □ Each order 7+ sources? □ Exact catalyst_datetime?
        □ BUY: T∈[2,48]h? SELL: T justified? □ Confidence ≥70 (≥80 edge)? □ Total ≤ {budget_max:.2f}? □ No repeats?

        Schema: {json.dumps(schema_json, indent=2, ensure_ascii=True)}
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
