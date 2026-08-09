# Dedicated backup runner. It is intentionally separate from the API/worker image:
# pg_dump and off-host backup credentials do not belong in an internet-facing process.
# The base matches the database major version required by ADR-0001.
FROM postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777

RUN apk add --no-cache age python3 \
 && mkdir -p /opt/healthcurve \
 && chown postgres:postgres /opt/healthcurve

COPY --chown=postgres:postgres src/healthcurve/operations/backup.py /opt/healthcurve/backup.py

USER postgres
ENTRYPOINT ["python3", "/opt/healthcurve/backup.py"]
