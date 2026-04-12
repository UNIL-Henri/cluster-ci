#!/bin/bash
set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <owner/repo>"
    echo "Exemple: $0 hjamet/cluster-ci"
    exit 1
fi

TARGET_REPO=$1

# Se placer à la racine du projet
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

echo "🎯 Préparation du Cluster pour le dépôt : $TARGET_REPO"

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
RUNNER_DIR="runners/${TARGET_REPO//\//-}"
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
echo "🔑 Récupération du Registration Token temporaire via API..."
RESPONSE=$(curl -sL \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/$TARGET_REPO/actions/runners/registration-token)

# Parse sécurisé avec python3 standard
REG_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")

if [ -z "$REG_TOKEN" ]; then
    echo "❌ Échec de récupération du token. L'API a répondu: $RESPONSE"
    echo "Vérifiez que votre PAT comporte bien le scope 'repo' et que le dépôt existe."
    exit 1
fi

echo "⚙️ Configuration du Runner local..."
./config.sh --url https://github.com/$TARGET_REPO --token $REG_TOKEN --unattended --replace --name "cluster-local-${TARGET_REPO//\//-}" --labels self-hosted,cluster-ci

echo "✅ Runner installé et configuré avec succès dans $RUNNER_DIR"
echo "👉 Pour le démarrer manuellement (Test DEV) : cd $RUNNER_DIR && ./run.sh"

# 5. Installation Systemd
echo "⚙️ Installation du service systemd..."
sudo ./svc.sh install
sudo ./svc.sh start

echo "🚀 Service systemd installé et démarré."
echo "   Commandes utiles :"
echo "   - sudo ./svc.sh status  : Voir l'état"
echo "   - sudo ./svc.sh stop    : Arrêter"
echo "   - sudo ./svc.sh start   : Démarrer"
