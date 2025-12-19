# MasounIA - Grok + IBKR (explication simple)

Ce projet sert a:
- lire ton portefeuille IBKR (positions + budget dispo),
- demander a Grok une idee de trades long terme,
- obtenir un JSON d'ordres exploitable par un script.

Ce n'est pas un conseil financier. Utilise un compte paper avant tout passage en reel.

## Prerequis
- Python 3.9+
- TWS ou IB Gateway lance, API active
- Cle API xAI (Grok)

Dependances:
```
pip install ib_insync
```

## Configuration rapide
Dans `.env`, ajoute au minimum:
```
XAI_API_KEY=ta_cle_xai
```

Optionnel (si besoin):
```
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=U1234567
IBKR_BUDGET_TAG=AvailableFunds
IBKR_BUDGET_CURRENCY=EUR
```

## Scripts (en bref)
- `ibkr_export_positions.py`: exporte tes positions + budget dispo en JSON.
- `grok41_fast_search.py`: appelle Grok et retourne un plan d'ordres en JSON.
- `ibkr_place_orders.py`: lit le JSON et place (ou verifie) les ordres.
- `ibkr_grok_pipeline.py`: enchaine export -> Grok -> `orders.json`.

## Utilisation simple (pipeline)
```
python ibkr_grok_pipeline.py "Propose des trades long terme" --out orders.json
```

Resultat:
- `orders.json` contient un JSON structure (resume, points clefs, ordres).

## Utilisation pas a pas
1) Exporter les positions:
```
python ibkr_export_positions.py --out positions.json
```

2) Appeler Grok avec tes positions:
```
python grok41_fast_search.py --positions positions.json "Propose des trades long terme"
```

3) Verifier ou placer les ordres:
```
python ibkr_place_orders.py orders.json --check
python ibkr_place_orders.py orders.json --submit
```

## Budget
Le budget est lu depuis IBKR via le tag `AvailableFunds` en EUR (par defaut).
Tu peux changer via:
```
--budget-tag --budget-currency
```

## Conseils pratiques
- Commence en paper trading.
- Verifie chaque ordre (symbole, quantite, devise, type d'ordre).
- Les ordres sont pensés long terme, pas pour du trading intraday.
