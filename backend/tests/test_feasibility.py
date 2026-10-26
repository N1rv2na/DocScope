import unittest
from decimal import Decimal

from docscope.feasibility import (
    FeasibilityError,
    estimate_cost,
    classify_connection_error,
    validate_qdrant_results,
    validate_llm_base_url,
)


class FeasibilityTest(unittest.TestCase):
    def test_cost_estimate_uses_measured_tokens_without_a_total_cap(self):
        result = estimate_cost(220, 80, Decimal("2"), Decimal("8"), 45, 5)
        self.assertEqual(result["sample_cost_cny"], "0.00108")
        self.assertEqual(result["projected_cost_cny"], "0.05400")
        self.assertEqual(result["estimated_total_cny"], "0.05400")
        expensive = estimate_cost(1000000, 0, Decimal("1"), Decimal("0"), 101, 0)
        self.assertEqual(expensive["estimated_total_cny"], "101.00000")

    def test_cost_estimate_rejects_invalid_measurements(self):
        with self.assertRaisesRegex(FeasibilityError, "measured"):
            estimate_cost(0, 0, Decimal("1"), Decimal("1"), 1, 0)
        with self.assertRaisesRegex(FeasibilityError, "integer"):
            estimate_cost("37", 16, Decimal(0), Decimal(0), 1, 0)

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
