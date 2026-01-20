#!/usr/bin/env python3
"""Audit memory.

The goal of this module is to provide Grok with *useful continuity* (recent
reasoning, attempted orders, errors) without blowing up token usage.

Key idea:
- keep full details in audit files (free)
- send a compact, structured memory snippet to the LLM (paid)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse


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
        except Exception:
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


def _short(text: str, max_len: int) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def _parse_error_code(text: str) -> str | None:
    # Common IBKR format: "Error 201, reqId ..."
    if not text:
        return None
    idx = text.find("Error")
    if idx < 0:
        return None
    tail = text[idx:].split()
    if len(tail) < 2:
        return None
    candidate = tail[1].strip(",").strip(":")
    return candidate if candidate.isdigit() else None


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def extract_full_compact_memory_context(audits: list[dict]) -> str:
    """Return full 48h memory, compacted (no URLs).

    This preserves all runs within the lookback window and all fields that
    matter for continuity:
    - what was proposed (orders)
    - why (summary + key_points)
    - what failed (errors)
    - what sources were cited (titles + domains, without URLs)

    URLs are intentionally omitted to save tokens. Grok can retrieve them again
    via web_search using the title and/or the domain.
    """
    if not audits:
        return ""

    def format_place_error(audit_data: dict) -> str | None:
        place = audit_data.get("place") or {}
        stderr = str(place.get("stderr", "") or "").strip()
        if not stderr:
            return None
        first_line = stderr.split("\n", 1)[0].strip()
        if not first_line:
            return None

        code = _parse_error_code(first_line)
        if not code:
            return first_line

        reason = first_line
        if ":" in reason:
            reason = reason.split(":", 1)[1].strip()
        reason_upper = reason.upper()
        if "MINIMUM DE 2000" in reason_upper or "MINIMUM 2000" in reason_upper:
            reason = "minimum 2000 EUR requis (marge/FX/short)"
        return f"IBKR#{code}: {reason}"

    def collect_sources(orders_data: dict) -> list[str]:
        refs: list[str] = []

        def add(url: str | None, title: str | None, publish_date: str | None = None):
            dom = _domain(url or "")
            t = (title or "").strip()
            if dom and t:
                item = f"{dom} | {t}"
            elif t:
                item = t
            elif dom:
                item = dom
            else:
                return
            if publish_date:
                item = f"{item} ({publish_date})"
            if item not in refs:
                refs.append(item)

        # Legacy sources
        sources = orders_data.get("sources")
        if isinstance(sources, list):
            for s in sources:
                if isinstance(s, dict):
                    add(s.get("url"), s.get("title"), s.get("publish_date"))

        macro = orders_data.get("macro_sources")
        if isinstance(macro, list):
            for s in macro:
                if isinstance(s, dict):
                    add(s.get("url"), s.get("title"), s.get("publish_date"))

        orders = orders_data.get("orders")
        if isinstance(orders, list):
            for o in orders:
                if not isinstance(o, dict):
                    continue
                for s in o.get("dedicated_sources") or []:
                    if isinstance(s, dict):
                        add(s.get("url"), s.get("title"), s.get("publish_date"))

        return refs

    audits_sorted = sorted(audits, key=lambda a: a.get("timestamp") or datetime.now(timezone.utc))

    lines: list[str] = []
    lines.append("MEMOIRE 48H (full, compact, sans URLs):")
    lines.append(
        "Instruction: conserve la coherence sur 48h, evite de repeter les memes idees/erreurs. Les URLs ne sont pas incluses; refais web_search si besoin."
    )

    for audit in reversed(audits_sorted):
        run_id = audit.get("run_id") or ""
        ts = audit.get("timestamp") or datetime.now(timezone.utc)
        audit_data = audit.get("audit_data") or {}
        orders_data = audit.get("orders_data") or {}

        status = str(audit_data.get("status", "unknown")).upper()
        ts_str = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

        err_parts: list[str] = []
        if audit_data.get("error"):
            err_parts.append(str(audit_data.get("error")))
        place_err = format_place_error(audit_data)
        if place_err:
            err_parts.append(place_err)
        err_str = " | ".join(err_parts) if err_parts else "-"

        orders = orders_data.get("orders") if isinstance(orders_data, dict) else None
        order_count = len(orders) if isinstance(orders, list) else 0
        lines.append(f"[{ts_str}] run={run_id} status={status} orders={order_count} err={err_str}")

        if isinstance(orders_data, dict) and orders_data.get("summary"):
            lines.append("  summary=" + str(orders_data.get("summary")))

        key_points = orders_data.get("key_points") if isinstance(orders_data, dict) else None
        if isinstance(key_points, list) and key_points:
            lines.append("  key_points=" + " | ".join(str(k) for k in key_points))

        if isinstance(orders, list) and orders:
            for o in orders:
                if not isinstance(o, dict):
                    continue
                action = o.get("action")
                symbol = o.get("symbol")
                qty = o.get("quantity")
                lp = o.get("limit_price")
                prim = o.get("primary_exchange")
                conf = o.get("confidence_score")
                src_count = o.get("source_count")
                timing = o.get("catalyst_timing") or {}
                cat = timing.get("catalyst_description") if isinstance(timing, dict) else None
                cat_dt = timing.get("catalyst_datetime") if isinstance(timing, dict) else None
                t_hours = timing.get("time_to_catalyst_hours") if isinstance(timing, dict) else None
                lines.append(
                    "  order="
                    + f"{action} {qty} {symbol} @ {lp} EUR"
                    + (f" prim={prim}" if prim else "")
                    + (f" T={t_hours}h" if t_hours is not None else "")
                    + (f" cat={cat}" if cat else "")
                    + (f" cat_dt={cat_dt}" if cat_dt else "")
                    + (f" conf={conf}" if conf is not None else "")
                    + (f" src={src_count}" if src_count is not None else "")
                )

        sources = collect_sources(orders_data) if isinstance(orders_data, dict) else []
        if sources:
            lines.append("  sources_cited=" + " || ".join(sources))

    return "\n".join(lines)
def build_memory_section(audit_dir: str = "audit", lookback_hours: int = 72) -> str:
    """Build full compact memory for the LLM (no URLs, no truncation/caps).

    Note: Details live in audit files; this returns a compact rendering that is
    stable across runs.
    """
    try:
        audits = get_recent_audits(audit_dir, lookback_hours)
        return extract_full_compact_memory_context(audits)
    except Exception as e:
        print(f"Warning: Failed to build memory context: {e}", file=sys.stderr)
        return ""
