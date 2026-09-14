#!/bin/sh
# View-only analyst roles for local-model analytical chat and MCP tools (ADR-0036).
#
# Migration 5e1a9c3d7b24 grants these roles SELECT on the curated analytics views only.
# This script always creates the roles as NOLOGIN with read-only defaults, so a restored
# dump that grants to them also works in an isolated restore database. Login is enabled
# only when both passwords are supplied. On a new volume it runs automatically; for an
# existing volume, set the variables for the postgres container and run this exact
# script once with docker compose exec. It is idempotent, and rerunning it rotates the
# passwords.
# postgres:16-alpine has no bash -- /bin/sh is busybox ash. Keep this POSIX.
set -eu

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" <<-'EOSQL'
    SELECT 'CREATE ROLE healthcurve_analytics_owner NOLOGIN'
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'healthcurve_analytics_owner') \gexec
    SELECT 'CREATE ROLE healthcurve_analyst NOLOGIN'
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'healthcurve_analyst') \gexec
    SELECT 'CREATE ROLE healthcurve_analyst_text NOLOGIN'
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'healthcurve_analyst_text') \gexec

    -- Defense in depth beneath the query validator and per-call transaction settings.
    ALTER ROLE healthcurve_analyst SET default_transaction_read_only = on;
    ALTER ROLE healthcurve_analyst SET statement_timeout = '15s';
    ALTER ROLE healthcurve_analyst SET idle_in_transaction_session_timeout = '60s';
    ALTER ROLE healthcurve_analyst_text SET default_transaction_read_only = on;
    ALTER ROLE healthcurve_analyst_text SET statement_timeout = '15s';
    ALTER ROLE healthcurve_analyst_text SET idle_in_transaction_session_timeout = '60s';
EOSQL

if [ -z "${POSTGRES_ANALYST_PASSWORD:-}" ] || [ -z "${POSTGRES_ANALYST_TEXT_PASSWORD:-}" ]; then
    echo "healthcurve: analyst roles present as NOLOGIN; passwords unset, login not enabled"
    exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set analyst_password="$POSTGRES_ANALYST_PASSWORD" \
     --set analyst_text_password="$POSTGRES_ANALYST_TEXT_PASSWORD" <<-'EOSQL'
    SELECT format('ALTER ROLE healthcurve_analyst LOGIN PASSWORD %L', :'analyst_password') \gexec
    SELECT format(
        'ALTER ROLE healthcurve_analyst_text LOGIN PASSWORD %L', :'analyst_text_password'
    ) \gexec
    GRANT CONNECT ON DATABASE :"DBNAME" TO healthcurve_analyst, healthcurve_analyst_text;
EOSQL

echo "healthcurve: healthcurve_analyst and healthcurve_analyst_text enabled for login"
