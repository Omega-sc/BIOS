"""Service topology DAG module (owned by P2).

Constructs dependency DAGs annotated with call counts, mean latencies,
error-propagation fractions, and anomalous metric onsets. Computes the failure frontier.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from tools.metrics import rank_metric_onsets


def _get_connection(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating whether it needs closing."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    return sqlite3.connect(str(db)), True


def build_dependency_dag(db: str | Path | sqlite3.Connection) -> nx.DiGraph:
    """Build directed acyclic graph of service call dependencies.

    Nodes represent services and are annotated with:
      - error_rate: fraction of failed spans (status_code >= 500)
      - metric_onset: anomalous onset timestamp (ms) of the primary service metric
      - span_count: total spans emitted

    Edges represent caller -> callee dependencies and are annotated with:
      - call_count: total cross-service invocations
      - mean_latency: mean duration of callee execution (ms)
      - error_propagation_fraction: fraction of caller errors correlated with callee errors

    Returns:
        nx.DiGraph: NetworkX directed graph.
    """
    conn, should_close = _get_connection(db)
    dag = nx.DiGraph()
    try:
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='spans'")
        if not cur.fetchone():
            return dag

        # 1. Discover all services from spans, metrics, and logs
        all_services: set[str] = set()
        cur.execute("SELECT DISTINCT service FROM spans")
        for r in cur.fetchall():
            if r[0]:
                all_services.add(r[0])

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'")
        if cur.fetchone():
            cur.execute("SELECT DISTINCT service FROM metrics")
            for r in cur.fetchall():
                if r[0]:
                    all_services.add(r[0])

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs'")
        if cur.fetchone():
            cur.execute("SELECT DISTINCT service FROM logs")
            for r in cur.fetchall():
                if r[0]:
                    all_services.add(r[0])

        if not all_services:
            return dag

        # 2. Metric onsets per service
        onsets = rank_metric_onsets(conn)
        service_onsets: dict[str, int | None] = {}
        for o in onsets:
            if o.service not in service_onsets or service_onsets[o.service] is None:
                service_onsets[o.service] = o.onset_corrected_ms

        # 3. Node statistics from spans
        cur.execute(
            """
            SELECT service, COUNT(*),
                   SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END)
            FROM spans
            GROUP BY service
            """
        )
        service_stats = dict((r[0], (r[1], r[2])) for r in cur.fetchall())

        for svc in all_services:
            count, errs = service_stats.get(svc, (0, 0))
            err_rate = round(float(errs) / max(1, count), 4) if count > 0 else 0.0
            dag.add_node(
                svc,
                error_rate=err_rate,
                metric_onset=service_onsets.get(svc),
                span_count=count,
            )

        # 4. Query cross-service parent-child span edges
        cur.execute(
            """
            SELECT p.service, c.service,
                   COUNT(*) as call_count,
                   AVG(c.end_ms - c.start_ms) as mean_latency,
                   SUM(CASE WHEN p.status_code >= 500 AND c.status_code >= 500 THEN 1 ELSE 0 END) as propagated_errors,
                   SUM(CASE WHEN p.status_code >= 500 THEN 1 ELSE 0 END) as parent_errors
            FROM spans p
            JOIN spans c ON p.span_id = c.parent_span_id
            WHERE p.service != c.service
            GROUP BY p.service, c.service
            """
        )
        edge_rows = cur.fetchall()

        for p_svc, c_svc, call_count, mean_lat, prop_errs, p_errs in edge_rows:
            prop_fraction = round(float(prop_errs) / max(1, p_errs), 4) if p_errs > 0 else 0.0
            dag.add_edge(
                p_svc,
                c_svc,
                call_count=call_count,
                mean_latency=round(float(mean_lat), 2) if mean_lat is not None else 0.0,
                error_propagation_fraction=prop_fraction,
            )

        # 5. Connect upstream client services (services with 0 spans but present in metrics/logs)
        cur.execute(
            "SELECT service, COUNT(*) FROM spans WHERE parent_span_id IS NULL GROUP BY service ORDER BY COUNT(*) DESC LIMIT 1"
        )
        root_row = cur.fetchone()
        root_svc = root_row[0] if root_row else None
        root_count = root_row[1] if root_row else 0

        if root_svc:
            root_stats = service_stats.get(root_svc, (0, 0))
            root_err_rate = (
                round(float(root_stats[1]) / max(1, root_stats[0]), 4) if root_stats[0] > 0 else 0.0
            )
            cur.execute(
                "SELECT AVG(end_ms - start_ms) FROM spans WHERE service = ? AND parent_span_id IS NULL",
                (root_svc,),
            )
            dur_row = cur.fetchone()
            root_mean_dur = (
                round(float(dur_row[0]), 2) if dur_row and dur_row[0] is not None else 50.0
            )

            client_services = [s for s in all_services if service_stats.get(s, (0, 0))[0] == 0]
            for client in client_services:
                dag.nodes[client]["error_rate"] = root_err_rate
                dag.nodes[client]["span_count"] = root_count
                dag.add_edge(
                    client,
                    root_svc,
                    call_count=root_count or 100,
                    mean_latency=root_mean_dur,
                    error_propagation_fraction=root_err_rate,
                )

        return dag

    except Exception:
        return nx.DiGraph()
    finally:
        if should_close:
            conn.close()


def to_node_link_json(dag: nx.DiGraph) -> str:
    """Export NetworkX dependency DAG as node-link JSON string."""
    data = nx.node_link_data(dag, edges="links")
    data["edges"] = list(data.get("links", []))
    return json.dumps(data, indent=2)


def failure_frontier(
    dag: nx.DiGraph | dict | None = None,
    db: str | Path | sqlite3.Connection | None = None,
) -> str | None:
    """Identify the earliest failing service boundary or deepest failing node in the DAG.

    Computed from node/edge attributes: deepest failing node whose dependencies are healthy,
    or the boundary edge with the earliest onset. Returns None if healthy.
    """
    if dag is None and db is not None:
        dag = build_dependency_dag(db)

    # Reconstruct from node-link dict if necessary
    if isinstance(dag, dict):
        dag = nx.node_link_graph(dag, edges="links")

    if dag is None or len(dag.nodes) == 0:
        return None

    # 1. Identify failing/anomalous nodes based on error_rate or anomalous metric onset
    failing_nodes: dict[str, tuple[float, int | None]] = {}
    for n in dag.nodes:
        err_rate = dag.nodes[n].get("error_rate", 0.0)
        onset = dag.nodes[n].get("metric_onset")
        if err_rate > 0.05 or onset is not None:
            failing_nodes[n] = (err_rate, onset)

    if not failing_nodes:
        return None

    # 2. Check for upstream client whose metric onset preceded all span errors (e.g. client retry flood)
    earliest_onset_node = None
    earliest_onset_val = None
    for n, (_, onset) in failing_nodes.items():
        if onset is not None:
            if earliest_onset_val is None or onset < earliest_onset_val:
                earliest_onset_val = onset
                earliest_onset_node = n

    if earliest_onset_node and dag.in_degree(earliest_onset_node) == 0:
        succs = list(dag.successors(earliest_onset_node))
        if succs:
            succ_onsets = [
                dag.nodes[s].get("metric_onset")
                for s in succs
                if dag.nodes[s].get("metric_onset") is not None
            ]
            if succ_onsets and earliest_onset_val < min(succ_onsets):
                return f"{earliest_onset_node} -> {succs[0]}"

    # 3. Find deepest failing nodes whose dependencies are healthy
    deepest_failing: list[str] = []
    for n in failing_nodes:
        successors = list(dag.successors(n))
        deps_healthy = all(s not in failing_nodes for s in successors)
        if deps_healthy:
            deepest_failing.append(n)

    # If a failing node has outgoing dependencies that are all healthy (e.g. gateway timeout misconfig)
    for n in deepest_failing:
        if list(dag.successors(n)):
            return n

    # For deepest failing leaf nodes (e.g. database or backend service)
    if deepest_failing:
        best_leaf = min(
            deepest_failing,
            key=lambda n: (
                failing_nodes[n][1] is None,
                failing_nodes[n][1] or 0,
                -failing_nodes[n][0],
            ),
        )
        failing_preds = [p for p in dag.predecessors(best_leaf) if p in failing_nodes]
        if failing_preds:
            best_pred = max(
                failing_preds,
                key=lambda p: (
                    dag.get_edge_data(p, best_leaf).get("error_propagation_fraction", 0.0),
                    dag.get_edge_data(p, best_leaf).get("mean_latency", 0.0),
                ),
            )
            return f"{best_pred} -> {best_leaf}"
        return best_leaf

    if earliest_onset_node:
        return earliest_onset_node

    return None


def infer_topology(db: str | Path | sqlite3.Connection) -> nx.DiGraph:
    """Compatibility alias for build_dependency_dag."""
    return build_dependency_dag(db)
