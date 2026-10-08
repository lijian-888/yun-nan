#!/usr/bin/env bash
# One-time setup for the isolated trial. Does not start Docker containers.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_DIR="$ROOT_DIR/deploy/ynaas-193"
ENV_FILE="$CONFIG_DIR/.env"
CERT_DIR="$CONFIG_DIR/certs"

if [[ -e "$ENV_FILE" || -e "$CERT_DIR/server.key" || -e "$CERT_DIR/server.crt" ]]; then
  echo 'Runtime secrets or certificate already exist; refusing to overwrite them.' >&2
  exit 1
fi
command -v openssl >/dev/null || { echo 'OpenSSL is required.' >&2; exit 1; }
umask 077
install -m 600 "$CONFIG_DIR/env.example" "$ENV_FILE"

for key in POSTGRES_PASSWORD APP_DATABASE_PASSWORD KEYCLOAK_DATABASE_PASSWORD \
  MINIO_ROOT_PASSWORD KEYCLOAK_ADMIN_PASSWORD INITIAL_RESEARCHER_PASSWORD \
  INITIAL_PROCESSOR_PASSWORD INITIAL_FIELD_ADMIN_PASSWORD; do
  grep -q "^${key}=CHANGE_ME_BEFORE_USE$" "$ENV_FILE" || {
    echo "Expected placeholder not found for $key; stopping." >&2
    exit 1
  }
  value="$(openssl rand -hex 24)"
  sed -i "s/^${key}=CHANGE_ME_BEFORE_USE$/${key}=${value}/" "$ENV_FILE"
done

mkdir -m 700 "$CERT_DIR"
openssl req -new -x509 -sha256 -days 365 -nodes \
  -config "$CONFIG_DIR/openssl-ip.cnf" \
  -keyout "$CERT_DIR/server.key" \
  -out "$CERT_DIR/server.crt" >/dev/null 2>&1
chmod 600 "$CERT_DIR/server.key" "$CERT_DIR/server.crt"
openssl x509 -in "$CERT_DIR/server.crt" -noout -text \
  | grep -q 'IP Address:172.16.123.193' || {
    echo 'The generated certificate lacks the expected IP SAN.' >&2
    exit 1
  }

bash "$ROOT_DIR/deploy/compose.sh" --env-file "$ENV_FILE" \
  -f "$ROOT_DIR/docker-compose.lan.yml" \
  -f "$CONFIG_DIR/compose.override.yml" config -q
echo 'Isolated runtime secrets, IP certificate and Compose configuration are ready.'
echo 'The model API key is intentionally empty. No containers were started.'
