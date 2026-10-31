CREATE TABLE spans(span_id TEXT PRIMARY KEY, trace_id TEXT, parent_span_id TEXT, service TEXT, name TEXT,
  start_ms INTEGER, end_ms INTEGER, status_code INTEGER, attrs TEXT);
CREATE TABLE logs(log_id TEXT PRIMARY KEY, trace_id TEXT, span_id TEXT, service TEXT, ts_ms INTEGER, level TEXT, message TEXT);
CREATE TABLE metrics(service TEXT, metric TEXT, ts_ms INTEGER, value REAL);
CREATE INDEX ix_spans_trace ON spans(trace_id);
CREATE INDEX ix_logs_svc_ts ON logs(service, ts_ms);
CREATE INDEX ix_metrics ON metrics(metric, ts_ms);
