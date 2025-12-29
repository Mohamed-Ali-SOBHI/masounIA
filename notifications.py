#!/usr/bin/env python3
"""
Système de notifications pour le bot de trading.
Envoie des alertes par email en cas d'événements critiques.
"""
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email_alert(subject, body, alert_type="INFO"):
    """
    Envoie une alerte par email avec un design HTML moderne.

    Args:
        subject: Sujet de l'email
        body: Corps du message
        alert_type: Type d'alerte (INFO, WARNING, ERROR, CRITICAL)

    Returns:
        True si l'email a été envoyé avec succès, False sinon
    """
    # Récupérer les paramètres depuis les variables d'environnement
    smtp_server = os.getenv("ALERT_SMTP_SERVER")
    smtp_port = int(os.getenv("ALERT_SMTP_PORT", "587"))
    smtp_user = os.getenv("ALERT_SMTP_USER")
    smtp_password = os.getenv("ALERT_SMTP_PASSWORD")
    alert_email = os.getenv("ALERT_EMAIL_TO")

    # Si les paramètres ne sont pas configurés, ne pas envoyer
    if not all([smtp_server, smtp_user, smtp_password, alert_email]):
        return False

    try:
        # Créer le message multipart
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        msg['To'] = alert_email
        msg['Subject'] = f"[{alert_type}] Bot MasounIA - {subject}"

        # Ajouter timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Couleurs minimalistes style Apple
        alert_colors = {
            "INFO": {"color": "#007AFF", "bg": "#F5F5F7"},
            "WARNING": {"color": "#FF9500", "bg": "#FFF8E1"},
            "ERROR": {"color": "#FF3B30", "bg": "#FFEBEE"},
            "CRITICAL": {"color": "#D70015", "bg": "#FFEBEE"}
        }

        color_config = alert_colors.get(alert_type, alert_colors["INFO"])

        # Version texte brut (fallback)
        text_body = f"""
Alerte du bot de trading MasounIA
================================

Type: {alert_type}
Date: {timestamp}

{body}

================================
Ceci est une alerte automatique.
"""

        # Version HTML minimaliste style Apple
        html_body = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MasounIA</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Helvetica Neue', sans-serif; background-color: #ffffff;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #ffffff;">
        <tr>
            <td align="center" style="padding: 60px 20px;">

                <!-- Container -->
                <table width="600" cellpadding="0" cellspacing="0">

                    <!-- Logo/Titre -->
                    <tr>
                        <td style="padding-bottom: 8px;">
                            <h1 style="margin: 0; font-size: 17px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.02em;">
                                MasounIA
                            </h1>
                        </td>
                    </tr>

                    <!-- Badge Alert Type -->
                    <tr>
                        <td style="padding-bottom: 32px;">
                            <span style="display: inline-block; padding: 4px 12px; background-color: {color_config['bg']}; color: {color_config['color']}; font-size: 12px; font-weight: 600; letter-spacing: 0.01em; border-radius: 12px;">
                                {alert_type}
                            </span>
                        </td>
                    </tr>

                    <!-- Sujet -->
                    <tr>
                        <td style="padding-bottom: 24px;">
                            <h2 style="margin: 0; font-size: 28px; font-weight: 600; color: #1d1d1f; letter-spacing: -0.03em; line-height: 1.2;">
                                {subject}
                            </h2>
                        </td>
                    </tr>

                    <!-- Corps du message -->
                    <tr>
                        <td style="padding-bottom: 32px;">
                            <div style="font-size: 15px; line-height: 1.6; color: #515154; white-space: pre-wrap;">
{body}
                            </div>
                        </td>
                    </tr>

                    <!-- Timestamp -->
                    <tr>
                        <td style="padding-bottom: 48px; border-bottom: 1px solid #d2d2d7;">
                            <p style="margin: 0; font-size: 13px; color: #86868b;">
                                {timestamp}
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding-top: 32px;">
                            <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #86868b;">
                                Notification automatique de votre bot de trading.
                            </p>
                        </td>
                    </tr>

                </table>

            </td>
        </tr>
    </table>
</body>
</html>
"""

        # Attacher les deux versions
        part1 = MIMEText(text_body, 'plain', 'utf-8')
        part2 = MIMEText(html_body, 'html', 'utf-8')

        msg.attach(part1)
        msg.attach(part2)

        # Se connecter au serveur SMTP et envoyer
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return True

    except Exception as e:
        print(f"Erreur lors de l'envoi de l'email: {e}")
        return False


def alert_execution_summary(grok_data, positions_data, orders_placed=None):
    """Notification de resume d'execution avec analyse Grok et performances."""

    # Extraire les donnees
    summary = grok_data.get("summary", "Aucune analyse disponible")
    orders = grok_data.get("orders", [])
    budget = positions_data.get("budget_eur", 0)
    cash = positions_data.get("total_cash", 0)
    nav = positions_data.get("net_liquidation", 0)
    using_margin = positions_data.get("using_margin", False)
    positions = positions_data.get("positions", [])

    # Calculer P&L total
    total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

    # Compter les ordres par type
    buy_count = sum(1 for o in orders if o.get("action") == "BUY")
    sell_count = sum(1 for o in orders if o.get("action") == "SELL")

    # Construire le sujet
    if using_margin:
        subject = "Margin Call"
    elif buy_count > 0 and sell_count == 0:
        subject = f"{buy_count} Achat{'s' if buy_count > 1 else ''}"
    elif sell_count > 0 and buy_count == 0:
        subject = f"{sell_count} Vente{'s' if sell_count > 1 else ''}"
    elif buy_count > 0 and sell_count > 0:
        subject = f"{buy_count} Achat{'s' if buy_count > 1 else ''}, {sell_count} Vente{'s' if sell_count > 1 else ''}"
    else:
        subject = "Aucun Ordre"

    # Créer version texte enrichie avec tableaux
    _create_rich_email(subject, summary, positions, orders, nav, cash, budget, total_pnl, using_margin, orders_placed)


def _create_rich_email(subject, summary, positions, orders, nav, cash, budget, total_pnl, using_margin, orders_placed):
    """Crée un email enrichi avec tableaux et graphiques."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import os
    from datetime import datetime

    smtp_server = os.getenv("ALERT_SMTP_SERVER")
    smtp_port = int(os.getenv("ALERT_SMTP_PORT", "587"))
    smtp_user = os.getenv("ALERT_SMTP_USER")
    smtp_password = os.getenv("ALERT_SMTP_PASSWORD")
    alert_email = os.getenv("ALERT_EMAIL_TO")

    if not all([smtp_server, smtp_user, smtp_password, alert_email]):
        return False

    alert_type = "CRITICAL" if using_margin else "INFO"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    alert_colors = {
        "INFO": {"color": "#007AFF", "bg": "#F5F5F7"},
        "CRITICAL": {"color": "#D70015", "bg": "#FFEBEE"}
    }
    color_config = alert_colors.get(alert_type, alert_colors["INFO"])

    # Trier positions par P&L
    sorted_positions = sorted(positions, key=lambda p: p.get("unrealized_pnl", 0))

    # Générer HTML des positions
    positions_html = ""
    for pos in sorted_positions[:10]:
        symbol = pos.get("symbol", "???")
        pnl = pos.get("unrealized_pnl", 0)
        pnl_pct = pos.get("unrealized_pnl_percent", 0)
        qty = pos.get("position", 0)

        pnl_color = "#34C759" if pnl >= 0 else "#FF3B30"
        sign = "+" if pnl >= 0 else ""

        positions_html += f"""
        <tr>
            <td style="padding: 12px 0; border-bottom: 1px solid #f5f5f7;">
                <strong style="font-size: 15px; color: #1d1d1f;">{symbol}</strong>
                <div style="font-size: 13px; color: #86868b; margin-top: 2px;">{int(qty)} actions</div>
            </td>
            <td style="padding: 12px 0; border-bottom: 1px solid #f5f5f7; text-align: right;">
                <div style="font-size: 15px; font-weight: 600; color: {pnl_color};">{sign}{pnl:.2f} EUR</div>
                <div style="font-size: 13px; color: #86868b; margin-top: 2px;">{sign}{pnl_pct:.1f}%</div>
            </td>
        </tr>
        """

    # Générer HTML des ordres
    orders_html = ""
    for order in orders[:10]:
        symbol = order.get("symbol", "???")
        action = order.get("action", "???")
        qty = order.get("quantity", 0)
        price = order.get("limit_price", 0)
        currency = order.get("currency", "USD")

        action_color = "#34C759" if action == "BUY" else "#FF3B30"
        action_bg = "#E8F5E9" if action == "BUY" else "#FFEBEE"

        orders_html += f"""
        <tr>
            <td style="padding: 12px 0; border-bottom: 1px solid #f5f5f7;">
                <span style="display: inline-block; padding: 4px 8px; background-color: {action_bg}; color: {action_color}; font-size: 11px; font-weight: 700; border-radius: 6px; margin-right: 8px;">{action}</span>
                <strong style="font-size: 15px; color: #1d1d1f;">{symbol}</strong>
            </td>
            <td style="padding: 12px 0; border-bottom: 1px solid #f5f5f7; text-align: right;">
                <div style="font-size: 15px; color: #1d1d1f;">{int(qty)} @ {price:.2f} {currency}</div>
            </td>
        </tr>
        """

    # P&L couleur et signe
    pnl_color = "#34C759" if total_pnl >= 0 else "#FF3B30"
    pnl_sign = "+" if total_pnl >= 0 else ""

    # Résumé compact des métriques
    metrics_line = f"NAV {nav:.2f} EUR • Cash {cash:.2f} EUR"
    if total_pnl != 0:
        metrics_line += f" • P&L {pnl_sign}{total_pnl:.2f} EUR"

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif; background-color: #ffffff;">
    <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table width="600" cellpadding="0" cellspacing="0">

                    <!-- Header -->
                    <tr>
                        <td style="padding-bottom: 8px;">
                            <h1 style="margin: 0; font-size: 17px; font-weight: 600; color: #1d1d1f;">MasounIA</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding-bottom: 24px;">
                            <h2 style="margin: 0; font-size: 24px; font-weight: 600; color: #1d1d1f; line-height: 1.2;">{subject}</h2>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding-bottom: 32px;">
                            <div style="font-size: 13px; color: #86868b;">{metrics_line}</div>
                        </td>
                    </tr>

                    <!-- Analyse Grok -->
                    <tr>
                        <td style="padding: 20px; background-color: #f5f5f7; border-radius: 12px; margin-bottom: 24px;">
                            <div style="font-size: 12px; font-weight: 700; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;">Analyse</div>
                            <div style="font-size: 15px; line-height: 1.6; color: #1d1d1f;">{summary}</div>
                        </td>
                    </tr>

                    <!-- Spacer -->
                    <tr><td style="height: 24px;"></td></tr>

                    <!-- Positions -->
                    <tr>
                        <td>
                            <div style="font-size: 12px; font-weight: 700; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px;">Positions ({len(positions)})</div>
                            <table width="100%" cellpadding="0" cellspacing="0">
                                {positions_html if positions else '<tr><td style="padding: 20px; text-align: center; color: #86868b; font-size: 15px;">Aucune position</td></tr>'}
                            </table>
                        </td>
                    </tr>

                    <!-- Spacer -->
                    <tr><td style="height: 24px;"></td></tr>

                    <!-- Ordres -->
                    <tr>
                        <td>
                            <div style="font-size: 12px; font-weight: 700; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px;">Ordres ({len(orders)})</div>
                            <table width="100%" cellpadding="0" cellspacing="0">
                                {orders_html if orders else '<tr><td style="padding: 20px; text-align: center; color: #86868b; font-size: 15px;">Aucun ordre</td></tr>'}
                            </table>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding-top: 32px; padding-bottom: 24px; border-top: 1px solid #d2d2d7;">
                            <p style="margin: 0; font-size: 12px; color: #86868b;">{timestamp}</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

    text_body = f"""
ANALYSE GROK
{summary}

PORTEFEUILLE
NAV: {nav:.2f} EUR
Cash: {cash:.2f} EUR
Budget: {budget:.2f} EUR
P&L: {pnl_sign}{total_pnl:.2f} EUR
"""

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        msg['To'] = alert_email
        msg['Subject'] = f"[{alert_type}] Bot MasounIA - {subject}"

        part1 = MIMEText(text_body, 'plain', 'utf-8')
        part2 = MIMEText(html_body, 'html', 'utf-8')

        msg.attach(part1)
        msg.attach(part2)

        import smtplib
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return True
    except Exception as e:
        print(f"Erreur email: {e}")
        return False


def test_notifications():
    """Test la configuration des notifications."""
    print("Test du système de notifications...")
    print(f"SMTP Server: {os.getenv('ALERT_SMTP_SERVER', 'NON CONFIGURE')}")
    print(f"SMTP User: {os.getenv('ALERT_SMTP_USER', 'NON CONFIGURE')}")
    print(f"Alert Email: {os.getenv('ALERT_EMAIL_TO', 'NON CONFIGURE')}")

    if send_email_alert(
        "Test de notification",
        "Ceci est un email de test. Si vous recevez cet email, les notifications fonctionnent correctement.",
        "INFO"
    ):
        print("[OK] Email de test envoye avec succes!")
        return True
    else:
        print("[ERREUR] Echec de l'envoi de l'email de test.")
        print("\nVerifiez que les variables suivantes sont configurees dans .env:")
        print("  ALERT_SMTP_SERVER=smtp.gmail.com")
        print("  ALERT_SMTP_PORT=587")
        print("  ALERT_SMTP_USER=votre.email@gmail.com")
        print("  ALERT_SMTP_PASSWORD=votre_mot_de_passe_application")
        print("  ALERT_EMAIL_TO=destinataire@example.com")
        return False


if __name__ == "__main__":
    # Test si exécuté directement
    from ibkr_shared import load_dotenv
    load_dotenv(".env")
    test_notifications()
