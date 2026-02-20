#!/bin/bash

# Build + deploy comparator NixOS config to EC2 (prefect-server style)

set -euo pipefail

STACK=${PULUMI_STACK:-prod}

PUBLIC_IP=$(pulumi stack output -s "$STACK" public_ip)
KEY_FILE=$(pulumi stack output -s "$STACK" --show-secrets private_key_path)
REMOTE_HOST="root@${PUBLIC_IP}"

echo "Step 1: Building NixOS configuration..."
STORE_PATH=$(nix build ./nix#nixosConfigurations.ec2.config.system.build.toplevel --print-out-paths)
echo "Build complete. System path: ${STORE_PATH}"

echo "Step 2: Copying system closure to ${REMOTE_HOST}..."
eval "$(ssh-agent -s)"
ssh-add "$KEY_FILE"
nix-copy-closure --to "${REMOTE_HOST}" "${STORE_PATH}"

echo "Step 3: Setting the new system profile on ${REMOTE_HOST}..."
ssh -i "${KEY_FILE}" "${REMOTE_HOST}" "sudo nix-env --profile /nix/var/nix/profiles/system --set ${STORE_PATH}"

echo "Step 4: Activating the new configuration on ${REMOTE_HOST}..."
ssh -i "${KEY_FILE}" "${REMOTE_HOST}" "sudo /nix/var/nix/profiles/system/bin/switch-to-configuration switch"

echo "Deployment finished successfully!"
