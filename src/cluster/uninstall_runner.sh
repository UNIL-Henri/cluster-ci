#!/bin/bash
set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <target_repo_or_org>"
    echo "Exemples :"
    echo "  Mode Dépôt  : $0 hjamet/cluster-ci"
    echo "  Mode Orga   : $0 hjamet-research"
    exit 1
fi

TARGET=$1

# Se placer à la racine du projet
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

echo "🗑️ Désinstallation du Runner pour la cible : $TARGET"

# Vérification du GITHUB_PAT
if [ ! -f ".env" ]; then
    echo "❌ Erreur: Fichier .env manquant à la racine. Requis pour désenregistrer le runner proprement de GitHub."
    exit 1
fi
source .env

if [ -z "$GITHUB_PAT" ]; then
    echo "❌ Erreur: GITHUB_PAT non défini dans le .env."
    exit 1
fi

RUNNER_DIR="runners/${TARGET//\//-}"

if [ ! -d "$RUNNER_DIR" ]; then
    echo "⚠️ Le dossier $RUNNER_DIR n'existe pas. Rien à désinstaller localement."
    exit 0
fi

cd "$RUNNER_DIR"

# 1. Arrêter et désinstaller le service Systemd
if [ -f "svc.sh" ]; then
    echo "🛑 Arrêt et désinstallation du service Systemd..."
    sudo ./svc.sh stop || true
    sudo ./svc.sh uninstall || true
fi

# 2. Désenregistrement dynamique via l'API GitHub
if [[ "$TARGET" == *"/"* ]]; then
    API_URL="https://api.github.com/repos/$TARGET/actions/runners/remove-token"
else
    API_URL="https://api.github.com/orgs/$TARGET/actions/runners/remove-token"
fi

echo "🔑 Récupération du Remove Token temporaire via API ($API_URL)..."
RESPONSE=$(curl -sL \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  $API_URL)

# Parse sécurisé avec python3 standard
REMOVE_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")

if [ -n "$REMOVE_TOKEN" ]; then
    echo "🗑️ Désenregistrement local du binaire..."
    ./config.sh remove --token "$REMOVE_TOKEN" || true
else
    echo "⚠️ Impossible d'obtenir le token de suppression. Le runner restera peut-être enregistré sur GitHub."
fi

# 3. Nettoyage local
cd "$BASE_DIR"
echo "🧹 Suppression du répertoire local $RUNNER_DIR..."
rm -rf "$RUNNER_DIR"

echo "✅ Désinstallation complète terminée."
