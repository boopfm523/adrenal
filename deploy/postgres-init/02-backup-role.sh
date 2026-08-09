#!/bin/sh
# Least-privilege read role used only by the dedicated backup runner.
#
# On a new volume this runs automatically when POSTGRES_BACKUP_PASSWORD is set. For an
# existing volume, set that variable for the postgres container and invoke this exact
# script once with docker compose exec; it is idempotent.
set -eu

if [ -z "${POSTGRES_BACKUP_PASSWORD:-}" ]; then
    echo "healthcurve: POSTGRES_BACKUP_PASSWORD unset; backup role not provisioned"
    exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set backup_password="$POSTGRES_BACKUP_PASSWORD" <<-'EOSQL'
    SELECT format('CREATE ROLE healthcurve_backup LOGIN PASSWORD %L', :'backup_password')
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'healthcurve_backup') \gexec

    -- Rerunning rotates the credential without widening privileges.
    SELECT format('ALTER ROLE healthcurve_backup PASSWORD %L', :'backup_password') \gexec
    GRANT CONNECT ON DATABASE :"DBNAME" TO healthcurve_backup;
    GRANT USAGE ON SCHEMA public, fact, plan, ai, ops, identity TO healthcurve_backup;
    GRANT SELECT ON ALL TABLES IN SCHEMA public, fact, plan, ai, ops, identity
        TO healthcurve_backup;
    GRANT SELECT ON ALL SEQUENCES IN SCHEMA public, fact, plan, ai, ops, identity
        TO healthcurve_backup;

    -- Migrations run as the database owner, so these defaults cover future objects.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public, fact, plan, ai, ops, identity
        GRANT SELECT ON TABLES TO healthcurve_backup;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public, fact, plan, ai, ops, identity
        GRANT SELECT ON SEQUENCES TO healthcurve_backup;

    REVOKE CREATE ON SCHEMA public, fact, plan, ai, ops, identity FROM healthcurve_backup;
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES
        IN SCHEMA public, fact, plan, ai, ops, identity FROM healthcurve_backup;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public, fact, plan, ai, ops, identity
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM healthcurve_backup;

    -- If migrations already created the queue, allow this dedicated worker to
    -- schedule/claim/complete backup jobs and nothing else. On a new volume the job
    -- migration performs the same conditional grant after creating ops.job.
    DO $grant$
    BEGIN
        IF to_regclass('ops.job') IS NOT NULL THEN
            GRANT SELECT, INSERT, UPDATE ON ops.job TO healthcurve_backup;
        END IF;
    END
    $grant$;
EOSQL

echo "healthcurve: healthcurve_backup provisioned read-only across durable schemas"
