"""Comprehensive deterministic tests for EvidenceEvaluator."""

from trace.engine.evaluator import (
    EvaluationRelationship,
    EvidenceEvaluator,
    EvidenceWeight,
)
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario


class TestBasicEvaluation:
    def test_tool_failure_evaluates_neutral_zero_score(self):
        evaluator = EvidenceEvaluator()
        h = Hypothesis(statement="Checkout connection leak")
        failed_ev = EvidenceItem(
            id="ev-fail",
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={},
            tool_succeeded=False,
            error_message="HTTP 503: Service Unavailable",
        )
        res = evaluator.evaluate(h, failed_ev)
        assert res.relationship == EvaluationRelationship.NEUTRAL
        assert res.score_delta == EvidenceWeight.NEUTRAL
        assert res.score_delta == 0
        assert res.rule_id == "RULE_TOOL_FAILURE"
        assert "503" in res.reason

    def test_unrelated_evidence_evaluates_neutral(self):
        evaluator = EvidenceEvaluator()
        h = Hypothesis(statement="Checkout connection leak")
        unrelated_ev = EvidenceItem(
            id="ev-unrelated",
            source_tool="fetch_recent_commits",
            query={"service": "payment"},
            raw_data={
                "commits": [
                    {"hash": "123", "message": "bump version", "diff_summary": "bump"}
                ]
            },
        )
        res = evaluator.evaluate(h, unrelated_ev)
        assert res.relationship == EvaluationRelationship.NEUTRAL
        assert res.score_delta == 0


class TestPrimaryScenarioEvaluation:
    """Tests evaluating real scenario data from the primary checkout_502 incident."""

    def setup_method(self):
        self.scenario = create_checkout_scenario()
        self.evaluator = EvidenceEvaluator()

    def test_db_overload_contradicted_by_healthy_metrics(self):
        """H1: Database overload contradicted by DB CPU 12.3% and normal memory (-3)."""
        h_db = Hypothesis(id="custom-h-db", statement="Database overload due to heavy traffic")

        # DB metrics from the scenario: CPU 12.3%, memory 34.1%
        db_metrics = None
        for sm in self.scenario.metrics:
            if sm.service == "checkout-db":
                db_metrics = sm.metrics
                break
        assert db_metrics is not None

        ev = EvidenceItem(
            id="ev-db-metrics",
            source_tool="query_metrics",
            query={"service": "checkout-db"},
            raw_data={"metrics": db_metrics},
        )

        res = self.evaluator.evaluate(h_db, ev)
        assert res.relationship == EvaluationRelationship.CONTRADICTS
        assert res.score_delta == EvidenceWeight.CONTRADICTION
        assert res.score_delta == -3
        assert res.rule_id == "RULE_DB_METRICS_NORMAL"
        assert "12.3%" in res.reason
        assert "below overload thresholds" in res.reason

    def test_db_overload_contradicted_by_healthy_db_logs(self):
        """H1: Database overload contradicted by DB logs showing healthy status (-3)."""
        h_db = Hypothesis(statement="Database overload")
        ev = EvidenceItem(
            id="ev-db-logs",
            source_tool="grep_logs",
            query={"service": "checkout-db", "pattern": "Health check"},
            raw_data={
                "matches": [
                    {
                        "service": "checkout-db",
                        "message": "Health check passed: database accepting connections normally",
                    }
                ]
            },
        )
        res = self.evaluator.evaluate(h_db, ev)
        assert res.relationship == EvaluationRelationship.CONTRADICTS
        assert res.score_delta == -3
        assert res.rule_id == "RULE_LOGS_DB_HEALTHY"

    def test_connection_leak_supported_by_timeout_logs(self):
        """H2: Connection leak supported by pool exhaustion and timeout logs (+2)."""
        h_leak = Hypothesis(statement="Checkout connection leak in connection pool")
        ev = EvidenceItem(
            id="ev-timeout-logs",
            source_tool="grep_logs",
            query={"service": "checkout", "pattern": "Connection timeout"},
            raw_data={
                "matches": [
                    {
                        "service": "checkout",
                        "message": (
                            "Connection timeout: failed to acquire database connection "
                            "within 5000ms (pool: 20/20 in use)"
                        ),
                    }
                ]
            },
        )
        res = self.evaluator.evaluate(h_leak, ev)
        assert res.relationship == EvaluationRelationship.SUPPORTS
        assert res.score_delta == EvidenceWeight.INDEPENDENT_SUPPORT
        assert res.score_delta == 2
        assert res.rule_id == "RULE_LOGS_POOL_TIMEOUT"

    def test_connection_leak_supported_by_deployment_logs(self):
        """H2: Connection leak correlated with deployment rollout in logs (+1)."""
        h_leak = Hypothesis(statement="Connection pool leak")
        ev = EvidenceItem(
            id="ev-deploy-log",
            source_tool="grep_logs",
            query={"service": "checkout", "pattern": "deployment"},
            raw_data={
                "matches": [
                    {
                        "service": "checkout",
                        "message": "Deployment v2.1.0 rolled out successfully",
                    }
                ]
            },
        )
        res = self.evaluator.evaluate(h_leak, ev)
        assert res.relationship == EvaluationRelationship.SUPPORTS
        assert res.score_delta == EvidenceWeight.TEMPORAL_CORRELATION
        assert res.score_delta == 1
        assert res.rule_id == "RULE_LOGS_DEPLOY_CORRELATION"

    def test_connection_leak_direct_confirmation_by_commit_diff(self):
        """H2: Direct confirmation from commit a1b2c3d4 removing connection close (+3)."""
        h_leak = Hypothesis(statement="Checkout connection leak")

        commit_leak = None
        for c in self.scenario.commits:
            if c.hash == "a1b2c3d4":
                commit_leak = c.model_dump()
                break
        assert commit_leak is not None

        ev = EvidenceItem(
            id="ev-commit-leak",
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={"commits": [commit_leak]},
        )
        res = self.evaluator.evaluate(h_leak, ev)
        assert res.relationship == EvaluationRelationship.SUPPORTS
        assert res.score_delta == EvidenceWeight.DIRECT_CONFIRMATION
        assert res.score_delta == 3
        assert res.rule_id == "RULE_COMMIT_LEAK_CONFIRMATION"
        assert "a1b2c3d4" in res.reason


class TestMissingEvidence:
    def test_missing_evidence_penalty(self):
        evaluator = EvidenceEvaluator()
        h = Hypothesis(statement="Database overload")
        res = evaluator.evaluate_missing_evidence(h, "checkout-db cpu metrics")
        assert res.relationship == EvaluationRelationship.NEUTRAL
        assert res.score_delta == EvidenceWeight.CRITICAL_MISSING
        assert res.score_delta == -1
        assert res.rule_id == "RULE_CRITICAL_EVIDENCE_MISSING"
        assert "checkout-db cpu metrics" in res.reason


class TestScoreAggregation:
    def test_aggregation_disproven_hypothesis(self):
        """H1 with +1 temporal and -3 contradiction aggregates to -2 and recommends DISPROVEN."""
        evaluator = EvidenceEvaluator()
        evals = [
            evaluator.evaluate(
                Hypothesis(id="h1", statement="Database overload"),
                EvidenceItem(
                    id="e1",
                    source_tool="query_metrics",
                    raw_data={"metrics": {"cpu_percent": 12.3, "memory_percent": 34.1}},
                ),
            )
        ]
        summary = evaluator.aggregate("h1", evals)
        assert summary.total_score == -3
        assert summary.has_contradiction is True
        assert summary.recommended_status == HypothesisStatus.DISPROVEN

    def test_aggregation_supported_hypothesis(self):
        """H2 with +2 timeout and +3 commit aggregates to +5 and recommends SUPPORTED."""
        evaluator = EvidenceEvaluator()
        h = Hypothesis(id="h2", statement="Checkout connection leak")
        ev_logs = EvidenceItem(
            id="e-log",
            source_tool="grep_logs",
            raw_data={"matches": [{"message": "connection pool exhausted"}]},
        )
        ev_commit = EvidenceItem(
            id="e-commit",
            source_tool="fetch_recent_commits",
            raw_data={
                "commits": [
                    {
                        "hash": "a1b2c3d4",
                        "diff_summary": "connections are no longer explicitly closed",
                    }
                ]
            },
        )
        evals = [evaluator.evaluate(h, ev_logs), evaluator.evaluate(h, ev_commit)]
        summary = evaluator.aggregate("h2", evals)
        assert summary.total_score == 5
        assert summary.has_direct_confirmation is True
        assert summary.has_contradiction is False
        assert summary.recommended_status == HypothesisStatus.SUPPORTED

    def test_aggregation_insufficient_hypothesis(self):
        """H with only weak temporal evidence (+1) or missing evidence (-1) remains INSUFFICIENT."""
        evaluator = EvidenceEvaluator()
        h = Hypothesis(id="h3", statement="Checkout connection leak")
        ev_deploy = EvidenceItem(
            id="e-deploy",
            source_tool="grep_logs",
            raw_data={"matches": [{"message": "deployment rolled out"}]},
        )
        eval_missing = evaluator.evaluate_missing_evidence(h, "git commit diff")
        evals = [evaluator.evaluate(h, ev_deploy), eval_missing]
        summary = evaluator.aggregate("h3", evals)
        assert summary.total_score == 0  # +1 -1
        assert summary.recommended_status == HypothesisStatus.INSUFFICIENT


class TestDeterminismAndIndependence:
    def test_repeated_evaluations_are_identical(self):
        evaluator = EvidenceEvaluator()
        h = Hypothesis(id="h-test", statement="Database overload")
        ev = EvidenceItem(
            id="ev-test",
            source_tool="query_metrics",
            raw_data={"metrics": {"cpu_percent": 12.3, "memory_percent": 34.1}},
        )

        res1 = evaluator.evaluate(h, ev)
        res2 = evaluator.evaluate(h, ev)
        assert res1.model_dump() == res2.model_dump()

    def test_independent_of_ground_truth(self):
        """Evaluation produces the same result regardless of incident ground_truth_root_cause."""
        evaluator = EvidenceEvaluator()
        h = Hypothesis(id="h-test", statement="Database overload")
        ev = EvidenceItem(
            id="ev-test",
            source_tool="query_metrics",
            raw_data={"metrics": {"cpu_percent": 12.3, "memory_percent": 34.1}},
        )

        # Evaluate directly
        res = evaluator.evaluate(h, ev)
        assert res.relationship == EvaluationRelationship.CONTRADICTS
        assert res.score_delta == -3

    def test_content_based_not_id_based(self):
        """Arbitrary hypothesis IDs with same statement produce identical evaluation."""
        evaluator = EvidenceEvaluator()
        ev = EvidenceItem(
            id="ev-test",
            source_tool="query_metrics",
            raw_data={"metrics": {"cpu_percent": 12.3, "memory_percent": 34.1}},
        )
        res_a = evaluator.evaluate(
            Hypothesis(id="arbitrary-id-1", statement="Database overload"), ev
        )
        res_b = evaluator.evaluate(
            Hypothesis(id="different-id-999", statement="Database overload"), ev
        )
        assert res_a.relationship == res_b.relationship
        assert res_a.score_delta == res_b.score_delta
        assert res_a.rule_id == res_b.rule_id
