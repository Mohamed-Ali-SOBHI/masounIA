# MASOUNIA

**Trading intelligent piloté par l'actualité en temps réel.**

MASOUNIA analyse automatiquement les marchés financiers 24/7, identifie les opportunités basées sur les événements (FDA, earnings, M&A) et exécute des trades sur votre compte Interactive Brokers.

---

## Qu'est-ce que MASOUNIA ?

Un bot de trading autonome qui combine l'IA Grok (xAI) et Interactive Brokers pour trader sur les catalyseurs de marché à court terme.

**Ce que fait MASOUNIA:**
- Scanne en continu les événements de marché (approbations FDA, résultats trimestriels, fusions-acquisitions)
- Analyse les actualités web et le sentiment sur X/Twitter en temps réel
- Calcule des scores de conviction basés sur 7 à 10+ sources vérifiées
- Place automatiquement des ordres LIMIT sur les meilleures opportunités
- Gère votre portefeuille avec des règles strictes de gestion du risque

**MASOUNIA ne trade que sur des événements haute conviction** (≥70% de confiance, 7+ sources, timing 2-48h avant le catalyseur). Si aucune opportunité ne répond aux critères, le bot préserve votre capital.

---

## Pourquoi MASOUNIA ?

### Réactivité
Les marchés réagissent en secondes aux nouvelles. MASOUNIA surveille 24/7 et agit avant que l'opportunité ne disparaisse.

### Discipline
Pas d'émotions. Seulement des décisions basées sur des données vérifiées, des timing précis et des critères de conviction stricts.

### Transparence
Chaque trade est accompagné de sa justification complète: sources, analyse, score de confiance, timing du catalyseur.

### Sécurité
- Budget limité automatiquement à 80% du cash disponible
- Protection anti-SHORT intégrée
- Ordres LIMIT uniquement (pas de market orders)
- Maximum 3-5 positions simultanées
- Validation des ventes (ne peut jamais vendre plus que détenu)

---

## Comment ça marche ?

### 1. Scan des catalyseurs
Toutes les heures, Grok analyse:
- Calendriers FDA (approbations PDUFA, NDA, BLA)
- Résultats financiers des entreprises (earnings)
- Annonces M&A et partenariats stratégiques
- Sentiment sur X/Twitter
- Macro-économie (Fed, ECB, VIX)

### 2. Sélection haute conviction
MASOUNIA ne trade que si:
- **7 sources minimum** confirment le catalyseur
- **Timing précis**: événement dans 2 à 48 heures
- **Confiance ≥70%** (score calculé selon qualité des sources)
- **Liquidité suffisante** (>500K volume/jour)

### 3. Gestion du portefeuille
Pour chaque position existante:
- Analyse des développements des 12 dernières heures
- Décision SELL si: catalyseur passé, nouvelle négative, perte >15%, gain >15%, ou aucun catalyseur dans les 48h
- Décision HOLD si: catalyseur imminent (12-36h) ou position profitable avec catalyseur à venir

### 4. Exécution automatique
- Ordres LIMIT avec buffer de sécurité (25 bps)
- Execution via Interactive Brokers API
- Email de confirmation avec détails complets
- Audit complet dans `audit/` avec toutes les données

**Résultat:** Un portefeuille qui ne contient que des positions avec un catalyseur proche et haute conviction.

---

## Démarrage rapide

### Prérequis
- Python 3.10+
- Compte Interactive Brokers (paper trading pour commencer)
- Clé API xAI (https://x.ai)
- IB Gateway ou TWS installé

### Installation

```bash
# Cloner et installer
git clone https://github.com/votre-repo/masounIA.git
cd masounIA
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Éditer .env et ajouter votre clé API xAI
```

### Configuration Interactive Brokers

1. **Lancer IB Gateway** (ou TWS)
2. **Activer l'API:**
   - Configuration → API → Settings
   - Cocher "Enable ActiveX and Socket Clients"
   - **DÉCOCHER "Read-Only API"** (pour permettre les ordres)
   - Port: **4002** (IB Gateway paper) ou **7497** (TWS paper)
3. **Se connecter** avec vos identifiants paper trading

### Lancer MASOUNIA

```bash
python run.py
```

Le bot démarre et tourne en boucle toutes les heures. Pour arrêter: `Ctrl+C`

---

## Ce que vous recevrez

### Email de synthèse
Après chaque analyse, vous recevez un email avec:
- Résumé de l'analyse de marché
- Positions actuelles avec P&L
- Nouveaux ordres passés (si opportunités trouvées)
- Macro-contexte (Fed, ECB, VIX)

### Fichiers générés

**`orders.json`** - Plan d'ordres Grok avec:
- Résumé de l'analyse
- Liste des ordres (BUY/SELL)
- Score de confiance par ordre
- Sources vérifiées (7-10+ par trade)
- Timing précis du catalyseur
- Disclaimer et contexte macro

**`positions.json`** - État du portefeuille:
- Positions actuelles avec P&L
- Cash disponible
- Ordres en attente
- Métriques de compte

**`audit/YYYYMMDD_HHMMSS/`** - Historique complet:
- Données brutes Grok
- Messages de debug
- Erreurs éventuelles

---

## Stratégie de trading

MASOUNIA se concentre sur le **catalyst trading** à court terme:

### Types de catalyseurs
1. **FDA**: Approbations PDUFA (biotechs/pharma)
2. **Earnings**: Résultats trimestriels avec guidance
3. **M&A**: Fusions, acquisitions, partenariats
4. **Macro**: Fed/ECB, données économiques majeures

### Fenêtre de trading
- **Entrée (BUY)**: 2-48h avant le catalyseur
- **Sortie (SELL)**: Après l'événement OU si changement de thèse

### Critères de sélection
- Confidence ≥70% (70% si timing optimal 12-36h, 80% sinon)
- Minimum 7 sources récentes (mix: officielles, market data, analystes, sentiment)
- Liquidité >500K volume/jour
- Pas de répétition (évite de racheter un symbole récemment tradé)

### Gestion du risque
- Maximum 80% du cash utilisé
- 3-5 positions simultanées maximum
- Stop-loss implicite: SELL si perte >15%
- Take-profit: SELL si gain >15% sans catalyseur imminent
- SELL automatique des positions >7 jours sans catalyseur

---

## Configuration avancée

### Variables d'environnement (`.env`)

```bash
# API xAI (REQUIS)
XAI_API_KEY=xai-xxx

# Interactive Brokers
IBKR_HOST=127.0.0.1
IBKR_PORT=4002                    # 4002 = IB Gateway paper, 7497 = TWS paper
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=                     # Auto-détecté si vide
IBKR_BUDGET_TAG=AvailableFunds
IBKR_BUDGET_CURRENCY=EUR
IBKR_LIMIT_BUFFER_BPS=25          # Buffer sur prix limite (0.25%)
IBKR_MD_WAIT=1.5

# Email (optionnel)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=votre@email.com
SMTP_PASSWORD=votre_mot_de_passe
SMTP_FROM=MASOUNIA <votre@email.com>
SMTP_TO=destinataire@email.com
```

### Restrictions géographiques (Compte IBKR EU)

**ETFs:**
- ✅ ETFs UCITS seulement (domiciliés en Europe)
- ❌ ETFs US interdits (SPY, QQQ, VOO...) - régulation MiFID II
- Exchanges: Euronext (AMS, PAR), Xetra, LSE, SIX

**Actions:**
- ✅ Actions US accessibles (AAPL, MSFT, NVDA...)
- ✅ Actions européennes accessibles
- Format: ticker seul sans suffixe (.AS, .PA, .L)

---

## Sécurité et bonnes pratiques

### Pour commencer
1. **Toujours démarrer en paper trading** (port 4002 ou 7497)
2. Vérifier que "Read-Only API" est **DÉCOCHÉ** dans IB Gateway
3. Surveiller les premiers trades manuellement
4. Lire `orders.json` pour comprendre les décisions de Grok

### En production
- Garder IB Gateway/TWS ouvert pendant que le bot tourne
- Surveiller les emails de synthèse
- Vérifier régulièrement le dossier `audit/`
- Utiliser `Ctrl+C` pour arrêter proprement le bot
- Considérer un capital dédié limité (ex: 1000-5000 EUR)

### Limites et disclaimers
- **Ce n'est PAS un conseil en investissement**
- Les performances passées ne garantissent pas les résultats futurs
- Le trading comporte un risque de perte en capital
- MASOUNIA est un outil d'aide à la décision, pas une garantie de profit
- Toujours vérifier les trades et ajuster selon votre tolérance au risque

---

## Support

- **Issues GitHub**: [github.com/votre-repo/masounIA/issues](https://github.com)
- **Documentation complète**: Voir dossier `docs/` (si disponible)
- **Logs d'audit**: Consultez `audit/YYYYMMDD_HHMMSS/` pour déboguer

---

## Licence

MIT License - Voir fichier `LICENSE`

**Disclaimer:** MASOUNIA est fourni "tel quel" sans garantie. L'utilisation de ce logiciel est à vos propres risques. Les auteurs ne sont pas responsables des pertes financières.

---

**MASOUNIA** - Trading intelligent. Basé sur les faits, pas les émotions.
