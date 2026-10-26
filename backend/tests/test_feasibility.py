import unittest
from decimal import Decimal

from docscope.feasibility import (
    FeasibilityError,
    calculate_budget,
    classify_connection_error,
    validate_qdrant_results,
    validate_llm_base_url,
)


class FeasibilityTest(unittest.TestCase):
    def test_budget_uses_measured_tokens_and_stays_below_cap(self):
        result = calculate_budget(100, 220, 80, Decimal("2"), Decimal("8"), 45, 5)
        self.assertEqual(result["sample_cost_cny"], "0.00108")
        self.assertEqual(result["projected_cost_cny"], "0.05400")
        self.assertEqual(result["remaining_cny"], "99.94600")

    def test_budget_rejects_missing_measurement_or_excess(self):
        with self.assertRaisesRegex(FeasibilityError, "measured"):
            calculate_budget(100, 0, 0, Decimal("1"), Decimal("1"), 1, 0)
        with self.assertRaisesRegex(FeasibilityError, "exceeds"):
            calculate_budget(Decimal("0.01"), 1000000, 0, Decimal("1"), Decimal("0"), 1, 0)
        with self.assertRaisesRegex(FeasibilityError, "exceeds"):
            calculate_budget(100, 1, 1, Decimal(0), Decimal(0), 0, 0, Decimal(91), Decimal(10))
        with self.assertRaisesRegex(FeasibilityError, "integer"):
            calculate_budget(100, "37", 16, Decimal(0), Decimal(0), 1, 0)

    def test_llm_base_url_allows_local_http_and_remote_https_only(self):
        validate_llm_base_url("http://127.0.0.1:3425/v1")
        validate_llm_base_url("https://example.com/v1")
        with self.assertRaisesRegex(FeasibilityError, "HTTPS"):
            validate_llm_base_url("http://example.com/v1")
        with self.assertRaisesRegex(FeasibilityError, "credentials"):
            validate_llm_base_url("https://user:pass@example.com/v1")

    def test_qdrant_queries_require_shared_chunk_ids(self):
        validate_qdrant_results([1, 2], [2], [2, 1], {1, 2, 3})
        with self.assertRaisesRegex(FeasibilityError, "unknown"):
            validate_qdrant_results([4], [2], [2], {1, 2, 3})
        with self.assertRaisesRegex(FeasibilityError, "fusion"):
            validate_qdrant_results([1], [2], [3], {1, 2, 3})

    def test_connection_failure_is_not_model_failure(self):
        self.assertEqual(classify_connection_error(ConnectionRefusedError()), "environment_unreachable")
        self.assertEqual(classify_connection_error(TimeoutError()), "environment_unreachable")
        self.assertEqual(classify_connection_error(ValueError()), "call_failed")


if __name__ == "__main__":
    unittest.main()
