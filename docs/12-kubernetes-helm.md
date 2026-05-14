# Kubernetes Helm Deployment

## Goal

The sample Helm chart under `deploy/helm/aetus` provides a low-resource Kubernetes deployment path for AETUS while keeping the database boundary explicit.

The default assumption is:

- AETUS service images are pulled from GHCR.
- Kafka and Kafka Connect can run as simple self-managed single-node services for small/private deployments.
- PostgreSQL/TimescaleDB may live outside Kubernetes on a VM, physical host, or separately operated DB platform.
- Redis is disposable and can run in-cluster by default.

## Deployment Shape

```mermaid
flowchart TB
    Device["Devices / SDK clients"] --> Ingest["ingest-api Deployment"]
    Ingest --> Kafka["Kafka Deployment<br/>single-node KRaft sample"]
    Kafka --> Connect["Kafka Connect Deployment<br/>JDBC Sink"]
    Connect --> Postgres["External or in-cluster PostgreSQL"]
    Query["query-api Deployment"] --> Postgres
    Query --> Redis["Redis Deployment"]
    AnomalyApi["anomaly-api Deployment"] --> Postgres
    AnomalyWorker["anomaly-worker Deployment"] --> Postgres
    AnomalyDispatcher["anomaly-dispatcher Deployment"] --> Postgres
```

## Chart Location

- Chart: `deploy/helm/aetus`
- Default values: `deploy/helm/aetus/values.yaml`
- External DB example: `deploy/helm/aetus/examples/external-postgres-values.yaml`
- In-cluster dev DB example: `deploy/helm/aetus/examples/incluster-dev-values.yaml`
- Chart README: `deploy/helm/aetus/README.md`

## Image Source

The chart defaults to GHCR:

```yaml
global:
  imageRegistry: ghcr.io/donghoonpark
```

Each service image can be overridden independently:

```yaml
ingest:
  image:
    repository: aetus-ingest-api
    tag: main
```

For local registries or private forks, override `global.imageRegistry`, image tags, and `global.imagePullSecrets`.

## External PostgreSQL

External DB is the default production-oriented path:

```yaml
postgres:
  enabled: false

secrets:
  postgresDsn: postgresql://aetus:change-me@10.0.0.10:5432/aetus
```

The external DB must be initialized before ingest:

```bash
psql "$AETUS_POSTGRES_DSN" -f services/postgres/initdb/00-base.sql
psql "$AETUS_POSTGRES_DSN" -f services/postgres/initdb/20-anomaly.sql
```

If TimescaleDB is available:

```bash
psql "$AETUS_POSTGRES_DSN" -f services/postgres/initdb/10-timescale.sql
```

## In-Cluster PostgreSQL

For a small development cluster:

```bash
helm upgrade --install aetus ./deploy/helm/aetus \
  --namespace aetus --create-namespace \
  --set postgres.enabled=true
```

This uses the project PostgreSQL image, so schema initialization follows the existing image behavior. It is intentionally not the recommended HA production DB path.

## Control DB Choice

The ingest control DB defaults to SQLite:

```yaml
ingest:
  controlDb:
    backend: sqlite
```

This is acceptable for one minimal ingest pod. Before scaling ingest replicas, switch it to PostgreSQL:

```yaml
ingest:
  replicas: 2
  controlDb:
    backend: postgres
    schema: control
```

The chart passes the same PostgreSQL DSN into `AETUS_CONTROL_DATABASE_URL` so the ingest service can use PostgreSQL-backed control tables.

## Minimum Resource Defaults

| Component | Request | Default replicas |
| --- | ---: | ---: |
| ingest-api | `50m / 128Mi` | 1 |
| query-api | `50m / 128Mi` | 1 |
| anomaly-api | `50m / 128Mi` | 1 |
| anomaly-worker | `50m / 128Mi` | 1 |
| anomaly-dispatcher | `25m / 96Mi` | 1 |
| Kafka | `250m / 512Mi` | 1 |
| Kafka Connect | `250m / 512Mi` | 1 |
| Redis | `25m / 64Mi` | 1 |
| PostgreSQL | `100m / 256Mi` | disabled |

## Operations Notes

- The chart is a sample deployment baseline, not a full HA production platform.
- Kafka and Kafka Connect are included because AETUS currently assumes that ingestion to DB is mediated by Kafka Connect JDBC Sink.
- PostgreSQL retention, Timescale compression, and hypertable policies remain DB-side concerns initialized by SQL files.
- `connector-init` runs as a Helm hook Job and registers the three JDBC sinks for raw events, metric staging, and signal frame staging.
- Ingress is disabled by default. Enable it only after deciding cluster ingress class, hostnames, and TLS policy.
- For isolated/private networks, HTTP-only internal service traffic remains possible. For public or shared clusters, terminate TLS at ingress and use the HMAC-required ingest option.
