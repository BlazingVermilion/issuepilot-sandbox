-- Run either SELECT on its own in Snowsight. Use a reporting role with SELECT
-- and USAGE and an active warehouse. No SET, worksheet state or personal IDs.
-- A workspace's RECEIVED records establish its cohort. Historical records use
-- replay_run_id. Follow-up events without an unambiguous workspace are excluded
-- from downstream counts; their cases are shown in ambiguous_cases.
WITH receipt_facts AS (
    SELECT BODY:case_id::VARCHAR AS case_id,
           COALESCE(BODY:metadata:workspace_id::VARCHAR,
                    BODY:metadata:replay_run_id::VARCHAR) AS workspace_id,
           BODY:metadata:source_number::INTEGER AS source_number,
           BODY:metadata:source_size::INTEGER AS source_size
    FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS
    WHERE BODY:kind::VARCHAR = 'RECEIVED'
), members AS (
    SELECT workspace_id, case_id, MAX(source_number) AS source_number, MAX(source_size) AS source_size
    FROM receipt_facts WHERE workspace_id IS NOT NULL
    GROUP BY workspace_id, case_id
), ownership AS (
    SELECT case_id, COUNT(*) AS workspace_count FROM members GROUP BY case_id
)
SELECT m.workspace_id,
       COUNT(DISTINCT m.source_number) AS received_issues,
       COUNT(DISTINCT m.case_id) AS received_cases,
       MAX(m.source_size) AS source_size,
       COUNT(DISTINCT CASE WHEN t.BODY:kind::VARCHAR = 'PROPOSED'
            AND t.BODY:metadata:engine::VARCHAR = 'ollama'
            AND t.BODY:metadata:model::VARCHAR = 'qwen3:8b' THEN m.case_id END) AS cases_with_qwen_proposal,
       COUNT(DISTINCT CASE WHEN t.BODY:kind::VARCHAR IN ('APPROVAL','REJECTION') THEN m.case_id END) AS reviewed_cases,
       COUNT(DISTINCT CASE WHEN t.BODY:kind::VARCHAR = 'COMPLETED' THEN m.case_id END) AS completed_cases,
       COUNT(DISTINCT CASE WHEN t.BODY:kind::VARCHAR = 'FAILED' THEN m.case_id END) AS cases_ever_failed,
       COUNT(DISTINCT CASE WHEN o.workspace_count > 1 THEN m.case_id END) AS ambiguous_cases,
       COUNT(DISTINCT t.EVENT_ID) AS distinct_event_ids,
       COUNT(t.EVENT_ID) - COUNT(DISTINCT t.EVENT_ID) AS duplicate_event_ids
FROM members m JOIN ownership o ON o.case_id = m.case_id
LEFT JOIN ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS t
       ON t.BODY:case_id::VARCHAR = m.case_id AND o.workspace_count = 1
GROUP BY m.workspace_id ORDER BY m.workspace_id;

-- A proposal version is counted once. A repeated decision uses the most recent
-- timestamp, with event ID for ties. Unreviewed proposals never dilute edit rate.
WITH receipt_facts AS (
    SELECT BODY:case_id::VARCHAR AS case_id,
           COALESCE(BODY:metadata:workspace_id::VARCHAR,
                    BODY:metadata:replay_run_id::VARCHAR) AS workspace_id,
           BODY:metadata:source_number::INTEGER AS source_number,
           BODY:metadata:source_size::INTEGER AS source_size
    FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS
    WHERE BODY:kind::VARCHAR = 'RECEIVED'
), members AS (
    SELECT workspace_id, case_id, MAX(source_number) AS source_number, MAX(source_size) AS source_size
    FROM receipt_facts WHERE workspace_id IS NOT NULL
    GROUP BY workspace_id, case_id
), ownership AS (
    SELECT case_id, COUNT(*) AS workspace_count FROM members GROUP BY case_id
), ranked AS (
    SELECT m.workspace_id, m.case_id, t.BODY:metadata:version::INTEGER AS ver,
           t.BODY:kind::VARCHAR AS kind, t.BODY:metadata:engine::VARCHAR AS engine,
           t.BODY:metadata:model::VARCHAR AS model, t.BODY:metadata:policy_hash::VARCHAR AS policy_hash,
           t.BODY:metadata:edited::BOOLEAN AS edited, t.BODY:metadata:latency_ms::FLOAT AS latency_ms,
           ROW_NUMBER() OVER (
               PARTITION BY m.workspace_id, m.case_id, t.BODY:metadata:version::INTEGER,
                            CASE WHEN t.BODY:kind::VARCHAR = 'PROPOSED' THEN 'proposal' ELSE 'decision' END
               ORDER BY t.BODY:created_at::VARCHAR DESC, t.EVENT_ID DESC
           ) AS rn
    FROM members m JOIN ownership o ON o.case_id = m.case_id AND o.workspace_count = 1
    JOIN ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS t ON t.BODY:case_id::VARCHAR = m.case_id
    WHERE t.BODY:kind::VARCHAR IN ('PROPOSED','APPROVAL','REJECTION')
      AND t.BODY:metadata:version::INTEGER IS NOT NULL
)
SELECT p.workspace_id, p.engine, p.model, p.policy_hash,
       COUNT(*) AS proposals, COUNT(d.case_id) AS reviewed,
       SUM(CASE WHEN d.kind = 'REJECTION' THEN 1 ELSE 0 END) AS rejected,
       ROUND(100.0 * SUM(CASE WHEN d.edited THEN 1 ELSE 0 END) / NULLIF(COUNT(d.case_id),0),1) AS edited_pct_of_reviewed,
       ROUND(MEDIAN(p.latency_ms) / 1000.0,2) AS median_draft_seconds
FROM ranked p LEFT JOIN ranked d
  ON p.workspace_id = d.workspace_id AND p.case_id = d.case_id AND p.ver = d.ver
 AND d.rn = 1 AND d.kind IN ('APPROVAL','REJECTION')
WHERE p.rn = 1 AND p.kind = 'PROPOSED'
GROUP BY p.workspace_id, p.engine, p.model, p.policy_hash
ORDER BY p.workspace_id, p.engine, p.model, p.policy_hash;
