import os
import secrets
import time
from urllib.parse import parse_qsl, quote, urlparse

import jwt
import requests


API_HOST = "api.coinbase.com"
API_BASE = f"https://{API_HOST}"


class CoinbaseError(RuntimeError):
    pass


class CoinbaseClient:
    def __init__(self, key_name, private_key, session=None):
        self.key_name = key_name
        self.private_key = private_key.replace("\\n", "\n")
        self.session = session or requests.Session()

    @classmethod
    def from_environment(cls):
        key_name = os.getenv("COINBASE_API_KEY_NAME")
        private_key = os.getenv("COINBASE_API_PRIVATE_KEY")
        if not key_name or not private_key:
            raise CoinbaseError("Les clés Coinbase ne sont pas configurées côté serveur.")
        return cls(key_name, private_key)

    def _jwt(self, method, path):
        uri = f"{method.upper()} {API_HOST}{path}"
        now = int(time.time())
        payload = {
            "sub": self.key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": uri,
        }
        try:
            return jwt.encode(
                payload,
                self.private_key,
                algorithm="ES256",
                headers={"kid": self.key_name, "nonce": secrets.token_hex()},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise CoinbaseError("La clé privée Coinbase est invalide.") from error

    def _get(self, path, params=None):
        parsed_path = urlparse(path)
        route = parsed_path.path
        query = dict(parse_qsl(parsed_path.query))
        if params:
            query.update(params)
        token = self._jwt("GET", route)
        try:
            response = self.session.get(
                f"{API_BASE}{route}",
                params=query or None,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=(5, 30),
            )
        except requests.RequestException as error:
            raise CoinbaseError("Connexion à Coinbase impossible.") from error
        if response.status_code >= 400:
            if response.status_code in {401, 403}:
                raise CoinbaseError(
                    "Coinbase a refusé la clé. Vérifiez qu'elle est active et limitée à la lecture."
                )
            raise CoinbaseError(f"Erreur Coinbase (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except ValueError as error:
            raise CoinbaseError("Réponse Coinbase illisible.") from error
        if not isinstance(payload, dict):
            raise CoinbaseError("Format de réponse Coinbase inattendu.")
        return payload

    def _pages(self, path, params=None):
        next_path = path
        next_params = dict(params or {})
        visited = set()
        while next_path:
            page_key = (next_path, tuple(sorted((next_params or {}).items())))
            if page_key in visited:
                raise CoinbaseError("Pagination Coinbase répétée; synchronisation interrompue.")
            visited.add(page_key)
            payload = self._get(next_path, next_params)
            data = payload.get("data")
            if not isinstance(data, list):
                raise CoinbaseError("Format de réponse Coinbase inattendu.")
            yield from data
            next_uri = (payload.get("pagination") or {}).get("next_uri")
            next_params = None
            if not next_uri:
                break
            parsed = urlparse(next_uri)
            if parsed.scheme and (
                parsed.scheme != "https" or parsed.netloc != API_HOST
            ):
                raise CoinbaseError("Coinbase a renvoyé un lien de pagination non autorisé.")
            next_path = parsed.path
            if not next_path.startswith("/v2/"):
                raise CoinbaseError("Chemin de pagination Coinbase invalide.")
            if parsed.query:
                next_path = f"{next_path}?{parsed.query}"

    def fetch_accounts(self):
        return list(self._pages("/v2/accounts", {"limit": 100}))

    def fetch_all_transactions(self, accounts=None):
        accounts = list(accounts) if accounts is not None else self.fetch_accounts()
        transactions = []
        seen = set()
        for account in accounts:
            account_id = account.get("id")
            if not account_id:
                continue
            path = f"/v2/accounts/{quote(str(account_id), safe='')}/transactions"
            for transaction in self._pages(path, {"limit": 100}):
                transaction_id = transaction.get("id")
                if transaction_id and transaction_id not in seen:
                    seen.add(transaction_id)
                    transaction["_account_id"] = account_id
                    transactions.append(transaction)
        return transactions
