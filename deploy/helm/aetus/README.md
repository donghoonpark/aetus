# AETUS Helm Chart

This chart deploys the AETUS backend stack with small default resource requests.

It uses published GHCR images by default:

- `ghcr.io/donghoonpark/aetus-ingest-api:main`
- `ghcr.io/donghoonpark/aetus-query-api:main`
- `ghcr.io/donghoonpark/aetus-anomaly:main`
- `ghcr.io/donghoonpark/aetus-kafka:main`
- `ghcr.io/donghoonpark/aetus-kafka-connect:main`
- `ghcr.io/donghoonpark/aetus-postgres:main`

## Minimal External PostgreSQL Install

Use this path when PostgreSQL/TimescaleDB is operated on a VM, physical machine, or separately managed database.

```bash
helm upgrade --install aetus ./deploy/helm/aetus \
  --namespace aetus --create-namespace \
  -f deploy/helm/aetus/examples/external-postgres-values.yaml
```

Initialize the database schema before ingesting data:

```bash
psql 'postgresql://aetus:change-me@10.0.0.10:5432/aetus' -f services/postgres/initdb/00-base.sql
psql 'postgresql://aetus:change-me@10.0.0.10:5432/aetus' -f services/postgres/initdb/20-anomaly.sql
```

If the external DB is TimescaleDB, also apply:

```bash
psql 'postgresql://aetus:change-me@10.0.0.10:5432/aetus' -f services/postgres/initdb/10-timescale.sql
```

## In-Cluster Development PostgreSQL

This is useful for a small test cluster. For real data, prefer external PostgreSQL or a separately operated database chart/operator.

```bash
helm upgrade --install aetus ./deploy/helm/aetus \
  --namespace aetus --create-namespace \
  -f deploy/helm/aetus/examples/incluster-dev-values.yaml
```

## Using an Existing Secret

Create a secret with the keys used by the chart:

```bash
kubectl create secret generic aetus-secrets -n aetus \
  --from-literal=bootstrap-token='bootstrap_shared_token' \
  --from-literal=device-tokens='esp32c5-test-001=devtok_test_001' \
  --from-literal=admin-password='' \
  --from-literal=query-jwt-secret='replace-with-32-byte-min-secret' \
  --from-literal=query-admin-token='replace-query-admin-token' \
  --from-literal=anomaly-admin-token='replace-anomaly-admin-token' \
  --from-literal=postgres-password='change-me' \
  --from-literal=postgres-dsn='postgresql://aetus:change-me@10.0.0.10:5432/aetus'
```

Then install with:

```bash
helm upgrade --install aetus ./deploy/helm/aetus \
  --namespace aetus --create-namespace \
  --set secrets.create=false \
  --set secrets.existingSecret=aetus-secrets
```

## Port Forward

```bash
kubectl -n aetus port-forward svc/aetus-ingest-api 18000:8000
kubectl -n aetus port-forward svc/aetus-query-api 18001:8000
kubectl -n aetus port-forward svc/aetus-anomaly-api 18002:8000
```

## Minimal Resource Profile

The default chart runs one replica per service:

| Component | Default request | Notes |
| --- | ---: | --- |
| Ingest API | `50m / 128Mi` | One pod, SQLite control DB by default |
| Query API | `50m / 128Mi` | Uses Redis for query cache |
| Anomaly API | `50m / 128Mi` | Rust HTTP API |
| Anomaly worker | `50m / 128Mi` | DB-backed detector worker |
| Anomaly dispatcher | `25m / 96Mi` | Webhook outbox dispatcher |
| Kafka | `250m / 512Mi` | Single-node KRaft, sample only |
| Kafka Connect | `250m / 512Mi` | Single JDBC sink worker |
| Redis | `25m / 64Mi` | Disposable query cache |
| PostgreSQL | `100m / 256Mi` | Disabled by default |

Before scaling ingest replicas, switch the control DB from SQLite to PostgreSQL:

```bash
helm upgrade --install aetus ./deploy/helm/aetus \
  --namespace aetus \
  --set ingest.controlDb.backend=postgres \
  --set ingest.controlDb.schema=control
```

## Important Limitations

- The bundled Kafka and Kafka Connect deployments are minimum self-managed samples, not a HA Kafka platform.
- External PostgreSQL must be reachable from the cluster and must be initialized with the AETUS schema.
- `postgres.enabled=true` is intended for development or small private deployments.
- If GHCR images are private in your fork, configure `global.imagePullSecrets`.
