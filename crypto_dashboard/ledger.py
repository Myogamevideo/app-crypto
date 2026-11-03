import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from .charts import build_dashboard_charts, history_dates
from .quality import (
    add_transaction_issue,
    add_unattributed_basis_issues,
    build_status_issues,
    summarize_quality_issues,
)

CATEGORIES = (
    "BUY",
    "SELL",
    "DIVIDEND",
    "Gain de pari",
    "Deposit",
    "Withdrawal",
    "Transfer",
    "Trade INT",
    "Other",
)

FIAT_CURRENCIES = {
    "EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "NZD", "SGD", "MXN",
    "BRL", "ZAR", "SEK", "NOK", "DKK", "PLN", "TRY", "EURC",
}
ZERO = Decimal("0")
NEGLIGIBLE_EUR_THRESHOLD = Decimal("0.50")


def decimal_value(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def canonical_asset(asset):
    symbol = (asset or "").upper()
    return "ETH" if symbol == "ETH2" else symbol


def default_category(transaction_type, amount=None, asset="", raw=None):
    raw = raw or {}
    amount = decimal_value(amount)
    asset = (asset or "").upper()
    trade = raw.get("advanced_trade_fill") or {}
    if not isinstance(trade, dict):
        trade = {}
    order_side = (trade.get("order_side") or raw.get("order_side") or "").lower()

    if transaction_type in {
        "staking_transfer", "unstaking_transfer", "retail_eth2_deprecation",
    }:
        return "Transfer"
    if transaction_type in {"earn_payout", "staking_reward", "interest",
                            "incentives_rewards_payout", "subscription_rebate"}:
        return "DIVIDEND"
    if transaction_type == "buy":
        return "BUY"
    if transaction_type == "sell":
        return "SELL"
    if transaction_type in {"fiat_deposit", "fiat_withdrawal"}:
        return "Deposit" if transaction_type == "fiat_deposit" else "Withdrawal"
    if transaction_type == "send":
        return "Deposit" if amount is not None and amount > ZERO else "Withdrawal"
    if transaction_type == "receive":
        return "Deposit"
    if transaction_type == "pro_withdrawal" and asset in FIAT_CURRENCIES:
        return "Withdrawal"
    if transaction_type == "pro_deposit" and asset in FIAT_CURRENCIES:
        return "Deposit"
    if transaction_type in {"pro_deposit", "pro_withdrawal", "intx_deposit", "intx_withdrawal"}:
        return "Transfer"
    if transaction_type == "advanced_trade_fill":
        if asset in FIAT_CURRENCIES and order_side in {"buy", "sell"}:
            return "BUY" if order_side == "buy" else "SELL"
        if amount is not None and amount > ZERO:
            if asset not in FIAT_CURRENCIES:
                return "BUY"
        if amount is not None and amount < ZERO:
            if asset not in FIAT_CURRENCIES:
                return "SELL"
        if order_side in {"buy", "sell"}:
            if asset not in FIAT_CURRENCIES:
                return "BUY" if order_side == "buy" else "SELL"
    if transaction_type in {"trade", "tx"} and amount is not None:
        return "BUY" if amount > ZERO else "SELL"
    if transaction_type == "retail_simple_dust":
        return "SELL"
    if transaction_type in {"wrap_asset", "unwrap_asset"}:
        return "Transfer"
    return "Other"


def normalized_transaction(row, category_overrides):
    if isinstance(row, dict):
        item = row
        raw = item.get("raw") or item
    else:
        item = dict(row)
        try:
            raw = json.loads(item.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
    transaction_id = item.get("id") or raw.get("id") or ""
    transaction_type = item.get("type") or raw.get("type") or "tx"
    amount = raw.get("amount")
    if not isinstance(amount, dict):
        amount = {
            "amount": item.get("amount"),
            "currency": item.get("amount_currency"),
        }
    native_amount = raw.get("native_amount")
    if not isinstance(native_amount, dict):
        native_amount = {
            "amount": item.get("native_amount"),
            "currency": item.get("native_currency"),
        }
    amount_decimal = decimal_value(amount.get("amount"))
    native_decimal = decimal_value(native_amount.get("amount"))
    return {
        "id": transaction_id,
        "type": transaction_type,
        "status": item.get("status") or raw.get("status") or "unknown",
        "date": item.get("created_at") or raw.get("created_at") or "",
        "asset": canonical_asset(amount.get("currency")),
        "amount": amount_decimal if amount_decimal is not None else ZERO,
        "native_amount": native_decimal,
        "native_currency": native_amount.get("currency") or "",
        "description": item.get("description") or raw.get("description") or "",
        "category": category_overrides.get(transaction_id)
        or default_category(transaction_type, amount_decimal, amount.get("currency"), raw),
    }


def _eur_value(tx, price_for=None):
    if tx.get("type") == "retail_simple_dust" and tx["category"] == "SELL":
        return ZERO
    native_amount = tx["native_amount"]
    value = None
    if native_amount not in (None, ZERO):
        if tx["native_currency"] == "EUR":
            value = abs(native_amount)
        elif tx["native_currency"] in FIAT_CURRENCIES and price_for:
            rate = price_for(tx["native_currency"], tx["date"][:10])
            if rate is not None:
                value = abs(native_amount) * rate
    estimated_categories = {
        "BUY", "SELL", "DIVIDEND", "Gain de pari", "Deposit", "Trade INT",
    }
    if (
        value is None
        and tx["category"] in estimated_categories
        and price_for
        and tx["asset"]
    ):
        price = price_for(tx["asset"], tx["date"][:10])
        if price is not None:
            estimate = abs(tx["amount"]) * price
            if tx["category"] in {"DIVIDEND", "Gain de pari", "Deposit"}:
                value = estimate
            elif estimate < NEGLIGIBLE_EUR_THRESHOLD:
                value = ZERO
    if (
        value is not None
        and tx["category"] in {"BUY", "SELL", "DIVIDEND", "Gain de pari", "Trade INT"}
        and abs(value) < NEGLIGIBLE_EUR_THRESHOLD
    ):
        return ZERO
    return value


def _negligible_oversell(tx, excess_quantity, eur_value, price_for):
    if eur_value is not None and tx["amount"] != ZERO:
        excess_value = abs(eur_value) * excess_quantity / abs(tx["amount"])
    elif price_for and tx["asset"]:
        price = price_for(tx["asset"], tx["date"][:10])
        excess_value = excess_quantity * price if price is not None else None
    else:
        excess_value = None
    return (
        excess_value is not None
        and excess_value < NEGLIGIBLE_EUR_THRESHOLD
    )


def build_activity_report(
    transactions, realized_by_year, realized_complete_by_year, price_for=None
):
    years_present = [int(tx["date"][:4]) for tx in transactions if len(tx["date"]) >= 4]
    years = list(range(min(years_present), max(years_present) + 1)) if years_present else []
    labels = (
        "EURWithdrawal", "EURDeposit", "Total", "DIVIDEND", "GAIN DE PARI", "EARN_PAYOUT",
        "BUY", "SELL", "RECEIVED", "SEND", "P/G", "%",
    )
    counts_labels = (
        "EURWithdrawal", "EURDeposit", "DIVIDEND_TRANSFERT", "DIVIDEND",
        "EARN_PAYOUT", "GAIN DE PARI", "BUY", "SELL", "RECEIVED", "SEND",
    )
    amounts = {
        label: {year: ZERO for year in years}
        for label in labels
        if label not in {"Total", "P/G", "%"}
    }
    amount_complete = {
        label: {year: True for year in years}
        for label in labels
        if label not in {"Total", "P/G", "%"}
    }
    counts = {label: {year: 0 for year in years} for label in counts_labels}
    line_counts = {year: 0 for year in years}

    def record(label, tx, value=None, count_label=None):
        year = int(tx["date"][:4])
        if year not in line_counts:
            return
        if value is not None:
            amounts[label][year] += value
        elif label in amount_complete:
            amount_complete[label][year] = False
        counts[count_label or label][year] += 1

    for tx in transactions:
        if len(tx["date"]) < 4:
            continue
        year = int(tx["date"][:4])
        if year not in line_counts:
            continue
        line_counts[year] += 1
        asset = tx["asset"].upper()
        kind = tx["type"]
        category = tx["category"]

        if kind in {"staking_transfer", "unstaking_transfer", "retail_eth2_deprecation"}:
            counts["DIVIDEND_TRANSFERT"][year] += 1
            continue

        if asset in FIAT_CURRENCIES:
            if kind in {"fiat_deposit", "pro_deposit"}:
                value = _eur_value(tx, price_for)
                record("EURDeposit", tx, value, "EURDeposit")
            elif kind in {"fiat_withdrawal", "pro_withdrawal"}:
                value = _eur_value(tx, price_for)
                record("EURWithdrawal", tx, -value if value is not None else None, "EURWithdrawal")
            continue

        if kind == "earn_payout":
            value = _eur_value(tx, price_for)
            record("EARN_PAYOUT", tx, value, "EARN_PAYOUT")
        elif category == "DIVIDEND":
            value = _eur_value(tx, price_for)
            record("DIVIDEND", tx, value, "DIVIDEND")
        elif category == "Gain de pari":
            value = _eur_value(tx, price_for)
            record("GAIN DE PARI", tx, value, "GAIN DE PARI")
        elif category == "BUY":
            value = _eur_value(tx, price_for)
            record("BUY", tx, value, "BUY")
        elif category == "SELL":
            value = _eur_value(tx, price_for)
            record("SELL", tx, -value if value is not None else None, "SELL")
        elif kind == "receive" or (kind == "send" and tx["amount"] > ZERO):
            value = _eur_value(tx, price_for)
            record("RECEIVED", tx, value, "RECEIVED")
        elif kind == "send" and tx["amount"] < ZERO:
            value = _eur_value(tx, price_for)
            record("SEND", tx, -value if value is not None else None, "SEND")

    year_values = {label: [] for label in labels}
    all_years = ["all_time", *years]

    def net_deposits(year):
        relevant_years = years if year == "all_time" else [year]
        if not all(
            amount_complete[label][y]
            for label in ("EURDeposit", "EURWithdrawal")
            for y in relevant_years
        ):
            return None
        return sum(
            (
                sum((amounts[label][y] for y in relevant_years), ZERO)
                for label in ("EURDeposit", "EURWithdrawal")
            ),
            ZERO,
        )

    for label in labels:
        for year in all_years:
            if label == "Total":
                value = net_deposits(year)
            elif label == "P/G":
                if year == "all_time":
                    relevant_years = years
                    value = (
                        sum((realized_by_year[y] for y in relevant_years), ZERO)
                        if all(realized_complete_by_year.get(y, True) for y in relevant_years)
                        else None
                    )
                else:
                    value = (
                        realized_by_year.get(year, ZERO)
                        if realized_complete_by_year.get(year, True)
                        else None
                    )
            elif label == "%":
                realized = year_values["P/G"][all_years.index(year)]
                net_deposit_value = net_deposits(year)
                value = (
                    realized / net_deposit_value * 100
                    if realized is not None and net_deposit_value
                    else None
                )
            else:
                if year == "all_time":
                    value = (
                        sum((amounts[label][y] for y in years), ZERO)
                        if all(amount_complete[label][y] for y in years)
                        else None
                    )
                else:
                    value = amounts[label][year] if amount_complete[label][year] else None
            year_values[label].append(value)

    count_values = {}
    for label in counts_labels:
        count_values[label] = [
            sum(counts[label].values()),
            *(counts[label][year] for year in years),
        ]
    return {
        "years": years,
        "all_time_label": "Depuis le début",
        "summary": {
            "buy": year_values["BUY"][0],
            "sell": year_values["SELL"][0],
            "received": year_values["RECEIVED"][0],
            "send": year_values["SEND"][0],
        },
        "value_rows": [
            {"label": label, "values": year_values[label]}
            for label in labels
        ],
        "count_rows": [
            {"label": label, "values": count_values[label]}
            for label in counts_labels
        ],
        "line_count_values": [
            sum(line_counts.values()),
            *(line_counts[year] for year in years),
        ],
    }


def build_dashboard(
    rows,
    category_overrides=None,
    as_of=None,
    complete=False,
    price_for=None,
    include_history=True,
    account_balances=None,
    account_balances_at=None,
):
    rows = list(rows)
    category_overrides = category_overrides or {}
    cutoff = as_of or date.today().isoformat()
    transactions = [
        normalized_transaction(row, category_overrides)
        for row in rows
    ]
    quality_issues = build_status_issues(rows, cutoff)
    transactions = [
        tx for tx in transactions
        if tx["date"][:10] <= cutoff and tx["status"] in {"completed", "complete"}
    ]
    transactions.sort(key=lambda tx: (tx["date"], tx["id"]))

    assets = {}
    realized_by_year = defaultdict(lambda: ZERO)
    realized_complete_by_year = defaultdict(lambda: True)
    external_fiat = ZERO
    for tx in transactions:
        asset = tx["asset"]
        if tx["category"] == "Transfer" and not (
            tx["type"] in {"pro_deposit", "pro_withdrawal"}
            and tx["amount"] < ZERO
        ):
            continue
        if not asset:
            continue
        if asset in FIAT_CURRENCIES:
            if (
                tx["category"] == "Deposit"
                and tx["native_currency"] == "EUR"
                and tx["type"] in {"fiat_deposit", "send"}
            ):
                external_fiat += abs(tx["amount"])
            continue
        state = assets.setdefault(
            asset,
            {
                "asset": asset,
                "buy_qty": ZERO,
                "buy_eur": ZERO,
                "sell_qty": ZERO,
                "sell_eur": ZERO,
                "received_qty": ZERO,
                "received_eur": ZERO,
                "sent_qty": ZERO,
                "sent_eur": ZERO,
                "trade_eur": ZERO,
                "invest_ext": ZERO,
                "quantity": ZERO,
                "basis": ZERO,
                "realized": ZERO,
                "basis_known": True,
            },
        )
        quantity = abs(tx["amount"])
        eur_value = _eur_value(tx, price_for)
        if tx["category"] == "Other":
            add_transaction_issue(
                quality_issues,
                tx,
                "Catégorie à vérifier",
                f"Le type Coinbase « {tx['type']} » est classé Other.",
                "Vérifiez le détail de l’opération et choisissez sa catégorie dans l’historique.",
            )
        if tx["category"] in {
            "BUY", "SELL", "DIVIDEND", "Gain de pari", "Trade INT",
        } and eur_value is None:
            add_transaction_issue(
                quality_issues,
                tx,
                "Valeur EUR manquante",
                (
                    f"Aucune valeur EUR fiable n’est disponible "
                    f"(montant natif : {tx['native_currency'] or 'absent'})."
                ),
                "Vérifiez le montant natif et la devise dans Coinbase; pour une devise sans cours EUR, ajoutez une valorisation fiable.",
            )
        if (
            tx["category"] == "Deposit"
            and asset not in FIAT_CURRENCIES
            and eur_value is None
        ):
            add_transaction_issue(
                quality_issues,
                tx,
                "Cours EUR d’entrée manquant",
                "Le dépôt crypto n’a pas de valeur EUR native et aucun cours historique ne permet de l’estimer.",
                "Vérifiez le cours EUR à la date du dépôt ou ajoutez une valorisation fiable.",
            )

        if tx["category"] == "BUY":
            state["buy_qty"] += quantity
            if eur_value is None:
                state["basis_known"] = False
            else:
                state["buy_eur"] += eur_value
                state["invest_ext"] += eur_value
                state["basis"] += eur_value
            state["quantity"] += quantity
        elif tx["category"] == "SELL":
            state["sell_qty"] += quantity
            if eur_value is not None:
                state["sell_eur"] += eur_value
            available = max(state["quantity"], ZERO)
            disposed = min(quantity, available)
            if disposed > ZERO:
                average_cost = state["basis"] / state["quantity"]
                disposed_basis = average_cost * disposed
                disposed_proceeds = (
                    eur_value * disposed / quantity if eur_value is not None else None
                )
                if disposed_proceeds is None:
                    state["basis_known"] = False
                else:
                    state["realized"] += disposed_proceeds - disposed_basis
                year = int(tx["date"][:4])
                if state["basis_known"] and disposed_proceeds is not None:
                    realized_by_year[year] += disposed_proceeds - disposed_basis
                else:
                    realized_complete_by_year[year] = False
                state["basis"] -= disposed_basis
                state["quantity"] -= disposed
            if disposed < quantity and not _negligible_oversell(
                tx, quantity - disposed, eur_value, price_for
            ):
                state["basis_known"] = False
                state["basis"] = ZERO
                state["quantity"] = ZERO
                realized_complete_by_year[int(tx["date"][:4])] = False
                add_transaction_issue(
                    quality_issues,
                    tx,
                    "Quantité vendue supérieure au solde",
                    f"La vente porte sur {quantity} {asset}, alors que le registre ne retrouve que {available}.",
                    "Importez l’historique d’acquisition antérieur ou vérifiez les transferts entrants manquants.",
                )
        elif tx["category"] in {"DIVIDEND", "Gain de pari"}:
            state["received_qty"] += quantity
            if eur_value is None:
                state["basis_known"] = False
            else:
                state["received_eur"] += eur_value
                state["basis"] += eur_value
            state["quantity"] += quantity
        elif tx["category"] == "Deposit":
            state["received_qty"] += quantity
            if eur_value is None:
                state["basis_known"] = False
            else:
                state["received_eur"] += eur_value
                state["basis"] += eur_value
            state["quantity"] += quantity
        elif tx["category"] == "Withdrawal":
            state["sent_qty"] += quantity
            if eur_value is not None:
                state["sent_eur"] += eur_value
            available = max(state["quantity"], ZERO)
            disposed = min(quantity, available)
            if disposed > ZERO:
                average_cost = state["basis"] / state["quantity"]
                state["basis"] -= average_cost * disposed
                state["quantity"] -= disposed
            if disposed < quantity and not _negligible_oversell(
                tx, quantity - disposed, eur_value, price_for
            ):
                state["basis"] = ZERO
                state["basis_known"] = False
                state["quantity"] = ZERO
                add_transaction_issue(
                    quality_issues,
                    tx,
                    "Transfert sortant supérieur au solde",
                    f"Le retrait porte sur {quantity} {asset}, alors que le registre ne retrouve que {available}.",
                    "Importez le dépôt ou l’achat antérieur, ou vérifiez si le transfert entrant provient d’un autre portefeuille.",
                )
        elif tx["category"] == "Transfer" and tx["type"] in {
            "pro_deposit", "pro_withdrawal",
        }:
            state["sent_qty"] += quantity
            available = max(state["quantity"], ZERO)
            disposed = min(quantity, available)
            if disposed > ZERO:
                average_cost = state["basis"] / state["quantity"]
                state["basis"] -= average_cost * disposed
                state["quantity"] -= disposed
            if disposed < quantity and not _negligible_oversell(
                tx, quantity - disposed, eur_value, price_for
            ):
                state["basis"] = ZERO
                state["basis_known"] = False
                state["quantity"] = ZERO
                add_transaction_issue(
                    quality_issues,
                    tx,
                    "Transfert sortant supérieur au solde",
                    f"Le retrait porte sur {quantity} {asset}, alors que le registre ne retrouve que {available}.",
                    "Importez l’historique Coinbase Pro ou vérifiez le portefeuille destinataire.",
                )
        elif tx["category"] == "Trade INT":
            if eur_value is not None and tx["amount"] < ZERO:
                state["trade_eur"] += eur_value
            if tx["amount"] > ZERO:
                if eur_value is None:
                    state["basis_known"] = False
                else:
                    state["basis"] += eur_value
                state["quantity"] += quantity
            elif tx["amount"] < ZERO:
                if ZERO < quantity <= state["quantity"]:
                    average_cost = state["basis"] / state["quantity"]
                    disposed_basis = average_cost * quantity
                    if eur_value is not None:
                        state["realized"] += eur_value - disposed_basis
                    else:
                        state["basis_known"] = False
                    state["basis"] -= disposed_basis
                else:
                    state["basis"] = ZERO
                    state["basis_known"] = False
                state["quantity"] -= quantity
        else:
            state["basis_known"] = False
            state["quantity"] += tx["amount"]
            add_transaction_issue(
                quality_issues,
                tx,
                "Opération non traitée",
                f"Le type Coinbase « {tx['type']} » ne modifie pas le registre de façon fiable.",
                "Examinez cette ligne et recatégorisez-la si nécessaire.",
            )

    normalized_account_balances = None
    balance_reconciliation = []
    if account_balances is not None and cutoff == date.today().isoformat():
        normalized_account_balances = defaultdict(lambda: ZERO)
        for symbol, amount in account_balances.items():
            normalized_account_balances[canonical_asset(symbol)] += (
                decimal_value(amount) or ZERO
            )
        ledger_balances = {
            asset: state["quantity"]
            for asset, state in assets.items()
        }
        for tx in transactions:
            if tx["asset"] in FIAT_CURRENCIES:
                ledger_balances[tx["asset"]] = (
                    ledger_balances.get(tx["asset"], ZERO) + tx["amount"]
                )
        reconciliation_assets = (
            set(ledger_balances) | set(normalized_account_balances)
        )
        last_transactions = {}
        for tx in transactions:
            if tx["asset"] in reconciliation_assets:
                last_transactions[tx["asset"]] = tx
        for asset in sorted(reconciliation_assets):
            ledger_quantity = ledger_balances.get(asset, ZERO)
            live_quantity = normalized_account_balances.get(asset, ZERO)
            difference = live_quantity - ledger_quantity
            difference_price = None
            if difference == ZERO:
                difference_value = ZERO
            else:
                difference_price = (
                    Decimal("1")
                    if asset == "EUR"
                    else price_for(asset, cutoff) if price_for else None
                )
                difference_value = (
                    abs(difference) * difference_price
                    if difference_price is not None
                    else None
                )
            latest = last_transactions.get(asset)
            state = assets.get(asset)
            if difference == ZERO:
                status = "Aligné"
            elif difference_value is None:
                status = "Écart — valeur inconnue"
            elif difference_value < NEGLIGIBLE_EUR_THRESHOLD:
                status = "Écart mineur"
            else:
                status = "À examiner"
            balance_reconciliation.append({
                "asset": asset,
                "coinbase_quantity": live_quantity,
                "ledger_quantity": ledger_quantity,
                "difference": difference,
                "difference_value_eur": difference_value,
                "status": status,
                "basis_status": (
                    "Non applicable"
                    if asset in FIAT_CURRENCIES
                    else "Connu" if state and state["basis_known"] else "Incomplet"
                ),
                "last_transaction_id": latest["id"] if latest else "",
                "last_transaction_date": latest["date"] if latest else "",
                "last_transaction_type": latest["type"] if latest else "",
            })
        for asset, live_quantity in normalized_account_balances.items():
            if asset in FIAT_CURRENCIES:
                continue
            state = assets.get(asset)
            if state is None and live_quantity == ZERO:
                continue
            if state is None:
                state = {
                    "asset": asset,
                    "buy_qty": ZERO,
                    "buy_eur": ZERO,
                    "sell_qty": ZERO,
                    "sell_eur": ZERO,
                    "received_qty": ZERO,
                    "received_eur": ZERO,
                    "sent_qty": ZERO,
                    "sent_eur": ZERO,
                    "trade_eur": ZERO,
                    "invest_ext": ZERO,
                    "quantity": ZERO,
                    "basis": ZERO,
                    "realized": ZERO,
                    "basis_known": False,
                }
                assets[asset] = state
            ledger_quantity = state["quantity"]
            difference = live_quantity - ledger_quantity
            if difference == ZERO:
                continue
            difference_price = (
                price_for(asset, cutoff)
                if price_for and asset and difference != ZERO
                else None
            )
            difference_value = (
                abs(difference) * difference_price
                if difference_price is not None
                else None
            )
            if ledger_quantity > ZERO:
                state["basis"] *= live_quantity / ledger_quantity
            elif live_quantity > ZERO:
                state["basis"] = ZERO
                state["basis_known"] = False
            if difference_value is None or difference_value >= NEGLIGIBLE_EUR_THRESHOLD:
                state["basis_known"] = False
                related = [tx for tx in transactions if tx["asset"] == asset]
                transaction = related[-1] if related else {}
                quality_issues.append({
                    "kind": "Écart de solde Coinbase",
                    "asset": asset,
                    "date": cutoff,
                    "transaction_id": transaction.get("id", ""),
                    "problem": (
                        f"Le solde Coinbase ({live_quantity} {asset}) diffère du "
                        f"solde reconstitué par les transactions ({ledger_quantity} {asset})."
                    ),
                    "recommendation": (
                        "Le solde affiché est aligné sur Coinbase, mais vérifiez l’historique "
                        "des achats, ventes et transferts avant d’utiliser le PRU ou les gains."
                    ),
                })
            state["quantity"] = live_quantity

        for item in balance_reconciliation:
            asset = item["asset"]
            if asset not in FIAT_CURRENCIES:
                state = assets.get(asset)
                item["basis_status"] = (
                    "Connu" if state and state["basis_known"] else "Incomplet"
                )

    rows_out = []
    pricing_incomplete = False
    for state in assets.values():
        quantity = state["quantity"]
        basis = state["basis"]
        price = None
        if quantity == ZERO:
            price = ZERO
        elif price_for and quantity > ZERO:
            price = price_for(state["asset"], cutoff)
        if quantity > ZERO and price is None:
            pricing_incomplete = True
            related = [
                tx for tx in transactions
                if tx["asset"] == state["asset"]
            ]
            transaction = related[-1] if related else {}
            quality_issues.append({
                "kind": "Cours EUR actuel manquant",
                "asset": state["asset"],
                "date": cutoff,
                "transaction_id": transaction.get("id", ""),
                "problem": f"Coinbase ne fournit pas de cours EUR pour le solde de {state['asset']}.",
                "recommendation": "Vérifiez le symbole de l’actif ou saisissez une valorisation provenant d’une source fiable.",
            })
        value = quantity * price if price is not None else None
        unrealized = (
            value - basis
            if value is not None and state["basis_known"] and quantity >= ZERO
            else None
        )
        rows_out.append(
            {
                **state,
                "realized": state["realized"] if state["basis_known"] else None,
                "pair": f'{state["asset"]}-EUR',
                "price_eur": price,
                "value_eur": value,
                "average_cost": basis / quantity
                if quantity > ZERO and state["basis_known"]
                else None,
                "unrealized": unrealized,
                "basis_known": state["basis_known"],
            }
        )
    rows_out.sort(key=lambda row: row["asset"])

    current_cash_value_eur = ZERO
    cash_pricing_complete = True
    if normalized_account_balances is not None:
        for currency in FIAT_CURRENCIES:
            quantity = normalized_account_balances.get(currency, ZERO)
            if quantity == ZERO:
                continue
            price = (
                Decimal("1")
                if currency == "EUR"
                else price_for(currency, cutoff) if price_for else None
            )
            if price is None:
                cash_pricing_complete = False
                quality_issues.append({
                    "kind": "Cours EUR actuel manquant",
                    "asset": currency,
                    "date": cutoff,
                    "transaction_id": "",
                    "problem": f"Coinbase ne fournit pas de cours EUR pour le solde de {currency}.",
                    "recommendation": "Vérifiez le symbole de la devise ou ajoutez une valorisation fiable.",
                })
            else:
                current_cash_value_eur += quantity * price

    warnings = []
    incomplete_assets = [row["asset"] for row in rows_out if not row["basis_known"]]
    if incomplete_assets:
        warnings.append(
            "PRU incomplet pour "
            + ", ".join(incomplete_assets)
            + " : historique d'achat/transfert incomplet ou cours EUR indisponible. "
            "Importez l'historique du portefeuille source; sans prix d'acquisition fiable, le PRU et les gains/pertes restent incomplets."
        )
    if pricing_incomplete:
        warnings.append("Certains cours EUR sont indisponibles pour la date sélectionnée.")
    assets_value = (
        sum((row["value_eur"] for row in rows_out), ZERO)
        if all(row["value_eur"] is not None for row in rows_out)
        else None
    )
    total_value = (
        assets_value + current_cash_value_eur
        if assets_value is not None and cash_pricing_complete
        else None
    )
    total_unrealized = (
        sum((row["unrealized"] for row in rows_out), ZERO)
        if rows_out and all(row["unrealized"] is not None for row in rows_out)
        else ZERO if not rows_out else None
    )
    price_for = price_for or (lambda _asset, _day: None)
    from .french_tax import estimate_french_tax_gain, required_price_days

    if complete and price_for:
        requirements = required_price_days(transactions, price_for)
        prefetch_prices = getattr(price_for, "prefetch", None)
        if prefetch_prices:
            prefetch_prices(requirements)
    french_tax = estimate_french_tax_gain(transactions, price_for, complete)
    activity_report = build_activity_report(
        transactions, realized_by_year, realized_complete_by_year, price_for
    )
    portfolio_history = []
    charts = None
    if include_history and transactions:
        history_days = history_dates(transactions, cutoff)
        chart_price = getattr(price_for, "chart_price", price_for)
        history_assets = {
            tx["asset"]
            for tx in transactions
            if tx["asset"] and tx["asset"] not in FIAT_CURRENCIES
        }
        prefetch_prices = getattr(price_for, "prefetch", None)
        if prefetch_prices and history_assets and history_days:
            prefetch_prices({
                asset: set(history_days)
                for asset in history_assets
            })
        for history_day in history_days:
            snapshot = build_dashboard(
                rows,
                category_overrides,
                as_of=history_day,
                complete=False,
                price_for=chart_price,
                include_history=False,
            )
            portfolio_history.append({
                "date": history_day,
                "value": snapshot["total_value_eur"],
                "realized": snapshot["total_realized_eur"],
                "unrealized": snapshot["total_unrealized_eur"],
            })
        crypto_transactions = [
            tx for tx in transactions
            if tx["asset"] not in FIAT_CURRENCIES
        ]
        charts = build_dashboard_charts(
            portfolio_history,
            rows_out,
            crypto_transactions,
            lambda tx: _eur_value(tx, chart_price),
            cutoff,
        )
        if charts["history_price_incomplete"]:
            warnings.append(
                "La courbe de valeur comporte des périodes sans cours EUR historique."
            )
        if charts["flow_incomplete"]:
            warnings.append(
                "Les achats/ventes mensuels sont incomplets pour certaines opérations sans valeur EUR."
            )
        if not complete:
            warnings.append(
                "L’historique complet du portefeuille n’est pas confirmé : "
                "les indicateurs portent uniquement sur les opérations importées."
            )
    add_unattributed_basis_issues(quality_issues, rows_out, transactions)
    quality_report = summarize_quality_issues(quality_issues)
    return {
        "as_of": cutoff,
        "assets": rows_out,
        "transactions": transactions,
        "total_value_eur": total_value,
        "total_invested_eur": sum((row["basis"] for row in rows_out), ZERO),
        "total_realized_eur": (
            sum((row["realized"] for row in rows_out), ZERO)
            if all(row["realized"] is not None for row in rows_out)
            else None
        ),
        "total_unrealized_eur": total_unrealized,
        "external_fiat_eur": external_fiat,
        "french_tax_gain_eur": french_tax["gain_eur"],
        "french_tax_complete": french_tax["complete"],
        "french_tax_reason": french_tax["reason"],
        "portfolio_complete": complete,
        "account_balances": (
            dict(account_balances)
            if normalized_account_balances is not None and account_balances is not None
            else None
        ),
        "account_balances_at": (
            account_balances_at if normalized_account_balances is not None else None
        ),
        "balance_reconciliation": balance_reconciliation,
        "activity_report": activity_report,
        "charts": charts,
        "quality_report": quality_report,
        "quality_issues": quality_issues,
        "warnings": warnings,
    }
