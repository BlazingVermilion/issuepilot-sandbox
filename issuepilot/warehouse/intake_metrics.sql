-- Source size is NULL if the imported events predate this metadata field.
-- Run from Snowsight after both sync calls return VERIFIED. Paste the exact workspace_id from manage.py status.
USE ROLE ISSUEPILOT_SYNC;
USE WAREHOUSE ISSUEPILOT_WH;
USE DATABASE ISSUEPILOT_DB;
USE SCHEMA ANALYTICS;
SET workspace_id = 'YOUR_WORKSPACE_ID';

-- One case per original issue. Drafts, decisions and failures are counted separately.
-- Keep both the number of cases and checked event IDs visible in the screenshot.
WITH cohort AS (
    SELECT DISTINCT body:case_id::VARCHAR AS case_id, body:metadata:source_number::INTEGER AS source_number
    FROM triage_events
    WHERE body:kind::VARCHAR = 'RECEIVED'
      AND COALESCE(body:metadata:workspace_id::VARCHAR, body:metadata:replay_run_id::VARCHAR) = $workspace_id
), e AS (
    SELECT c.source_number, t.event_id, t.body:case_id::VARCHAR AS case_id,
           t.body:kind::VARCHAR AS kind, t.body:metadata AS meta
    FROM cohort c JOIN triage_events t ON c.case_id = t.body:case_id::VARCHAR
), totals AS (
    SELECT COUNT(DISTINCT source_number) AS received_issues,
           MAX(IFF(kind='RECEIVED', meta:source_size::INTEGER, NULL)) AS source_size,
           COUNT(DISTINCT IFF(kind='PROPOSED' AND meta:model::VARCHAR='qwen3:8b',case_id,NULL)) AS cases_with_qwen_proposal,
           COUNT(DISTINCT IFF(kind IN ('APPROVAL','REJECTION'),case_id,NULL)) AS reviewed_cases,
           COUNT(DISTINCT IFF(kind='FAILED',case_id,NULL)) AS cases_ever_failed,
           COUNT(*) - COUNT(DISTINCT event_id) AS duplicate_event_ids
    FROM e
)
SELECT received_issues, source_size, cases_with_qwen_proposal, reviewed_cases,
       cases_ever_failed, duplicate_event_ids FROM totals;

-- Model/policy operational measures: reviewed denominator includes approvals and rejections.
-- Each row below represents a PROPOSED version; a case may have multiple versions.
WITH cohort AS (
    SELECT DISTINCT body:case_id::VARCHAR AS id FROM triage_events
    WHERE body:kind::VARCHAR='RECEIVED' AND COALESCE(body:metadata:workspace_id::VARCHAR, body:metadata:replay_run_id::VARCHAR)=$workspace_id
), events AS (
    SELECT t.body:case_id::VARCHAR AS id, t.body:kind::VARCHAR AS kind,
           t.body:metadata:version::INT AS ver, t.body:metadata AS m
    FROM triage_events t JOIN cohort c ON c.id=t.body:case_id::VARCHAR
)
SELECT p.m:model::VARCHAR AS model, LEFT(p.m:policy_hash::VARCHAR,12) AS policy_version,
       COUNT(*) AS proposals, COUNT(d.id) AS reviewed,
       COALESCE(COUNT_IF(d.kind='REJECTION'),0) AS rejected,
       ROUND(100.0*COALESCE(COUNT_IF(d.m:edited::BOOLEAN),0)/NULLIF(COUNT(d.id),0),1) AS edited_pct_of_reviewed,
       ROUND(MEDIAN(p.m:latency_ms::FLOAT)/1000,2) AS median_draft_seconds
FROM events p LEFT JOIN events d ON p.id=d.id AND p.ver=d.ver AND d.kind IN ('APPROVAL','REJECTION')
WHERE p.kind='PROPOSED' GROUP BY 1,2;
