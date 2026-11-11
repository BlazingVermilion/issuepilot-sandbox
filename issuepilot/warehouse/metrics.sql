-- Business question: which engine/policy produces proposals maintainers must edit?
-- One row per case version, never one row per webhook delivery.
WITH proposals AS (
  SELECT BODY:case_id::VARCHAR AS case_id,
         BODY:metadata:version::INTEGER AS version,
         BODY:metadata:engine::VARCHAR AS engine,
         BODY:metadata:model::VARCHAR AS model,
         BODY:metadata:policy_hash::VARCHAR AS policy_hash,
         BODY:metadata:latency_ms::FLOAT AS latency_ms
  FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS WHERE BODY:kind::VARCHAR='PROPOSED'
), decisions AS (
  SELECT BODY:case_id::VARCHAR AS case_id, BODY:metadata:version::INTEGER AS version,
         BODY:kind::VARCHAR AS kind, BODY:metadata:edited::BOOLEAN AS edited
  FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS
  WHERE BODY:kind::VARCHAR IN ('APPROVAL','REJECTION')
)
SELECT p.engine,p.model,p.policy_hash,COUNT(*) AS proposals,
       COUNT(d.kind) AS reviewed,
       COUNT_IF(d.kind='APPROVAL') AS approved,
       COUNT_IF(d.kind='REJECTION') AS rejected,
       100.0*COUNT_IF(d.edited)/NULLIF(COUNT(d.kind),0) AS edited_pct_of_reviewed,
       MEDIAN(p.latency_ms) AS median_draft_latency_ms
FROM proposals p LEFT JOIN decisions d ON p.case_id=d.case_id AND p.version=d.version
GROUP BY p.engine,p.model,p.policy_hash;

-- Approval edits are measurable. Subsequent team re-routing is NOT captured in
-- v0.1, so do not claim routing accuracy or team reassignment reduction.
SELECT BODY:kind::VARCHAR AS event_kind, COUNT(*) AS events
FROM ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS
GROUP BY event_kind ORDER BY events DESC;
