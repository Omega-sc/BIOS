SCHEMA_VERSION = "1.0"
SERVICES = ["gateway", "auth-service", "db-pool"]
METRIC_SOURCES = {  # metric -> emitting service
    "db_pool.active_connections": "db-pool",
    "db_pool.queries_in_flight": "db-pool",
    "db_pool.wait_queue_len": "db-pool",
    "auth.latency_p99": "auth-service",
    "gateway.error_rate_5xx": "gateway",
    "frontend.cpu_pct": "frontend",
}
SKEW_MS = {
    "auth-service": 12_000
}  # injected skew; agents must ESTIMATE it, never read this constant
POOL_MAX = 20
T0_MS = 1700000000000  # Epoch ms of incident-day 12:00:00 (e.g. 1700000000000)
