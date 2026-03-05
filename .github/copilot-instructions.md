# Copilot Instructions for az-scout-bdd-ingestion

## Project overview

Ingestion pipelines, batch jobs, and infrastructure for the az-scout BDD-SKU ecosystem. Collects Azure VM pricing and spot data, enriches SKU metadata, and computes pricing statistics — all stored in PostgreSQL.

## Tech stack

- **Backend:** Python 3.11+, psycopg2/psycopg3, requests
- **Infrastructure:** Terraform 1.5+ (Azure PostgreSQL Flexible Server, Container Apps, ACR)
- **Database:** PostgreSQL 17+
- **Containers:** Docker (python:3.12-slim)
- **Tools:** uv (package manager), ruff (lint + format), mypy, pytest

## Project structure

```
ingestion/                    # Azure pricing + spot data collector CLI
├── app/
│   ├── main.py              # Entry point
│   ├── collectors/          # Pricing + spot collectors
│   ├── core/                # DB, logging, config
│   └── shared/              # Shared utilities
├── Dockerfile
└── pyproject.toml

sku-mapper-job/               # SKU naming convention parser + enrichment
├── src/sku_mapper_job/
├── tests/
├── Dockerfile
└── pyproject.toml

price-aggregator-job/         # Pricing statistics aggregator
├── src/price_aggregator_job/
├── tests/
├── Dockerfile
└── pyproject.toml

infra/                        # Terraform infrastructure
├── main.tf                  # Provider + resource group
├── container-apps.tf        # Container Apps + scheduled jobs
├── variables.tf             # Input variables
├── outputs.tf               # Outputs
└── terraform.tfvars.example # Example config

sql/
└── schema.sql               # Full PostgreSQL schema (8 tables)

postgresql/
├── docker-compose.yml       # Local dev PostgreSQL
└── plugin-config.example.toml

migration/                    # ADX → PostgreSQL migration scripts
├── migrate_adx_to_pg.py
├── alter_unique.sql
└── requirements.txt
```

## Code conventions

- **Python:** All functions must have type annotations. Follow ruff rules: `E, F, I, W, UP, B, SIM`. Line length is 100.
- **Terraform:** Use consistent variable naming with descriptions and defaults.
- **SQL:** Use `IF NOT EXISTS` for all CREATE statements. Proper indexing for query patterns.

## Pipeline schedule

| Time (UTC) | Job | Cron |
|---|---|---|
| 02:00 daily | Ingestion (pricing + spot) | `0 2 * * *` |
| Every hour | Spot historization | `0 * * * *` |
| 04:00 daily | SKU Mapper | `0 4 * * *` |
| 04:30 daily | Price Aggregator | `30 4 * * *` |

## Database patterns

- All jobs connect to PostgreSQL via environment variables (PG* or POSTGRES_*).
- Auth supports password and Azure Managed Identity (MSI).
- Schema is in `sql/schema.sql` — mounted into PostgreSQL container at init.
- Jobs track progress in `job_runs` and `job_logs` tables.
- Use `ON CONFLICT` for idempotent upserts.

## Testing patterns

- Each sub-package has its own `tests/` directory.
- Tests use `unittest.mock.patch` for external dependencies.
- Run with: `cd <job-dir> && uv run pytest`

## Design constraints

### Do

- Keep each job self-contained with its own Dockerfile and pyproject.toml.
- Use batch operations for large inserts.
- Track job progress in `job_runs` table.
- Handle per-item failures without aborting the entire job.
- Use environment variables for all configuration.

### Do NOT

- Do NOT share code between jobs (each is independently deployable).
- Do NOT call Azure ARM APIs that require authentication in the pricing collector.
- Do NOT modify the database schema without updating `sql/schema.sql`.
- Do NOT hardcode connection strings or credentials.
- Do NOT introduce global mutable state.
