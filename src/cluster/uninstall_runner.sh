#!/bin/bash
set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <target_repo_or_org>"
    echo "Examples:"
    echo "  Repo Mode: $0 hjamet/cluster-ci"
    echo "  Org Mode : $0 hjamet-research"
    exit 1
fi

TARGET=$1

# Go to project root
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

echo "🗑️ Uninstalling Runner for target: $TARGET"

# GITHUB_PAT verification
if [ ! -f ".env" ]; then
    echo "❌ Error: Missing .env file at root. Required to properly unregister the runner from GitHub."
    exit 1
fi
source .env

if [ -z "$GITHUB_PAT" ]; then
    echo "❌ Error: GITHUB_PAT not defined in .env."
    exit 1
fi

RUNNER_DIR="runners/${TARGET//\//-}"

if [ ! -d "$RUNNER_DIR" ]; then
    echo "⚠️ Folder $RUNNER_DIR does not exist. Nothing to uninstall locally."
    exit 0
fi

cd "$RUNNER_DIR"

# 1. Stop and uninstall Systemd service
if [ -f "svc.sh" ]; then
    echo "🛑 Stopping and uninstalling Systemd service..."
    sudo ./svc.sh stop || true
    sudo ./svc.sh uninstall || true
fi

# 2. Dynamic unregistration via GitHub API
if [[ "$TARGET" == *"/"* ]]; then
    API_URL="https://api.github.com/repos/$TARGET/actions/runners/remove-token"
else
    API_URL="https://api.github.com/orgs/$TARGET/actions/runners/remove-token"
fi

echo "🔑 Retrieving temporary Remove Token via API ($API_URL)..."
RESPONSE=$(curl -sL \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  $API_URL)

# Secure parse with standard python3
REMOVE_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")

if [ -n "$REMOVE_TOKEN" ]; then
    echo "🗑️ Local unregistration of the binary..."
    ./config.sh remove --token "$REMOVE_TOKEN" || true
else
    echo "⚠️ Unable to obtain removal token. The runner might remain registered on GitHub."
fi

# 3. Local cleanup
cd "$BASE_DIR"
echo "🧹 Removing local directory $RUNNER_DIR..."
rm -rf "$RUNNER_DIR"

echo "✅ Full uninstallation complete."
