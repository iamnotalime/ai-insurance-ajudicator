# Security Policy

## Supported Deployment

Production deployments must run with `ENVIRONMENT=production`, explicit
`ALLOWED_HOSTS`, explicit `CORS_ORIGINS`, Redis-backed rate limiting, database
persistence, and non-placeholder secrets.

## Secret Handling

Do not commit `.env` files, API keys, JWT secrets, database passwords, Redis
passwords, or encryption keys. Use a secret manager or deployment platform
environment variables.

## Authentication

The API accepts JWT bearer tokens and API keys. API keys are compared using
constant-time comparison and are fingerprinted before being used in logs or
rate-limit identifiers.

## Reporting Vulnerabilities

Open a private security advisory or contact the repository owner directly. Do
not disclose exploitable details in public issues before a fix is available.
