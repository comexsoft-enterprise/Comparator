#!/bin/bash

set -euo pipefail

# Port-forward services running on the comparator EC2 instance to localhost.
# Uses Pulumi stack outputs for host + SSH key.

STACK=${PULUMI_STACK:-prod}
USER=${EC2_USER:-root}

HOST=$(pulumi stack output -s "$STACK" public_dns || true)
if [[ -z "${HOST}" ]]; then
  HOST=$(pulumi stack output -s "$STACK" public_ip)
fi

KEY_FILE=$(pulumi stack output -s "$STACK" --show-secrets private_key_path)

if [[ -z "${HOST}" ]]; then
  echo "ERROR: No host found in Pulumi outputs (public_dns/public_ip)." >&2
  echo "Run: pulumi stack output -s ${STACK} --json" >&2
  exit 1
fi

# Ports forwarded locally (default binds to 127.0.0.1 only):
# - MongoDB:    27017
# - Postgres:   5432
# - Neo4j Bolt: 7687
# - Neo4j HTTP: 7474
# - Comparator API (optional): 8000
#
# If you want to access this tunnel from another machine (e.g., over Tailscale),
# set one of these:
# - `BIND_ADDR=<ip>` (recommended; e.g. your tailscale IP from `ip a`, like 100.x.y.z)
# - `BIND_ALL=1` (bind 0.0.0.0; less safe)

BIND_ADDR=${BIND_ADDR:-}
if [[ -z "${BIND_ADDR}" && -n "${BIND_ALL:-}" ]]; then
  BIND_ADDR="0.0.0.0"
fi

SSH_BIND_FLAGS=()
PF_HOST="127.0.0.1"
if [[ -n "${BIND_ADDR}" ]]; then
  # Allow remote hosts to connect to the forwarded local ports.
  SSH_BIND_FLAGS+=("-g")
  PF_HOST="${BIND_ADDR}"
fi

ssh -i "$KEY_FILE" -N \
  -o ConnectTimeout=10 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=10 \
  -o ServerAliveCountMax=1 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "${SSH_BIND_FLAGS[@]}" \
  -L ${PF_HOST}:27017:127.0.0.1:27017 \
  -L ${PF_HOST}:5432:127.0.0.1:5432 \
  -L ${PF_HOST}:7687:127.0.0.1:7687 \
  -L ${PF_HOST}:7474:127.0.0.1:7474 \
  ${FORWARD_API:+-L ${PF_HOST}:8000:127.0.0.1:8000} \
  "${USER}@${HOST}"
