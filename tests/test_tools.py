"""Tests for TRACE 2.0 investigation tools.

Verifies:
  - BaseTool metadata (name, description, schema)
  - grep_logs: positive match, no match, level filter, determinism
  - query_metrics: valid query, unknown resource, determinism
  - fetch_recent_commits: checkout commits, unknown service, limit, determinism
  - Tool failure simulation: 503, no fabricated data
  - Input validation: missing/invalid params
  - Data boundary: ground truth never in tool results
"""

from trace.models.action import ActionResult
from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import create
from trace.tools.base import BaseTool
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario(**kwargs):
    return create(**kwargs)


# ---------------------------------------------------------------------------
# BaseTool contract
# ---------------------------------------------------------------------------


class TestBaseToolContract:
    """Every tool must satisfy the BaseTool interface."""

    TOOL_CLASSES = [GrepLogsTool, QueryMetricsTool, FetchRecentCommitsTool]

    def test_all_are_base_tool_instances(self):
        scenario = _scenario()
        for cls in self.TOOL_CLASSES:
            tool = cls(scenario)
            assert isinstance(tool, BaseTool)

    def test_all_have_nonempty_name(self):
        scenario = _scenario()
        for cls in self.TOOL_CLASSES:
            tool = cls(scenario)
            assert isinstance(tool.name, str)
            assert len(tool.name) > 0

    def test_all_have_nonempty_description(self):
        scenario = _scenario()
        for cls in self.TOOL_CLASSES:
            tool = cls(scenario)
            assert isinstance(tool.description, str)
            assert len(tool.description) > 0

    def test_all_have_parameters_schema_with_properties(self):
        scenario = _scenario()
        for cls in self.TOOL_CLASSES:
            tool = cls(scenario)
            schema = tool.get_parameters_schema()
            assert isinstance(schema, dict)
            assert "properties" in schema

    def test_tool_names_are_unique(self):
        scenario = _scenario()
        names = [cls(scenario).name for cls in self.TOOL_CLASSES]
        assert len(names) == len(set(names))

    def test_expected_tool_names(self):
        scenario = _scenario()
        names = {cls(scenario).name for cls in self.TOOL_CLASSES}
        assert names == {"grep_logs", "query_metrics", "fetch_recent_commits"}


# ---------------------------------------------------------------------------
# grep_logs
# ---------------------------------------------------------------------------


class TestGrepLogs:
    def _tool(self, **kwargs):
        return GrepLogsTool(_scenario(**kwargs))

    def test_positive_match_502(self):
        result = self._tool().execute(service="checkout", pattern="502")
        assert result.success is True
        assert result.data["count"] > 0
        for match in result.data["matches"]:
            assert "502" in match["message"]

    def test_positive_match_timeout(self):
        result = self._tool().execute(service="checkout", pattern="timeout")
        assert result.success is True
        assert result.data["count"] > 0

    def test_positive_match_connection_pool(self):
        result = self._tool().execute(service="checkout", pattern="connection pool")
        assert result.success is True
        assert result.data["count"] > 0

    def test_no_match_unlikely_pattern(self):
        result = self._tool().execute(service="checkout", pattern="segfault_xyz_unlikely")
        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["matches"] == []

    def test_no_match_wrong_service(self):
        result = self._tool().execute(service="nonexistent-service", pattern="error")
        assert result.success is True
        assert result.data["count"] == 0

    def test_filter_by_error_level(self):
        result = self._tool().execute(service="checkout", pattern="connection", level="ERROR")
        assert result.success is True
        assert result.data["count"] > 0
        for match in result.data["matches"]:
            assert match["level"] == "ERROR"

    def test_filter_by_warn_level(self):
        result = self._tool().execute(service="checkout", pattern="pool", level="WARN")
        assert result.success is True
        assert result.data["count"] > 0
        for match in result.data["matches"]:
            assert match["level"] == "WARN"

    def test_filter_by_info_level(self):
        result = self._tool().execute(service="checkout", pattern="deployment", level="INFO")
        assert result.success is True
        assert result.data["count"] >= 1
        for match in result.data["matches"]:
            assert match["level"] == "INFO"

    def test_level_filter_case_insensitive_input(self):
        r_upper = self._tool().execute(service="checkout", pattern="502", level="ERROR")
        r_lower = self._tool().execute(service="checkout", pattern="502", level="error")
        assert r_upper.data == r_lower.data

    def test_pattern_case_insensitive(self):
        r_lower = self._tool().execute(service="checkout", pattern="http 502")
        r_upper = self._tool().execute(service="checkout", pattern="HTTP 502")
        assert r_lower.data == r_upper.data

    def test_returns_action_result(self):
        result = self._tool().execute(service="checkout", pattern="502")
        assert isinstance(result, ActionResult)

    def test_deterministic_repeated_calls(self):
        tool = self._tool()
        r1 = tool.execute(service="checkout", pattern="502")
        r2 = tool.execute(service="checkout", pattern="502")
        assert r1.data == r2.data

    def test_db_service_logs(self):
        result = self._tool().execute(service="checkout-db", pattern="health")
        assert result.success is True
        assert result.data["count"] >= 1


# ---------------------------------------------------------------------------
# query_metrics
# ---------------------------------------------------------------------------


class TestQueryMetrics:
    def _tool(self, **kwargs):
        return QueryMetricsTool(_scenario(**kwargs))

    def test_checkout_db_all_metrics(self):
        result = self._tool().execute(service="checkout-db")
        assert result.success is True
        assert result.data["found"] is True
        metrics = result.data["metrics"]
        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics

    def test_checkout_db_cpu_around_12(self):
        result = self._tool().execute(service="checkout-db")
        cpu = result.data["metrics"]["cpu_percent"]
        assert 10 <= cpu <= 15, f"DB CPU should be ~12%, got {cpu}"

    def test_checkout_db_memory_normal(self):
        result = self._tool().execute(service="checkout-db")
        mem = result.data["metrics"]["memory_percent"]
        assert mem < 50, f"DB memory should be normal, got {mem}%"

    def test_specific_metric_found(self):
        result = self._tool().execute(service="checkout-db", metric_name="cpu_percent")
        assert result.success is True
        assert result.data["metric_found"] is True
        assert "cpu_percent" in result.data["metrics"]

    def test_specific_metric_not_found(self):
        result = self._tool().execute(service="checkout-db", metric_name="nonexistent_metric")
        assert result.success is True
        assert result.data["metric_found"] is False
        assert result.data["metrics"] == {}

    def test_unknown_service(self):
        result = self._tool().execute(service="nonexistent-service")
        assert result.success is True
        assert result.data["found"] is False
        assert result.data["metrics"] == {}

    def test_checkout_service_metrics(self):
        result = self._tool().execute(service="checkout")
        assert result.success is True
        assert result.data["found"] is True
        metrics = result.data["metrics"]
        assert metrics["active_db_connections"] == metrics["db_connection_pool_size"]

    def test_returns_action_result(self):
        result = self._tool().execute(service="checkout-db")
        assert isinstance(result, ActionResult)

    def test_deterministic_repeated_calls(self):
        tool = self._tool()
        r1 = tool.execute(service="checkout-db")
        r2 = tool.execute(service="checkout-db")
        assert r1.data == r2.data


# ---------------------------------------------------------------------------
# fetch_recent_commits
# ---------------------------------------------------------------------------


class TestFetchRecentCommits:
    def _tool(self, **kwargs):
        return FetchRecentCommitsTool(_scenario(**kwargs))

    def test_checkout_has_commits(self):
        result = self._tool().execute(service="checkout")
        assert result.success is True
        assert result.data["count"] > 0

    def test_checkout_contains_buggy_commit(self):
        result = self._tool().execute(service="checkout")
        hashes = [c["hash"] for c in result.data["commits"]]
        assert "a1b2c3d4" in hashes

    def test_commit_has_required_fields(self):
        result = self._tool().execute(service="checkout")
        for commit in result.data["commits"]:
            assert "hash" in commit
            assert "author" in commit
            assert "message" in commit
            assert "timestamp" in commit
            assert "files_changed" in commit
            assert "diff_summary" in commit
            assert len(commit["diff_summary"]) > 0

    def test_buggy_commit_hints_at_connection_issue(self):
        result = self._tool().execute(service="checkout")
        buggy = next(c for c in result.data["commits"] if c["hash"] == "a1b2c3d4")
        summary_lower = buggy["diff_summary"].lower()
        assert "connection" in summary_lower
        assert "close" in summary_lower or "release" in summary_lower

    def test_buggy_commit_does_not_declare_root_cause(self):
        result = self._tool().execute(service="checkout")
        buggy = next(c for c in result.data["commits"] if c["hash"] == "a1b2c3d4")
        summary_lower = buggy["diff_summary"].lower()
        assert "root cause" not in summary_lower
        assert "bug" not in summary_lower
        assert "leak" not in summary_lower

    def test_unknown_service(self):
        result = self._tool().execute(service="nonexistent-service")
        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["commits"] == []

    def test_limit_restricts_results(self):
        result = self._tool().execute(service="checkout", limit=1)
        assert result.success is True
        assert len(result.data["commits"]) <= 1

    def test_returns_action_result(self):
        result = self._tool().execute(service="checkout")
        assert isinstance(result, ActionResult)

    def test_deterministic_repeated_calls(self):
        tool = self._tool()
        r1 = tool.execute(service="checkout")
        r2 = tool.execute(service="checkout")
        assert r1.data == r2.data


# ---------------------------------------------------------------------------
# Tool failure simulation
# ---------------------------------------------------------------------------


class TestToolFailureSimulation:
    def test_fetch_commits_503_fails(self):
        scenario = _scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable — Git service is temporarily down",
                ),
            }
        )
        result = FetchRecentCommitsTool(scenario).execute(service="checkout")
        assert result.success is False
        assert "503" in result.error

    def test_failed_result_has_no_data(self):
        scenario = _scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable",
                ),
            }
        )
        result = FetchRecentCommitsTool(scenario).execute(service="checkout")
        assert result.data is None, "Failed tool must NOT contain fabricated data"

    def test_grep_logs_failure(self):
        scenario = _scenario(
            tool_failures={
                "grep_logs": ToolFailureConfig(
                    enabled=True,
                    error_message="Log aggregation service unavailable",
                ),
            }
        )
        result = GrepLogsTool(scenario).execute(service="checkout", pattern="error")
        assert result.success is False
        assert result.data is None

    def test_query_metrics_failure(self):
        scenario = _scenario(
            tool_failures={
                "query_metrics": ToolFailureConfig(
                    enabled=True,
                    error_message="Metrics backend timeout",
                ),
            }
        )
        result = QueryMetricsTool(scenario).execute(service="checkout-db")
        assert result.success is False
        assert result.data is None

    def test_failure_is_deterministic(self):
        scenario = _scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable",
                ),
            }
        )
        tool = FetchRecentCommitsTool(scenario)
        r1 = tool.execute(service="checkout")
        r2 = tool.execute(service="checkout")
        assert r1.success is False
        assert r2.success is False
        assert r1.error == r2.error

    def test_other_tools_work_when_one_fails(self):
        """Only the configured tool should fail; others remain operational."""
        scenario = _scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503",
                ),
            }
        )
        assert GrepLogsTool(scenario).execute(
            service="checkout", pattern="502"
        ).success is True
        assert QueryMetricsTool(scenario).execute(
            service="checkout-db"
        ).success is True

    def test_disabled_failure_does_not_affect_tool(self):
        """ToolFailureConfig with enabled=False should NOT cause a failure."""
        scenario = _scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(enabled=False),
            }
        )
        result = FetchRecentCommitsTool(scenario).execute(service="checkout")
        assert result.success is True


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_grep_logs_missing_required_params(self):
        tool = GrepLogsTool(_scenario())
        result = tool.execute()
        assert result.success is False
        assert result.error is not None

    def test_grep_logs_missing_pattern(self):
        tool = GrepLogsTool(_scenario())
        result = tool.execute(service="checkout")
        assert result.success is False

    def test_query_metrics_missing_service(self):
        tool = QueryMetricsTool(_scenario())
        result = tool.execute()
        assert result.success is False

    def test_fetch_commits_missing_service(self):
        tool = FetchRecentCommitsTool(_scenario())
        result = tool.execute()
        assert result.success is False

    def test_fetch_commits_limit_zero(self):
        tool = FetchRecentCommitsTool(_scenario())
        result = tool.execute(service="checkout", limit=0)
        assert result.success is False

    def test_fetch_commits_limit_negative(self):
        tool = FetchRecentCommitsTool(_scenario())
        result = tool.execute(service="checkout", limit=-1)
        assert result.success is False

    def test_fetch_commits_limit_too_large(self):
        tool = FetchRecentCommitsTool(_scenario())
        result = tool.execute(service="checkout", limit=100)
        assert result.success is False


# ---------------------------------------------------------------------------
# Data boundary: ground truth never in tool results
# ---------------------------------------------------------------------------


class TestDataBoundary:
    """Tools must NEVER expose ground truth, root cause labels, or simulator metadata."""

    FORBIDDEN_KEYS = ["ground_truth", "root_cause", "disproven_hypotheses"]

    def _assert_no_ground_truth(self, result: ActionResult):
        if result.data is None:
            return  # Failed tool — no data to check
        data_str = str(result.data).lower()
        for key in self.FORBIDDEN_KEYS:
            assert key not in data_str, f"Tool result contains forbidden key: {key}"

    def test_grep_logs_no_ground_truth(self):
        tool = GrepLogsTool(_scenario())
        self._assert_no_ground_truth(tool.execute(service="checkout", pattern="502"))
        self._assert_no_ground_truth(tool.execute(service="checkout", pattern="connection"))
        self._assert_no_ground_truth(tool.execute(service="checkout-db", pattern="health"))

    def test_query_metrics_no_ground_truth(self):
        tool = QueryMetricsTool(_scenario())
        self._assert_no_ground_truth(tool.execute(service="checkout-db"))
        self._assert_no_ground_truth(tool.execute(service="checkout"))

    def test_fetch_commits_no_ground_truth(self):
        tool = FetchRecentCommitsTool(_scenario())
        self._assert_no_ground_truth(tool.execute(service="checkout"))
        self._assert_no_ground_truth(tool.execute(service="payment"))
