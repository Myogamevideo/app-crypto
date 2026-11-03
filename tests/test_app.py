import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from crypto_dashboard import create_app
from crypto_dashboard.database import (
    get_connection,
    store_account_balances,
    store_transactions,
)
from crypto_dashboard import query_transactions, parse_transaction_filters
from crypto_dashboard.market import CoinbaseMarketData, MarketProductUnavailableError


class DashboardAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "APP_PASSWORD": "test-password",
            "DATABASE_PATH": os.path.join(self.tempdir.name, "dashboard.sqlite3"),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_dashboard_requires_login_and_sets_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        login = self.client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.headers["X-Frame-Options"], "DENY")
        self.assertIn("Content-Security-Policy", login.headers)

    def test_login_csrf_and_empty_dashboard_render(self):
        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        invalid = self.client.post("/login", data={"csrf_token": "bad", "password": "test-password"})
        self.assertEqual(invalid.status_code, 400)
        logged_in = self.client.post(
            "/login", data={"csrf_token": token, "password": "test-password"}
        )
        self.assertEqual(logged_in.status_code, 302)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COINBASE_API_KEY_NAME", None)
            os.environ.pop("COINBASE_API_PRIVATE_KEY", None)
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Configurez Coinbase".encode(), dashboard.data)

    def test_dashboard_renders_synced_portfolio(self):
        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        self.client.post("/login", data={"csrf_token": token, "password": "test-password"})
        connection = get_connection(self.app.config["DATABASE_PATH"])
        try:
            store_transactions(connection, [{
                "id": "buy-1",
                "type": "buy",
                "status": "completed",
                "created_at": f"{date.today().isoformat()}T12:00:00Z",
                "amount": {"amount": "2", "currency": "BTC"},
                "native_amount": {"amount": "-200", "currency": "EUR"},
            }])
            store_account_balances(connection, [{
                "currency": "BTC",
                "balance": {"amount": "2", "currency": "BTC"},
            }])
            connection.commit()
        finally:
            connection.close()

        with patch.dict(os.environ, {
            "COINBASE_API_KEY_NAME": "test-key",
            "COINBASE_API_PRIVATE_KEY": "test-key",
        }), patch.object(CoinbaseMarketData, "price_eur", return_value=Decimal("150")), \
                patch.object(CoinbaseMarketData, "daily_closes", return_value={}):
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"BTC-EUR", dashboard.data)
        self.assertIn(b"Activit\xc3\xa9 par ann\xc3\xa9e", dashboard.data)
        self.assertIn(b"NOMBRE D\xe2\x80\x99OP\xc3\x89RATIONS", dashboard.data)
        self.assertIn(b'class="mobile-holdings"', dashboard.data)
        self.assertIn(b'class="mobile-transactions"', dashboard.data)
        self.assertIn(b'class="chart-svg"', dashboard.data)
        self.assertIn("Rapprochement Coinbase".encode(), dashboard.data)
        self.assertIn("Solde Coinbase".encode(), dashboard.data)
        self.assertIn("buy-1".encode(), dashboard.data)
        self.assertIn("300,00 €".encode(), dashboard.data)
        self.assertIn("100,00 €".encode(), dashboard.data)

    def test_dashboard_renders_when_realized_gain_is_unknown(self):
        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        self.client.post("/login", data={"csrf_token": token, "password": "test-password"})
        connection = get_connection(self.app.config["DATABASE_PATH"])
        try:
            store_transactions(connection, [{
                "id": "receive-1",
                "type": "receive",
                "status": "completed",
                "created_at": f"{date.today().isoformat()}T12:00:00Z",
                "amount": {"amount": "1", "currency": "ETH"},
                "native_amount": {"amount": "100", "currency": "EUR"},
            }])
            connection.commit()
        finally:
            connection.close()

        with patch.dict(os.environ, {
            "COINBASE_API_KEY_NAME": "test-key",
            "COINBASE_API_PRIVATE_KEY": "test-key",
        }), patch.object(CoinbaseMarketData, "price_eur", return_value=Decimal("150")), \
                patch.object(CoinbaseMarketData, "daily_closes", return_value={}):
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)

    def test_dashboard_shows_market_errors_with_transaction_links(self):
        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        self.client.post("/login", data={"csrf_token": token, "password": "test-password"})
        connection = get_connection(self.app.config["DATABASE_PATH"])
        try:
            store_transactions(connection, [{
                "id": "buy-quality",
                "type": "buy",
                "status": "completed",
                "created_at": f"{(date.today() - timedelta(days=60)).isoformat()}T12:00:00Z",
                "amount": {"amount": "2", "currency": "BTC"},
                "native_amount": {"amount": "-200", "currency": "EUR"},
            }])
            connection.commit()
        finally:
            connection.close()

        with patch.dict(os.environ, {
            "COINBASE_API_KEY_NAME": "test-key",
            "COINBASE_API_PRIVATE_KEY": "test-key",
        }), patch.object(CoinbaseMarketData, "price_eur", return_value=Decimal("150")), \
                patch.object(
                    CoinbaseMarketData,
                    "daily_closes",
                    side_effect=MarketProductUnavailableError(
                        "Marché historique EUR indisponible pour BTC (HTTP 400)."
                    ),
                ):
            dashboard = self.client.get("/")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Qualité des données".encode(), dashboard.data)
        self.assertIn("HTTP 400".encode(), dashboard.data)
        self.assertIn("Transactions liées".encode(), dashboard.data)
        self.assertIn(b"q=buy-quality", dashboard.data)

    def test_transaction_filters_sort_and_paginate(self):
        connection = get_connection(self.app.config["DATABASE_PATH"])
        try:
            store_transactions(connection, [
                {
                    "id": f"tx-{index:02}",
                    "type": "buy" if index % 2 == 0 else "sell",
                    "status": "completed",
                    "created_at": f"2025-01-{index + 1:02}T12:00:00Z",
                    "amount": {"amount": str(index + 1), "currency": "BTC"},
                    "native_amount": {"amount": "-10", "currency": "EUR"},
                }
                for index in range(30)
            ])
            connection.commit()
            filters = parse_transaction_filters({
                "page": "2",
                "per_page": "25",
                "sort": "created_at",
                "direction": "desc",
                "category": "BUY",
            })
            rows, total, pages, page = query_transactions(connection, filters)
            self.assertEqual(total, 15)
            self.assertEqual(pages, 1)
            self.assertEqual(page, 1)
            self.assertEqual(len(rows), 15)
            self.assertTrue(all(row["display_category"] == "BUY" for row in rows))

            filters = parse_transaction_filters({"per_page": "25", "sort": "created_at"})
            rows, total, pages, page = query_transactions(connection, filters)
            self.assertEqual((total, pages, page, len(rows)), (30, 2, 1, 25))
        finally:
            connection.close()

        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        self.client.post("/login", data={"csrf_token": token, "password": "test-password"})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COINBASE_API_KEY_NAME", None)
            os.environ.pop("COINBASE_API_PRIVATE_KEY", None)
            dashboard = self.client.get("/?per_page=25")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Page 1 sur 2", dashboard.data)
        self.assertIn(b"Suivante", dashboard.data)

    def test_transaction_category_filters_use_coinbase_order_side_and_transfers(self):
        connection = get_connection(self.app.config["DATABASE_PATH"])
        try:
            store_transactions(connection, [
                {
                    "id": "fill-buy",
                    "type": "advanced_trade_fill",
                    "status": "completed",
                    "created_at": "2025-01-01T12:00:00Z",
                    "amount": {"amount": "1", "currency": "BTC"},
                    "native_amount": {"amount": "-100", "currency": "EUR"},
                    "advanced_trade_fill": {"order_side": "buy"},
                },
                {
                    "id": "fill-sell",
                    "type": "advanced_trade_fill",
                    "status": "completed",
                    "created_at": "2025-01-02T12:00:00Z",
                    "amount": {"amount": "-1", "currency": "BTC"},
                    "native_amount": {"amount": "120", "currency": "EUR"},
                    "advanced_trade_fill": {"order_side": "sell"},
                },
                {
                    "id": "fill-sell-usdc-received",
                    "type": "advanced_trade_fill",
                    "status": "completed",
                    "created_at": "2025-01-02T12:00:00Z",
                    "amount": {"amount": "56.87", "currency": "USDC"},
                    "native_amount": {"amount": "52.3", "currency": "EUR"},
                    "advanced_trade_fill": {"order_side": "sell"},
                },
                {
                    "id": "fill-buy-eur-spent",
                    "type": "advanced_trade_fill",
                    "status": "completed",
                    "created_at": "2025-01-02T12:00:00Z",
                    "amount": {"amount": "-100", "currency": "EUR"},
                    "native_amount": {"amount": "-100", "currency": "EUR"},
                    "advanced_trade_fill": {"order_side": "buy"},
                },
                {
                    "id": "stake",
                    "type": "staking_transfer",
                    "status": "completed",
                    "created_at": "2025-01-03T12:00:00Z",
                    "amount": {"amount": "-1", "currency": "ETH"},
                    "native_amount": {"amount": "0", "currency": "EUR"},
                },
            ])
            connection.commit()
            for category, expected_ids in (
                ("BUY", {"fill-buy", "fill-sell-usdc-received", "fill-buy-eur-spent"}),
                ("SELL", {"fill-sell"}),
                ("Transfer", {"stake"}),
            ):
                filters = parse_transaction_filters({"category": category})
                rows, total, _pages, _page = query_transactions(connection, filters)
                self.assertEqual(total, len(expected_ids))
                self.assertEqual({row["id"] for row in rows}, expected_ids)
            filters = parse_transaction_filters({"category": "BUY", "asset": "USDC"})
            rows, total, _pages, _page = query_transactions(connection, filters)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["id"], "fill-sell-usdc-received")
            filters = parse_transaction_filters({"category": "BUY", "asset": "EUR"})
            rows, total, _pages, _page = query_transactions(connection, filters)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["id"], "fill-buy-eur-spent")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
