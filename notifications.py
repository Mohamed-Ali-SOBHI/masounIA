#!/usr/bin/env python3
"""
Système de notifications email pour MasounIA.
Design épuré : logo centré, badge, cartes positions/ordres.
"""
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_eur(value):
    return f"{_to_float(value):.2f} EUR"


def _get_smtp_settings():
    """Read SMTP settings from environment.

    Returns a dict with all fields as strings (or None if not configured).
    """
    smtp_server = os.getenv("ALERT_SMTP_SERVER")
    smtp_user = os.getenv("ALERT_SMTP_USER")
    smtp_password = os.getenv("ALERT_SMTP_PASSWORD")
    alert_email = os.getenv("ALERT_EMAIL_TO")

    if not all([smtp_server, smtp_user, smtp_password, alert_email]):
        return None

    return {
        "server": str(smtp_server),
        "port": int(os.getenv("ALERT_SMTP_PORT", "587")),
        "user": str(smtp_user),
        "password": str(smtp_password),
        "to": str(alert_email),
    }


def _send_smtp(settings, msg):
    try:
        with smtplib.SMTP(settings["server"], settings["port"]) as server:
            server.starttls()
            server.login(settings["user"], settings["password"])
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Erreur lors de l'envoi de l'email: {exc}")
        return False


def _subject_from_orders(using_margin, buy_count, sell_count):
    if using_margin:
        return "Margin Call"
    if buy_count > 0 and sell_count == 0:
        return f"{buy_count} Achat{'s' if buy_count > 1 else ''}"
    if sell_count > 0 and buy_count == 0:
        return f"{sell_count} Vente{'s' if sell_count > 1 else ''}"
    if buy_count > 0 and sell_count > 0:
        return (
            f"{buy_count} Achat{'s' if buy_count > 1 else ''}, "
            f"{sell_count} Vente{'s' if sell_count > 1 else ''}"
        )
    return "Aucun Ordre"


def _alert_type(using_margin, orders_placed):
    if using_margin:
        return "CRITICAL"
    if orders_placed and orders_placed > 0:
        return "TRADE"
    return "INFO"


def _build_alert_html(subject: str, subtitle: str, body: str) -> str:
    """Template HTML simple pour les alertes génériques."""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f5f5f7;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f7;">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table width="680" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.06);">
          <tr>
            <td style="padding:28px 28px 16px 28px; text-align:center;">
              <img src="image.png" alt="MasounIA" style="height:68px; width:auto; display:block; margin:0 auto 14px auto;">
              <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.1em;color:#86868b;margin-bottom:6px;">Rapport d'activité</div>
              <h1 style="margin:0;font-size:26px;font-weight:700;letter-spacing:-0.02em;color:#1d1d1f;">{subject}</h1>
              <div style="margin-top:6px;font-size:14px;color:#86868b;">{subtitle}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 28px 28px;">
              <div style="background:#f5f5f7;border-radius:16px;padding:20px;font-size:15px;line-height:1.6;color:#111827;white-space:pre-wrap;">{body}</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def send_email_alert(subject, body, alert_type="INFO"):
    """
    Envoie une alerte par email (template simple avec logo centré).
    """
    settings = _get_smtp_settings()
    if settings is None:
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = settings["user"]
    msg["To"] = settings["to"]
    msg["Subject"] = f"[{alert_type}] Bot MasounIA - {subject}"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text_body = f"""Alerte MasounIA
Type: {alert_type}
Date: {timestamp}

{body}
"""
    html_body = _build_alert_html(subject, f"{alert_type} • {timestamp}", body)

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return _send_smtp(settings, msg)


def alert_execution_summary(grok_data, positions_data, orders_placed=None):
    """
    Notification de résumé d'exécution avec analyse Grok et P&L.
    """
    summary = grok_data.get("summary", "Aucune analyse disponible")
    orders = grok_data.get("orders", []) or []
    positions = positions_data.get("positions", []) or []
    budget = _to_float(positions_data.get("budget_eur"))
    cash = _to_float(positions_data.get("total_cash"))
    nav = _to_float(positions_data.get("net_liquidation"))
    using_margin = bool(positions_data.get("using_margin", False))

    total_pnl = sum(_to_float(p.get("unrealized_pnl"), 0.0) for p in positions)
    realized_pnl = positions_data.get("realized_pnl_total")
    global_pnl = total_pnl + _to_float(realized_pnl, 0.0)
    pnl_sign = "+" if global_pnl >= 0 else ""

    buy_count = sum(1 for o in orders if o.get("action") == "BUY")
    sell_count = sum(1 for o in orders if o.get("action") == "SELL")

    subject = _subject_from_orders(using_margin, buy_count, sell_count)

    # Construire HTML pour positions
    positions_html = ""
    for pos in sorted(positions, key=lambda p: p.get("unrealized_pnl", 0))[:10]:
        symbol = pos.get("symbol", "?")
        qty = pos.get("position", 0)
        pnl_val = _to_float(pos.get("unrealized_pnl"), 0.0)
        pnl_pct = pos.get("unrealized_pnl_percent")
        if pnl_pct is None:
            # ibkr_export_positions.py uses pnl_percent
            pnl_pct = pos.get("pnl_percent")
        pnl_pct = _to_float(pnl_pct, 0.0)
        color = "#34C759" if pnl_val >= 0 else "#FF3B30"
        sign = "+" if pnl_val >= 0 else ""
        positions_html += f"""
        <tr>
          <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0;">
            <strong style="font-size: 15px; color: #1d1d1f;">{symbol}</strong>
            <div style="font-size: 13px; color: #86868b;">{int(qty)} actions</div>
          </td>
          <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; text-align: right;">
            <div style="font-size: 15px; font-weight: 600; color: {color};">{sign}{pnl_val:.2f} EUR</div>
            <div style="font-size: 13px; color: #86868b;">{sign}{pnl_pct:.1f}%</div>
          </td>
        </tr>
        """

    orders_html = ""
    for order in orders[:10]:
        symbol = order.get("symbol", "?")
        action = order.get("action", "?")
        qty = _to_float(order.get("quantity"), 0.0)
        price = order.get("limit_price")
        currency = order.get("currency", "USD")
        action_color = "#34C759" if action == "BUY" else "#FF3B30"
        action_bg = "#E8F5E9" if action == "BUY" else "#FFEBEE"

        if price is None:
            # When Grok outputs limit orders without pricing, show a placeholder.
            price_str = "N/A"
        else:
            price_str = f"{_to_float(price):.2f}"

        orders_html += f"""
        <tr>
          <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0;">
            <span style="display:inline-block;padding:4px 8px;background-color:{action_bg};color:{action_color};font-size:11px;font-weight:700;border-radius:6px;margin-right:8px;">{action}</span>
            <strong style="font-size: 15px; color: #1d1d1f;">{symbol}</strong>
          </td>
          <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; text-align: right;">
            <div style="font-size: 15px; color: #1d1d1f;">{int(qty)} @ {price_str} {currency}</div>
          </td>
        </tr>
        """

    metrics_line = f"Valeur du portefeuille {_fmt_eur(nav)} • Cash {_fmt_eur(cash)}"
    metrics_line += f" • P&L global {pnl_sign}{_to_float(global_pnl):.2f} EUR"
    metrics_line += f" • Budget {_fmt_eur(budget)}"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f5f5f7;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f7;">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table width="680" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.06);">
          <tr>
            <td style="padding:28px 28px 16px 28px; text-align:center;">
              <img src="image.png" alt="MasounIA" style="height:68px; width:auto; display:block; margin:0 auto 14px auto;">
              <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.1em;color:#86868b;margin-bottom:6px;">Résumé d'exécution</div>
              <h1 style="margin:0;font-size:26px;font-weight:700;letter-spacing:-0.02em;color:#1d1d1f;">{subject}</h1>
              <div style="margin-top:6px;font-size:14px;color:#86868b;">{metrics_line}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 28px 28px;">
              <div style="background:#f5f5f7;border-radius:16px;padding:20px;font-size:15px;line-height:1.6;color:#111827;white-space:pre-wrap;">{summary}</div>
              <div style="background:#f5f5f7;border-radius:16px;padding:16px;margin-top:12px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                  <tr>
                    <td style="width:50%;vertical-align:top;padding-right:8px;">
                      <div style="font-size:12px;font-weight:700;color:#6b7280;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">Positions ({len(positions)})</div>
                      <table width="100%" cellpadding="0" cellspacing="0">
                        {positions_html if positions else '<tr><td style="padding: 18px; text-align: center; color: #9ca3af; font-size: 14px;">Aucune position</td></tr>'}
                      </table>
                    </td>
                    <td style="width:50%;vertical-align:top;padding-left:8px;">
                      <div style="font-size:12px;font-weight:700;color:#6b7280;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">Ordres ({len(orders)})</div>
                      <table width="100%" cellpadding="0" cellspacing="0">
                        {orders_html if orders else '<tr><td style="padding: 18px; text-align: center; color: #9ca3af; font-size: 14px;">Aucun ordre</td></tr>'}
                      </table>
                    </td>
                  </tr>
                </table>
              </div>
              <div style="border-top:1px solid #e5e7eb;padding-top:16px;margin-top:16px;font-size:12px;color:#9ca3af;text-align:left;">{timestamp}</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_body = f"""ANALYSE GROK
{summary}

PORTEFEUILLE
Valeur du portefeuille: {_fmt_eur(nav)}
Cash: {_fmt_eur(cash)}
Budget: {_fmt_eur(budget)}
P&L global: {pnl_sign}{_to_float(global_pnl):.2f} EUR
"""

    settings = _get_smtp_settings()
    if settings is None:
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = settings["user"]
    msg["To"] = settings["to"]
    alert_type = _alert_type(using_margin, orders_placed)
    msg["Subject"] = f"[{alert_type}] Bot MasounIA - {subject}"
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return _send_smtp(settings, msg)


def test_notifications():
    """Test la configuration des notifications."""
    print("Test du système de notifications...")
    print(f"SMTP Server: {os.getenv('ALERT_SMTP_SERVER', 'NON CONFIGURE')}")
    print(f"SMTP User: {os.getenv('ALERT_SMTP_USER', 'NON CONFIGURE')}")
    print(f"Alert Email: {os.getenv('ALERT_EMAIL_TO', 'NON CONFIGURE')}")

    if send_email_alert(
        "Test de notification",
        "Ceci est un email de test. Si vous recevez cet email, les notifications fonctionnent correctement.",
        "INFO",
    ):
        print("[OK] Email de test envoyé avec succès!")
        return True
    else:
        print("[ERREUR] Échec de l'envoi de l'email de test.")
        print("\nVérifiez que les variables suivantes sont configurées dans .env:")
        print("  ALERT_SMTP_SERVER=smtp.gmail.com")
        print("  ALERT_SMTP_PORT=587")
        print("  ALERT_SMTP_USER=votre.email@gmail.com")
        print("  ALERT_SMTP_PASSWORD=votre_mot_de_passe_application")
        print("  ALERT_EMAIL_TO=destinataire@example.com")
        return False


if __name__ == "__main__":
    from ibkr_shared import load_dotenv
    load_dotenv(".env")
    test_notifications()
