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

# Pipe toute la sortie (stdout et stderr) vers la console ET vers un fichier log local
LOG_FILE="$BASE_DIR/cluster-ci-runs.log"
exec > >(tee -a "$LOG_FILE") 2>&1

function log_info() {
    echo -e "[$(date +'%Y-%m-%d %H:%M:%S')] ℹ️  $1"
}

function log_success() {
    echo -e "[$(date +'%Y-%m-%d %H:%M:%S')] ✅ $1"
}

function log_error() {
    echo -e "[$(date +'%Y-%m-%d %H:%M:%S')] ❌ $1"
}

echo "=========================================================================="
log_info "CLUSTER-CI: Début de l'orchestration GitOps Runner"
log_info "   Dépôt cible   : $TARGET_REPO"
log_info "   Branche cible : $TARGET_BRANCH"
log_info "   Dossier Run   : $BASE_DIR/$REPO_WORK_DIR"
echo "=========================================================================="

# 1. Création / bascule dans repositories/
log_info "[Etape 1/3] Initialisation du cache local..."
mkdir -p "$BASE_DIR/repositories/$(dirname "$TARGET_REPO")"
cd "$BASE_DIR/repositories/$(dirname "$TARGET_REPO")"

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
    log_info "[Etape 2/3] Premier fetch du dépôt. Clonage en cours..."
    git clone "$REPO_URL" "$REPO_BASENAME"
else
    log_info "[Etape 2/3] Dépôt existant trouvé. Mise à jour..."
fi

cd "$REPO_BASENAME"

# Force la remote URL au cas où elle aurait changé (token éphémère)
git remote set-url origin "$REPO_URL"

# Force la récupération des dernières références (on spécifie explicitement le mapping
# de la branche vers origin/branche car le fetch conditionnel GitHub Actions l'omet parfois)
log_info "Synchronisation de la référence distante origin/$TARGET_BRANCH..."
git fetch origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"

# Validation de sécurité : est-ce que la branche existe sur le remote ?
if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
    log_error "La branche origin/$TARGET_BRANCH n'existe pas ou est introuvable."
    exit 1
fi

# Basculer et reset hard pour s'assurer que l'arbre Git est propre
log_info "Checkout forcé de la branche et re-synchronisation..."
git checkout -f -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
git reset --hard "origin/$TARGET_BRANCH"

log_success "Arbre Git synchronisé. Les artefacts (.dvc/cache etc.) sont préservés pour la réutilisation."

# 3. Lancement de l'environnement uv et de l'exécution
log_info "[Etape 3/3] Synchronisation de l'environnement Python avec uv..."
if ! command -v uv &> /dev/null; then
    source "$HOME/.local/bin/env" || true
fi

if [ -f "pyproject.toml" ]; then
    uv sync
else
    log_info "Aucun fichier pyproject.toml trouvé. Etape uv sync ignorée."
fi

log_info "Exécution du pipeline de recherche asynchrone (dvc repro)..."
uv run python -c "import time; print('⏳ Lancement du test simulé DVC...'); time.sleep(2); print('✅ Vérification UV ok. DVC Repro simulé avec succès.')"

echo "=========================================================================="
log_success "CLUSTER-CI: Exécution GitOps terminée avec succès."
echo "=========================================================================="

# Tronquer log à 2000 lignes max (efface le tout début pour ne garder que la fin)
if [ -f "$LOG_FILE" ]; then
    tail -n 2000 "$LOG_FILE" > "${LOG_FILE}.tmp"
    mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
