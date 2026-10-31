# Telemetry Record Formats & Schema Contracts

All records are serialized as JSON Lines (JSONL), one record per line.
Each record contains a `"type"` discriminator field: `"span"`, `"log"`, or `"metric"`.

## 1. Span Record
Emitted by distributed tracing across microservices (`gateway`, `auth-service`, `db-pool`).

```json
{
  "type": "span",
  "span_id": "span-gateway-100",
  "trace_id": "trace-100",
  "parent_span_id": null,
  "service": "gateway",
  "name": "GET /subsidy/claim",
  "start_ms": 1700000000000,
  "end_ms": 1700000000085,
  "status_code": 200,
  "attrs": {"http.method": "GET", "http.route": "/subsidy/claim"}
}
```

- `span_id`: Unique identifier for the span.
- `trace_id`: Correlates spans within a single distributed request.
- `parent_span_id`: Identifier of parent span, or `null` for root span.
- `service`: Microservice name (`gateway`, `auth-service`, `db-pool`).
- `name`: Operation or endpoint name.
- `start_ms`: Epoch millisecond start timestamp (uncorrected, subject to clock skew).
- `end_ms`: Epoch millisecond end timestamp (uncorrected).
- `status_code`: HTTP-like status code (200, 500, 504; 0 = unset).
- `attrs`: Arbitrary string/primitive dictionary of attributes.

## 2. Log Line Record
Emitted by application logger.

```json
{
  "type": "log",
  "log_id": "log-auth-501",
  "trace_id": "trace-100",
  "span_id": "span-auth-100",
  "service": "auth-service",
  "ts_ms": 1700000012010,
  "level": "INFO",
  "message": "Authenticating citizen token"
}
```

- `log_id`: Unique identifier for the log record.
- `trace_id`: Correlated trace id (optional, may be null).
- `span_id`: Correlated span id (optional, may be null).
- `service`: Emitting service (`gateway`, `auth-service`, `db-pool`, `frontend`).
- `ts_ms`: Epoch millisecond timestamp (uncorrected).
- `level`: Log level (`DEBUG`, `INFO`, `WARN`, `ERROR`).
- `message`: Human-readable log message.

## 3. Metric Point Record
Emitted periodically by service telemetry.

```json
{
  "type": "metric",
  "service": "db-pool",
  "metric": "db_pool.active_connections",
  "ts_ms": 1700000000000,
  "value": 14.0
}
```

Standard Metric Names and Sources:
- `db_pool.active_connections` (`db-pool`): Number of active connections held in the pool (max 20).
- `db_pool.queries_in_flight` (`db-pool`): Number of concurrent queries actively executing.
- `db_pool.wait_queue_len` (`db-pool`): Number of threads waiting to acquire a connection.
- `auth.latency_p99` (`auth-service`): p99 latency of authentication operations in milliseconds.
- `gateway.error_rate_5xx` (`gateway`): Rate/fraction of 5xx responses returned to clients.
- `frontend.cpu_pct` (`frontend`): CPU utilization percentage of client frontends.

## 4. Clock Skew and Timestamps
- Raw telemetry timestamps in `telemetry.jsonl` are unaligned.
- `auth-service` carries a simulated +12,000 ms (+12 s) clock skew on its raw clock.
- Investigation tools must dynamically estimate and compensate for clock skew using parent-child causality bounding or log correlations.
