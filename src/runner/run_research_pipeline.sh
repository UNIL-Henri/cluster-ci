#!/bin/bash
set -e

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <owner/repo> <branch_name>"
    echo "Exemple: $0 hjamet/llm-as-recommender main"
    exit 1
fi

TARGET_REPO=$1
TARGET_BRANCH=$2
GH_TOKEN=$3

# Se placer à la racine du projet cluster-ci
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

REPO_WORK_DIR="repositories/$TARGET_REPO"

echo "=================================================="
echo "🚀 CLUSTER-CI: Début de l'exécution"
echo "📦 Dépôt cible  : $TARGET_REPO"
echo "🌿 Branche cible: $TARGET_BRANCH"
echo "📂 Dossier local: $BASE_DIR/$REPO_WORK_DIR"
echo "=================================================="

# 1. Création / bascule dans repositories/
mkdir -p "$BASE_DIR/repositories"
cd "$BASE_DIR/repositories"

# Extraire juste le nom final du repo pour le dossier (ex: llm-as-recommender)
REPO_BASENAME=$(basename "$TARGET_REPO")

if [ -n "$GH_TOKEN" ]; then
    # Authentification https silencieuse pour GitHub Actions
    REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${TARGET_REPO}.git"
else
    REPO_URL="https://github.com/${TARGET_REPO}.git"
fi

# 2. Gestion de l'état Git
if [ ! -d "$REPO_BASENAME/.git" ]; then
    echo "⏳ Premier fetch du dépôt. Clonage..."
    git clone "$REPO_URL" "$REPO_BASENAME"
fi

cd "$REPO_BASENAME"

# Force la remote URL au cas où elle aurait changé (token éphémère)
git remote set-url origin "$REPO_URL"

# Force la récupération des dernières références
echo "🔄 Synchronisation de la branche: $TARGET_BRANCH"
git fetch origin

# Validation de sécurité : est-ce que la branche existe sur le remote ?
if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
    echo "❌ Erreur: La branche origin/$TARGET_BRANCH n'existe pas."
    exit 1
fi

# Basculer et reset hard pour s'assurer que l'arbre Git est propre
# (Note: Cela n'affecte pas les fichiers DVC non-trackés par Git !)
git checkout -f -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
git reset --hard "origin/$TARGET_BRANCH"

echo "✅ Arbre Git synchronisé. Les artefacts (.dvc/cache etc.) sont préservés."

# 3. Lancement de l'environnement uv et de l'exécution
echo "🐍 Synchronisation de l'environnement Python avec uv..."
if ! command -v uv &> /dev/null; then
    # Essayer de charger le path par défaut de uv si installé silencieusement
    source "$HOME/.local/bin/env" || true
fi

uv sync

echo "⚙️ Exécution du job asynchrone (dvc repro)..."
# (Espace réservé à l'insertion du auth DVC plus tard)
uv run python -c "print('Vérification UV ok. DVC Repro simulé avec succès.')"
# En vrai, on fera : uv run dvc repro quand le remote DVC sera testable !

echo "🎉 CLUSTER-CI: Exécution terminée avec succès."
