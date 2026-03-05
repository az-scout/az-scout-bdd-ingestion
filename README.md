# az-scout-bdd-ingestion

Ingestion pipelines, batch jobs, and infrastructure for the [az-scout](https://github.com/lrivallain/az-scout) BDD-SKU ecosystem. Collects Azure VM pricing, spot eviction data, enriches SKU metadata, and aggregates statistics — all stored in PostgreSQL.

## Architecture

```
                                     ┌──────────────────────────────┐
                                     │        PostgreSQL 17         │
                                     │ (Azure Flexible Server)      │
                                     │                              │
┌────────────────────────┐  INSERT   │  retail_prices_vm            │
│  Ingestion (02:00 UTC) │──────────▸│  spot_eviction_rates         │   READ    ┌────────────────────┐
│  • Azure Pricing API   │           │  spot_price_history          │◂─────────│  az_scout_bdd_api  │
│  • Spot Eviction Rates │           │  job_runs / job_logs         │           │  (REST API)        │
│  • Spot Price History  │           │                              │           └────────────────────┘
└────────────────────────┘           │                              │
                                     │                              │
┌────────────────────────┐  INSERT   │  vm_sku_catalog              │
│  SKU Mapper (04:00)    │──────────▸│                              │
│  • Parse SKU names     │           │                              │
│  • Enrich metadata     │           │                              │
└────────────────────────┘           │                              │
                                     │                              │
┌────────────────────────┐  INSERT   │  price_summary               │
│  Price Aggregator      │──────────▸│                              │
│  (04:30 UTC)           │           │                              │
│  • Stats per region    │           │                              │
└────────────────────────┘           └──────────────────────────────┘
```

### Pipeline Schedule (UTC)

| Time | Job | Description |
|---|---|---|
| 02:00 daily | **Ingestion** | Collects retail prices + spot eviction rates from Azure public APIs |
| Every hour | **Spot historization** | Records hourly spot eviction snapshots |
| 04:00 daily | **SKU Mapper** | Parses `Standard_*` naming convention, enriches `vm_sku_catalog` |
| 04:30 daily | **Price Aggregator** | Computes pricing statistics (avg, median, p10-p90) per region/category |

## Components

| Directory | Description |
|---|---|
| `ingestion/` | One-shot CLI collecting Azure VM retail prices and spot data into PostgreSQL |
| `sku-mapper-job/` | Batch job parsing SKU names and enriching metadata |
| `price-aggregator-job/` | Batch job computing pricing statistics per region/category |
| `infra/` | Terraform IaC — PostgreSQL, Container Apps, ACR, scheduled jobs, API container |
| `sql/` | PostgreSQL schema (8 tables + indexes) |
| `postgresql/` | Docker Compose for local development |
| `migration/` | ADX → PostgreSQL historical data migration scripts |

## Prerequisites

- **Python 3.11+**
- **Docker** and **Docker Compose** v2+
- **Azure CLI** (`az login`) — for Terraform deployment
- **Terraform** 1.5+ — for infrastructure provisioning
- **uv** — Python package manager

## Local Development

### 1. Start PostgreSQL

```bash
cd postgresql/
docker compose up -d
```

This creates a PostgreSQL 17 container with database `azscout` (user/password: `azscout`), initializes the schema from `sql/schema.sql`, and exposes port 5432.

### 2. Run ingestion manually

```bash
cd ingestion/
docker build -t bdd-sku-ingestion .
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  bdd-sku-ingestion
```

### 3. Run SKU Mapper

```bash
cd sku-mapper-job/
uv sync
PGHOST=localhost PGPORT=5432 PGDATABASE=azscout PGUSER=azscout PGPASSWORD=azscout \
  uv run python -m sku_mapper_job.main
```

### 4. Run Price Aggregator

```bash
cd price-aggregator-job/
uv sync
PGHOST=localhost PGPORT=5432 PGDATABASE=azscout PGUSER=azscout PGPASSWORD=azscout \
  uv run python -m price_aggregator_job.main
```

## Deployment

### Infrastructure (Terraform)

```bash
cd infra/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your Azure subscription ID and passwords

terraform init
terraform plan
terraform apply
```

This provisions:
- **Azure Database for PostgreSQL Flexible Server** (schema auto-applied)
- **Azure Container Registry** (ACR) for Docker images
- **Azure Container Apps Environment** with:
  - Scheduled ingestion job (daily 02:00 UTC)
  - Spot historization job (hourly)
  - SKU Mapper job (daily 04:00 UTC)
  - Price Aggregator job (daily 04:30 UTC)
  - API container app (from [az_scout_bdd_api](https://github.com/rsabile/az_scout_bdd_api))
- **System-assigned Managed Identity** for passwordless DB auth

> **Note:** The API container image is built from the [az_scout_bdd_api](https://github.com/rsabile/az_scout_bdd_api) repository. Build and push the image to ACR before running `terraform apply`.

## Environment Variables

### Ingestion

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `azscout` | Database name |
| `POSTGRES_USER` | `azscout` | Database user |
| `POSTGRES_PASSWORD` | — | Database password |
| `ENABLE_AZURE_PRICING_COLLECTOR` | `false` | Enable retail pricing collector |
| `ENABLE_SPOT_COLLECTOR` | `false` | Enable spot eviction collector |
| `MAX_PRICING_ITEMS` | `-1` | Max pricing items (-1 = unlimited) |
| `MAX_SPOT_ITEMS` | `-1` | Max spot items (-1 = unlimited) |

### SKU Mapper / Price Aggregator

| Variable | Default | Description |
|---|---|---|
| `PGHOST` | `localhost` | PostgreSQL host |
| `PGPORT` | `5432` | PostgreSQL port |
| `PGDATABASE` | `az_scout` | Database name |
| `PGUSER` | `postgres` | Database user |
| `PGPASSWORD` | — | Database password |
| `PGSSLMODE` | `disable` | SSL mode |
| `LOG_LEVEL` | `info` | Log level |
| `DRY_RUN` | `false` | Parse only, skip DB writes |
| `BATCH_SIZE` | `1000` | Rows per upsert batch |

## Database Schema

The schema (`sql/schema.sql`) defines 8 tables:

| Table | Description |
|---|---|
| `job_runs` | Tracks ingestion runs per collector (status, timing, counts) |
| `job_logs` | Per-run log entries (level, message, context) |
| `retail_prices_vm` | VM retail pricing from Azure Retail Prices API |
| `spot_eviction_rates` | VM spot eviction rates from Resource Graph |
| `spot_price_history` | VM spot price history snapshots |
| `vm_sku_catalog` | Enriched SKU metadata (family, series, vCPUs, category) |
| `price_summary` | Pre-aggregated pricing stats per region/category |

## Related Repositories

- **[az_scout_bdd_api](https://github.com/rsabile/az_scout_bdd_api)** — REST API serving the data stored by this pipeline
- **[az-scout-plugin-bdd-sku](https://github.com/rsabile/az-scout-plugin-bdd-sku)** — az-scout plugin (UI + MCP tools) that consumes the API
- **[az-scout](https://github.com/lrivallain/az-scout)** — Core application

## License

[MIT](LICENSE.txt)
