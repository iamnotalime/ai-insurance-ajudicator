# Insurance AI Adjudicator

FastAPI service for automated insurance claim adjudication using a multi-agent
workflow, durable audit trails, Redis-backed queues and rate limiting, and
Prometheus/OpenTelemetry observability.

## What It Provides

- Claim submission, lookup, listing, batch submission, and manual adjudication APIs.
- Agent workflow for document extraction, policy analysis, fraud detection, and final decisioning.
- PostgreSQL persistence for claims, policies, decisions, fraud indicators, and audit logs.
- Redis-backed idempotency, background processing queue, and distributed rate limiting.
- Health, readiness, metrics, structured logs, tracing hooks, and security headers.
- Production fail-closed configuration validation for secrets, CORS, hosts, database schema policy, and LLM provider.

## Project Layout

```text
src/
  agents/          Agent implementations and orchestration
  api/             FastAPI app and routes
  config/          Environment-backed settings and validation
  database/        SQLAlchemy models, sessions, repositories
  middleware/      Auth, rate limiting, correlation, security headers
  observability/   Metrics, logging, tracing
  services/        LLM, Redis, audit services
  utils/           Circuit breaker, encryption, sanitization
  workers/         Redis queue claim processor
tests/             Pytest suite
alembic/           Database migrations
monitoring/        Prometheus and Grafana provisioning
```

## Local Development

Use Python 3.11 or 3.12 for parity with CI and production.

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

For local development, `LLM_PROVIDER=mock` and `FF_USE_DATABASE=false` are the
fastest way to exercise core behavior without external services.

Run tests:

```bash
python -m pytest
```

Run the API:

```bash
uvicorn src.api.routes:app --host 0.0.0.0 --port 8000 --reload
```

## Docker Development Stack

```bash
docker compose up --build
```

The development stack publishes API, PostgreSQL, Redis, Prometheus, Grafana, and
Jaeger ports for local inspection.

## Production Deployment

Read [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) before setting
`ENVIRONMENT=production`.

Production mode requires:

- high-entropy `JWT_SECRET`, `API_KEYS`, `ENCRYPTION_KEY`, `DB_PASSWORD`, and Redis credentials
- explicit `CORS_ORIGINS` and `ALLOWED_HOSTS`
- `LLM_PROVIDER` set to a real provider, not `mock`
- `DB_AUTO_CREATE_TABLES=false`
- database migrations applied before app startup
- Redis available for distributed rate limiting

Apply migrations:

```bash
alembic upgrade head
```

Run the production compose stack:

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

## Authentication

Requests require either a JWT bearer token or an API key.

API key header:

```bash
curl http://localhost:8000/api/v1/claims \
  -H "X-API-Key: your-api-key"
```

JWT bearer token:

```bash
curl http://localhost:8000/api/v1/claims \
  -H "Authorization: Bearer your-jwt"
```

## Example Claim Submission

```bash
curl -X POST http://localhost:8000/api/v1/claims \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-Idempotency-Key: claim-123" \
  -d '{
    "claim_type": "auto",
    "policy_number": "POL-2024-001",
    "date_of_loss": "2026-05-15",
    "description": "Vehicle collision at an intersection with bumper damage.",
    "total_amount_claimed": 15000.00,
    "items": [
      {
        "description": "Front bumper repair",
        "category": "repair",
        "amount_claimed": 15000.00,
        "date_of_loss": "2026-05-15"
      }
    ]
  }'
```

## Health And Observability

- `GET /health`: liveness and dependency status.
- `GET /ready`: readiness for traffic.
- `GET /metrics`: Prometheus metrics.

Request metrics use route templates to avoid high-cardinality labels. Audit logs
redact sensitive fields before logging or persistence.

## CI

GitHub Actions installs the package, compiles `src` and `tests`, and runs the
pytest suite with mock LLM, no database requirement, and tracing disabled.

## Security

See [SECURITY.md](SECURITY.md).
