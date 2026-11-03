import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crypto_dashboard.database import (
    cache_price,
    cached_price_is_fresh,
    get_connection,
    get_account_balances,
    get_cached_price,
    initialize_database,
    migrate_market_price_cache,
    store_account_balances,
)


class MarketPriceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.tempdir.name, "prices.sqlite3")
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_expired_negative_cache_entries_are_invalidated_once(self):
        connection = get_connection(self.database_path)
        try:
            cache_price(connection, "POLY", "2025-10-29", None)
            migrate_market_price_cache(connection)
            self.assertEqual(
                get_cached_price(connection, "POLY", "2025-10-29"),
                (False, None, None),
            )

            cache_price(connection, "POLY", "2025-10-29", None)
            migrate_market_price_cache(connection)
            self.assertEqual(
                get_cached_price(connection, "POLY", "2025-10-29")[0],
                True,
            )
        finally:
            connection.close()

    def test_negative_cache_freshness_expires(self):
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(seconds=30)).isoformat()
        expired = (now - timedelta(seconds=301)).isoformat()

        self.assertTrue(cached_price_is_fresh(fresh, 300))
        self.assertFalse(cached_price_is_fresh(expired, 300))
        self.assertFalse(cached_price_is_fresh("invalid", 300))

    def test_account_balances_are_aggregated_without_losing_decimal_precision(self):
        connection = get_connection(self.database_path)
        try:
            store_account_balances(connection, [
                {"currency": "USDC", "balance": {"amount": "1.231994", "currency": "USDC"}},
                {"currency": "USDC", "balance": {"amount": "1.300000", "currency": "USDC"}},
                {"currency": "EUR", "balance": {"amount": "0", "currency": "EUR"}},
            ])
            balances, synced_at = get_account_balances(connection)
            self.assertEqual(balances, {
                "EUR": "0",
                "USDC": "2.531994",
            })
            self.assertTrue(synced_at)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
