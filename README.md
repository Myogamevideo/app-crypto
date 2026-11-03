# Atlas Crypto

Tableau de bord privé et auto-hébergé pour synchroniser un compte Coinbase, suivre les transactions et estimer la performance du portefeuille en EUR. Sur mobile, les tableaux sont remplacés par des cartes lisibles; le tableau de bord présente aussi l’évolution de la valeur, la répartition des actifs, les flux mensuels, les gains réalisés/latents et la couverture des PRU.

## Démarrage avec Docker

1. Copiez `.env.example` vers `.env`.
2. Définissez un mot de passe fort (`APP_PASSWORD`) et une clé de session aléatoire (`APP_SECRET_KEY`, générable avec `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`). Limitez l’accès au fichier `.env` (`chmod 600 .env`).
3. Créez une clé API CDP Coinbase limitée à la lecture des comptes et transactions. Ajoutez son nom et sa clé privée dans `.env`. N’activez ni trading ni transferts.
4. Lancez `docker compose up --build -d`, puis ouvrez `http://localhost:8000`.
5. Connectez-vous, puis lancez **Synchroniser**. Le service `sync` répète l’import toutes les 15 minutes.

Les clés Coinbase restent côté serveur dans les variables d’environnement et ne sont jamais envoyées au navigateur. La base SQLite est conservée dans un volume Docker. Le port est limité à `127.0.0.1`; pour un accès distant, configurez un reverse proxy HTTPS et `COOKIE_SECURE=true`. Ne publiez pas directement le port sur Internet.

Les modifications du code ne sont pas montées en direct dans les conteneurs : reconstruisez et redémarrez avec `docker compose up --build -d`. Après une simple modification de `.env`, relancez `docker compose up -d`; un rebuild n’est pas nécessaire.

## Calculs et limites

- Les transactions Coinbase sont importées par compte et toutes les pages du curseur API sont parcourues; les imports ultérieurs mettent à jour les éléments existants sans les dupliquer. L’interface indique le nombre d’opérations et les dates couvertes, et propose recherche, filtres, tri et pagination.
- Chaque synchronisation enregistre aussi les soldes actuels des comptes Coinbase. À la date du jour, les quantités du portefeuille sont réconciliées sur ces soldes; tout écart supérieur à 0,50 € apparaît dans « Qualité des données » et rend le PRU/gain concerné incomplet. Les périodes passées restent calculées à partir des transactions importées.
- Le panneau « Rapprochement Coinbase » détaille, par devise, le solde de l’API, le solde reconstitué par les transactions, l’écart et sa valeur EUR si le cours est disponible, l’état du PRU et la dernière transaction liée. Il est limité à la date du jour et ne corrige pas rétroactivement le registre.
- Le suivi par crypto utilise le PRU moyen; les cours actuels et historiques sont demandés à Coinbase en priorité. En secours, l’application essaie les chandeliers publics Binance, puis l’API publique CoinGecko (clé facultative). Pour MATIC après sa migration 1:1 vers POL, le cours de POL est utilisé; pour POLY (Polymath), CoinGecko est utilisé quand Binance n’a plus de chandeliers.
- Les données d’exchange ne couvrent que les jours où une paire avait des échanges. VET/EUR et NEAR/EUR n’ont pas de chandeliers début 2021 sur Binance; le calcul utilise alors les paires USDT/BTC. Les cours CoinGecko sont soumis aux limites du service public ou de la clé configurée et peuvent ne pas être disponibles pour chaque date. Des actifs lancés après 2021 (par exemple ONDO ou RONIN) n’ont naturellement pas de cours avant leur cotation. Les cours journaliers sont des estimations, pas des cotations à l’heure exacte de chaque transaction.
- Les fills Coinbase sont traités jambe par jambe : le signe du montant de chaque crypto détermine achat ou vente (par exemple, une jambe USDC positive lors d’une vente ETH/USDC est une acquisition d’USDC); les jambes fiat suivent le sens `order_side`. Les mouvements de staking, transferts internes et migrations ETH2 sont neutres dans les avoirs consolidés et l’estimation fiscale.
- `retail_simple_dust` est traité comme une cession à produit nul : la quantité est retirée et, si son PRU est connu, sa base devient une perte réalisée. L’opération reste incomplète si la quantité détruite dépasse les avoirs reconstitués ou si leur PRU est inconnu.
- Si Coinbase renvoie une valeur native nulle pour une récompense, une estimation en EUR utilise le cours historique quotidien de l’actif; faute de cours, le PRU reste marqué incomplet.
- Pour une crypto gagnée sur un pari, recatégorisez la réception en `Gain de pari`. Si Coinbase ne fournit pas de valeur native, elle est estimée au cours EUR quotidien et ajoutée à la base du PRU; elle est ventilée séparément dans l’activité annuelle, pas comme un achat ni comme une plus-value de vente. Cette valorisation de portefeuille n’établit pas le traitement fiscal du gain : l’estimation fiscale française reste indisponible pour ces entrées externes.
- Les autres dépôts crypto entrants sans valeur native exploitable sont également estimés au cours EUR quotidien pour éviter une base manquante, mais il faut vérifier s’il s’agit d’un transfert personnel, d’un don ou d’un autre revenu. Un cours absent laisse la base incomplète.
- Pour les achats, ventes, récompenses et gains de pari, toute valeur EUR inférieure à 0,50 € est ramenée à 0,00 €; si la valeur native manque, le cours de l’actif à la date sert seulement à établir ce seuil. Un excédent vendu ou transféré sur le solde est ignoré comme poussière si sa valeur estimée au cours de la transaction est inférieure à 0,50 €; au-delà, l’anomalie reste signalée et le PRU incomplet.
- Le symbole historique ETH2 est regroupé avec ETH et valorisé via les marchés ETH-EUR, car les produits de cotation ETH2 ne sont plus disponibles.
- Les types non reconnus restent classés « Other » et peuvent être recatégorisés dans l’interface. Les commissions ne sont pas isolées automatiquement dans le calcul du PRU ou des plus-values; vérifiez les exports Coinbase pour leur traitement fiscal, et ne déduisez jamais une commission au moyen d’une règle fixe liée à une devise.
- Le tableau « Activité par année » ventile les flux par année et depuis l’import : les ventes sont négatives, les achats positifs, et P/G réalisée suit le PRU moyen disponible. Le pourcentage affiché est P/G réalisée divisée par les dépôts EUR nets, pas un rendement annualisé; les cours ou bases manquants sont affichés « — ».
- Les graphiques tracent les valorisations mensuelles en EUR sur les 12 derniers mois et les flux crypto catégorisés. Les variations sur 1, 3, 6 et 12 mois sont la différence de P/G réalisée + latente entre deux dates de comparaison, pas un rendement annualisé; elles sont masquées si les PRU ou les cours requis sont incomplets. La valeur actuelle des actifs sans PRU fiable est une exposition estimée, pas le coût d’acquisition manquant.
- Les comptes d’opérations comptent les lignes Coinbase importées (pas les ordres uniques); les deux jambes d’un trade ne sont comptées dans BUY/SELL que côté crypto. La différence des transferts reçus/envoyés est un flux estimé, pas un bénéfice.
- Le repère fiscal applique la formule française au portefeuille global uniquement si l’utilisateur confirme que l’historique et les actifs sont complets. Les retraits crypto, avoirs hors Coinbase, cours manquants ou bases inconnues rendent l’estimation indisponible. Les cours journaliers et la valeur affichée ne constituent pas une déclaration fiscale ni un conseil professionnel.
- L’API Coinbase requiert une clé valide avec les droits de lecture `view`. OAuth est réservé aux partenaires Coinbase approuvés; cette application est conçue pour un usage personnel.

## Développement

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests
```

Le démarrage local requiert `APP_PASSWORD` et `APP_SECRET_KEY`. La clé Coinbase peut être laissée absente pendant le développement; l’interface affichera alors l’étape de configuration.
