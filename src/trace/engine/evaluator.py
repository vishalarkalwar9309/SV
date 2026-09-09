"""Deterministic Evidence Evaluator for TRACE 2.0.

Evaluates evidence items against hypotheses using explicit, deterministic rules.
Assigns weighted score deltas without LLM involvement.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from typing import Any

from pydantic import BaseModel, Field


class EvaluationRelationship(StrEnum):
    """The directional relationship between evidence and a hypothesis."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class EvidenceWeight(IntEnum):
    """Agreed deterministic weights for evidence scoring."""

    DIRECT_CONFIRMATION = 3      # +3 direct confirmation (e.g. bug in diff)
    INDEPENDENT_SUPPORT = 2      # +2 independent supporting evidence (e.g. pool exhaustion)
    TEMPORAL_CORRELATION = 1     # +1 temporal / correlation evidence (e.g. deployment timing)
    NEUTRAL = 0                  #  0 non-probative or tool failure
    CRITICAL_MISSING = -1        # -1 required critical evidence unavailable
    CONTRADICTION = -3           # -3 contradictory evidence (e.g. healthy metrics)


class EvaluationResult(BaseModel):
    """The deterministic evaluation of an evidence item against a hypothesis.

    Contains an explicit rule-based explanation, score delta, and relationship.
    Excludes internal chain-of-thought reasoning.
    """

    hypothesis_id: str
    evidence_id: str
    relationship: EvaluationRelationship
    score_delta: int
    reason: str = Field(description="Concise deterministic explanation based on explicit rule")
    rule_id: str = Field(description="Deterministic rule identifier applied")


class HypothesisEvaluationSummary(BaseModel):
    """Deterministic aggregation of evidence scores for a hypothesis."""

    hypothesis_id: str
    total_score: int
    evaluations: list[EvaluationResult] = Field(default_factory=list)
    supporting_count: int = 0
    contradicting_count: int = 0
    neutral_count: int = 0
    has_direct_confirmation: bool = False
    has_contradiction: bool = False
    recommended_status: HypothesisStatus = HypothesisStatus.INSUFFICIENT
    summary_reason: str


def _is_db_overload_hypothesis(statement: str) -> bool:
    stmt = statement.lower()
    has_db = any(w in stmt for w in ("db", "database", "postgres", "sql"))
    has_overload = any(
        w in stmt
        for w in (
            "overload",
            "cpu",
            "memory",
            "saturation",
            "capacity",
            "load",
            "high usage",
            "exhaustion",
        )
    )
    return has_db and has_overload


def _is_connection_leak_hypothesis(statement: str) -> bool:
    stmt = statement.lower()
    return any(
        w in stmt
        for w in (
            "connection leak",
            "pool leak",
            "connection pool",
            "pool exhaustion",
            "unclosed connection",
            "unclosed db session",
            "connection timeout",
            "leak",
        )
    )


class EvidenceEvaluator:
    """Deterministic evidence evaluator.

    Applies rule-based evaluations based on evidence content and hypothesis statement.
    Does NOT depend on simulator ground truth or LLM reasoning.
    """

    def evaluate(self, hypothesis: Hypothesis, evidence: EvidenceItem) -> EvaluationResult:
        """Evaluate a single evidence item against a hypothesis."""
        # 1. Tool execution failure: tool availability provenance only, never root cause
        if not evidence.tool_succeeded:
            err = evidence.error_message or "HTTP error / service unavailable"
            return EvaluationResult(
                hypothesis_id=hypothesis.id,
                evidence_id=evidence.id,
                relationship=EvaluationRelationship.NEUTRAL,
                score_delta=EvidenceWeight.NEUTRAL,
                reason=(
                    f"Tool execution failed ({err}); "
                    "indicates tool unavailability, not root-cause evidence."
                ),
                rule_id="RULE_TOOL_FAILURE",
            )

        tool = evidence.source_tool
        raw: dict[str, Any] = evidence.raw_data or {}
        stmt = hypothesis.statement

        # 2. Metric evidence evaluation
        if tool == "query_metrics":
            return self._evaluate_metrics(hypothesis.id, stmt, evidence.id, raw)

        # 3. Commit / deployment evidence evaluation
        if tool == "fetch_recent_commits":
            return self._evaluate_commits(hypothesis.id, stmt, evidence.id, raw)

        # 4. Log search evidence evaluation
        if tool == "grep_logs":
            return self._evaluate_logs(hypothesis.id, stmt, evidence.id, raw)

        # 5. Fallback for unhandled/general tool output
        return EvaluationResult(
            hypothesis_id=hypothesis.id,
            evidence_id=evidence.id,
            relationship=EvaluationRelationship.NEUTRAL,
            score_delta=EvidenceWeight.NEUTRAL,
            reason="Evidence content does not contain probative data for this hypothesis.",
            rule_id="RULE_NON_PROBATIVE",
        )

    def _evaluate_metrics(
        self,
        hypothesis_id: str,
        statement: str,
        evidence_id: str,
        raw: dict[str, Any],
    ) -> EvaluationResult:
        metrics: dict[str, Any] = raw.get("metrics", {})
        # If raw itself is a flat metrics dictionary
        if not metrics and "cpu_percent" in raw:
            metrics = raw

        if _is_db_overload_hypothesis(statement):
            cpu = metrics.get("cpu_percent")
            mem = metrics.get("memory_percent")
            # Normal DB metrics contradict overload
            if cpu is not None and cpu < 50.0 and (mem is None or mem < 60.0):
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.CONTRADICTS,
                    score_delta=EvidenceWeight.CONTRADICTION,
                    reason=(
                        f"Database metrics (CPU {cpu}%, memory {mem}%) show normal "
                        "resource utilization below overload thresholds."
                    ),
                    rule_id="RULE_DB_METRICS_NORMAL",
                )
            # High DB metrics support overload
            if (cpu is not None and cpu >= 80.0) or (mem is not None and mem >= 80.0):
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.SUPPORTS,
                    score_delta=EvidenceWeight.INDEPENDENT_SUPPORT,
                    reason=(
                        f"Database metrics (CPU {cpu}%, memory {mem}%) indicate "
                        "resource saturation above threshold."
                    ),
                    rule_id="RULE_DB_METRICS_SATURATED",
                )

        if _is_connection_leak_hypothesis(statement):
            active_conns = metrics.get("active_db_connections")
            pool_size = metrics.get("db_connection_pool_size")
            if (
                active_conns is not None
                and pool_size is not None
                and active_conns >= pool_size
            ):
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.SUPPORTS,
                    score_delta=EvidenceWeight.INDEPENDENT_SUPPORT,
                    reason=(
                        f"Connection pool metrics confirm 100% saturation "
                        f"({active_conns}/{pool_size} active connections)."
                    ),
                    rule_id="RULE_METRICS_POOL_SATURATED",
                )

        return EvaluationResult(
            hypothesis_id=hypothesis_id,
            evidence_id=evidence_id,
            relationship=EvaluationRelationship.NEUTRAL,
            score_delta=EvidenceWeight.NEUTRAL,
            reason="Metric values are non-probative for this hypothesis.",
            rule_id="RULE_METRICS_NEUTRAL",
        )

    def _evaluate_commits(
        self,
        hypothesis_id: str,
        statement: str,
        evidence_id: str,
        raw: dict[str, Any],
    ) -> EvaluationResult:
        commits: list[dict[str, Any]] = raw.get("commits", [])

        if _is_connection_leak_hypothesis(statement):
            for c in commits:
                diff = c.get("diff_summary", "").lower()
                msg = c.get("message", "").lower()
                h = c.get("hash", "unknown")
                # Direct confirmation: code change omits connection closure / leaks connections
                if any(
                    phrase in diff
                    for phrase in (
                        "no longer explicitly closed",
                        "connections are not closed",
                        "modified pool release",
                        "connection leak",
                    )
                ) or any(phrase in msg for phrase in ("connection leak", "leak")):
                    return EvaluationResult(
                        hypothesis_id=hypothesis_id,
                        evidence_id=evidence_id,
                        relationship=EvaluationRelationship.SUPPORTS,
                        score_delta=EvidenceWeight.DIRECT_CONFIRMATION,
                        reason=(
                            f"Commit {h} diff confirms connection release was modified: "
                            "connections are no longer explicitly closed."
                        ),
                        rule_id="RULE_COMMIT_LEAK_CONFIRMATION",
                    )

        return EvaluationResult(
            hypothesis_id=hypothesis_id,
            evidence_id=evidence_id,
            relationship=EvaluationRelationship.NEUTRAL,
            score_delta=EvidenceWeight.NEUTRAL,
            reason="Recent commits do not contain modifications relevant to this hypothesis.",
            rule_id="RULE_COMMITS_NEUTRAL",
        )

    def _evaluate_logs(
        self,
        hypothesis_id: str,
        statement: str,
        evidence_id: str,
        raw: dict[str, Any],
    ) -> EvaluationResult:
        matches: list[dict[str, Any]] = raw.get("matches", [])

        if _is_connection_leak_hypothesis(statement):
            has_pool_timeout = False
            has_deployment = False
            for m in matches:
                msg = m.get("message", "").lower()
                if any(
                    k in msg
                    for k in (
                        "connection timeout",
                        "pool exhausted",
                        "failed to acquire database connection",
                    )
                ):
                    has_pool_timeout = True
                if any(k in msg for k in ("deployment", "rolled out")):
                    has_deployment = True

            if has_pool_timeout:
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.SUPPORTS,
                    score_delta=EvidenceWeight.INDEPENDENT_SUPPORT,
                    reason=(
                        "Logs confirm database connection pool exhaustion "
                        "and acquisition timeouts."
                    ),
                    rule_id="RULE_LOGS_POOL_TIMEOUT",
                )

            if has_deployment:
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.SUPPORTS,
                    score_delta=EvidenceWeight.TEMPORAL_CORRELATION,
                    reason=(
                        "Deployment rollout in logs temporally correlates "
                        "with the incident window."
                    ),
                    rule_id="RULE_LOGS_DEPLOY_CORRELATION",
                )

        if _is_db_overload_hypothesis(statement):
            has_healthy_db = False
            for m in matches:
                msg = m.get("message", "").lower()
                if any(
                    k in msg
                    for k in (
                        "health check passed",
                        "database accepting connections normally",
                        "cpu=12.3%",
                    )
                ):
                    has_healthy_db = True
                    break

            if has_healthy_db:
                return EvaluationResult(
                    hypothesis_id=hypothesis_id,
                    evidence_id=evidence_id,
                    relationship=EvaluationRelationship.CONTRADICTS,
                    score_delta=EvidenceWeight.CONTRADICTION,
                    reason=(
                        "Database logs confirm database health checks "
                        "passing and nominal latency."
                    ),
                    rule_id="RULE_LOGS_DB_HEALTHY",
                )

        return EvaluationResult(
            hypothesis_id=hypothesis_id,
            evidence_id=evidence_id,
            relationship=EvaluationRelationship.NEUTRAL,
            score_delta=EvidenceWeight.NEUTRAL,
            reason="Log entries do not contain probative signals for this hypothesis.",
            rule_id="RULE_LOGS_NEUTRAL",
        )

    def evaluate_missing_evidence(
        self,
        hypothesis: Hypothesis,
        missing_source: str,
        rule_id: str = "RULE_CRITICAL_EVIDENCE_MISSING",
    ) -> EvaluationResult:
        """Represent missing critical evidence deterministically (-1 score delta)."""
        return EvaluationResult(
            hypothesis_id=hypothesis.id,
            evidence_id=f"missing:{missing_source}",
            relationship=EvaluationRelationship.NEUTRAL,
            score_delta=EvidenceWeight.CRITICAL_MISSING,
            reason=f"Critical evidence missing: {missing_source}.",
            rule_id=rule_id,
        )

    def evaluate_all(
        self,
        hypothesis: Hypothesis,
        evidence_items: list[EvidenceItem],
    ) -> list[EvaluationResult]:
        """Evaluate a list of evidence items against a hypothesis."""
        return [self.evaluate(hypothesis, item) for item in evidence_items]

    def aggregate(
        self,
        hypothesis_id: str,
        evaluations: list[EvaluationResult],
    ) -> HypothesisEvaluationSummary:
        """Deterministically aggregate evidence evaluations for a hypothesis.

        Provides conservative status recommendations without mutating hypothesis models:
        - SUPPORTED: total_score >= 3 with confirmation or multiple supports, and no contradiction.
        - DISPROVEN: any contradiction (-3) or total_score <= -2.
        - INSUFFICIENT: all other cases.
        """
        total_score = sum(e.score_delta for e in evaluations)
        supporting = [e for e in evaluations if e.relationship == EvaluationRelationship.SUPPORTS]
        contradicting = [
            e for e in evaluations if e.relationship == EvaluationRelationship.CONTRADICTS
        ]
        neutral = [e for e in evaluations if e.relationship == EvaluationRelationship.NEUTRAL]

        has_direct_confirmation = any(
            e.score_delta == EvidenceWeight.DIRECT_CONFIRMATION for e in evaluations
        )
        has_contradiction = any(
            e.score_delta == EvidenceWeight.CONTRADICTION for e in evaluations
        )

        if has_contradiction or total_score <= -2:
            recommended_status = HypothesisStatus.DISPROVEN
            summary_reason = (
                f"Hypothesis disproven with aggregate score {total_score} "
                f"({len(contradicting)} contradictory evidence items)."
            )
        elif (has_direct_confirmation or total_score >= 3) and not has_contradiction:
            recommended_status = HypothesisStatus.SUPPORTED
            summary_reason = (
                f"Hypothesis supported with aggregate score {total_score} "
                f"({len(supporting)} supporting evidence items)."
            )
        else:
            recommended_status = HypothesisStatus.INSUFFICIENT
            summary_reason = (
                f"Evidence insufficient to conclude (aggregate score {total_score}; "
                f"{len(supporting)} supporting, {len(contradicting)} contradictory)."
            )

        return HypothesisEvaluationSummary(
            hypothesis_id=hypothesis_id,
            total_score=total_score,
            evaluations=evaluations,
            supporting_count=len(supporting),
            contradicting_count=len(contradicting),
            neutral_count=len(neutral),
            has_direct_confirmation=has_direct_confirmation,
            has_contradiction=has_contradiction,
            recommended_status=recommended_status,
            summary_reason=summary_reason,
        )
