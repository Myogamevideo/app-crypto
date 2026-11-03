import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from crypto_dashboard.coinbase import CoinbaseClient, CoinbaseError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


class CoinbaseClientTests(unittest.TestCase):
    def test_builds_short_lived_es256_token_for_the_exact_endpoint(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        client = CoinbaseClient("organizations/org/apiKeys/key", pem)
        token = client._jwt("GET", "/v2/accounts")
        claims = jwt.decode(token, options={"verify_signature": False})
        header = jwt.get_unverified_header(token)
        self.assertEqual(claims["uri"], "GET api.coinbase.com/v2/accounts")
        self.assertEqual(claims["iss"], "cdp")
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "organizations/org/apiKeys/key")
        self.assertLessEqual(claims["exp"] - claims["nbf"], 120)

    def test_fetches_all_accounts_and_paginated_transactions(self):
        session = FakeSession([
            FakeResponse({
                "data": [{"id": "account-1"}],
                "pagination": {"next_uri": None},
            }),
            FakeResponse({
                "data": [{"id": "tx-1", "type": "buy"}],
                "pagination": {
                    "next_uri": "/v2/accounts/account-1/transactions?starting_after=tx-1&limit=100"
                },
            }),
            FakeResponse({
                "data": [{"id": "tx-2", "type": "sell"}],
                "pagination": {"next_uri": None},
            }),
        ])
        client = CoinbaseClient("key-name", "private-key", session)
        with patch.object(client, "_jwt", return_value="token") as make_jwt:
            accounts = client.fetch_accounts()
            transactions = client.fetch_all_transactions(accounts)

        self.assertEqual([tx["id"] for tx in transactions], ["tx-1", "tx-2"])
        self.assertEqual(accounts, [{"id": "account-1"}])
        self.assertEqual([tx["_account_id"] for tx in transactions], ["account-1"] * 2)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(make_jwt.call_args_list[2].args, ("GET", "/v2/accounts/account-1/transactions"))
        self.assertEqual(session.calls[2][1], {"starting_after": "tx-1", "limit": "100"})

    def test_rejects_external_pagination_link(self):
        session = FakeSession([
            FakeResponse({
                "data": [],
                "pagination": {"next_uri": "https://example.org/v2/accounts?limit=100"},
            }),
        ])
        client = CoinbaseClient("key-name", "private-key", session)
        with patch.object(client, "_jwt", return_value="token"):
            with self.assertRaises(CoinbaseError):
                list(client._pages("/v2/accounts", {"limit": 100}))


if __name__ == "__main__":
    unittest.main()
