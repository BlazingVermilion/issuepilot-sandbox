-- Event activity across all workspaces, including live cases without a dataset ID.
-- Use intake_metrics.sql for per-workspace proposal and review denominators.
SELECT BODY:kind::VARCHAR AS event_kind, COUNT(*) AS mirrored_rows,
       COUNT(DISTINCT EVENT_ID) AS distinct_event_ids,
       COUNT(DISTINCT BODY:case_id::VARCHAR) AS distinct_cases,
       MIN(BODY:created_at::VARCHAR) AS earliest_event_utc,
       MAX(BODY:created_at::VARCHAR) AS latest_event_utc
FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS
GROUP BY BODY:kind::VARCHAR ORDER BY mirrored_rows DESC, event_kind;
