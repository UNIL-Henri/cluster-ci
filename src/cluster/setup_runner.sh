#!/bin/bash
set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <target_repo_or_org> [role]"
    echo "Roles: headnode, worker (défaut: headnode)"
    echo "Exemples :"
    echo "  Headnode : $0 hjamet/cluster-ci headnode"
    echo "  Worker   : $0 hjamet/cluster-ci worker"
    exit 1
fi

TARGET=$1
ROLE=${2:-headnode}

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

# 2. Vérification du GITHUB_PAT
if [ ! -f ".env" ]; then
    echo "❌ Erreur: Fichier .env manquant à la racine. Veuillez le créer avec GITHUB_PAT=..."
    exit 1
fi
source .env

if [ -z "$GITHUB_PAT" ]; then
    echo "❌ Erreur: GITHUB_PAT non défini dans le .env."
    exit 1
fi

# 3. Préparation du dossier contenant le runner
# On crée un dossier dédié par repo pour pouvoir en gérer plusieurs sur le compte personnel
RUNNER_DIR="runners/${TARGET//\//-}"
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ ! -f "config.sh" ]; then
    echo "⬇️ Téléchargement du binaire Runner GitHub Actions..."
    RUNNER_VERSION="2.321.0"
    curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
    tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
    rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
fi

# 4. Enregistrement dynamique via l'API GitHub
if [[ "$TARGET" == *"/"* ]]; then
    API_URL="https://api.github.com/repos/$TARGET/actions/runners/registration-token"
else
    API_URL="https://api.github.com/orgs/$TARGET/actions/runners/registration-token"
fi

echo "🔑 Récupération du Registration Token temporaire via API ($API_URL)..."
RESPONSE=$(curl -sL \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  $API_URL)

# Parse sécurisé avec python3 standard
REG_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")

if [ -z "$REG_TOKEN" ]; then
    echo "❌ Échec de récupération du token. L'API a répondu: $RESPONSE"
    echo "Vérifiez que votre PAT comporte bien le scope 'repo' et que le dépôt existe."
    exit 1
fi

echo "⚙️ Configuration du Runner local..."
./config.sh --url https://github.com/$TARGET --token $REG_TOKEN --unattended --replace --name "cluster-local-${TARGET//\//-}" --labels self-hosted,cluster-ci

echo "✅ Runner installé et configuré avec succès dans $RUNNER_DIR"
echo "👉 Pour le démarrer manuellement (Test DEV) : cd $RUNNER_DIR && ./run.sh"

# 5. Installation Systemd
if [ "$ROLE" == "headnode" ]; then
    echo "⚙️ Installation du service systemd pour le Runner GitHub..."
    sudo ./svc.sh install
    sudo ./svc.sh start
    echo "🚀 Service GitHub Runner installé et démarré."

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
    sudo systemctl enable cluster-scheduler cluster-scheduler-loop
    sudo systemctl start cluster-scheduler cluster-scheduler-loop
    echo "🚀 Services Scheduler démarrés."

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
    sudo systemctl start cluster-worker
    echo "🚀 Service Worker Agent installé et démarré."
fi
echo "   Commandes utiles :"
echo "   - sudo ./svc.sh status  : Voir l'état"
echo "   - sudo ./svc.sh stop    : Arrêter"
echo "   - sudo ./svc.sh start   : Démarrer"

# 6. Lien global pour l'orchestrateur
echo "🔗 Création du lien symbolique global /usr/local/bin/cluster-ci-run..."
sudo ln -sf "$BASE_DIR/src/runner/run_research_pipeline.sh" /usr/local/bin/cluster-ci-run
