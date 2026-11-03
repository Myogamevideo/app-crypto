# Guide du dépôt — Atlas Crypto

## Contexte du projet

Application privée et auto-hébergée de suivi de portefeuille crypto, avec synchronisation Coinbase en lecture seule, tableau de bord en EUR, catégories modifiables, PRU moyen et repère fiscal français prudent.

## Consignes pour les prochaines modifications

- Maintenir ce fichier à jour au fil de la session lorsque l’architecture, les fonctionnalités, les commandes, les variables de configuration ou les tests changent. Garder les consignes durables ici; ne pas en faire un journal de conversation.
- Répondre et documenter en français, sauf si le code ou les conventions de dépendances demandent l’anglais.
- Préserver une interface originale; ne pas copier les marques, contenus ou interfaces de Waltio, Koinly, Blockpit ou Binance Tax.
- Ne jamais demander, afficher, journaliser ou committer de véritables clés Coinbase, mots de passe ou secrets. Les secrets résident uniquement dans `.env`, qui doit rester ignoré par Git.
- Conserver les clés Coinbase côté serveur et en lecture seule. Ne jamais ajouter de permissions d’envoi, transfert ou trading à la synchronisation.
- Ne présenter aucun indicateur de performance ou calcul fiscal comme fiable lorsque les transactions, les prix EUR, le portefeuille global ou le coût d’acquisition sont incomplets. Expliquer visiblement les limites.
- Garder la génération des graphiques historiques bornée à une fenêtre de 12 mois pour éviter que les appels de cours pendant le rendu dépassent le délai des workers web.
- Garder la navigation accessible au clavier et lisible sur petits écrans; remplacer les tableaux de synthèse, d’avoirs et de transactions par des cartes empilées sur mobile. Garder les tableaux complets sur grand écran.
- Préserver les limites d’historique et de cours dans les graphiques et indicateurs; masquer une mesure de performance lorsque sa base ou ses valorisations de comparaison sont incomplètes.
- Le panneau « Qualité des données » du dashboard regroupe les opérations et valorisations à vérifier, conserve le détail des erreurs de marché, et relie les problèmes aux transactions Coinbase correspondantes.
- Les synchronisations automatiques et manuelles enregistrent les soldes Coinbase actuels. Pour la date du jour uniquement, réconcilier les quantités sur ces soldes et signaler tout écart matériel; ne jamais inventer la base de coût manquante. Les dates historiques restent fondées sur le registre de transactions.
- Les soldes instantanés sont agrégés par devise, y compris les soldes fiat et EURC; ils servent à la quantité et à la valeur courantes, jamais à reconstituer un historique. Un écart supérieur ou égal à 0,50 € invalide le PRU et les performances de l’actif concerné, même si sa quantité est alignée sur Coinbase.
- Le panneau « Rapprochement Coinbase » expose par actif le solde de l’API, la quantité du registre avant réconciliation, l’écart signé, sa valeur EUR si connue, l’état du PRU et la dernière transaction associée. Il ne concerne que la date courante; garder le registre brut intact et ne pas présenter la quantité réconciliée comme une correction de l’historique.
- Classer les mouvements internes de staking et transferts sans les compter comme achats/ventes; ne pas déduire de frais selon une règle fixe de devise ou sans donnée de frais explicite.
- Valoriser les dépôts crypto entrants (receive/envoi positif classé Deposit) sans valeur native exploitable au cours EUR historique du jour; utiliser cette estimation comme base d’acquisition pour le PRU, mais pas comme plus-value réalisée. Si le cours manque, conserver un diagnostic. Ne pas traiter cette convention d’estimation comme une conclusion fiscale : le calcul français reste prudent pour les entrées externes non documentées.
- Permettre de recatégoriser une crypto gagnée sur un pari en `Gain de pari`; utiliser son cours EUR journalier comme base indicative du PRU, l’afficher distinctement dans l’activité annuelle, et garder le calcul fiscal français incomplet pour ce type d’entrée externe.
- Ramener à zéro les valeurs EUR inférieures à 0,50 € pour achats, ventes, récompenses et gains de pari; un cours historique peut estimer une opération à valeur native manquante afin d’appliquer le seuil. Tolérer seulement un dépassement de solde lors d’une vente ou d’un transfert sortant dont la valeur au cours de transaction est inférieure à 0,50 €; conserver les diagnostics et l’incomplétude au-dessus du seuil ou si sa valeur ne peut pas être estimée.
- Utiliser `Decimal` pour les montants de transactions, de prix et de calculs financiers.
- Pour l’API Coinbase, signer chaque requête privée avec un JWT ES256 valide, limiter la pagination à l’hôte officiel et définir des délais d’attente réseau.
- Suivre les conventions de sécurité existantes : authentification, jeton CSRF pour les mutations, cookies HTTP-only, en-têtes de sécurité et base SQLite persistante.
- Garder `.env.example` à jour si les variables de configuration changent; c’est un modèle sans secrets qui doit rester suivi par Git.

## Architecture

- `crypto_dashboard/__init__.py` : configuration Flask, authentification, routes, filtres, tri et pagination SQL, rendu et mise en cache des cours.
- `crypto_dashboard/coinbase.py` : authentification CDP et récupération paginée des comptes et transactions.
- `crypto_dashboard/database.py` : schéma et accès SQLite, y compris les instantanés de soldes Coinbase.
- `crypto_dashboard/ledger.py` : catégories (y compris les transferts neutres), normalisation Coinbase, calcul du portefeuille au PRU moyen, rapprochement des soldes courants, séries historiques et synthèse annuelle des flux/résultats.
- `crypto_dashboard/charts.py` : agrégats mensuels, indicateurs de couverture du PRU et géométrie SVG accessible des graphiques.
- `crypto_dashboard/market.py` : cours Coinbase en priorité, puis chandeliers publics Binance convertis en EUR et CoinGecko public en dernier recours; après la migration 1:1 de MATIC, utiliser POL sur Binance/CoinGecko.
- `crypto_dashboard/quality.py` : diagnostics des transactions non finalisées, coûts d’acquisition inconnus et cours EUR absents ou en erreur.
- `crypto_dashboard/french_tax.py` : estimation des cessions françaises au niveau du portefeuille complet.
- `crypto_dashboard/templates/` et `crypto_dashboard/static/` : interface française.
- `sync_worker.py` : synchronisation automatique périodique.
- `Dockerfile` et `docker-compose.yml` : déploiement auto-hébergé.
- `tests/` : tests unitaires, tests Flask, tests Coinbase et cours de marché.

## Configuration et commandes

Variables documentées dans `.env.example` :

- `APP_PASSWORD` et `APP_SECRET_KEY` : requis pour démarrer.
- `COINBASE_API_KEY_NAME` et `COINBASE_API_PRIVATE_KEY` : clé CDP en lecture seule; facultatifs pour afficher l’écran de configuration, requis pour synchroniser.
- `COINGECKO_API_KEY` : facultative, clé CoinGecko Demo pour améliorer les limites du dernier recours public si Coinbase et Binance n’ont pas de cours.
- `DATABASE_PATH` : chemin SQLite persistant.
- `COOKIE_SECURE` : activer les cookies sécurisés derrière HTTPS.

Déploiement : copier un modèle `.env` vers `.env`, compléter la configuration, puis exécuter `docker compose up --build -d`. Le port est publié uniquement sur `127.0.0.1:8000`.

Les fichiers source ne sont pas montés en direct dans les conteneurs : après une modification de code, reconstruire avec `docker compose up --build -d`. Après une modification de `.env` seule, `docker compose up -d` suffit.

L’écran des transactions affiche les dates extrêmes réellement importées depuis Coinbase. Si la date de début est trop récente, vérifier les permissions de lecture des transactions Coinbase et confirmer que l’API renvoie des liens `pagination.next_uri` jusqu’aux opérations anciennes.

Les fills `advanced_trade_fill` sont classés jambe par jambe selon le signe du montant crypto; les jambes fiat suivent `order_side`, et ce champ est le repli pour une jambe crypto de montant nul. `ETH2` est un alias de `ETH` pour les cours et les avoirs; ses transferts de migration restent neutres. Les `staking_transfer`, `unstaking_transfer`, `retail_eth2_deprecation` et transferts internes sont neutres dans le solde consolidé et l’estimation fiscale. Les `pro_deposit` et `pro_withdrawal` crypto à quantité négative diminuent les avoirs et la base au PRU moyen, sans créer de vente ni de gain/perte réalisée. Les récompenses avec une valeur native nulle peuvent utiliser le cours historique de l’actif, sinon leur PRU reste incomplet. Ne jamais supposer une commission fixe : les frais ne sont intégrés que lorsque leur devise et leur relation aux jambes sont établies sans double comptage.

`retail_simple_dust` est une cession à produit nul dans le registre du portefeuille : retirer la quantité et réaliser une perte correspondant au PRU connu; garder le résultat incomplet si les avoirs ou leur coût d’acquisition ne sont pas établis. Les dépôts crypto entrants sans valeur native exploitable prennent le cours EUR journalier comme base d’acquisition estimée; les gains de pari peuvent être recatégorisés et ventilés séparément, sans être considérés comme des plus-values réalisées ni comme une conclusion fiscale.

Les valeurs EUR inférieures à 0,50 € des opérations BUY/SELL/récompenses/gains de pari sont affichées comme 0,00 €. Un dépassement de quantité vendue ou transférée sortante est toléré uniquement si l’excédent vaut moins de 0,50 € au cours de transaction; sinon il reste un problème de qualité.

Le rapport annuel utilise une ligne par année présente et « depuis le début ». Les ventes sont négatives, les achats positifs; P/G réalisée utilise les événements de vente et le PRU moyen disponible. Le pourcentage est P/G réalisée divisée par les dépôts EUR nets, jamais un rendement annualisé. Afficher « — » si les prix ou la base sont incomplets ou si le dénominateur est nul. Le nombre de lignes est le nombre de transactions Coinbase importées, et non le nombre d’ordres uniques. Les graphiques de portefeuille sont limités aux 12 derniers mois; les appels de cours doivent rester bornés pour éviter les délais d’attente des workers web.

Tests : `python -m unittest discover -s tests -v`. Validation de syntaxe : `python -m compileall -q crypto_dashboard run.py sync_worker.py`.
