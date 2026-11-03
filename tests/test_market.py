import unittest
from datetime import datetime, timezone
from decimal import Decimal

from crypto_dashboard.market import (
    BinanceMarketData,
    CoinbaseMarketData,
    CoinGeckoMarketData,
    FallbackMarketData,
    MarketProductUnavailableError,
)


class FakeResponse:
    status_code = 200

    def __init__(self, candles):
        self.payload = {"candles": candles}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse([])


class PayloadResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class CoinGeckoSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers, timeout))
        return PayloadResponse(self.payload)


class BinanceSession:
    def __init__(self, candles_by_symbol):
        self.candles_by_symbol = candles_by_symbol
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return PayloadResponse(self.candles_by_symbol.get(params["symbol"], []))


class MarketDataTests(unittest.TestCase):
    def test_historical_dates_are_requested_in_bounded_batches(self):
        session = FakeSession()
        market = CoinbaseMarketData(session=session)
        prices = market.daily_closes("BTC", ["2020-01-01", "2022-01-01"])
        self.assertEqual(prices, {})
        self.assertEqual(len(session.calls), 3)
        self.assertTrue(all(call[1]["granularity"] == "ONE_DAY" for call in session.calls))

    def test_eth2_historical_quotes_use_ethereum_market_product(self):
        session = FakeSession()
        market = CoinbaseMarketData(session=session)
        market.daily_closes("ETH2", ["2024-05-02"])
        self.assertIn("/products/ETH-EUR/candles", session.calls[0][0])

    def test_coingecko_returns_eur_history_for_vechain_and_near(self):
        timestamp = int(
            datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp() * 1000
        )
        session = CoinGeckoSession({"prices": [[timestamp, 0.0456]]})
        market = CoinGeckoMarketData("demo-key", session=session)

        prices = market.daily_closes("VET", ["2025-01-02"])

        self.assertEqual(prices, {"2025-01-02": Decimal("0.0456")})
        self.assertIn("/coins/vechain/market_chart/range", session.calls[0][0])
        self.assertEqual(session.calls[0][2], {"x-cg-demo-api-key": "demo-key"})
        self.assertEqual(session.calls[0][1]["vs_currency"], "eur")

        near_session = CoinGeckoSession({"prices": []})
        CoinGeckoMarketData("demo-key", session=near_session).daily_closes(
            "NEAR", ["2025-01-02"]
        )
        self.assertIn("/coins/near-protocol/market_chart/range", near_session.calls[0][0])

    def test_coingecko_can_use_public_api_without_key_and_maps_matic_to_pol(self):
        session = CoinGeckoSession({"prices": []})
        market = CoinGeckoMarketData(None, session=session)

        market.daily_closes("MATIC", ["2025-10-29"])

        self.assertIn(
            "/coins/polygon-ecosystem-token/market_chart/range",
            session.calls[0][0],
        )
        self.assertEqual(session.calls[0][2], {})

    def test_coingecko_fallback_runs_only_for_unavailable_coinbase_market(self):
        timestamp = int(
            datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp() * 1000
        )
        fallback = CoinGeckoMarketData(
            "demo-key",
            session=CoinGeckoSession({"prices": [[timestamp, 0.0456]]}),
        )

        class UnavailableCoinbase:
            def daily_closes(self, _asset, _days):
                raise MarketProductUnavailableError("Coinbase HTTP 400")

        prices = FallbackMarketData(UnavailableCoinbase(), fallback).daily_closes(
            "VET", ["2025-01-02"]
        )

        self.assertEqual(prices["2025-01-02"], Decimal("0.0456"))

    def test_binance_converts_polymath_btc_candles_to_eur(self):
        day_ms = int(
            datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        session = BinanceSession({
            "POLYUSDT": [],
            "POLYBTC": [[day_ms, "0.000002", "0.000004", "0.000001", "0.000003", "10"]],
            "BTCEUR": [[day_ms, "25000", "26000", "24000", "25000", "1"]],
        })
        market = BinanceMarketData(session=session)

        prices = market.daily_closes("POLY", ["2021-01-01"])

        self.assertEqual(prices, {"2021-01-01": Decimal("0.075000")})
        self.assertEqual(
            [call[1]["symbol"] for call in session.calls],
            ["POLYUSDT", "POLYBTC", "BTCEUR"],
        )

    def test_binance_converts_usdt_pair_with_same_day_eur_rate(self):
        day_ms = int(
            datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        session = BinanceSession({
            "VETEUR": [],
            "VETUSDT": [[day_ms, "0.02", "0.03", "0.01", "0.025", "10"]],
            "EURUSDT": [[day_ms, "1.20", "1.21", "1.19", "1.25", "10"]],
        })
        market = BinanceMarketData(session=session)

        prices = market.daily_closes("VET", ["2021-01-01"])

        self.assertEqual(prices, {"2021-01-01": Decimal("0.020")})

    def test_matic_uses_polygon_token_pair_after_its_migration(self):
        day_ms = int(
            datetime(2025, 10, 29, tzinfo=timezone.utc).timestamp() * 1000
        )
        session = BinanceSession({
            "POLUSDT": [[day_ms, "0.16", "0.17", "0.15", "0.1684", "10"]],
            "EURUSDT": [[day_ms, "1.16", "1.17", "1.15", "1.16", "10"]],
        })
        market = BinanceMarketData(session=session)

        prices = market.daily_closes("MATIC", ["2025-10-29"])

        self.assertEqual(
            prices,
            {"2025-10-29": Decimal("0.1684") / Decimal("1.16")},
        )
        self.assertIn("POLUSDT", [call[1]["symbol"] for call in session.calls])

    def test_polymath_symbol_uses_its_explicit_coingecko_id(self):
        session = CoinGeckoSession({"prices": []})
        market = CoinGeckoMarketData("demo-key", session=session)

        market.daily_closes("POLY", ["2025-01-02"])

        self.assertIn("/coins/polymath/market_chart/range", session.calls[0][0])

    def test_unmapped_coingecko_asset_is_not_guessed(self):
        market = CoinGeckoMarketData("demo-key", session=CoinGeckoSession({}))

        with self.assertRaises(MarketProductUnavailableError):
            market.daily_closes("XYZ", ["2025-01-02"])


if __name__ == "__main__":
    unittest.main()
