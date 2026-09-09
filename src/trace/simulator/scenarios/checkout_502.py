"""checkout_502 — Primary demo scenario: Checkout service 502 errors.

Timeline:
  2024-01-14 10:00  payment-sdk bump (unrelated)
  2024-01-14 16:30  checkout error-format fix (unrelated)
  2024-01-15 12:00  checkout query-batching commit (THE BUG — removes connection close)
  2024-01-15 14:28  checkout v2.1.0 deployment completes
  2024-01-15 14:30+ connection pool exhaustion → 502 errors begin

Ground truth (test/validation only):
  Root cause = connection pool leak introduced by commit a1b2c3d4
  Database overload is a plausible but incorrect hypothesis
"""

from __future__ import annotations

from trace.models.incident import Incident, Severity
from trace.simulator.models import (
    CommitRecord,
    GroundTruth,
    LogRecord,
    ScenarioData,
    ServiceMetrics,
    ToolFailureConfig,
)

SCENARIO_ID = "checkout_502"


def create(
    tool_failures: dict[str, ToolFailureConfig] | None = None,
) -> ScenarioData:
    """Create the checkout_502 scenario.

    Args:
        tool_failures: Optional per-tool failure configuration.
                       Pass ``{"fetch_recent_commits": ToolFailureConfig(enabled=True)}``
                       to simulate a 503 on the commits tool.
    """
    return ScenarioData(
        scenario_id=SCENARIO_ID,
        incident=_build_incident(),
        logs=_build_logs(),
        metrics=_build_metrics(),
        commits=_build_commits(),
        ground_truth=_build_ground_truth(),
        tool_failures=tool_failures or {},
    )


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

def _build_incident() -> Incident:
    return Incident(
        id=SCENARIO_ID,
        title="Checkout 502 Errors",
        service="checkout",
        severity=Severity.HIGH,
        initial_observation=(
            "Checkout service is returning HTTP 502 errors. "
            "Database connection timeout errors are increasing."
        ),
        ground_truth_root_cause=(
            "Connection pool leak introduced by commit a1b2c3d4 which removed "
            "explicit connection close in the request cleanup path."
        ),
    )


# ---------------------------------------------------------------------------
# Log data
# ---------------------------------------------------------------------------

def _build_logs() -> list[LogRecord]:
    """Seeded logs showing 502s + connection timeouts + healthy DB.

    The logs make database overload a plausible initial hypothesis:
    - connection timeouts reference the database
    - pool exhaustion messages appear

    But DB-side logs show the database is healthy, and pool exhaustion
    messages hint at a client-side leak rather than server overload.
    """
    return [
        # --- Pre-incident: normal operations ---
        LogRecord(
            timestamp="2024-01-15T14:25:00Z",
            level="INFO",
            service="checkout",
            message="Request processing nominal, avg response time 45ms",
        ),
        LogRecord(
            timestamp="2024-01-15T14:27:00Z",
            level="INFO",
            service="checkout",
            message="Database connection pool healthy: 5/20 connections in use",
        ),
        # --- Deployment ---
        LogRecord(
            timestamp="2024-01-15T14:28:00Z",
            level="INFO",
            service="checkout",
            message="Deployment v2.1.0 rolled out successfully",
        ),
        # --- Errors begin (within 2 minutes of deployment) ---
        LogRecord(
            timestamp="2024-01-15T14:30:01Z",
            level="ERROR",
            service="checkout",
            message=(
                "Connection timeout: failed to acquire database connection "
                "within 5000ms (pool: 19/20 in use)"
            ),
        ),
        LogRecord(
            timestamp="2024-01-15T14:30:03Z",
            level="ERROR",
            service="checkout",
            message="HTTP 502 returned to client request_id=req-a1b2c3",
        ),
        LogRecord(
            timestamp="2024-01-15T14:30:05Z",
            level="WARN",
            service="checkout",
            message="Connection pool nearing capacity: 19/20 connections in use",
        ),
        LogRecord(
            timestamp="2024-01-15T14:30:07Z",
            level="ERROR",
            service="checkout",
            message=(
                "Connection timeout: failed to acquire database connection "
                "within 5000ms (pool: 20/20 in use)"
            ),
        ),
        LogRecord(
            timestamp="2024-01-15T14:30:10Z",
            level="ERROR",
            service="checkout",
            message="HTTP 502 returned to client request_id=req-d4e5f6",
        ),
        # --- Pool fully exhausted ---
        LogRecord(
            timestamp="2024-01-15T14:31:00Z",
            level="WARN",
            service="checkout",
            message=(
                "Connection pool exhausted: 20/20 connections in use, "
                "all acquired and none returned"
            ),
        ),
        LogRecord(
            timestamp="2024-01-15T14:31:01Z",
            level="ERROR",
            service="checkout",
            message=(
                "Connection timeout: failed to acquire database connection "
                "within 5000ms (pool: 20/20 in use)"
            ),
        ),
        LogRecord(
            timestamp="2024-01-15T14:31:05Z",
            level="ERROR",
            service="checkout",
            message="HTTP 502 returned to client request_id=req-g7h8i9",
        ),
        # --- Database is healthy (contradicts DB-overload hypothesis) ---
        LogRecord(
            timestamp="2024-01-15T14:31:30Z",
            level="INFO",
            service="checkout-db",
            message="Health check passed: database accepting connections normally",
        ),
        LogRecord(
            timestamp="2024-01-15T14:31:31Z",
            level="INFO",
            service="checkout-db",
            message=(
                "Database stats: active_connections=45/200, cpu=12.3%, "
                "query_latency_avg=2.4ms"
            ),
        ),
        # --- Continued errors ---
        LogRecord(
            timestamp="2024-01-15T14:32:00Z",
            level="ERROR",
            service="checkout",
            message=(
                "Connection timeout: failed to acquire database connection "
                "within 5000ms (pool: 20/20 in use)"
            ),
        ),
        LogRecord(
            timestamp="2024-01-15T14:32:05Z",
            level="ERROR",
            service="checkout",
            message="HTTP 502 returned to client request_id=req-j1k2l3",
        ),
        # --- Other services (unrelated, normal) ---
        LogRecord(
            timestamp="2024-01-15T14:29:00Z",
            level="INFO",
            service="payment",
            message="Payment processing nominal, avg latency 23ms",
        ),
        LogRecord(
            timestamp="2024-01-15T14:30:00Z",
            level="INFO",
            service="inventory",
            message="Stock sync completed for 1,247 items",
        ),
    ]


# ---------------------------------------------------------------------------
# Metric data
# ---------------------------------------------------------------------------

def _build_metrics() -> list[ServiceMetrics]:
    """Seeded metrics — DB is NOT overloaded; checkout pool IS exhausted.

    checkout-db: low CPU, normal memory, plenty of headroom
      → contradicts H1 (database overload)

    checkout: pool 20/20 = fully exhausted on the client side
      → supports H2 (connection leak)
    """
    return [
        ServiceMetrics(
            service="checkout-db",
            metrics={
                "cpu_percent": 12.3,
                "memory_percent": 34.1,
                "active_connections": 45,
                "max_connections": 200,
                "disk_io_percent": 8.7,
                "query_latency_avg_ms": 2.4,
                "replication_lag_ms": 0.5,
            },
        ),
        ServiceMetrics(
            service="checkout",
            metrics={
                "request_rate_per_min": 1250,
                "error_rate_5xx_per_min": 89.3,
                "p99_latency_ms": 12500,
                "active_db_connections": 20,
                "db_connection_pool_size": 20,
                "cpu_percent": 67.2,
                "memory_percent": 54.8,
            },
        ),
        ServiceMetrics(
            service="payment",
            metrics={
                "cpu_percent": 23.1,
                "memory_percent": 41.0,
                "request_rate_per_min": 800,
                "error_rate_5xx_per_min": 0.0,
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Commit / deployment data
# ---------------------------------------------------------------------------

def _build_commits() -> list[CommitRecord]:
    """Seeded commits — includes the connection-leak commit.

    Commit a1b2c3d4 is the root cause. Its diff_summary mentions that
    connections are no longer explicitly closed, but frames it as an
    intentional optimisation. The agent must *infer* the leak from this
    evidence combined with the pool-exhaustion logs and metrics.

    The diff does NOT say "this is the root cause".
    """
    return [
        CommitRecord(
            hash="a1b2c3d4",
            author="jsmith",
            message="feat(checkout): optimize database query batching",
            timestamp="2024-01-15T12:00:00Z",
            service="checkout",
            files_changed=[
                "src/checkout/db/connection_pool.py",
                "src/checkout/db/query_batcher.py",
            ],
            diff_summary=(
                "Refactored connection handling to support query batching. "
                "Modified pool release logic: connections are no longer "
                "explicitly closed on request completion to allow reuse "
                "across batched operations within the same transaction window."
            ),
        ),
        CommitRecord(
            hash="e4f5g6h7",
            author="mjones",
            message="fix(checkout): update error response format for API v2",
            timestamp="2024-01-14T16:30:00Z",
            service="checkout",
            files_changed=["src/checkout/api/responses.py"],
            diff_summary=(
                "Updated error response body to include correlation_id "
                "and timestamp fields for API v2 compliance."
            ),
        ),
        CommitRecord(
            hash="b8c9d0e1",
            author="alee",
            message="chore(payment): bump payment-sdk to 3.4.1",
            timestamp="2024-01-14T10:00:00Z",
            service="payment",
            files_changed=["requirements.txt"],
            diff_summary=(
                "Updated payment-sdk dependency from 3.4.0 to 3.4.1 "
                "for PCI compliance patch."
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Ground truth (tests/validation ONLY — never exposed to agents or tools)
# ---------------------------------------------------------------------------

def _build_ground_truth() -> GroundTruth:
    return GroundTruth(
        root_cause=(
            "Connection pool leak in the checkout service caused by commit "
            "a1b2c3d4. The commit removed the explicit connection.close() "
            "call in the request cleanup path, preventing connections from "
            "being returned to the pool."
        ),
        root_cause_service="checkout",
        root_cause_commit_hash="a1b2c3d4",
        disproven_hypotheses=["Database overload"],
    )
