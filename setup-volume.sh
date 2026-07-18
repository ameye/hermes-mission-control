#!/bin/bash
# One-time setup: create a Docker named volume pointing to your Hermes data.
# Run this ONCE on your Contabo VPS via SSH.
#
# Usage:
#   ssh user@contabo
#   bash /path/to/setup-volume.sh

set -e

HERMES_HOST_PATH="${1:-/home/hermeswebui/.hermes}"

echo "🔍 Checking path: $HERMES_HOST_PATH"

if [ ! -d "$HERMES_HOST_PATH" ]; then
  echo "❌ Directory does not exist: $HERMES_HOST_PATH"
  echo "Usage: $0 /path/to/your/.hermes"
  exit 1
fi

echo "✅ Directory exists"
ls -la "$HERMES_HOST_PATH" | head -5

echo ""
echo "🔧 Creating Docker volume: hermes-live-data"

docker volume create \
  --driver local \
  --opt type=none \
  --opt device="$HERMES_HOST_PATH" \
  --opt o=bind \
  hermes-live-data

echo ""
echo "✅ Volume created!"
docker volume inspect hermes-live-data

echo ""
echo "🚀 Now deploy your Mission Control container via Coolify."
echo "   The compose file references 'hermes-live-data' as an external volume."
