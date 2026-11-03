from collections import defaultdict


def canonical_asset(asset):
    symbol = (asset or "").upper()
    return "ETH" if symbol == "ETH2" else symbol


def _asset_transactions(rows, asset, cutoff):
    return [
        row for row in rows
        if canonical_asset(row["amount_currency"]) == asset
        and row["created_at"][:10] <= cutoff
    ]


def build_market_quality_issues(price_errors, cached_prices, rows, cutoff):
    grouped = defaultdict(lambda: {"dates": set(), "errors": set()})
    for (asset, day), error in price_errors.items():
        grouped[asset]["dates"].add(day)
        grouped[asset]["errors"].add(error)
    for (asset, day), price in cached_prices.items():
        if price is None and day <= cutoff:
            grouped[asset]["dates"].add(day)

    issues = []
    for asset, details in sorted(grouped.items()):
        related = _asset_transactions(rows, asset, cutoff)
        dates = sorted(details["dates"])
        error = "; ".join(sorted(details["errors"]))
        date_summary = (
            f"{len(dates)} date(s), notamment {', '.join(dates[:3])}"
            if dates
            else "date non précisée"
        )
        issues.append({
            "kind": "Cours EUR",
            "asset": asset,
            "date": dates[-1] if dates else "",
            "dates": dates,
            "date_summary": date_summary,
            "transaction_id": related[-1]["id"] if related else "",
            "transaction_ids": [row["id"] for row in related[-5:]],
            "problem": error or (
                f"Aucun cours EUR n’a été retourné par les sources consultées "
                f"pour {asset} ({date_summary})."
            ),
            "recommendation": (
                "Les cours sont recherchés auprès de Coinbase puis de Binance, et de "
                "CoinGecko public (clé API facultative). Vérifiez la disponibilité des "
                "chandeliers pour chaque date; si aucune source ne couvre ces jours, "
                "ajoutez une valorisation fiable avant de considérer le PRU et les "
                "performances comme complets."
            ),
        })
    return issues


def add_transaction_issue(issues, transaction, kind, problem, recommendation):
    issues.append({
        "kind": kind,
        "asset": transaction["asset"],
        "date": transaction["date"][:10],
        "transaction_id": transaction["id"],
        "problem": problem,
        "recommendation": recommendation,
    })


def add_unattributed_basis_issues(issues, assets, transactions):
    reported_assets = {issue["asset"] for issue in issues if issue["asset"]}
    for asset in assets:
        if asset["basis_known"] or asset["asset"] in reported_assets:
            continue
        related = [
            tx for tx in transactions
            if tx["asset"] == asset["asset"]
            and tx["category"] in {"Deposit", "Withdrawal", "Other"}
        ]
        if not related:
            related = [tx for tx in transactions if tx["asset"] == asset["asset"]]
        transaction = related[-1] if related else {}
        issues.append({
            "kind": "PRU incomplet",
            "asset": asset["asset"],
            "date": transaction.get("date", "")[:10],
            "transaction_id": transaction.get("id", ""),
            "problem": (
                f"La base d’acquisition de {asset['asset']} reste incomplète à partir "
                "des opérations et valorisations disponibles."
            ),
            "recommendation": (
                "Vérifiez les transferts entrants et sortants ainsi que les cours historiques; "
                "ajoutez l’historique source ou une valorisation fiable si nécessaire."
            ),
        })


def summarize_quality_issues(issues, limit=100):
    ordered = sorted(
        issues,
        key=lambda issue: (issue["date"], issue["kind"], issue["transaction_id"]),
        reverse=True,
    )
    return {
        "count": len(ordered),
        "items": ordered[:limit],
        "truncated": len(ordered) > limit,
    }


def build_status_issues(rows, cutoff):
    issues = []
    for row in rows:
        if row["created_at"][:10] > cutoff or row["status"] in {"completed", "complete"}:
            continue
        issues.append({
            "kind": "Statut",
            "asset": canonical_asset(row["amount_currency"]),
            "date": row["created_at"][:10],
            "transaction_id": row["id"],
            "problem": f"Opération non finalisée : statut « {row['status']} ».",
            "recommendation": (
                "Vérifiez son statut dans Coinbase. Elle est exclue des soldes et des calculs "
                "tant qu’elle n’est pas terminée."
            ),
        })
    return issues
