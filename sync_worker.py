import logging
import os
import time
from datetime import datetime, timezone

from crypto_dashboard.coinbase import CoinbaseClient, CoinbaseError
from crypto_dashboard.database import (
    get_connection,
    initialize_database,
    set_setting,
    store_account_balances,
    store_transactions,
)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("coinbase-sync")
database_path = os.getenv("DATABASE_PATH", "data/crypto-dashboard.sqlite3")
interval_seconds = int(os.getenv("SYNC_INTERVAL_SECONDS", "900"))


def sync_once():
    client = CoinbaseClient.from_environment()
    accounts = client.fetch_accounts()
    transactions = client.fetch_all_transactions(accounts)
    connection = get_connection(database_path)
    try:
        store_transactions(connection, transactions)
        store_account_balances(connection, accounts)
        set_setting(connection, "last_sync", datetime.now(timezone.utc).isoformat())
        set_setting(connection, "sync_error", "")
        connection.commit()
    finally:
        connection.close()
    logger.info("Synchronisation Coinbase réussie (%d transactions).", len(transactions))


initialize_database(database_path)
while True:
    try:
        sync_once()
    except CoinbaseError as error:
        connection = get_connection(database_path)
        try:
            set_setting(connection, "sync_error", str(error))
            connection.commit()
        finally:
            connection.close()
        logger.error("Synchronisation impossible: %s", error)
    time.sleep(interval_seconds)
