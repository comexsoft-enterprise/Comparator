#!/bin/bash

set -euo pipefail

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

exec ssh -i "$KEY_FILE" \
  -o ConnectTimeout=7 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=5 \
  -o ServerAliveCountMax=1 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "${USER}@${HOST}"
