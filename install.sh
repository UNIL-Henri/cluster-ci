#!/bin/bash
set -e

ROLE=$1

if [[ "$ROLE" == "headnode" || "$ROLE" == "worker" ]]; then
    # --- Infrastructure Deployment (Dispatcher) ---
    echo "🏗️  Cluster-CI: Infrastructure Deployment ($ROLE)"

    if ! command -v git &> /dev/null; then
        echo "❌ Error: git is not installed on this machine."
        exit 1
    fi

    INSTALL_DIR=${INSTALL_DIR:-"$HOME/cluster-ci"}
    REPO_URL="https://github.com/UNIL-DESI/cluster-ci.git"

    # Charger l'existant si disponible
    if [ -f "$INSTALL_DIR/.env" ]; then
        # On extrait proprement pour éviter de sourcer n'importe quoi
        [ -z "$GITHUB_PAT" ] && GITHUB_PAT=$(grep "^GITHUB_PAT=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$HEADNODE_URL" ] && HEADNODE_URL=$(grep "^HEADNODE_URL=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$CLUSTER_TOKEN" ] && CLUSTER_TOKEN=$(grep "^CLUSTER_TOKEN=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$GITHUB_CLIENT_ID" ] && GITHUB_CLIENT_ID=$(grep "^GITHUB_CLIENT_ID=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$GITHUB_CLIENT_SECRET" ] && GITHUB_CLIENT_SECRET=$(grep "^GITHUB_CLIENT_SECRET=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
        [ -z "$DOCKER_BASE_IMAGE" ] && DOCKER_BASE_IMAGE=$(grep "^DOCKER_BASE_IMAGE=" "$INSTALL_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi

    if [ "$ROLE" == "headnode" ]; then
        if [ -z "$GITHUB_PAT" ]; then
            echo "🔑 GITHUB_PAT not detected."
            read -rs -p "Please enter your GitHub PAT (with repo & workflow access): " GITHUB_PAT
            echo ""
        fi
        TARGET_REPO=$2
        if [ -z "$TARGET_REPO" ]; then
            echo "🎯 Target not detected (owner/repo or organization)."
            read -p "Please enter the GitHub target to monitor: " TARGET_REPO
        fi

        if [ -z "$GITHUB_CLIENT_ID" ]; then
            echo "🔑 GITHUB_CLIENT_ID not detected (Optional but recommended for the Dashboard)."
            read -p "Please enter the GitHub OAuth Client ID (leave empty to skip): " GITHUB_CLIENT_ID
            echo ""
        fi
        if [ -n "$GITHUB_CLIENT_ID" ] && [ -z "$GITHUB_CLIENT_SECRET" ]; then
            echo "🔑 GITHUB_CLIENT_SECRET not detected."
            read -rs -p "Please enter the GitHub OAuth Client Secret: " GITHUB_CLIENT_SECRET
            echo ""
        fi

        if [ -z "$GITHUB_PAT" ] || [ -z "$TARGET_REPO" ]; then
            echo "❌ Error: GITHUB_PAT and TARGET_REPO are required for a headnode."
            exit 1
        fi
    else
        if [ -z "$HEADNODE_URL" ]; then
            echo "🔗 HEADNODE_URL not detected."
            read -p "Please enter the Headnode URL (e.g., http://192.168.1.10:5000): " HEADNODE_URL
        fi
        if [ -z "$CLUSTER_TOKEN" ]; then
            echo "🔑 CLUSTER_TOKEN not detected (required to authenticate with the Headnode)."
            read -rs -p "Please enter the Cluster Token: " CLUSTER_TOKEN
            echo ""
        fi

        if [ -z "$HEADNODE_URL" ] || [ -z "$CLUSTER_TOKEN" ]; then
            echo "❌ Error: HEADNODE_URL and CLUSTER_TOKEN are required for a worker."
            exit 1
        fi
    fi

    # 1. Clone or update the repository
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "📂 Cloning repository into $INSTALL_DIR..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    else
        echo "📂 Updating repository in $INSTALL_DIR..."
        cd "$INSTALL_DIR" && git pull && cd - > /dev/null
    fi

    # 2. .env configuration (selective update)
    echo "📝 Configuring environment variables..."
    mkdir -p "$INSTALL_DIR"
    TOUCH_ENV="$INSTALL_DIR/.env"
    [ ! -f "$TOUCH_ENV" ] && touch "$TOUCH_ENV"

    update_env_var() {
        local var_name=$1
        local var_value=$2
        if [ -n "$var_value" ]; then
            if grep -q "^$var_name=" "$TOUCH_ENV"; then
                # Remplacement portable de sed -i (compatible macOS/Linux)
                local tmp_env=$(mktemp)
                grep -v "^$var_name=" "$TOUCH_ENV" > "$tmp_env"
                echo "$var_name=$var_value" >> "$tmp_env"
                mv "$tmp_env" "$TOUCH_ENV"
            else
                echo "$var_name=$var_value" >> "$TOUCH_ENV"
            fi
        fi
    }

    if [ "$ROLE" == "headnode" ] && [ -z "$CLUSTER_TOKEN" ] && [ ! -f "$INSTALL_DIR/.env" ]; then
        # Génération d'un token aléatoire pour le cluster
        CLUSTER_TOKEN=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)
    fi

    update_env_var "GITHUB_PAT" "$GITHUB_PAT"
    update_env_var "HEADNODE_URL" "$HEADNODE_URL"
    update_env_var "CLUSTER_TOKEN" "$CLUSTER_TOKEN"
    update_env_var "GITHUB_CLIENT_ID" "$GITHUB_CLIENT_ID"
    update_env_var "GITHUB_CLIENT_SECRET" "$GITHUB_CLIENT_SECRET"

    # Default Docker image for NVIDIA ARM (Jetson/Grace)
    [ -z "$DOCKER_BASE_IMAGE" ] && DOCKER_BASE_IMAGE="nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3"
    update_env_var "DOCKER_BASE_IMAGE" "$DOCKER_BASE_IMAGE"

    # 3. Local setup execution
    echo "🚀 Starting system installation..."
    cd "$INSTALL_DIR"
    bash src/cluster/setup_runner.sh "$TARGET_REPO" "$ROLE"

    echo "✅ $ROLE deployment completed successfully in $INSTALL_DIR."

    if [ "$ROLE" == "headnode" ]; then
        IP_ADDR=$(hostname -I | awk '{print $1}')
        echo ""
        echo "🎉 Your Headnode is ready!"
        echo "👉 To add Workers, use the following command on your other machines:"
        echo "CLUSTER_TOKEN=\"$CLUSTER_TOKEN\" HEADNODE_URL=\"http://$IP_ADDR:5000\" curl -sSL $REPO_URL/raw/main/install.sh | bash -s -- worker"
        echo ""
    fi
    exit 0

else
    # --- Client-side Installation (Research Project) ---
    echo "🚀 Cluster-CI: Client Installation"

    # 1. Git environment check
    if [ ! -d ".git" ]; then
        echo "❌ Error: This script must be run at the root of a Git repository."
        exit 1
    fi

    # 2. GitHub Actions workflow injection
    echo "📦 Injecting GitHub Actions workflow..."
    mkdir -p .github/workflows

    cat <<EOF > .github/workflows/cluster-ci.yml
name: Cluster-CI Execution

# =========================================================================================
# ⚠️ AI AGENT INSTRUCTIONS (JOULES / CURSOR) ⚠️
# DO NOT MODIFY THIS FILE.
#
# This workflow is managed by the external Cluster-CI orchestrator. It executes
# the pipeline in a fully configured environment where dependencies (uv) and
# SECRETS (like DVC_GDRIVE_CREDENTIALS) are automatically injected by the runner.
#
# If you need to run tests or research scripts, DO NOT create a separate workflow
# file running native shell commands (like 'pytest' or 'uv run ...'). Native steps
# won't have access to the cluster's secure environment variables.
#
# INSTEAD:
# 1. Create a DVC stage in 'dvc.yaml' (e.g., 'dvc stage add -n test pytest ...').
# 2. Put the stage name(s) in the '.cluster-ci' file at the root of the repo.
# 3. The orchestrator will automatically run 'dvc repro <stage>' with all secrets loaded.
# =========================================================================================

on:
  push:
    branches: [ main, master ]
  pull_request:
    branches: [ main, master ]
  workflow_dispatch:

concurrency:
  group: \${{ github.repository }}
  cancel-in-progress: true

jobs:
  execute-on-cluster:
    name: Execute Research Pipeline
    runs-on: self-hosted
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: GPU Hardware Validation
        run: |
          echo "🚀 Starting GPU healthcheck..."
          docker run --rm --gpus all nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3 \
            python3 -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); assert torch.cuda.is_available()"

      - name: Run Orchestrator
        run: /usr/local/bin/cluster-ci-run "\${{ github.repository }}" "\${{ github.head_ref || github.ref_name }}" "\${{ secrets.GITHUB_TOKEN }}"
EOF

    # 3. .cluster-ci configuration file injection
    if [ ! -f ".cluster-ci" ]; then
        echo "📄 Creating .cluster-ci file..."
        cat <<EOF > .cluster-ci
# =========================================================================================
# ⚠️ AI AGENT INSTRUCTIONS (JOULES / CURSOR) ⚠️
# This file controls which DVC stages are executed on the remote cluster.
#
# IMPORTANT: The remote environment already has all secret variables injected
# automatically (GCP_CREDENTIALS, DVC_GDRIVE_TOKEN, etc.). You don't need to
# worry about authentication or environment variables.
#
# Leave this file empty to run the full 'dvc repro' pipeline by default.
# To run specific stages, list them below (one per line or separated by spaces).
# Example:
# unit_tests integration_tests train_step
# =========================================================================================
EOF
        echo "✅ .cluster-ci file created."
    else
        echo "⚠️ .cluster-ci file already present, not overwritten."
    fi

    echo ""
    echo "🎉 Installation complete!"
    echo "👉 Remember to commit and push the generated files:"
    echo "   git add .github/workflows/cluster-ci.yml .cluster-ci"
    echo "   git commit -m \"docs: cluster-ci integration\""
    echo "   git push"
    echo ""
    echo "💡 Note for Joules AI: Any modification to the research pipeline must be reflected in .cluster-ci if you want to limit execution to specific stages."
fi
