# MasounIA - Bot de Trading IA avec Grok

Trading automatique base sur l'actualite en temps reel.
Grok analyse les news (web + X) et place des ordres sur Interactive Brokers.

**IMPORTANT**: Ce n'est PAS un conseil financier. Utilise un compte paper avant le reel.

---

## Utilisation Ultra-Simple

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Configuration
Copie `.env.example` vers `.env` et ajoute ta cle API xAI:
```bash
XAI_API_KEY=ta_cle_xai_ici
```

### 3. Lance TWS ou IB Gateway
- Active l'API (Configuration > API > Enable ActiveX and Socket Clients)
- DECOCHER "Read-Only API"
- Port paper trading: 4002 (IB Gateway) ou 7497 (TWS)

### 4. Lance le bot
```bash
python run.py
```

**C'est tout !** Le bot va tourner **en boucle toutes les heures** :
1. Recuperer ton portefeuille IBKR
2. Analyser les news des dernieres 48-72h (web + X search autonome)
3. Proposer des trades bases sur l'actualite
4. Placer les ordres automatiquement
5. Attendre 1 heure et recommencer

**Arreter le bot** : Appuie sur `Ctrl+C`

---

## Comment ca marche ?

Le bot utilise **Grok 4.1 Fast** (xAI) avec acces a:
- **Web Search**: recherche en temps reel sur le web
- **X Search**: analyse du sentiment sur X/Twitter
- **Agentic Tools**: execution autonome des recherches cote serveur

### Strategie
- Scan des news majeures (earnings, Fed, geopolitique...)
- Analyse du sentiment sur X
- Identification de catalyseurs court/moyen terme
- Verification croisee de multiples sources
- Generation d'ordres avec justification detaillee
- Recherches autonomes cote serveur (plus rapide)

### Securite
- Budget toujours depuis ton compte IBKR
- Validation des ventes (ne peut pas vendre plus que detenu)
- Ordres LIMIT avec buffer automatique
- Limite de 3-5 positions maximum

### Restrictions geographiques (Compte IBKR europeen)
- ETFs: Uniquement UCITS ETFs (domicilies en Europe)
  * Listes sur: Euronext Amsterdam, Paris, Xetra, LSE
  * JAMAIS d'ETFs US - non disponibles en Europe (regulation MiFID II)
- Actions: Actions US accessibles (entreprises individuelles)
- Format: Ticker seul sans suffixe d'exchange (.AS, .PA, .L, etc.)

---

## Configuration Avancee (optionnel)

Dans `.env`, tu peux configurer:
```bash
IBKR_HOST=127.0.0.1          # Adresse IBKR
IBKR_PORT=4002               # Port (4002=IB Gateway paper, 7497=TWS paper)
IBKR_CLIENT_ID=1             # ID client unique
IBKR_ACCOUNT=U1234567        # Numero de compte (auto-detecte si vide)
IBKR_BUDGET_TAG=AvailableFunds
IBKR_BUDGET_CURRENCY=EUR
IBKR_LIMIT_BUFFER_BPS=25     # Buffer prix limite (25 basis points)
IBKR_MD_WAIT=1.5             # Attente donnees marche (secondes)
```

---

## Fichiers generes

- `positions.json`: Export du portefeuille
- `orders.json`: Ordres generes par Grok (avec sources et justifications)

---

## Scripts Internes (avance)

Si tu veux plus de controle, tu peux utiliser les scripts individuellement:

```bash
# Exporter positions seulement
python ibkr_export_positions.py --out positions.json

# Analyser avec Grok seulement
python grok41_fast_search.py --positions positions.json

# Placer ordres seulement (sans soumettre)
python ibkr_place_orders.py orders.json --check

# Pipeline complet avec options
python ibkr_grok_pipeline.py --query "Focus secteur tech" --check
```

---

## Conseils de Securite

1. **Commence en PAPER TRADING** (port 4002 pour IB Gateway, 7497 pour TWS)
2. DECOCHER "Read-Only API" dans les parametres API
3. Le bot tourne en BOUCLE toutes les heures - surveille-le regulierement
4. Verifie les fichiers `orders.json` generes pour comprendre les decisions
5. Le budget est automatiquement limite a ce qui est disponible
6. Les ordres sont des suggestions IA, pas des garanties
7. Garde IB Gateway ouvert pendant que le bot tourne
8. Utilise `Ctrl+C` pour arreter proprement le bot
