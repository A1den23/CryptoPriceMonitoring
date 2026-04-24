import unittest

from monitor.alert_evaluators import (
    MilestoneEvaluator,
    VolatilityEvaluator,
    VolumeAnomalyEvaluator,
)


class AlertEvaluatorTests(unittest.TestCase):
    def test_milestone_evaluator_detects_crossing(self) -> None:
        evaluator = MilestoneEvaluator("BTCUSDT", 1000.0)

        crossed, milestone = evaluator.has_crossed(99_500.0, 100_100.0)

        self.assertTrue(crossed)
        self.assertEqual(milestone, 100_000.0)

    def test_volatility_evaluator_returns_reasons_and_next_state(self) -> None:
        evaluator = VolatilityEvaluator()
        metrics = evaluator.build_metrics([100.0, 103.0, 97.0, 104.0])

        evaluation = evaluator.evaluate(metrics, threshold=3.0, last_cumulative_volatility=0.0)

        self.assertTrue(evaluation.is_volatile)
        self.assertGreater(evaluation.next_cumulative_volatility, 0)
        self.assertTrue(evaluation.reasons)

    def test_volume_anomaly_evaluator_uses_previous_samples_as_baseline(self) -> None:
        evaluation = VolumeAnomalyEvaluator.evaluate([100.0, 100.0, 500.0], 3.0)

        self.assertIsNotNone(evaluation)
        self.assertEqual(evaluation.avg_volume, 100.0)
        self.assertEqual(evaluation.volume_multiplier, 5.0)
        self.assertTrue(evaluation.should_alert)


if __name__ == "__main__":
    unittest.main()
