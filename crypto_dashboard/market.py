from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import requests


class MarketDataError(RuntimeError):
    pass


class MarketProductUnavailableError(MarketDataError):
    pass


class CoinbaseMarketData:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    @staticmethod
    def _market_asset(asset):
        symbol = asset.upper()
        return "ETH" if symbol == "ETH2" else symbol

    def price_eur(self, asset, on_date):
        if not asset:
            return None
        symbol = self._market_asset(asset)
        if symbol == "EUR":
            return Decimal("1")
        if on_date > date.today():
            return None
        if on_date == date.today():
            return self._spot(symbol)
        return self._daily_close(symbol, on_date)

    def _spot(self, asset):
        pair = quote(f"{asset.upper()}-EUR", safe="")
        try:
            response = self.session.get(
                f"https://api.coinbase.com/v2/prices/{pair}/spot",
                timeout=(4, 12),
            )
        except requests.RequestException as error:
            raise MarketDataError(f"Cours EUR indisponible pour {asset}.") from error
        if response.status_code == 404:
            return self._exchange_rate(asset)
        if response.status_code == 400:
            raise MarketProductUnavailableError(
                f"Marché EUR indisponible pour {asset} (HTTP 400)."
            )
        if response.status_code >= 400:
            raise MarketDataError(f"Erreur de cours Coinbase pour {asset} (HTTP {response.status_code}).")
        try:
            amount = response.json()["data"]["amount"]
            return Decimal(str(amount))
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            raise MarketDataError(f"Réponse de cours invalide pour {asset}.") from error

    def _exchange_rate(self, asset):
        try:
            response = self.session.get(
                "https://api.coinbase.com/v2/exchange-rates",
                params={"currency": asset.upper()},
                timeout=(4, 12),
            )
        except requests.RequestException as error:
            raise MarketDataError(f"Taux de change EUR indisponible pour {asset}.") from error
        if response.status_code >= 400:
            if response.status_code in {400, 404}:
                raise MarketProductUnavailableError(
                    f"Marché historique EUR indisponible pour {asset} "
                    f"(HTTP {response.status_code})."
                )
            raise MarketDataError(
                f"Erreur de taux de change Coinbase pour {asset} (HTTP {response.status_code})."
            )
        try:
            rate = response.json().get("data", {}).get("rates", {}).get("EUR")
            return Decimal(str(rate)) if rate is not None else None
        except (ValueError, TypeError, InvalidOperation) as error:
            raise MarketDataError(f"Taux de change Coinbase invalide pour {asset}.") from error

    def _daily_close(self, asset, on_date):
        product_id = quote(f"{self._market_asset(asset)}-EUR", safe="")
        start = datetime.combine(on_date, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        try:
            response = self.session.get(
                f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles",
                params={
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                    "granularity": "ONE_DAY",
                },
                timeout=(4, 12),
            )
        except requests.RequestException as error:
            raise MarketDataError(f"Cours historique indisponible pour {asset}.") from error
        if response.status_code == 404:
            raise MarketProductUnavailableError(
                f"Marché historique EUR indisponible pour {asset} (HTTP 404)."
            )
        if response.status_code >= 400:
            raise MarketDataError(
                f"Erreur de cours historique Coinbase pour {asset} (HTTP {response.status_code})."
            )
        try:
            candles = response.json().get("candles", [])
            if not candles:
                return None
            return Decimal(str(candles[0]["close"]))
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            raise MarketDataError(f"Réponse de cours historique invalide pour {asset}.") from error

    def daily_closes(self, asset, requested_dates):
        days = sorted({date.fromisoformat(day) for day in requested_dates})
        if not days:
            return {}

        product_id = quote(f"{self._market_asset(asset)}-EUR", safe="")
        results = {}
        cursor = days[0]
        final_day = days[-1]
        while cursor <= final_day:
            chunk_end = min(cursor + timedelta(days=299), final_day)
            start = datetime.combine(cursor, time.min, tzinfo=timezone.utc)
            end = datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=timezone.utc)
            try:
                response = self.session.get(
                    f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles",
                    params={
                        "start": int(start.timestamp()),
                        "end": int(end.timestamp()),
                        "granularity": "ONE_DAY",
                    },
                    timeout=(4, 20),
                )
            except requests.RequestException as error:
                raise MarketDataError(f"Cours historiques indisponibles pour {asset}.") from error
            if response.status_code in {400, 404}:
                raise MarketProductUnavailableError(
                    f"Marché historique EUR indisponible pour {asset} "
                    f"(HTTP {response.status_code})."
                )
            if response.status_code >= 400:
                raise MarketDataError(
                    f"Erreur de cours historiques Coinbase pour {asset} (HTTP {response.status_code})."
                )
            if response.status_code == 200:
                try:
                    for candle in response.json().get("candles", []):
                        candle_day = datetime.fromtimestamp(
                            int(candle["start"]), tz=timezone.utc
                        ).date()
                        results[candle_day.isoformat()] = Decimal(str(candle["close"]))
                except (ValueError, KeyError, TypeError, InvalidOperation) as error:
                    raise MarketDataError(
                        f"Réponse de cours historique invalide pour {asset}."
                    ) from error
            cursor = chunk_end + timedelta(days=1)
        return {day.isoformat(): results[day.isoformat()] for day in days if day.isoformat() in results}


class BinanceMarketData:
    ASSET_PAIRS = {
        "VET": (("VETEUR", "VET", "EUR"), ("VETUSDT", "VET", "USDT"), ("VETBTC", "VET", "BTC")),
        "NEAR": (("NEAREUR", "NEAR", "EUR"), ("NEARUSDT", "NEAR", "USDT"), ("NEARBTC", "NEAR", "BTC")),
        "POLY": (("POLYUSDT", "POLY", "USDT"), ("POLYBTC", "POLY", "BTC")),
        "MATIC": (
            ("MATICEUR", "MATIC", "EUR"),
            ("MATICUSDT", "MATIC", "USDT"),
            ("MATICBTC", "MATIC", "BTC"),
            ("POLEUR", "POL", "EUR"),
            ("POLUSDT", "POL", "USDT"),
            ("POLBTC", "POL", "BTC"),
        ),
        "RONIN": (("RONINEUR", "RONIN", "EUR"), ("RONINUSDT", "RONIN", "USDT"), ("RONINBTC", "RONIN", "BTC")),
        "ONDO": (("ONDOEUR", "ONDO", "EUR"), ("ONDOUSDT", "ONDO", "USDT"), ("ONDOBTC", "ONDO", "BTC")),
    }
    BASE_URL = "https://api.binance.com/api/v3/klines"
    MAX_CANDLES = 1000

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def price_eur(self, asset, on_date):
        return self.daily_closes(asset, [on_date.isoformat()]).get(on_date.isoformat())

    def _daily_pair_closes(self, symbol, days):
        if not days:
            return {}
        closes = {}
        first_day = date.fromisoformat(days[0])
        last_day = date.fromisoformat(days[-1])
        cursor = first_day
        while cursor <= last_day:
            chunk_end = min(
                cursor + timedelta(days=self.MAX_CANDLES - 1),
                last_day,
            )
            start = datetime.combine(cursor, time.min, tzinfo=timezone.utc)
            end = datetime.combine(
                chunk_end + timedelta(days=1), time.min, tzinfo=timezone.utc
            )
            try:
                response = self.session.get(
                    self.BASE_URL,
                    params={
                        "symbol": symbol,
                        "interval": "1d",
                        "startTime": int(start.timestamp() * 1000),
                        "endTime": int(end.timestamp() * 1000) - 1,
                        "limit": self.MAX_CANDLES,
                    },
                    timeout=(4, 20),
                )
            except requests.RequestException as error:
                raise MarketDataError(
                    f"Cours historiques Binance indisponibles pour {symbol}."
                ) from error
            if response.status_code in {400, 404}:
                return {}
            if response.status_code >= 400:
                raise MarketDataError(
                    f"Erreur de cours historiques Binance (HTTP {response.status_code})."
                )
            try:
                for candle in response.json():
                    day = datetime.fromtimestamp(
                        int(candle[0]) / 1000, tz=timezone.utc
                    ).date().isoformat()
                    closes[day] = Decimal(str(candle[4]))
            except (ValueError, KeyError, TypeError, IndexError, InvalidOperation) as error:
                raise MarketDataError("Réponse de cours historique Binance invalide.") from error
            cursor = chunk_end + timedelta(days=1)
        return closes

    def daily_closes(self, asset, requested_dates):
        days = sorted({date.fromisoformat(day).isoformat() for day in requested_dates})
        if not days:
            return {}
        pairs = self.ASSET_PAIRS.get(asset.upper())
        if pairs is None:
            raise MarketProductUnavailableError(
                f"Aucune paire Binance de secours n’est configurée pour {asset}."
            )

        results = {}
        missing_days = set(days)
        for symbol, _base_asset, quote_asset in pairs:
            pair_prices = self._daily_pair_closes(
                symbol,
                sorted(missing_days),
            )
            if quote_asset == "EUR":
                converted = pair_prices
            elif quote_asset == "USDT":
                needed_rates = {
                    day: rate
                    for day, rate in self._daily_pair_closes(
                        "EURUSDT", sorted(pair_prices)
                    ).items()
                    if rate > 0
                }
                converted = {
                    day: price / needed_rates[day]
                    for day, price in pair_prices.items()
                    if day in needed_rates
                }
            elif quote_asset == "BTC":
                needed_rates = {
                    day: rate
                    for day, rate in self._daily_pair_closes(
                        "BTCEUR", sorted(pair_prices)
                    ).items()
                    if rate > 0
                }
                converted = {
                    day: price * needed_rates[day]
                    for day, price in pair_prices.items()
                    if day in needed_rates
                }
            else:
                continue
            results.update(converted)
            missing_days.difference_update(converted)
            if not missing_days:
                break
        return {day: results[day] for day in days if day in results}


class CoinGeckoMarketData:
    ASSET_IDS = {
        "NEAR": "near-protocol",
        "VET": "vechain",
        "RONIN": "ronin",
        "ONDO": "ondo-finance",
        "MATIC": "polygon-ecosystem-token",
        "POLY": "polymath",
    }
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, api_key, session=None):
        self.api_key = api_key
        self.session = session or requests.Session()

    def _coin_id(self, asset):
        coin_id = self.ASSET_IDS.get(asset.upper())
        if coin_id is None:
            raise MarketProductUnavailableError(
                f"Aucun identifiant CoinGecko n’est configuré pour {asset}."
            )
        return coin_id

    def _get(self, path, params):
        headers = (
            {"x-cg-demo-api-key": self.api_key}
            if self.api_key
            else {}
        )
        try:
            response = self.session.get(
                f"{self.BASE_URL}/{path}",
                params=params,
                headers=headers,
                timeout=(4, 12),
            )
        except requests.RequestException as error:
            raise MarketDataError("Connexion à CoinGecko impossible.") from error
        if response.status_code >= 400:
            raise MarketDataError(
                f"Erreur de cours CoinGecko (HTTP {response.status_code})."
            )
        try:
            return response.json()
        except (ValueError, TypeError) as error:
            raise MarketDataError("Réponse de cours CoinGecko invalide.") from error

    def price_eur(self, asset, on_date):
        coin_id = self._coin_id(asset)
        if on_date > date.today():
            return None
        if on_date == date.today():
            payload = self._get(
                "simple/price",
                {"ids": coin_id, "vs_currencies": "eur"},
            )
            value = payload.get(coin_id, {}).get("eur")
            return Decimal(str(value)) if value is not None else None
        prices = self.daily_closes(asset, [on_date.isoformat()])
        return prices.get(on_date.isoformat())

    def daily_closes(self, asset, requested_dates):
        days = sorted({date.fromisoformat(day) for day in requested_dates})
        if not days:
            return {}
        coin_id = self._coin_id(asset)
        payload = self._get(
            f"coins/{coin_id}/market_chart/range",
            {
                "vs_currency": "eur",
                "from": days[0].isoformat(),
                "to": (days[-1] + timedelta(days=1)).isoformat(),
            },
        )
        prices_by_day = {}
        try:
            for timestamp_ms, value in payload.get("prices", []):
                day = datetime.fromtimestamp(
                    int(timestamp_ms) / 1000, tz=timezone.utc
                ).date().isoformat()
                prices_by_day[day] = Decimal(str(value))
        except (ValueError, TypeError, OverflowError, InvalidOperation) as error:
            raise MarketDataError("Réponse de cours CoinGecko invalide.") from error
        return {
            day.isoformat(): prices_by_day[day.isoformat()]
            for day in days
            if day.isoformat() in prices_by_day
        }


class FallbackMarketData:
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    def price_eur(self, asset, on_date):
        try:
            price = self.primary.price_eur(asset, on_date)
        except MarketProductUnavailableError as primary_error:
            try:
                return self.fallback.price_eur(asset, on_date)
            except MarketDataError as fallback_error:
                raise MarketDataError(
                    f"{primary_error} Source alternative : {fallback_error}"
                ) from fallback_error
        if price is not None:
            return price
        return self.fallback.price_eur(asset, on_date)

    def daily_closes(self, asset, requested_dates):
        days = sorted(set(requested_dates))
        if not days:
            return {}
        try:
            prices = self.primary.daily_closes(asset, days)
        except MarketProductUnavailableError as primary_error:
            try:
                return self.fallback.daily_closes(asset, days)
            except MarketDataError as fallback_error:
                raise MarketDataError(
                    f"{primary_error} Source alternative : {fallback_error}"
                ) from fallback_error
        missing_days = [day for day in days if day not in prices]
        if missing_days:
            fallback_prices = self.fallback.daily_closes(asset, missing_days)
            prices.update(fallback_prices)
        return prices
