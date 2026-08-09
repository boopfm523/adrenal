# Rate limits and abuse controls

HealthCurve uses Redis for shared, durable fixed-window limits. The API and Telegram
worker use the same model-call counter. Redis keys contain a SHA-256 digest of the
owner UUID or normalized login address, never an email address, message, prompt, or
health value.

| Scope | Default | Identity | Exceeded behavior |
|---|---:|---|---|
| Login | 5 / 15 minutes | normalized login address | HTTP 429 |
| Local model | 30 / hour | owner UUID | HTTP 429 or an explicit Telegram reply |
| Report generation | 6 / hour | owner UUID | HTTP 429 |

Successful HTTP decisions include `RateLimit-Limit`, `RateLimit-Remaining`, and
`RateLimit-Reset`. Rejections additionally include `Retry-After` and a structured
`rate_limit_exceeded` error. If Redis is unavailable, protected HTTP work fails closed
with `503 rate_limit_unavailable`; Telegram says nothing was recorded and points to
deterministic commands. There is no silent drop or unmetered fallback.

Configuration is available through the `HC_*_RATE_LIMIT` and
`HC_*_RATE_WINDOW_S` variables in `.env.example`. Production requires
`HC_REDIS_URL`. Docker Compose enables Redis AOF (`appendfsync everysec`) on the
`redis_data` volume, so application or Redis restarts preserve counters.

## Operations

An exceeded decision logs only the scope as `reason_code`; identifiers and payloads
are not logged. Alert on repeated `rate limit exceeded` or
`rate limit decision unavailable` events. A Redis outage deliberately blocks login,
model use, and report generation; restore Redis rather than bypassing the limiter.

To inspect a development counter without exposing the key material:

```bash
docker compose exec redis redis-cli --scan --pattern 'hc:rate:*'
```

Deleting counters weakens the security boundary and is not a routine recovery step.
If it is unavoidable, record the incident and delete only the exact hashed key.
