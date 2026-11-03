import json
import unittest
from datetime import date
from decimal import Decimal

from crypto_dashboard.charts import history_dates
from crypto_dashboard.ledger import build_dashboard, default_category
from crypto_dashboard.french_tax import estimate_french_tax_gain


def row(transaction_id, kind, amount, currency, native_amount, native_currency="EUR",
        created_at="2025-01-01T12:00:00Z", status="completed"):
    return {
        "id": transaction_id,
        "type": kind,
        "status": status,
        "created_at": created_at,
        "amount": amount,
        "amount_currency": currency,
        "native_amount": native_amount,
        "native_currency": native_currency,
        "raw_json": json.dumps({
            "id": transaction_id,
            "type": kind,
            "status": status,
            "created_at": created_at,
            "amount": {"amount": amount, "currency": currency},
            "native_amount": {"amount": native_amount, "currency": native_currency},
        }),
    }


class LedgerTests(unittest.TestCase):
    def test_chart_history_is_limited_to_the_previous_twelve_months(self):
        days = history_dates(
            [{"date": "2021-01-01T12:00:00Z"}],
            "2026-10-29",
        )
        self.assertIn("2026-10-29", days)
        self.assertIn("2025-10-29", days)
        self.assertGreaterEqual(
            min(date.fromisoformat(day) for day in days),
            date(2025, 10, 29),
        )
        self.assertLessEqual(len(days), 18)

    def test_category_mapping(self):
        self.assertEqual(default_category("buy"), "BUY")
        self.assertEqual(default_category("sell"), "SELL")
        self.assertEqual(default_category("earn_payout"), "DIVIDEND")
        self.assertEqual(default_category("fiat_deposit"), "Deposit")
        self.assertEqual(default_category("send"), "Withdrawal")
        self.assertEqual(default_category("send", "0.5", "BTC"), "Deposit")
        self.assertEqual(default_category("send", "-0.5", "BTC"), "Withdrawal")
        self.assertEqual(default_category("staking_transfer"), "Transfer")
        self.assertEqual(
            default_category(
                "advanced_trade_fill",
                "56.87",
                "USDC",
                {"advanced_trade_fill": {"order_side": "sell"}},
            ),
            "BUY",
        )
        self.assertEqual(
            default_category(
                "advanced_trade_fill",
                "-0.03",
                "ETH",
                {"advanced_trade_fill": {"order_side": "sell"}},
            ),
            "SELL",
        )
        self.assertEqual(
            default_category(
                "advanced_trade_fill",
                "-170.24",
                "EUR",
                {"advanced_trade_fill": {"order_side": "buy"}},
            ),
            "BUY",
        )
        self.assertEqual(
            default_category(
                "advanced_trade_fill",
                "52.3",
                "EUR",
                {"advanced_trade_fill": {"order_side": "sell"}},
            ),
            "SELL",
        )
        self.assertEqual(default_category("retail_eth2_deprecation"), "Transfer")

    def test_average_cost_realized_and_remaining_basis(self):
        rows = [
            row("buy-1", "buy", "2", "BTC", "-200", created_at="2025-01-01T12:00:00Z"),
            row("buy-2", "buy", "2", "BTC", "-400", created_at="2025-01-02T12:00:00Z"),
            row("sell-1", "sell", "-1", "BTC", "150", created_at="2025-01-03T12:00:00Z"),
        ]
        result = build_dashboard(rows, as_of="2025-01-03")
        btc = result["assets"][0]
        self.assertEqual(btc["quantity"], 3)
        self.assertEqual(btc["basis"], 450)
        self.assertEqual(btc["average_cost"], 150)
        self.assertEqual(btc["realized"], 0)
        self.assertEqual(btc["sell_qty"], 1)

    def test_negative_pro_deposit_reduces_balance_without_realizing_gain(self):
        rows = [
            row("buy-1", "buy", "1", "ETH", "-100"),
            row("pro-out-1", "pro_deposit", "-0.25", "ETH", "25"),
            row("pro-out-2", "pro_withdrawal", "-0.25", "ETH", "25"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01", include_history=False)

        eth = result["assets"][0]
        self.assertEqual(eth["quantity"], Decimal("0.5"))
        self.assertEqual(eth["basis"], Decimal("50.0"))
        self.assertEqual(eth["realized"], Decimal("0"))
        self.assertFalse(any(
            issue["kind"] == "Transfert sortant supérieur au solde"
            for issue in result["quality_issues"]
        ))

    def test_current_coinbase_balance_corrects_quantity_and_flags_material_gap(self):
        today = date.today().isoformat()
        rows = [row("buy-1", "buy", "10", "USDC", "-10")]
        result = build_dashboard(
            rows,
            as_of=today,
            include_history=False,
            account_balances={"USDC": "2.531994", "EUR": "0", "EURC": "0"},
            account_balances_at="2026-01-01T12:00:00+00:00",
            price_for=lambda _asset, _day: Decimal("1"),
        )

        usdc = result["assets"][0]
        self.assertEqual(usdc["quantity"], Decimal("2.531994"))
        self.assertEqual(usdc["value_eur"], Decimal("2.531994"))
        self.assertFalse(usdc["basis_known"])
        self.assertEqual(result["account_balances"]["USDC"], "2.531994")
        reconciliation = next(
            item for item in result["balance_reconciliation"]
            if item["asset"] == "USDC"
        )
        self.assertEqual(reconciliation["ledger_quantity"], Decimal("10"))
        self.assertEqual(reconciliation["difference"], Decimal("-7.468006"))
        self.assertEqual(reconciliation["difference_value_eur"], Decimal("7.468006"))
        self.assertEqual(reconciliation["last_transaction_id"], "buy-1")
        self.assertEqual(reconciliation["status"], "À examiner")
        self.assertEqual(reconciliation["basis_status"], "Incomplet")
        self.assertTrue(any(
            issue["kind"] == "Écart de solde Coinbase"
            and issue["asset"] == "USDC"
            for issue in result["quality_issues"]
        ))

    def test_activity_report_summarizes_amounts_and_realized_gain_by_year(self):
        rows = [
            row("deposit-1", "fiat_deposit", "100", "EUR", "100", created_at="2021-01-01T10:00:00Z"),
            row("withdraw-1", "fiat_withdrawal", "-20", "EUR", "-20", created_at="2021-01-02T10:00:00Z"),
            row("buy-1", "buy", "1", "BTC", "-80", created_at="2021-01-03T10:00:00Z"),
            row("sell-1", "sell", "-0.5", "BTC", "50", created_at="2022-01-03T10:00:00Z"),
        ]
        result = build_dashboard(rows, as_of="2022-12-31")
        report = result["activity_report"]
        rows_by_label = {row["label"]: row["values"] for row in report["value_rows"]}
        counts_by_label = {row["label"]: row["values"] for row in report["count_rows"]}

        self.assertEqual(report["years"], [2021, 2022])
        self.assertEqual(rows_by_label["EURDeposit"], [Decimal("100"), Decimal("100"), Decimal("0")])
        self.assertEqual(rows_by_label["EURWithdrawal"], [Decimal("-20"), Decimal("-20"), Decimal("0")])
        self.assertEqual(rows_by_label["Total"], [Decimal("80"), Decimal("80"), Decimal("0")])
        self.assertEqual(rows_by_label["BUY"], [Decimal("80"), Decimal("80"), Decimal("0")])
        self.assertEqual(rows_by_label["SELL"], [Decimal("-50"), Decimal("0"), Decimal("-50")])
        self.assertEqual(rows_by_label["P/G"], [Decimal("10"), Decimal("0"), Decimal("10")])
        self.assertEqual(rows_by_label["%"], [Decimal("12.5"), Decimal("0"), None])
        self.assertEqual(counts_by_label["BUY"], [1, 1, 0])
        self.assertEqual(report["line_count_values"], [4, 3, 1])

    def test_deposit_without_native_value_uses_historical_price_as_basis(self):
        rows = [row("receive-1", "receive", "0.25", "ETH", "0", native_currency="EUR")]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda asset, day: Decimal("50") if asset == "ETH" and day == "2025-01-01" else None,
        )
        eth = result["assets"][0]

        self.assertTrue(eth["basis_known"])
        self.assertEqual(eth["received_eur"], Decimal("12.50"))
        self.assertEqual(eth["basis"], Decimal("12.50"))
        self.assertEqual(eth["average_cost"], Decimal("50"))
        self.assertFalse(any(
            issue["kind"] == "Cours EUR d’entrée manquant"
            for issue in result["quality_issues"]
        ))

    def test_deposit_without_available_historical_price_is_flagged(self):
        rows = [row("receive-1", "receive", "0.25", "ETH", "50", native_currency="USD")]
        result = build_dashboard(rows, as_of="2025-01-01")
        self.assertFalse(result["assets"][0]["basis_known"])
        self.assertTrue(result["warnings"])
        self.assertTrue(any(
            issue["kind"] == "Cours EUR d’entrée manquant"
            for issue in result["quality_issues"]
        ))

    def test_betting_winnings_are_reported_separately_and_added_to_basis(self):
        rows = [row(
            "bet-win",
            "receive",
            "0.5",
            "ETH",
            "0",
            created_at="2025-01-01T12:00:00Z",
        )]
        result = build_dashboard(
            rows,
            category_overrides={"bet-win": "Gain de pari"},
            as_of="2025-01-01",
            price_for=lambda _asset, _day: Decimal("100"),
        )

        eth = result["assets"][0]
        value_rows = {
            entry["label"]: entry["values"]
            for entry in result["activity_report"]["value_rows"]
        }
        count_rows = {
            entry["label"]: entry["values"]
            for entry in result["activity_report"]["count_rows"]
        }
        self.assertEqual(eth["quantity"], Decimal("0.5"))
        self.assertEqual(eth["received_eur"], Decimal("50.0"))
        self.assertEqual(eth["basis"], Decimal("50.0"))
        self.assertEqual(value_rows["GAIN DE PARI"], [Decimal("50.0"), Decimal("50.0")])
        self.assertEqual(count_rows["GAIN DE PARI"], [1, 1])
        self.assertFalse(result["french_tax_complete"])

    def test_dashboard_charts_show_basis_coverage_and_suppress_incomplete_performance(self):
        rows = [
            row("buy-btc", "buy", "1", "BTC", "-100", created_at="2024-01-01T12:00:00Z"),
            row("receive-eth", "receive", "1", "ETH", "0", created_at="2024-06-01T12:00:00Z"),
        ]
        prices = {"BTC": Decimal("150"), "ETH": Decimal("70")}
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda asset, day: prices.get(asset)
            if day == "2025-01-01"
            else None,
        )

        charts = result["charts"]
        self.assertEqual(charts["basis_coverage_percent"], Decimal("150") / Decimal("220") * 100)
        self.assertEqual(charts["unknown_basis_value_eur"], Decimal("70"))
        self.assertTrue(charts["value_chart"]["paths"][0]["path"])
        self.assertIsNotNone(charts["flow_chart"])
        self.assertTrue(all(period["value_eur"] is None for period in charts["performance_periods"]))
        self.assertTrue(any("historique complet" in warning for warning in result["warnings"]))

    def test_dashboard_reports_estimated_result_change_for_a_complete_period(self):
        rows = [
            row("buy-btc", "buy", "1", "BTC", "-100", created_at="2024-01-01T12:00:00Z"),
        ]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda _asset, day: Decimal("100") if day < "2025-01-01" else Decimal("130"),
        )
        periods = {item["label"]: item["value_eur"] for item in result["charts"]["performance_periods"]}
        self.assertEqual(periods["1 an"], Decimal("30"))
        self.assertEqual(periods["6 mois"], Decimal("30"))
        self.assertEqual(periods["Depuis le début"], Decimal("30"))

    def test_future_transactions_are_excluded(self):
        rows = [
            row("buy-1", "buy", "1", "ETH", "-100", created_at="2025-01-01T12:00:00Z"),
            row("buy-2", "buy", "1", "ETH", "-200", created_at="2025-01-02T12:00:00Z"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01")
        self.assertEqual(result["assets"][0]["quantity"], 1)

    def test_usd_native_amounts_convert_to_eur_and_price_unrealized_holdings(self):
        rows = [row("buy-1", "buy", "2", "BTC", "-200", native_currency="USD")]
        prices = {"USD": Decimal("0.9"), "BTC": Decimal("150")}
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda asset, _day: prices.get(asset),
        )
        btc = result["assets"][0]
        self.assertEqual(btc["buy_eur"], Decimal("180.0"))
        self.assertEqual(btc["value_eur"], Decimal("300"))
        self.assertEqual(btc["unrealized"], Decimal("120.0"))
        self.assertEqual(result["total_value_eur"], Decimal("300"))
        self.assertEqual(result["total_unrealized_eur"], Decimal("120.0"))

    def test_pending_transactions_are_excluded(self):
        rows = [row("buy-1", "buy", "1", "ETH", "-100", status="pending")]
        self.assertEqual(build_dashboard(rows)["assets"], [])

    def test_internal_staking_transfers_do_not_change_total_holdings(self):
        rows = [
            row("buy-1", "buy", "2", "ETH", "-100"),
            row("stake-1", "staking_transfer", "-1", "ETH", "0"),
            row("unstake-1", "unstaking_transfer", "1", "ETH", "0"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01")
        self.assertEqual(result["assets"][0]["quantity"], Decimal("2"))
        self.assertEqual(result["assets"][0]["basis"], Decimal("100"))

    def test_zero_native_value_reward_uses_historical_asset_price(self):
        rows = [row("reward-1", "staking_reward", "0.5", "DOT", "0")]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda asset, _day: Decimal("6") if asset == "DOT" else None,
        )
        dot = result["assets"][0]
        self.assertEqual(dot["received_eur"], Decimal("3.0"))
        self.assertEqual(dot["basis"], Decimal("3.0"))
        self.assertTrue(dot["basis_known"])

    def test_eth2_reward_is_consolidated_with_eth(self):
        rows = [
            row("eth-buy", "buy", "1", "ETH", "-100"),
            row("eth2-reward", "staking_reward", "0.1", "ETH2", "0"),
        ]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda asset, _day: Decimal("100") if asset == "ETH" else None,
        )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["asset"], "ETH")
        self.assertEqual(result["assets"][0]["quantity"], Decimal("1.1"))
        self.assertEqual(result["assets"][0]["basis"], Decimal("110.0"))

    def test_sell_fill_receives_stablecoin_and_sells_base_asset(self):
        rows = [
            row("eth-buy", "buy", "0.03", "ETH", "-30"),
            row("eth-sell-leg", "advanced_trade_fill", "-0.03", "ETH", "-52.31"),
            {
                **row("usdc-received", "advanced_trade_fill", "56.87", "USDC", "52.3"),
                "raw_json": json.dumps({
                    "id": "usdc-received",
                    "type": "advanced_trade_fill",
                    "status": "completed",
                    "created_at": "2025-01-01T12:00:00Z",
                    "amount": {"amount": "56.87", "currency": "USDC"},
                    "native_amount": {"amount": "52.3", "currency": "EUR"},
                    "advanced_trade_fill": {"order_side": "sell"},
                }),
            },
        ]
        result = build_dashboard(rows, as_of="2025-01-01")
        holdings = {asset["asset"]: asset for asset in result["assets"]}
        self.assertEqual(holdings["ETH"]["quantity"], Decimal("0"))
        self.assertEqual(holdings["ETH"]["realized"], Decimal("22.31"))
        self.assertEqual(holdings["USDC"]["quantity"], Decimal("56.87"))
        self.assertEqual(holdings["USDC"]["basis"], Decimal("52.3"))
        self.assertTrue(holdings["USDC"]["basis_known"])

    def test_oversell_does_not_create_negative_holdings(self):
        rows = [
            row("buy-1", "buy", "1", "BTC", "-100"),
            row("sell-1", "sell", "-2", "BTC", "300"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01")
        self.assertEqual(result["assets"][0]["quantity"], Decimal("0"))
        self.assertFalse(result["assets"][0]["basis_known"])
        self.assertTrue(any(
            issue["kind"] == "Quantité vendue supérieure au solde"
            for issue in result["quality_issues"]
        ))

    def test_sub_euro_missing_trade_value_is_zero_and_not_flagged(self):
        rows = [row("small-buy", "buy", "0.01", "ETH", "0")]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda _asset, _day: Decimal("20"),
        )

        eth = result["assets"][0]
        self.assertEqual(eth["buy_eur"], Decimal("0"))
        self.assertTrue(eth["basis_known"])
        self.assertFalse(any(
            issue["kind"] == "Valeur EUR manquante"
            for issue in result["quality_issues"]
        ))

    def test_exactly_half_euro_is_not_below_the_tolerance(self):
        rows = [row("half-euro-buy", "buy", "0.01", "ETH", "0.50")]
        result = build_dashboard(rows, as_of="2025-01-01")

        self.assertEqual(result["assets"][0]["buy_eur"], Decimal("0.50"))

    def test_sub_half_euro_oversold_quantity_does_not_invalidate_basis(self):
        rows = [
            row("buy-1", "buy", "1", "BTC", "-100"),
            row("sell-1", "sell", "-1.1", "BTC", "0.55"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01")

        btc = result["assets"][0]
        self.assertEqual(btc["quantity"], Decimal("0"))
        self.assertTrue(btc["basis_known"])
        self.assertFalse(any(
            issue["kind"] == "Quantité vendue supérieure au solde"
            for issue in result["quality_issues"]
        ))

    def test_sub_half_euro_withdrawal_rounding_difference_is_ignored(self):
        rows = [
            row(
                "buy-sol",
                "buy",
                "0.12330196839080",
                "SOL",
                "-10",
                created_at="2024-12-01T12:00:00Z",
            ),
            row(
                "withdraw-sol",
                "send",
                "-0.123301971",
                "SOL",
                "-10",
                created_at="2024-12-13T12:00:00Z",
            ),
        ]
        result = build_dashboard(
            rows,
            as_of="2024-12-13",
            price_for=lambda _asset, _day: Decimal("200"),
        )

        sol = result["assets"][0]
        self.assertEqual(sol["quantity"], Decimal("0"))
        self.assertTrue(sol["basis_known"])
        self.assertFalse(any(
            issue["kind"] == "Transfert sortant supérieur au solde"
            for issue in result["quality_issues"]
        ))

    def test_withdrawal_exceeding_balance_by_more_than_half_euro_is_flagged(self):
        rows = [
            row("buy-sol", "buy", "1", "SOL", "-100"),
            row("withdraw-sol", "send", "-1.01", "SOL", "-101"),
        ]
        result = build_dashboard(
            rows,
            as_of="2025-01-01",
            price_for=lambda _asset, _day: Decimal("100"),
        )

        self.assertFalse(result["assets"][0]["basis_known"])
        self.assertTrue(any(
            issue["kind"] == "Transfert sortant supérieur au solde"
            for issue in result["quality_issues"]
        ))

    def test_dust_disposal_with_zero_proceeds_realizes_loss_at_known_basis(self):
        rows = [
            row("buy-atom", "buy", "1", "ATOM", "-10"),
            row("dust-atom", "retail_simple_dust", "-0.25", "ATOM", "0"),
        ]
        result = build_dashboard(rows, as_of="2025-01-01")
        atom = result["assets"][0]
        self.assertEqual(atom["quantity"], Decimal("0.75"))
        self.assertEqual(atom["basis"], Decimal("7.50"))
        self.assertEqual(atom["sell_qty"], Decimal("0.25"))
        self.assertEqual(atom["sell_eur"], Decimal("0"))
        self.assertEqual(atom["realized"], Decimal("-2.50"))
        self.assertTrue(atom["basis_known"])
        self.assertEqual(result["total_realized_eur"], Decimal("-2.50"))

    def test_french_calculation_uses_global_portfolio_basis(self):
        transactions = [
            {
                "asset": "BTC", "amount": Decimal("1"), "category": "BUY", "date": "2025-01-01",
                "native_amount": Decimal("-100"), "native_currency": "EUR",
            },
            {
                "asset": "ETH", "amount": Decimal("1"), "category": "BUY", "date": "2025-01-01",
                "native_amount": Decimal("-100"), "native_currency": "EUR",
            },
            {
                "asset": "BTC", "amount": Decimal("-0.5"), "category": "SELL", "date": "2025-01-02",
                "native_amount": Decimal("150"), "native_currency": "EUR",
            },
        ]
        result = estimate_french_tax_gain(
            transactions,
            lambda asset, day: 300 if asset == "BTC" else 100,
            portfolio_complete=True,
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["gain_eur"], 75)

    def test_french_calculation_requires_complete_portfolio(self):
        result = estimate_french_tax_gain([], lambda _asset, _day: 1, portfolio_complete=False)
        self.assertIsNone(result["gain_eur"])
        self.assertFalse(result["complete"])

    def test_internal_staking_transfers_do_not_change_french_tax_quantities(self):
        transactions = [
            {
                "asset": "DOT", "amount": Decimal("1"), "category": "BUY", "date": "2025-01-01",
                "native_amount": Decimal("-10"), "native_currency": "EUR",
            },
            {
                "asset": "DOT", "amount": Decimal("-1"), "category": "Transfer", "date": "2025-01-01",
                "native_amount": Decimal("-10"), "native_currency": "EUR",
            },
            {
                "asset": "DOT", "amount": Decimal("1"), "category": "Transfer", "date": "2025-01-01",
                "native_amount": Decimal("10"), "native_currency": "EUR",
            },
            {
                "asset": "DOT", "amount": Decimal("-0.5"), "category": "SELL", "date": "2025-01-02",
                "native_amount": Decimal("15"), "native_currency": "EUR",
            },
        ]
        result = estimate_french_tax_gain(
            transactions,
            lambda _asset, _day: Decimal("20"),
            portfolio_complete=True,
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["gain_eur"], Decimal("7.5"))


if __name__ == "__main__":
    unittest.main()
