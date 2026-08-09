#!/bin/sh
# Schema partition and role privileges (ADR-0001).
#
# This is where SAFE-15 and SAFE-16 stop being a convention and become a privilege:
# `healthcurve_ai` holds no INSERT, UPDATE, or DELETE on the fact and plan schemas, so
# no AI code path can write a fact or approve a plan even if the application has a bug.
#
# Runs once, on first initialisation of an empty data volume.
# postgres:16-alpine has no bash -- /bin/sh is busybox ash. Keep this POSIX.
set -eu

: "${POSTGRES_AI_PASSWORD:?POSTGRES_AI_PASSWORD must be set so the restricted AI role can be created}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set ai_password="$POSTGRES_AI_PASSWORD" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS btree_gist;  -- exclusion constraints for regimen versions
    CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- digests for source checksums

    CREATE SCHEMA IF NOT EXISTS fact;  -- recorded facts
    CREATE SCHEMA IF NOT EXISTS plan;  -- physician-approved plan
    CREATE SCHEMA IF NOT EXISTS ai;    -- AI drafts and analyses
    CREATE SCHEMA IF NOT EXISTS ops;   -- jobs, audit, import batches (not a safety category)
    CREATE SCHEMA IF NOT EXISTS identity;  -- owner account and sessions

    -- The restricted AI role.
    SELECT format('CREATE ROLE healthcurve_ai LOGIN PASSWORD %L', :'ai_password')
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'healthcurve_ai') \gexec

    GRANT CONNECT ON DATABASE :"DBNAME" TO healthcurve_ai;

    -- Read-only on facts and plans: AI may compare the record with the plan and cite
    -- what it read (SAFE-05), but may never write either (SAFE-15, SAFE-16).
    GRANT USAGE ON SCHEMA fact, plan TO healthcurve_ai;
    GRANT SELECT ON ALL TABLES IN SCHEMA fact, plan TO healthcurve_ai;
    ALTER DEFAULT PRIVILEGES IN SCHEMA fact, plan GRANT SELECT ON TABLES TO healthcurve_ai;

    -- Revoke writes explicitly, including anything inherited from PUBLIC, and make the
    -- restriction apply to tables future migrations create as well. Without the
    -- ALTER DEFAULT PRIVILEGES line, a new table would arrive unprotected.
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA fact, plan FROM healthcurve_ai;
    ALTER DEFAULT PRIVILEGES IN SCHEMA fact, plan
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM healthcurve_ai;

    -- AI owns its own namespace.
    GRANT USAGE, CREATE ON SCHEMA ai TO healthcurve_ai;
    GRANT ALL ON ALL TABLES IN SCHEMA ai TO healthcurve_ai;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT ALL ON TABLES TO healthcurve_ai;

    -- And may record its own job progress and audit entries.
    GRANT USAGE ON SCHEMA ops TO healthcurve_ai;
    GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO healthcurve_ai;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ops
        GRANT SELECT, INSERT, UPDATE ON TABLES TO healthcurve_ai;

    -- The AI role has no business reading credentials or sessions at all.
    REVOKE ALL ON SCHEMA identity FROM healthcurve_ai;

    REVOKE ALL ON SCHEMA fact, plan FROM PUBLIC;
EOSQL

echo "healthcurve: schemas fact/plan/ai/ops/identity created; healthcurve_ai restricted to read-only on fact and plan"
