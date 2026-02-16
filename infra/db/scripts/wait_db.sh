mkdir -p scripts
cat > scripts/wait_db.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${1:-fundlink-db}"
DB_USER="${2:-fundlink}"
DB_NAME="${3:-fundlink}"
TIMEOUT_SEC="${4:-60}"

start_ts=$(date +%s)
echo "Waiting for Postgres to be ready... container=${CONTAINER_NAME} user=${DB_USER} db=${DB_NAME} timeout=${TIMEOUT_SEC}s"

while true; do
  if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
    echo "Postgres is accepting connections."
    exit 0
  fi

  now_ts=$(date +%s)
  if (( now_ts - start_ts > TIMEOUT_SEC )); then
    echo "ERROR: timed out waiting for Postgres readiness." >&2
    docker logs --tail 80 "${CONTAINER_NAME}" || true
    exit 1
  fi

  sleep 1
done
EOF

chmod +x scripts/wait_db.sh

