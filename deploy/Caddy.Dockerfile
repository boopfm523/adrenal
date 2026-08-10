# Build the private SPA and serve it from the existing public-edge process.
# Node is build-only; the production image contains Caddy and static assets, not npm.
FROM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS frontend-builder

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts

COPY frontend ./
RUN npm run check:api && npm run build

# caddy:2.10-alpine, pinned identically to the prior Compose service (T6).
FROM caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/Caddyfile.tailscale deploy/healthcurve-app.caddy /etc/caddy/
COPY --from=frontend-builder /web/dist /srv
