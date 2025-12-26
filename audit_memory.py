#!/usr/bin/env python3
"""
Module pour extraire et formatter la memoire des audits recents.
Permet au bot de se souvenir de ses decisions passees et d'apprendre de ses erreurs.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_recent_audits(audit_dir: str = "audit", lookback_hours: int = 72) -> list[dict]:
    """
    Scanner le repertoire audit pour trouver les runs recents.

    Args:
        audit_dir: Chemin vers le repertoire audit (defaut: "audit")
        lookback_hours: Periode de lookback en heures (defaut: 72 = 3 jours)

    Returns:
        Liste de dicts avec donnees audit parsees, triee chronologiquement (oldest first)
        Chaque dict contient: {
            'run_id': str,
            'timestamp': datetime,
            'audit_data': dict (audit.json parse),
            'orders_data': dict | None (orders.json parse)
        }
    """
    audits = []

    # Verifier que le repertoire existe
    if not os.path.isdir(audit_dir):
        return []

    # Calculer le timestamp de cutoff
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # Scanner les sous-repertoires
    try:
        entries = os.scandir(audit_dir)
    except Exception as e:
        print(f"Warning: Failed to scan audit directory {audit_dir}: {e}", file=sys.stderr)
        return []

    for entry in entries:
        if not entry.is_dir():
            continue

        # Parser le nom du repertoire (format: YYYYMMDD_HHMMSS)
        run_id = entry.name
        try:
            timestamp = datetime.strptime(run_id, "%Y%m%d_%H%M%S")
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            # Nom invalide, skip
            continue

        # Filtrer par lookback period
        if timestamp < cutoff:
            continue

        # Lire audit.json (requis)
        audit_path = os.path.join(entry.path, "audit.json")
        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to read {audit_path}: {e}", file=sys.stderr)
            continue

        # Lire orders.json (optionnel - peut ne pas exister si grok a echoue)
        orders_path = os.path.join(entry.path, "orders.json")
        orders_data = None
        try:
            with open(orders_path, "r", encoding="utf-8") as f:
                orders_data = json.load(f)
        except Exception as e:
            # orders.json peut manquer si erreur avant grok
            pass

        audits.append({
            'run_id': run_id,
            'timestamp': timestamp,
            'audit_data': audit_data,
            'orders_data': orders_data
        })

    # Trier chronologiquement (oldest first)
    audits.sort(key=lambda x: x['timestamp'])

    return audits


def extract_memory_context(audits: list[dict], max_tokens: int = 500) -> str:
    """
    Extraire et formatter un contexte memoire concis depuis les audits recents.

    Args:
        audits: Liste de dicts audit depuis get_recent_audits()
        max_tokens: Budget approximatif de tokens (defaut: 500)

    Returns:
        String formatee en francais, prete pour injection dans le prompt
    """
    if not audits:
        return ""

    # Limiter au max 6 audits les plus recents
    audits = audits[-6:]

    lines = ["HISTORIQUE RECENT (dernieres 72h):", ""]

    for audit in audits:
        run_id = audit['run_id']
        audit_data = audit['audit_data']
        orders_data = audit['orders_data']

        # Parser timestamp (format: YYYYMMDD_HHMMSS)
        try:
            dt = datetime.strptime(run_id, "%Y%m%d_%H%M%S")
            timestamp_str = dt.strftime("%d/%m %H:%M")
        except ValueError:
            timestamp_str = run_id[:13]  # Fallback

        # Status et erreurs d'execution
        status = audit_data.get('status', 'unknown')
        error = audit_data.get('error')
        place_data = audit_data.get('place', {})
        place_stderr = place_data.get('stderr', '')
        has_execution_error = place_stderr and ('Error' in place_stderr or 'Cancelled' in place_stderr)

        if status == 'error' or error or has_execution_error:
            # RUN FAILED
            lines.append(f"[{timestamp_str}] RUN FAILED")

            # Extraire erreur d'execution depuis place.stderr si disponible
            if place_stderr:
                # Parser les erreurs IBKR
                if 'Error 10311' in place_stderr:
                    # Erreur routing direct - extraire symboles
                    symbols = []
                    for line in place_stderr.split('\n'):
                        if 'symbol=' in line and 'exchange=' in line:
                            # Extraire symbol et exchange
                            try:
                                sym_start = line.index("symbol='") + 8
                                sym_end = line.index("'", sym_start)
                                symbol = line[sym_start:sym_end]
                                exch_start = line.index("exchange='") + 10
                                exch_end = line.index("'", exch_start)
                                exchange = line[exch_start:exch_end]
                                symbols.append(f"{symbol} (exchange={exchange})")
                            except:
                                pass
                    if symbols:
                        lines.append(f"- Erreur 10311: Direct routing interdit - {', '.join(symbols)}")
                        lines.append(f"- Solution: Utiliser exchange=SMART au lieu de NASDAQ/NYSE")
                    else:
                        lines.append(f"- Erreur 10311: Direct routing (NASDAQ/NYSE) interdit, utiliser SMART")
                else:
                    # Autre erreur - premier ligne
                    error_lines = place_stderr.strip().split('\n')
                    if error_lines:
                        error_msg = error_lines[0][:100]  # Truncate
                        lines.append(f"- Erreur execution: {error_msg}")
            elif error:
                lines.append(f"- Erreur: {error}")

            # Si orders.json existe, montrer ce qui etait propose
            if orders_data and 'orders' in orders_data:
                orders = orders_data['orders']
                if orders:
                    order = orders[0]  # Premier ordre seulement
                    symbol = order.get('symbol', '?')
                    action = order.get('action', '?')
                    qty = order.get('quantity', 0)
                    price = order.get('limit_price', 0)
                    rationale = order.get('rationale', '')[:50]  # Truncate
                    lines.append(f"- Ordre propose: {action} {qty} {symbol} @ {price} ({rationale})")

            lines.append("")

        else:
            # RUN OK
            if not orders_data:
                lines.append(f"[{timestamp_str}] OK - Pas de donnees")
                lines.append("")
                continue

            # Budget utilise
            budget_eur = orders_data.get('budget_eur', 0)
            estimated = orders_data.get('estimated_total_eur', 0)
            if budget_eur > 0:
                pct = int((estimated / budget_eur) * 100)
                # Formater montants (k = milliers)
                if estimated >= 1000:
                    est_str = f"{int(estimated/1000)}k"
                else:
                    est_str = f"{int(estimated)}"
                if budget_eur >= 1000:
                    budget_str = f"{int(budget_eur/1000)}k"
                else:
                    budget_str = f"{int(budget_eur)}"
                lines.append(f"[{timestamp_str}] OK - Budget: {est_str} / {budget_str} EUR ({pct}%)")
            else:
                lines.append(f"[{timestamp_str}] OK")

            # Summary (truncate)
            summary = orders_data.get('summary', '')[:100]
            if summary:
                lines.append(f"- Strategie: {summary}")

            # Key points (premiers 2 seulement)
            key_points = orders_data.get('key_points', [])
            if key_points:
                # Extraire catalyseurs si present
                for kp in key_points[:2]:
                    if 'Catalyseur' in kp or 'catalyseur' in kp:
                        lines.append(f"- {kp[:80]}")
                        break

            # Orders (lister symboles)
            orders = orders_data.get('orders', [])
            if orders:
                order_strs = []
                for order in orders[:3]:  # Max 3 orders
                    symbol = order.get('symbol', '?')
                    action = order.get('action', '?')
                    qty = order.get('quantity', 0)
                    price = order.get('limit_price', 0)
                    order_strs.append(f"{action} {int(qty)} {symbol} @ {price}")
                lines.append(f"- Ordres: {', '.join(order_strs)}")
            else:
                lines.append("- Aucun ordre (pas de catalyseurs)")

            lines.append("")

    # Joindre
    result = "\n".join(lines)

    # Verifier taille approximative (1 token ~= 4 chars)
    approx_tokens = len(result) / 4

    # Si depasse budget, drop oldest audit iterativement
    while approx_tokens > max_tokens and len(audits) > 1:
        audits = audits[1:]  # Drop oldest
        # Reconstruire (recursion simple)
        return extract_memory_context(audits, max_tokens)

    # Ajouter instruction finale
    result += "\n---\nUtilise cet historique pour eviter erreurs repetees et continuer strategie coherente.\n"

    return result


def build_memory_section(audit_dir: str = "audit", lookback_hours: int = 72) -> str:
    """
    Fonction de haut niveau qui combine get_recent_audits + extract_memory_context.

    Args:
        audit_dir: Chemin vers le repertoire audit
        lookback_hours: Periode de lookback en heures

    Returns:
        String formatee pour injection dans le prompt, ou "" si aucun audit
    """
    try:
        audits = get_recent_audits(audit_dir, lookback_hours)
        if not audits:
            return ""
        return extract_memory_context(audits)
    except Exception as e:
        print(f"Warning: Failed to build memory context: {e}", file=sys.stderr)
        return ""
