"""Tests for the TRACE 2.0 deterministic simulator.

Verifies:
  - checkout_502 scenario exists with expected data
  - Ground truth is available for tests/validation
  - Ground truth is NOT returned by investigation tools (tested in test_tools.py)
  - Tool failure configuration works
  - Repeated creation is deterministic
"""

from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import SCENARIO_ID, create

# ---------------------------------------------------------------------------
# Scenario existence and structure
# ---------------------------------------------------------------------------


class TestCheckout502Exists:
    def test_scenario_id(self):
        scenario = create()
        assert scenario.scenario_id == SCENARIO_ID
        assert scenario.scenario_id == "checkout_502"

    def test_incident_service(self):
        scenario = create()
        assert scenario.incident.service == "checkout"

    def test_incident_severity(self):
        scenario = create()
        assert scenario.incident.severity == "high"

    def test_incident_observation_mentions_502(self):
        scenario = create()
        assert "502" in scenario.incident.initial_observation

    def test_incident_observation_mentions_timeout(self):
        scenario = create()
        obs = scenario.incident.initial_observation.lower()
        assert "timeout" in obs or "connection" in obs


# ---------------------------------------------------------------------------
# Seeded log data
# ---------------------------------------------------------------------------


class TestCheckout502Logs:
    def test_logs_exist(self):
        scenario = create()
        assert len(scenario.logs) > 0

    def test_checkout_error_logs(self):
        scenario = create()
        errors = [
            log for log in scenario.logs if log.level == "ERROR" and log.service == "checkout"
        ]
        assert len(errors) >= 3, "Need enough error logs to tell a compelling story"

    def test_timeout_logs(self):
        scenario = create()
        timeouts = [log for log in scenario.logs if "timeout" in log.message.lower()]
        assert len(timeouts) >= 1

    def test_502_logs(self):
        scenario = create()
        http_502s = [log for log in scenario.logs if "502" in log.message]
        assert len(http_502s) >= 1

    def test_db_health_logs_exist(self):
        """DB-side logs should show the database is healthy."""
        scenario = create()
        db_logs = [log for log in scenario.logs if log.service == "checkout-db"]
        assert len(db_logs) >= 1

    def test_multiple_services_present(self):
        """Logs should contain entries from multiple services for realistic filtering."""
        scenario = create()
        services = {log.service for log in scenario.logs}
        assert len(services) >= 2


# ---------------------------------------------------------------------------
# Seeded metric data
# ---------------------------------------------------------------------------


class TestCheckout502Metrics:
    def test_metrics_exist(self):
        scenario = create()
        assert len(scenario.metrics) > 0

    def test_db_metrics_cpu_around_12(self):
        scenario = create()
        db = next(m for m in scenario.metrics if m.service == "checkout-db")
        assert 10 <= db.metrics["cpu_percent"] <= 15, "DB CPU should be ~12%"

    def test_db_metrics_memory_normal(self):
        scenario = create()
        db = next(m for m in scenario.metrics if m.service == "checkout-db")
        assert db.metrics["memory_percent"] < 50

    def test_db_metrics_not_overloaded(self):
        """Active connections well below max → DB is not resource-exhausted."""
        scenario = create()
        db = next(m for m in scenario.metrics if m.service == "checkout-db")
        assert db.metrics["active_connections"] < db.metrics["max_connections"]

    def test_checkout_pool_exhausted(self):
        """Checkout connection pool should be at capacity."""
        scenario = create()
        checkout = next(m for m in scenario.metrics if m.service == "checkout")
        active = checkout.metrics["active_db_connections"]
        pool_size = checkout.metrics["db_connection_pool_size"]
        assert active == pool_size


# ---------------------------------------------------------------------------
# Seeded commit data
# ---------------------------------------------------------------------------


class TestCheckout502Commits:
    def test_commits_exist(self):
        scenario = create()
        assert len(scenario.commits) > 0

    def test_checkout_commits_exist(self):
        scenario = create()
        checkout = [c for c in scenario.commits if c.service == "checkout"]
        assert len(checkout) >= 1

    def test_buggy_commit_present(self):
        scenario = create()
        hashes = {c.hash for c in scenario.commits}
        assert "a1b2c3d4" in hashes

    def test_buggy_commit_mentions_connection_handling(self):
        scenario = create()
        buggy = next(c for c in scenario.commits if c.hash == "a1b2c3d4")
        summary_lower = buggy.diff_summary.lower()
        assert "connection" in summary_lower
        assert "close" in summary_lower or "release" in summary_lower

    def test_buggy_commit_does_not_declare_root_cause(self):
        """The commit diff must NOT directly say 'this is the root cause'."""
        scenario = create()
        buggy = next(c for c in scenario.commits if c.hash == "a1b2c3d4")
        summary_lower = buggy.diff_summary.lower()
        assert "root cause" not in summary_lower
        assert "bug" not in summary_lower
        assert "leak" not in summary_lower


# ---------------------------------------------------------------------------
# Ground truth (validation only)
# ---------------------------------------------------------------------------


class TestCheckout502GroundTruth:
    def test_ground_truth_available(self):
        scenario = create()
        gt = scenario.ground_truth
        assert gt.root_cause
        assert len(gt.root_cause) > 0

    def test_ground_truth_service(self):
        scenario = create()
        assert scenario.ground_truth.root_cause_service == "checkout"

    def test_ground_truth_commit_exists_in_data(self):
        scenario = create()
        gt = scenario.ground_truth
        commit_hashes = {c.hash for c in scenario.commits}
        assert gt.root_cause_commit_hash in commit_hashes

    def test_ground_truth_disproves_db_overload(self):
        scenario = create()
        assert "Database overload" in scenario.ground_truth.disproven_hypotheses

    def test_incident_ground_truth_field_set(self):
        scenario = create()
        assert scenario.incident.ground_truth_root_cause is not None


# ---------------------------------------------------------------------------
# Tool failure configuration
# ---------------------------------------------------------------------------


class TestToolFailureConfig:
    def test_no_failures_by_default(self):
        scenario = create()
        assert scenario.tool_failures == {}

    def test_fetch_commits_failure(self):
        scenario = create(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable — Git service is temporarily down",
                ),
            }
        )
        cfg = scenario.tool_failures["fetch_recent_commits"]
        assert cfg.enabled is True
        assert "503" in cfg.error_message

    def test_multiple_tool_failures(self):
        scenario = create(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(enabled=True),
                "grep_logs": ToolFailureConfig(enabled=True, error_message="Log service down"),
            }
        )
        assert len(scenario.tool_failures) == 2

    def test_failure_config_does_not_affect_data(self):
        """Configuring a failure should not remove the underlying data."""
        normal = create()
        failed = create(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(enabled=True),
            }
        )
        assert len(failed.commits) == len(normal.commits)
        assert len(failed.logs) == len(normal.logs)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_creation_equivalent(self):
        s1 = create()
        s2 = create()
        assert s1.scenario_id == s2.scenario_id
        assert len(s1.logs) == len(s2.logs)
        assert len(s1.metrics) == len(s2.metrics)
        assert len(s1.commits) == len(s2.commits)

    def test_log_content_stable(self):
        s1 = create()
        s2 = create()
        for l1, l2 in zip(s1.logs, s2.logs):
            assert l1.timestamp == l2.timestamp
            assert l1.level == l2.level
            assert l1.service == l2.service
            assert l1.message == l2.message

    def test_metric_values_stable(self):
        s1 = create()
        s2 = create()
        for m1, m2 in zip(s1.metrics, s2.metrics):
            assert m1.service == m2.service
            assert m1.metrics == m2.metrics

    def test_commit_data_stable(self):
        s1 = create()
        s2 = create()
        for c1, c2 in zip(s1.commits, s2.commits):
            assert c1.hash == c2.hash
            assert c1.diff_summary == c2.diff_summary
