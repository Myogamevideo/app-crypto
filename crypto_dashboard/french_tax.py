from collections import defaultdict
from datetime import date
from decimal import Decimal

from .ledger import FIAT_CURRENCIES, ZERO, _eur_value


def required_price_days(transactions, price_for=None):
    """Return historical quotes needed for a French portfolio-level estimate."""
    for tx in transactions:
        if tx["category"] == "Transfer":
            continue
        if tx["asset"] in FIAT_CURRENCIES:
            continue
        if tx["category"] in {
            "DIVIDEND", "Gain de pari", "Deposit", "Withdrawal", "Other",
        }:
            return {}
        if tx["category"] in {"BUY", "SELL"} and _eur_value(tx, price_for) is None:
            return {}

    quantities = defaultdict(lambda: ZERO)
    needed = defaultdict(set)
    for tx in transactions:
        if tx["category"] == "Transfer":
            continue
        asset = tx["asset"]
        if not asset or asset in FIAT_CURRENCIES:
            continue
        if tx["category"] == "BUY":
            quantities[asset] += abs(tx["amount"])
        elif tx["category"] == "SELL":
            quantity = abs(tx["amount"])
            if quantities[asset] < quantity:
                return {}
            for held_asset, held_quantity in quantities.items():
                if held_quantity > ZERO:
                    needed[held_asset].add(tx["date"][:10])
            quantities[asset] -= quantity
        elif tx["category"] == "Trade INT":
            quantities[asset] += tx["amount"]
    return needed


def estimate_french_tax_gain(transactions, price_for, portfolio_complete):
    """Estimate French portfolio-level taxable gains using daily market closes."""
    if not portfolio_complete:
        return {"gain_eur": None, "complete": False, "reason": "Portefeuille global non confirmé."}

    quantities = defaultdict(lambda: ZERO)
    total_basis = ZERO
    taxable_gain = ZERO
    basis_complete = True
    prices_complete = True
    saw_disposal = False

    for tx in transactions:
        if tx["category"] == "Transfer":
            continue
        asset = tx["asset"]
        if not asset or asset in FIAT_CURRENCIES:
            continue
        quantity = abs(tx["amount"])
        if tx["category"] == "BUY":
            value = _eur_value(tx, price_for)
            if value is None:
                basis_complete = False
            else:
                total_basis += value
            quantities[asset] += quantity
        elif tx["category"] in {"DIVIDEND", "Gain de pari", "Deposit"}:
            # A transfer-in is not a new tax acquisition; its original basis is not in this history.
            basis_complete = False
            quantities[asset] += quantity
        elif tx["category"] == "Withdrawal":
            # The asset may still be held outside Coinbase, so Coinbase-only valuation is incomplete.
            return {
                "gain_eur": None,
                "complete": False,
                "reason": "Un retrait crypto nécessite de renseigner les actifs détenus hors Coinbase.",
            }
        elif tx["category"] == "Trade INT":
            quantities[asset] += tx["amount"]
        elif tx["category"] == "SELL":
            proceeds = _eur_value(tx, price_for)
            if proceeds is None or total_basis < ZERO:
                basis_complete = False
                quantities[asset] -= quantity
                continue
            if quantities[asset] < quantity:
                return {
                    "gain_eur": None,
                    "complete": False,
                    "reason": "La quantité détenue ne correspond pas à l'historique importé.",
                }
            portfolio_value = ZERO
            for held_asset, held_quantity in quantities.items():
                if held_quantity <= ZERO:
                    continue
                price = price_for(held_asset, tx["date"][:10])
                if price is None:
                    prices_complete = False
                    break
                portfolio_value += held_quantity * price
            if not prices_complete or portfolio_value <= ZERO:
                prices_complete = False
                quantities[asset] -= quantity
                continue
            allocated_basis = total_basis * proceeds / portfolio_value
            taxable_gain += proceeds - allocated_basis
            total_basis -= allocated_basis
            quantities[asset] -= quantity
            saw_disposal = True
        else:
            if tx["amount"] > ZERO:
                basis_complete = False
                quantities[asset] += tx["amount"]
            elif tx["amount"] < ZERO:
                quantities[asset] += tx["amount"]

    if not basis_complete or not prices_complete:
        reason = "Historique incomplet : prix EUR ou coût d'acquisition manquant."
        return {"gain_eur": None, "complete": False, "reason": reason}
    if not saw_disposal:
        return {"gain_eur": ZERO, "complete": True, "reason": None}
    return {
        "gain_eur": taxable_gain,
        "complete": True,
        "reason": "Estimation basée sur les cours de clôture quotidiens Coinbase.",
    }
