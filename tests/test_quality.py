import unittest

from crypto_dashboard.quality import (
    build_market_quality_issues,
    build_status_issues,
    summarize_quality_issues,
)


class DataQualityTests(unittest.TestCase):
    def test_market_issues_group_missing_quotes_and_link_related_transactions(self):
        rows = [
            {
                "id": f"tx-{day}",
                "amount_currency": "BTC",
                "created_at": f"{day}T12:00:00Z",
            }
            for day in ("2025-01-01", "2025-01-02")
        ]
        issues = build_market_quality_issues(
            {("BTC", "2025-01-01"): "HTTP 400"},
            {
                ("BTC", "2025-01-02"): None,
                ("ETH", "2025-01-02"): None,
            },
            rows,
            "2025-01-02",
        )

        by_asset = {issue["asset"]: issue for issue in issues}
        self.assertEqual(by_asset["BTC"]["dates"], ["2025-01-01", "2025-01-02"])
        self.assertEqual(by_asset["BTC"]["problem"], "HTTP 400")
        self.assertEqual(by_asset["BTC"]["transaction_ids"], ["tx-2025-01-01", "tx-2025-01-02"])
        self.assertEqual(by_asset["ETH"]["transaction_ids"], [])
        self.assertIn("Aucun cours EUR", by_asset["ETH"]["problem"])

    def test_status_issues_include_only_nonfinalized_transactions_through_cutoff(self):
        rows = [
            {"id": "pending", "amount_currency": "ETH", "created_at": "2025-01-01T10:00:00Z", "status": "pending"},
            {"id": "done", "amount_currency": "BTC", "created_at": "2025-01-01T11:00:00Z", "status": "completed"},
            {"id": "future", "amount_currency": "SOL", "created_at": "2025-01-03T10:00:00Z", "status": "pending"},
        ]

        issues = build_status_issues(rows, "2025-01-02")

        self.assertEqual([issue["transaction_id"] for issue in issues], ["pending"])

    def test_issue_summary_reports_truncation_without_losing_total_count(self):
        issues = [
            {
                "date": f"2025-01-{index:02}",
                "kind": "Erreur",
                "transaction_id": str(index),
            }
            for index in range(1, 4)
        ]

        summary = summarize_quality_issues(issues, limit=2)

        self.assertEqual(summary["count"], 3)
        self.assertEqual(len(summary["items"]), 2)
        self.assertTrue(summary["truncated"])


if __name__ == "__main__":
    unittest.main()
