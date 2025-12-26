#!/usr/bin/env python3
import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone

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
    current_date_str = current_time.strftime("%A %d %B %Y, %H:%M UTC")

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

    # Build system prompt
    system_prompt = textwrap.dedent(
        f"""\
        News-driven trading analyst. IBKR.

        CONTEXT: Bot runs hourly. Previous run: ~1h ago. Next run: in 1h.
        - Existing positions may be yours. Adjust/close based on new developments.
        - High-conviction only. No major catalysts? Return empty orders [].
        - {current_date_str}. Recent = last 24-72h ({(current_time.replace(hour=0, minute=0, second=0) - timedelta(days=3)).strftime('%d %b %Y')}-now).
        - {markets_context}

        ============================================================
        *** CONTRAINTE BUDGETAIRE STRICTE (PRIORITE ABSOLUE) ***
        ============================================================
        Budget disponible: {budget_eur:.2f} EUR
        MAXIMUM ABSOLU utilisable pour achats: {budget_max:.2f} EUR (80% du budget)

        INTERDICTION TOTALE de proposer des ordres dont le total depasse {budget_max:.2f} EUR

        CALCUL OBLIGATOIRE avant de proposer des ordres:
        1. Pour chaque ordre BUY: cout = quantity × limit_price
        2. Additionner TOUS les couts BUY → estimated_total_eur
        3. Verifier: estimated_total_eur <= {budget_max:.2f} EUR
        4. Si depassement: REDUIRE les quantites OU SUPPRIMER des ordres

        VERIFICATION FINALE (avant de retourner le JSON):
        - Calculer la somme totale de tous les ordres BUY
        - Si somme > {budget_max:.2f} EUR → AJUSTER immediatement
        - Ne JAMAIS retourner un plan qui depasse {budget_max:.2f} EUR
        - Preferer MOINS d'ordres que de depasser le budget
        - En cas de doute, rester sous {budget_eur * 0.70:.2f} EUR (70%)
        ============================================================

        {margin_status}

        {memory_context if memory_context else ""}

        MANDATORY RESEARCH (web_search + x_search):
        1. Scan 24-72h news: earnings, regulation, central banks, geopolitics, sector trends, M&A
        2. X sentiment: market mood, analyst opinions, breaking news, retail shifts
        3. Verify: 2-3 sources, check prices/volume, find catalysts (earnings dates, events)
        4. Thesis: connect news to trades, spot over-reactions, sector rotations, contrarian plays

        STRATEGY:
        - Base on RECENT news/trends. Cite specific events per order (rationale field).
        - 2-5 day to monthly catalysts. Adapt to risk-on/off regime.
        - Review current positions: SELL on negative news. Manage concentration.
        - Avoid repetition: if a symbol was already bought in recent history, do not propose another BUY unless a new major catalyst appeared after the last run; otherwise return orders=[].
        - Seek diversification across sectors and symbols. Avoid repetition: if a symbol was already bought in recent history, do not propose another buy unless a new major catalyst appeared after the last run (do not reuse the same catalyst). Otherwise orders=[].
        - LONG ONLY: BUY to open, SELL to close existing positions. NO SHORT SELLING (action=SELL without position).
        - BUY: max 3-5 liquid positions. LIMIT orders, current prices.
          → RAPPEL BUDGET: Total de TOUS les BUY ne doit PAS depasser {budget_max:.2f} EUR
          → Calculer le cout (quantity × limit_price) AVANT de proposer chaque ordre
          → Ajuster les quantites si necessaire pour rester sous {budget_max:.2f} EUR
        - time_in_force: DAY or GTC. exchange: ALWAYS use SMART (never NASDAQ/NYSE/etc).
        - security_type: STK (stocks), ETF (etfs).

        SELECTION DES ACTIONS SELON LES MARCHES OUVERTS:
        - Consulter "{markets_context}" pour savoir quels marches sont ouverts
        - Si US ouvert: Chercher catalyseurs recents sur marches americains
        - Si US ferme mais Europe ouvert: Chercher catalyseurs recents sur marches europeens
        - Si US ferme mais Asie ouvert: Chercher catalyseurs recents sur marches asiatiques
        - Adapter la recherche de news aux regions des marches ouverts
        - Si aucun marche ouvert: retourner orders=[] avec explication dans summary

        EUROPEAN IBKR RESTRICTIONS:
        - ETFs: ONLY UCITS (European-domiciled). NO US ETFs.
        - Stocks: All markets tradeable (no restrictions).
        - Symbol: base ticker ONLY (NO exchange suffix). Exchange in separate field.
        - Currency: match instrument domicile.

        SOURCES: 1-2 per instrument + macro context. Cite X posts if used. Insufficient data? orders=[].

        OUTPUT (French, ASCII, strict JSON):
        - summary: strategy + news drivers
        - key_points: timeline, thesis, catalysts, risks, portfolio impact, sentiment, sources
        - budget_eur: {budget_eur:.2f} (recopier exactement cette valeur)
        - estimated_total_eur: SOMME EXACTE des (quantity × limit_price) pour TOUS les ordres BUY
          → ATTENTION CRITIQUE: NE DOIT PAS DEPASSER {budget_max:.2f} EUR
          → Si depassement: reduire quantites ou supprimer ordres AVANT de retourner le JSON
          → Verifier le calcul: additionner tous les couts BUY
        - orders: with rationale (news/catalyst)
        - disclaimer: educational, not advice
        - JSON only, match schema, no promises.

        VERIFICATION PRE-ENVOI OBLIGATOIRE:
        Avant de retourner le JSON final, verifier une derniere fois:
        → estimated_total_eur <= {budget_max:.2f} EUR ?
        → Si NON: AJUSTER les ordres MAINTENANT (reduire quantites ou supprimer)
        → Si OUI: OK, retourner le JSON

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
