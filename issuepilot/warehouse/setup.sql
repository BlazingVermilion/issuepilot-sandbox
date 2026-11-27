-- Provision the standard destination. Existing data and key registrations are retained.
-- Use the object owner if this account uses custom provisioning roles.
USE ROLE SYSADMIN;
CREATE WAREHOUSE IF NOT EXISTS ISSUEPILOT_WH
  WAREHOUSE_SIZE='XSMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=TRUE;
CREATE DATABASE IF NOT EXISTS ISSUEPILOT_DB;
CREATE SCHEMA IF NOT EXISTS ISSUEPILOT_DB.ANALYTICS;
CREATE TABLE IF NOT EXISTS ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS (
  EVENT_ID VARCHAR NOT NULL, BODY VARIANT NOT NULL, HASH VARCHAR NOT NULL
);
USE ROLE SECURITYADMIN;
CREATE ROLE IF NOT EXISTS ISSUEPILOT_SYNC;
-- On a new service user, its owner must register the deployment's PUBLIC key separately.
CREATE USER IF NOT EXISTS ISSUEPILOT_SVC TYPE=SERVICE;
-- Substitute the exact user name here if this installation uses another service identity.
GRANT ROLE ISSUEPILOT_SYNC TO USER ISSUEPILOT_SVC;
GRANT USAGE ON WAREHOUSE ISSUEPILOT_WH TO ROLE ISSUEPILOT_SYNC;
GRANT USAGE ON DATABASE ISSUEPILOT_DB TO ROLE ISSUEPILOT_SYNC;
GRANT USAGE ON SCHEMA ISSUEPILOT_DB.ANALYTICS TO ROLE ISSUEPILOT_SYNC;
GRANT SELECT, INSERT ON TABLE ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS TO ROLE ISSUEPILOT_SYNC;
-- Next: run check_access.sql as the administrator. Configure key-pair login separately.
-- Snowflake standard-table uniqueness constraints are not enforced. The exporter
-- serializes runs in PostgreSQL and checks duplicate IDs and full readback content.
