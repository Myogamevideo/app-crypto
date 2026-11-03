import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


MARKET_PRICE_CACHE_VERSION = "coinbase-binance-fallback-1"


def get_connection(database_path):
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path):
    connection = get_connection(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                amount TEXT NOT NULL,
                amount_currency TEXT NOT NULL,
                native_amount TEXT,
                native_currency TEXT,
                created_at TEXT NOT NULL,
                description TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS categories (
                transaction_id TEXT PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
                category TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_prices (
                asset TEXT NOT NULL,
                price_date TEXT NOT NULL,
                price_eur TEXT,
                cached_at TEXT NOT NULL,
                PRIMARY KEY(asset, price_date)
            );
            CREATE TABLE IF NOT EXISTS account_balances (
                currency TEXT PRIMARY KEY,
                amount TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS transactions_created_at_idx
                ON transactions(created_at);
            CREATE INDEX IF NOT EXISTS transactions_account_id_idx
                ON transactions(account_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def store_transactions(connection, transactions):
    synced_at = datetime.now(timezone.utc).isoformat()
    for transaction in transactions:
        amount = transaction.get("amount") or {}
        native_amount = transaction.get("native_amount") or {}
        connection.execute(
            """
            INSERT INTO transactions (
                id, account_id, type, status, amount, amount_currency,
                native_amount, native_currency, created_at, description, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_id = excluded.account_id,
                type = excluded.type,
                status = excluded.status,
                amount = excluded.amount,
                amount_currency = excluded.amount_currency,
                native_amount = excluded.native_amount,
                native_currency = excluded.native_currency,
                created_at = excluded.created_at,
                description = excluded.description,
                raw_json = excluded.raw_json,
                synced_at = excluded.synced_at
            """,
            (
                transaction["id"],
                transaction.get("_account_id", ""),
                transaction.get("type", "tx"),
                transaction.get("status", "unknown"),
                str(amount.get("amount", "0")),
                amount.get("currency", ""),
                str(native_amount.get("amount")) if native_amount.get("amount") is not None else None,
                native_amount.get("currency"),
                transaction.get("created_at", ""),
                transaction.get("description"),
                json.dumps(transaction, separators=(",", ":"), sort_keys=True),
                synced_at,
            ),
        )


def store_account_balances(connection, accounts):
    balances = {}
    for account in accounts:
        balance = account.get("balance") or {}
        currency = balance.get("currency") or account.get("currency")
        amount = balance.get("amount")
        if not currency or amount is None:
            continue
        try:
            decimal_amount = Decimal(str(amount))
        except InvalidOperation as error:
            raise ValueError(f"Solde Coinbase invalide pour {currency}.") from error
        balances[currency] = balances.get(currency, Decimal("0")) + decimal_amount

    if not balances:
        return
    synced_at = datetime.now(timezone.utc).isoformat()
    connection.execute("DELETE FROM account_balances")
    connection.executemany(
        "INSERT INTO account_balances(currency, amount, synced_at) VALUES (?, ?, ?)",
        [
            (currency, str(amount), synced_at)
            for currency, amount in balances.items()
        ],
    )


def get_account_balances(connection):
    rows = connection.execute(
        "SELECT currency, amount, synced_at FROM account_balances ORDER BY currency"
    ).fetchall()
    if not rows:
        return None, None
    return (
        {row["currency"]: row["amount"] for row in rows},
        rows[0]["synced_at"],
    )


def read_setting(connection, key):
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(connection, key, value):
    connection.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_cached_price(connection, asset, price_date):
    row = connection.execute(
        "SELECT price_eur, cached_at FROM market_prices WHERE asset = ? AND price_date = ?",
        (asset, price_date),
    ).fetchone()
    if row is None:
        return False, None, None
    return True, row["price_eur"], row["cached_at"]


def cached_price_is_fresh(cached_at, max_age_seconds):
    if not cached_at:
        return False
    try:
        cached_datetime = datetime.fromisoformat(cached_at)
    except ValueError:
        return False
    if cached_datetime.tzinfo is None:
        cached_datetime = cached_datetime.replace(tzinfo=timezone.utc)
    age_seconds = (
        datetime.now(timezone.utc) - cached_datetime.astimezone(timezone.utc)
    ).total_seconds()
    return 0 <= age_seconds < max_age_seconds


def migrate_market_price_cache(connection):
    if read_setting(connection, "market_price_cache_version") == MARKET_PRICE_CACHE_VERSION:
        return
    connection.execute("DELETE FROM market_prices WHERE price_eur IS NULL")
    set_setting(connection, "market_price_cache_version", MARKET_PRICE_CACHE_VERSION)
    connection.commit()


def cache_price(connection, asset, price_date, price):
    connection.execute(
        "INSERT INTO market_prices(asset, price_date, price_eur, cached_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(asset, price_date) DO UPDATE SET "
        "price_eur = excluded.price_eur, cached_at = excluded.cached_at",
        (
            asset,
            price_date,
            str(price) if price is not None else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()


def cache_prices(connection, asset, prices):
    cached_at = datetime.now(timezone.utc).isoformat()
    connection.executemany(
        "INSERT INTO market_prices(asset, price_date, price_eur, cached_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(asset, price_date) DO UPDATE SET "
        "price_eur = excluded.price_eur, cached_at = excluded.cached_at",
        [
            (asset, day, str(price) if price is not None else None, cached_at)
            for day, price in prices.items()
        ],
    )
    connection.commit()
