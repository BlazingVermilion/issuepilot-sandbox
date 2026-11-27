-- Standard installation: execute the entire SHOW ->> SHOW ->> WITH chain as
-- ONE statement as SECURITYADMIN (or the grant administrator). An interactive
-- reporting user is a separate identity; it does not inherit the service's role.
SHOW GRANTS OF ROLE ISSUEPILOT_SYNC
  ->> SHOW GRANTS TO ROLE ISSUEPILOT_SYNC
  ->> WITH required_grants (seq, object_kind, object_name, needed) AS (
        SELECT * FROM VALUES
          (1, 'USER', 'ISSUEPILOT_SVC', 'ROLE ISSUEPILOT_SYNC'),
          (2, 'WAREHOUSE', 'ISSUEPILOT_WH', 'USAGE'),
          (3, 'DATABASE', 'ISSUEPILOT_DB', 'USAGE'),
          (4, 'SCHEMA', 'ISSUEPILOT_DB.ANALYTICS', 'USAGE'),
          (5, 'TABLE', 'ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS', 'SELECT'),
          (6, 'TABLE', 'ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS', 'INSERT')
      ), actual_grants (object_kind, object_name, held) AS (
        SELECT 'USER', "grantee_name", 'ROLE ISSUEPILOT_SYNC'
          FROM $2 WHERE "granted_to" = 'USER' AND "role" = 'ISSUEPILOT_SYNC'
        UNION ALL
        SELECT "granted_on", "name", "privilege"
          FROM $1 WHERE "granted_to" = 'ROLE' AND "grantee_name" = 'ISSUEPILOT_SYNC'
      )
      SELECT r.seq, r.object_kind, r.object_name, r.needed,
             IFF(COUNT(a.held) > 0, 'PASS', 'MISSING') AS result
      FROM required_grants r LEFT JOIN actual_grants a
        ON a.object_kind = r.object_kind AND a.object_name = r.object_name AND a.held = r.needed
      GROUP BY r.seq, r.object_kind, r.object_name, r.needed
      ORDER BY r.seq;
