#!/usr/bin/env bash
# Sets up the Datadog DBM monitoring user in the postgres pod.
# Usage: NAMESPACE=infra-advisor POSTGRES_USER=... POSTGRES_DB=... DD_POSTGRES_PASSWORD=... bash setup-dbm.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-infra-advisor}"

POD=$(kubectl get pod -n "$NAMESPACE" -l app=postgres -o jsonpath='{.items[0].metadata.name}')
echo "→ Using pod: $POD"

psql_exec() {
  kubectl exec -n "$NAMESPACE" "$POD" -- psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
}

# Create user only if it doesn't exist
if kubectl exec -n "$NAMESPACE" "$POD" -- psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -tAc "SELECT 1 FROM pg_roles WHERE rolname='datadog'" | grep -q 1; then
  echo "  datadog user already exists — skipping CREATE USER"
else
  kubectl exec -n "$NAMESPACE" "$POD" -- psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v "pw=$DD_POSTGRES_PASSWORD" \
    -c "CREATE USER datadog WITH PASSWORD :'pw'"
  echo "  ✓ datadog user created"
fi

# Apply grants and explain_statement function
kubectl exec -i -n "$NAMESPACE" "$POD" -- \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$(dirname "$0")/dbm-setup.sql"

echo "✓ Datadog DBM user and permissions configured"
