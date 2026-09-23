"""Automated Query Plan Optimizer & Statistics Index Stabilizer.

Provides SQLite EXPLAIN QUERY PLAN inspection, table scan and temp B-tree detection,
automated index recommendation, and database statistics stabilization via ANALYZE.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PlanCostEstimate:
    """Estimated cost metric and complexity flags for an executed or prospective query."""
    estimated_cost: float
    has_scan: bool
    has_temp_btree: bool


@dataclass
class QueryPlanAnalysis:
    """Detailed structural analysis of an SQLite query plan."""
    query: str
    plan_steps: list[str]
    has_full_scan: bool
    has_temp_btree: bool
    scanned_tables: list[str]
    used_indices: list[str]
    # Set when EXPLAIN failed: the flags above are then unknown, not a clean plan.
    error: str | None = None


@dataclass
class IndexRecommendation:
    """Actionable schema recommendation to stabilize query plan performance."""
    table_name: str
    columns: list[str]
    suggested_sql: str
    reason: str


@dataclass
class PlanStabilizerConfig:
    """Configuration options for query plan analysis and statistics maintenance."""
    enable_stat_refresh: bool = True


@dataclass
class QueryPlanTelemetry:
    """Aggregate metrics for query plan analysis and index stabilization."""
    queries_analyzed: int
    scans_detected: int
    temp_btrees_detected: int
    optimizations_performed: int
    indices_recommended: int


class StatisticsIndexStabilizer:
    """Executes database distribution statistics updates to prevent query plan degradation."""

    def __init__(self, enable_stat_refresh: bool = True) -> None:
        self.enable_stat_refresh = enable_stat_refresh
        self.optimizations_count = 0

    def stabilize_database(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """Execute SQLite ANALYZE and PRAGMA optimize to refresh query planner statistics."""
        cursor = conn.cursor()
        analyze_done = False
        pragma_done = False
        errors: list[str] = []

        if self.enable_stat_refresh:
            try:
                cursor.execute("ANALYZE")
                analyze_done = True
            except sqlite3.Error as err:
                errors.append(f"ANALYZE: {err}")
                logger.warning("Failed running ANALYZE: %s", err)

        try:
            cursor.execute("PRAGMA optimize")
            pragma_done = True
            self.optimizations_count += 1
        except sqlite3.Error as err:
            errors.append(f"PRAGMA optimize: {err}")
            logger.warning("Failed running PRAGMA optimize: %s", err)

        return {
            "ok": pragma_done and (analyze_done or not self.enable_stat_refresh),
            "analyze_executed": analyze_done,
            "pragma_optimize_executed": pragma_done,
            "total_optimizations": self.optimizations_count,
            "errors": errors,
        }


class QueryPlanOptimizer:
    """Automated analyzer and index stabilizer for SQLite queries."""

    def __init__(
        self,
        config: PlanStabilizerConfig | None = None,
        stabilizer: StatisticsIndexStabilizer | None = None,
    ) -> None:
        self.config = config or PlanStabilizerConfig()
        self.stabilizer = stabilizer or StatisticsIndexStabilizer(
            enable_stat_refresh=self.config.enable_stat_refresh
        )

        self._queries_analyzed = 0
        self._scans_detected = 0
        self._temp_btrees_detected = 0
        self._indices_recommended = 0

    def analyze_query(self, conn: sqlite3.Connection, query: str) -> QueryPlanAnalysis:
        """Extract and evaluate the SQLite query execution plan."""
        self._queries_analyzed += 1
        plan_steps: list[str] = []
        scanned_tables: list[str] = []
        used_indices: list[str] = []
        has_full_scan = False
        has_temp_btree = False
        error: str | None = None

        cursor = conn.cursor()
        try:
            cursor.execute(f"EXPLAIN QUERY PLAN {query}")
            rows = cursor.fetchall()
            for row in rows:
                # Format of EXPLAIN QUERY PLAN: (id, parent, notused, detail)
                detail = str(row[-1]) if row else ""
                plan_steps.append(detail)

                # Check for full table scans. "SCAN t USING [COVERING] INDEX i" walks an
                # index in order (ORDER BY / covering reads); it is not a table scan.
                scan_match = re.search(r"\bSCAN(?:\s+TABLE)?\s+(\w+)", detail, re.IGNORECASE)
                if scan_match and not re.search(r"\bUSING\s+(?:COVERING\s+)?INDEX\b", detail, re.IGNORECASE):
                    has_full_scan = True
                    table = scan_match.group(1)
                    if table not in scanned_tables:
                        scanned_tables.append(table)

                # Check for temp b-tree
                if "USE TEMP B-TREE" in detail.upper():
                    has_temp_btree = True

                # Check for index usage
                idx_match = re.search(r"\bUSING\s+(?:COVERING\s+)?INDEX\s+(\w+)", detail, re.IGNORECASE)
                if idx_match:
                    idx = idx_match.group(1)
                    if idx not in used_indices:
                        used_indices.append(idx)

        except sqlite3.Error as err:
            error = str(err)
            logger.warning("Error explaining query plan for '%s': %s", query, err)

        if has_full_scan:
            self._scans_detected += 1
        if has_temp_btree:
            self._temp_btrees_detected += 1

        return QueryPlanAnalysis(
            query=query,
            plan_steps=plan_steps,
            has_full_scan=has_full_scan,
            has_temp_btree=has_temp_btree,
            scanned_tables=scanned_tables,
            used_indices=used_indices,
            error=error,
        )

    def estimate_plan_cost(self, conn: sqlite3.Connection, query: str) -> PlanCostEstimate:
        """Compute an abstract cost score based on scans and sorting steps."""
        analysis = self.analyze_query(conn, query)
        cost = 1.0
        if analysis.has_full_scan:
            cost += 10.0 * max(1, len(analysis.scanned_tables))
        if analysis.has_temp_btree:
            cost += 5.0
        if analysis.used_indices:
            cost = max(1.0, cost - 2.0)

        return PlanCostEstimate(
            estimated_cost=cost,
            has_scan=analysis.has_full_scan,
            has_temp_btree=analysis.has_temp_btree,
        )

    def recommend_indices(self, conn: sqlite3.Connection, query: str) -> list[IndexRecommendation]:
        """Inspect scanned predicates in query and recommend covering indices."""
        analysis = self.analyze_query(conn, query)
        recommendations: list[IndexRecommendation] = []

        if not analysis.has_full_scan:
            return recommendations

        for table in analysis.scanned_tables:
            # Extract simple WHERE predicate columns
            where_match = re.search(r"\bWHERE\b\s+(.*?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|$)", query, re.IGNORECASE)
            cols: list[str] = []
            if where_match:
                predicate_clause = where_match.group(1)
                found_cols = re.findall(r"\b([a-zA-Z_]\w*)\s*(?:=|IS|IN|<|>|LIKE)", predicate_clause)
                for col in found_cols:
                    if col.lower() not in ("and", "or", "not", "null") and col not in cols:
                        cols.append(col)

            if not cols:
                # No predicate column to index: an index cannot remove this scan.
                continue

            index_name = f"idx_{table}_{'_'.join(cols)}"
            suggested_sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({', '.join(cols)})"
            rec = IndexRecommendation(
                table_name=table,
                columns=cols,
                suggested_sql=suggested_sql,
                reason=f"Eliminates full table scan on {table} for columns: {', '.join(cols)}",
            )
            recommendations.append(rec)
            self._indices_recommended += 1

        return recommendations

    def get_telemetry(self) -> QueryPlanTelemetry:
        """Return aggregate telemetry metrics."""
        return QueryPlanTelemetry(
            queries_analyzed=self._queries_analyzed,
            scans_detected=self._scans_detected,
            temp_btrees_detected=self._temp_btrees_detected,
            optimizations_performed=self.stabilizer.optimizations_count,
            indices_recommended=self._indices_recommended,
        )

    def export_telemetry_dict(self) -> dict[str, Any]:
        """Export telemetry and configuration as dictionary."""
        return {
            "metrics": asdict(self.get_telemetry()),
            "config": asdict(self.config),
        }
