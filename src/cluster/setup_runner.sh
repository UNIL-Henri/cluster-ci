#!/bin/bash
set -e

ROLE=${2:-headnode}
TARGET=$1

if [ "$ROLE" == "headnode" ] && [ -z "$TARGET" ]; then
    echo "Usage: $0 <target_repo_or_org> headnode"
    exit 1
fi

# Se placer à la racine du projet
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

echo "🎯 Préparation du Cluster pour la cible : $TARGET"

# 1. Vérification / Installation de uv
if ! command -v uv &> /dev/null; then
    echo "📦 Installation de uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env || true
else
    echo "✅ uv est déjà installé."
fi

# 1.5 Vérification / Installation de DVC
if ! command -v dvc &> /dev/null; then
    echo "📦 Installation de DVC (globale via uv)..."
    uv tool install dvc
else
    echo "✅ dvc est déjà installé."
fi

# 2. Chargement de l'environnement
if [ -f ".env" ]; then
    source .env
fi

# 2.5 Vérification des pré-requis par rôle
if [ "$ROLE" == "headnode" ]; then
    if [ -z "$GITHUB_PAT" ]; then
        echo "❌ Erreur: GITHUB_PAT non défini. Requis pour le rôle headnode."
        exit 1
    fi
fi

# 3. Préparation du dossier contenant le runner
# On télécharge une fois le runner dans un dossier template
TEMPLATE_DIR="runners/template"
mkdir -p "$TEMPLATE_DIR"

if [ ! -f "$TEMPLATE_DIR/config.sh" ]; then
    echo "⬇️ Téléchargement du binaire Runner GitHub Actions..."
    RUNNER_VERSION="2.321.0"
    curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
    tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -C "$TEMPLATE_DIR"
    rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
fi

# On prépare 2 slots pour les runners éphémères
for i in 1 2; do
    SLOT_DIR="runners/slot$i"
    if [ ! -d "$SLOT_DIR" ]; then
        echo "📂 Initialisation du slot $i..."
        cp -r "$TEMPLATE_DIR" "$SLOT_DIR"
    fi
done

# 5. Installation Systemd
if [ "$ROLE" == "headnode" ]; then
    echo "⚙️ Installation du service systemd pour le Runner Manager éphémère..."

    # Création du service systemd pour le runner manager
    cat <<EOF | sudo tee /etc/systemd/system/cluster-runner-manager.service
[Unit]
Description=Cluster-CI Ephemeral Runner Manager
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BASE_DIR
Environment="TARGET_REPO=$TARGET"
Environment="GITHUB_PAT=$GITHUB_PAT"
ExecStart=$(which python3) $BASE_DIR/src/scheduler/runner_manager.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    echo "⚙️ Configuration du Scheduler Headnode..."
    # Installation des dépendances pour le scheduler
    uv pip install flask psutil requests

    # Création du service systemd pour le scheduler API
    cat <<EOF | sudo tee /etc/systemd/system/cluster-scheduler.service
[Unit]
Description=Cluster-CI Scheduler API
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BASE_DIR
ExecStart=$(which python3) $BASE_DIR/src/scheduler/headnode_service.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    # Création du service systemd pour le scheduler loop
    cat <<EOF | sudo tee /etc/systemd/system/cluster-scheduler-loop.service
[Unit]
Description=Cluster-CI Scheduler Loop
After=cluster-scheduler.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$BASE_DIR
ExecStart=$(which python3) $BASE_DIR/src/scheduler/scheduler_loop.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable cluster-scheduler cluster-scheduler-loop cluster-runner-manager
    sudo systemctl restart cluster-scheduler cluster-scheduler-loop cluster-runner-manager
    echo "🚀 Services Scheduler et Runner Manager démarrés."

else
    echo "⚙️ Configuration du Worker Agent..."
    uv pip install requests psutil

    cat <<EOF | sudo tee /etc/systemd/system/cluster-worker.service
[Unit]
Description=Cluster-CI Worker Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BASE_DIR
EnvironmentFile=$BASE_DIR/.env
ExecStart=$(which python3) $BASE_DIR/src/scheduler/worker_agent.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable cluster-worker
    sudo systemctl restart cluster-worker
    echo "🚀 Service Worker Agent installé et démarré."
fi
echo "   Commandes utiles :"
echo "   - sudo ./svc.sh status  : Voir l'état"
echo "   - sudo ./svc.sh stop    : Arrêter"
echo "   - sudo ./svc.sh start   : Démarrer"

# 6. Lien global pour l'orchestrateur
echo "🔗 Création du lien symbolique global /usr/local/bin/cluster-ci-run..."
sudo ln -sf "$BASE_DIR/src/runner/run_research_pipeline.sh" /usr/local/bin/cluster-ci-run
