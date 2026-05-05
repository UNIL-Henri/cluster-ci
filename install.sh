#!/bin/bash
set -e

ROLE=$1

if [[ "$ROLE" == "headnode" || "$ROLE" == "worker" ]]; then
    # --- Infrastructure Deployment (Dispatcher) ---
    echo "🏗️  Cluster-CI : Déploiement de l'Infrastructure ($ROLE)"

    if ! command -v git &> /dev/null; then
        echo "❌ Erreur : git n'est pas installé sur cette machine."
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
            echo "🔑 GITHUB_PAT non détecté."
            read -rs -p "Veuillez entrer votre GitHub PAT (avec accès repo & workflow) : " GITHUB_PAT
            echo ""
        fi
        TARGET_REPO=$2
        if [ -z "$TARGET_REPO" ]; then
            echo "🎯 Cible non détectée (owner/repo ou organisation)."
            read -p "Veuillez entrer la cible GitHub à surveiller : " TARGET_REPO
        fi

        if [ -z "$GITHUB_CLIENT_ID" ]; then
            echo "🔑 GITHUB_CLIENT_ID non détecté (Optionnel mais recommandé pour le Dashboard)."
            read -p "Veuillez entrer l'ID Client OAuth GitHub (laissez vide pour ignorer) : " GITHUB_CLIENT_ID
            echo ""
        fi
        if [ -n "$GITHUB_CLIENT_ID" ] && [ -z "$GITHUB_CLIENT_SECRET" ]; then
            echo "🔑 GITHUB_CLIENT_SECRET non détecté."
            read -rs -p "Veuillez entrer le Secret Client OAuth GitHub : " GITHUB_CLIENT_SECRET
            echo ""
        fi

        if [ -z "$GITHUB_PAT" ] || [ -z "$TARGET_REPO" ]; then
            echo "❌ Erreur : GITHUB_PAT et TARGET_REPO sont obligatoires pour un headnode."
            exit 1
        fi
    else
        if [ -z "$HEADNODE_URL" ]; then
            echo "🔗 HEADNODE_URL non détecté."
            read -p "Veuillez entrer l'URL du Headnode (ex: http://192.168.1.10:5000) : " HEADNODE_URL
        fi
        if [ -z "$CLUSTER_TOKEN" ]; then
            echo "🔑 CLUSTER_TOKEN non détecté (requis pour s'authentifier auprès du Headnode)."
            read -rs -p "Veuillez entrer le Token du Cluster : " CLUSTER_TOKEN
            echo ""
        fi

        if [ -z "$HEADNODE_URL" ] || [ -z "$CLUSTER_TOKEN" ]; then
            echo "❌ Erreur : HEADNODE_URL et CLUSTER_TOKEN sont obligatoires pour un worker."
            exit 1
        fi
    fi

    # 1. Clonage ou mise à jour du dépôt
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "📂 Clonage du dépôt dans $INSTALL_DIR..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    else
        echo "📂 Mise à jour du dépôt dans $INSTALL_DIR..."
        cd "$INSTALL_DIR" && git pull && cd - > /dev/null
    fi

    # 2. Configuration du .env (mise à jour sélective)
    echo "📝 Configuration des variables d'environnement..."
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

    # 3. Exécution du setup local
    echo "🚀 Lancement de l'installation système..."
    cd "$INSTALL_DIR"
    bash src/cluster/setup_runner.sh "$TARGET_REPO" "$ROLE"

    echo "✅ Déploiement du $ROLE terminé avec succès dans $INSTALL_DIR."

    if [ "$ROLE" == "headnode" ]; then
        IP_ADDR=$(hostname -I | awk '{print $1}')
        echo ""
        echo "🎉 Votre Headnode est prêt !"
        echo "👉 Pour ajouter des Workers, utilisez la commande suivante sur vos autres machines :"
        echo "CLUSTER_TOKEN=\"$CLUSTER_TOKEN\" HEADNODE_URL=\"http://$IP_ADDR:5000\" curl -sSL $REPO_URL/raw/main/install.sh | bash -s -- worker"
        echo ""
    fi
    exit 0

else
    # --- Client-side Installation (Research Project) ---
    echo "🚀 Cluster-CI : Installation Client"

    # 1. Vérification environnement Git
    if [ ! -d ".git" ]; then
        echo "❌ Erreur : Ce script doit être exécuté à la racine d'un dépôt Git."
        exit 1
    fi

    # 2. Injection du workflow GitHub Actions
    echo "📦 Injection du workflow GitHub Actions..."
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

      - name: Run Orchestrator
        run: /usr/local/bin/cluster-ci-run "\${{ github.repository }}" "\${{ github.head_ref || github.ref_name }}" "\${{ secrets.GITHUB_TOKEN }}"
EOF

    # 3. Injection du fichier de configuration .cluster-ci
    if [ ! -f ".cluster-ci" ]; then
        echo "📄 Création du fichier .cluster-ci..."
        cat <<EOF > .cluster-ci
# =========================================================================================
# ⚠️ AI AGENT INSTRUCTIONS (JOULES / CURSOR) ⚠️
# Ce fichier contrôle quelles étapes DVC sont exécutées sur le cluster distant.
#
# IMPORTANT : L'environnement distant a déjà toutes les variables secrètes injectées
# automatiquement (GCP_CREDENTIALS, DVC_GDRIVE_TOKEN, etc.). Tu n'as pas besoin de
# t'occuper de l'authentification ni des variables d'environnement.
#
# Laisse ce fichier vide pour exécuter tout le pipeline 'dvc repro' par défaut.
# Pour exécuter des étapes spécifiques, liste-les ci-dessous (une par ligne ou séparées par des espaces).
# Exemple :
# test_unitaires test_integration step_train
# =========================================================================================
EOF
        echo "✅ Fichier .cluster-ci créé."
    else
        echo "⚠️ Fichier .cluster-ci déjà présent, non écrasé."
    fi

    echo ""
    echo "🎉 Installation terminée !"
    echo "👉 Pensez à commiter et pusher les fichiers générés :"
    echo "   git add .github/workflows/cluster-ci.yml .cluster-ci"
    echo "   git commit -m \"docs: integration cluster-ci\""
    echo "   git push"
    echo ""
    echo "💡 Note pour l'IA Joules : Toute modification de la pipeline de recherche doit être reflétée dans .cluster-ci si vous voulez limiter l'exécution à certaines étapes."
fi
