# Production Readiness

This service is configured to fail closed in production. Set `ENVIRONMENT=production`
only when all required dependencies and secrets are present.

## Required Runtime Configuration

Set these values through your secret manager or deployment platform:

- `DB_PASSWORD`: strong database password, not a dev default.
- `REDIS_PASSWORD`: required for the production compose stack.
- `JWT_SECRET`: at least 32 characters.
- `ENCRYPTION_KEY`: urlsafe base64 string that decodes to exactly 32 bytes.
- `API_KEYS`: comma-separated high-entropy API keys, each at least 24 characters.
- `LLM_PROVIDER`: `anthropic` or `openai`; `mock` is rejected in production.
- `LLM_API_KEY` and `LLM_MODEL`: provider credentials and model name.
- `CORS_ORIGINS`: explicit allowed origins, never `*` in production.
- `ALLOWED_HOSTS`: explicit hostnames accepted by `TrustedHostMiddleware`.
- `DB_AUTO_CREATE_TABLES=false`: production schema changes must use migrations.

Generate an encryption key with:

```bash
python -c "from src.utils.encryption import EncryptionKeyManager; print(EncryptionKeyManager.generate_master_key())"
```

## Database

Production startup verifies database connectivity but does not create tables.
Apply migrations before rolling the app:

```bash
alembic upgrade head
```

The application persists adjudication decisions and fraud indicators through
`ClaimRepository.update_decision`, so processed claims keep a durable decision
trail instead of only a status update.

## Deployment

Build and run the production compose stack:

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

The production compose file avoids publishing Postgres and Redis ports, requires
secrets through environment expansion, and runs the API and worker from the
production image target.

## Health Semantics

- `/health`: liveness plus dependency status. Returns `status=degraded` when a
  configured dependency is unhealthy.
- `/ready`: readiness for traffic. In production, database and Redis must be
  healthy and agents must be initialized.

## CI

GitHub Actions runs on pushes and pull requests to `main`:

- install the package with dev dependencies
- compile `src` and `tests`
- run the pytest suite with mock LLM and in-memory data mode
