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
SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
BASE_DIR="$( cd "$( dirname "$SCRIPT_PATH" )/../.." >/dev/null 2>&1 && pwd )"
cd "$BASE_DIR"

# Injection des variables d'environnement globales (.env et .env.secrets)
if [ -f "$BASE_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$BASE_DIR/.env" || true
    set +a
fi
if [ -f "$BASE_DIR/.env.secrets" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$BASE_DIR/.env.secrets" || true
    set +a
fi

# Mode délégation : Si on n'est pas explicitement en mode exécuteur,
# on délègue la tâche à l'ordonnanceur via submit_job.py
if [ "$CLUSTER_CI_MODE" != "executor" ]; then
    echo "🌐 Mode Délégation activé. Soumission du job à l'ordonnanceur..."
    python3 "$BASE_DIR/src/scheduler/submit_job.py" "$TARGET_REPO" "$TARGET_BRANCH"
    exit $?
fi

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

# 1.5 JIT Garbage Collection & Metadata update
log_info "[Etape 1.5/3] Gestion du ramasse-miettes (GC) JIT..."
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" run-gc
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-running "$TARGET_REPO"

function update_status_idle() {
    log_info "Mise à jour des métadonnées (statut idle)..."
    [ -n "$DVC_VIEWER_PID" ] && kill -9 "$DVC_VIEWER_PID" 2>/dev/null || true
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-idle "$TARGET_REPO" "$BASE_DIR/repositories/$TARGET_REPO"
}
trap update_status_idle EXIT

# 2. Purge préventive & Gestion de l'état Git
log_info "[Etape 2/3] Purge préventive des processus dvc-viewer résiduels..."
# On cherche les processus dvc-viewer dont le CWD correspond au répertoire de travail du projet
for pid in $(pgrep -f "dvc-viewer" || true); do
    if pwdx "$pid" 2>/dev/null | grep -q ": $BASE_DIR/$REPO_WORK_DIR$"; then
        log_info "Nettoyage du processus dvc-viewer fantôme (PID: $pid)..."
        kill -9 "$pid" 2>/dev/null || true
    fi
done

if [ ! -d "$REPO_BASENAME/.git" ]; then
    log_info "[Etape 2.1/3] Premier fetch du dépôt. Clonage en cours..."
    git clone "$REPO_URL" "$REPO_BASENAME"
else
    log_info "[Etape 2.1/3] Dépôt existant trouvé. Mise à jour..."
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

# Enregistrement du hash du commit courant pour la traçabilité
git rev-parse HEAD > .cluster-ci-commit

log_success "Arbre Git synchronisé. Les artefacts (.dvc/cache etc.) sont préservés pour la réutilisation."

# Enregistrement du hash du commit courant pour la traçabilité
git rev-parse HEAD > .cluster-ci-commit

# 3. Lancement de l'environnement uv et de l'exécution
log_info "[Etape 3/3] Synchronisation de l'environnement Python avec uv..."

# Injection des variables d'environnement globales (.env et .env.secrets)
log_info "Chargement des credentials globaux pour l'exécution..."


log_info "Variables d'environnement disponibles :"
while IFS='=' read -r name value; do
    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then continue; fi
    if [[ -z "$value" ]]; then
        log_info "   $name=(vide)"
    else
        first_chars="${value:0:3}"
        log_info "   $name=${first_chars}***"
    fi
done < <(env | sort)

if ! command -v uv &> /dev/null; then
    source "$HOME/.local/bin/env" || true
fi

if [ -f "pyproject.toml" ]; then
    uv sync
else
    log_info "Aucun fichier pyproject.toml trouvé. Etape uv sync ignorée."
fi

log_info "Installation de dvc-viewer..."
uv pip install git+https://github.com/UNIL-DESI/dvc-viewer.git

if [ ! -f ".cluster-ci" ]; then
    log_error "Fichier .cluster-ci introuvable à la racine du dépôt. Exécution avortée."
    exit 1
fi

log_info "Lecture des paramètres depuis .cluster-ci..."
# Nettoyage des commentaires, suppression des flags internes comme --ram, et mise en une seule ligne des arguments
DVC_ARGS=$(grep -v '^\s*#' .cluster-ci | sed 's/--ram [0-9.]*//g' | tr '\n' ' ' | xargs)

if [ -z "$DVC_ARGS" ]; then
    log_info "Aucun argument spécifié dans .cluster-ci. Exécution de tout le pipeline."
else
    log_info "Arguments détectés : $DVC_ARGS"
fi

log_info "Analyse AST via dvc-viewer..."
uv run dvc-viewer hash

log_info "Recherche d'un port libre pour dvc-viewer..."
VIEWER_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
log_info "Port sélectionné : $VIEWER_PORT"
echo "$VIEWER_PORT" > .cluster-ci-viewer-port

log_info "Lancement du serveur live dvc-viewer sur le port $VIEWER_PORT..."
uv run dvc-viewer --port "$VIEWER_PORT" > "$BASE_DIR/dvc-viewer.log" 2>&1 &
DVC_VIEWER_PID=$!

if [ -n "$DVC_REMOTE_P2P_URL" ]; then
    log_info "Data Plane: Configuration d'un remote P2P dynamique vers $DVC_REMOTE_P2P_URL..."

    # Construction de l'URL vers le cache du pair via le Data Plane (Worker Agent /fetch_artifact)
    # L'agent sert 'repositories/' à la racine, donc on pointe vers .dvc/cache/files/md5
    PEER_REMOTE_URL="$DVC_REMOTE_P2P_URL/$TARGET_REPO/.dvc/cache/files/md5"

    uv run dvc remote add peer_remote "$PEER_REMOTE_URL" --local

    # Mesure de la taille du cache avant le pull (en octets)
    CACHE_BEFORE=$(du -sb .dvc/cache 2>/dev/null | cut -f1 || echo 0)

    log_info "Récupération des données depuis le pair (P2P pull strict)..."
    if ! uv run dvc pull -r peer_remote; then
        log_error "Échec critique du transfert P2P depuis $PEER_REMOTE_URL. Abandon pour éviter un recalcul coûteux."
        exit 1
    fi

    # Mesure après et calcul de la différence
    CACHE_AFTER=$(du -sb .dvc/cache 2>/dev/null | cut -f1 || echo 0)
    VOL_BYTES=$((CACHE_AFTER - CACHE_BEFORE))
    VOL_MB=$(python3 -c "print(round($VOL_BYTES / (1024*1024), 2))")

    log_success "Transfert P2P réussi : $VOL_MB Mo rapatriés depuis le pair."
fi

log_info "Lancement de : uv run dvc repro $DVC_ARGS"
uv run dvc repro $DVC_ARGS

# 4. Data Router: Vérification avant Push
log_info "[Etape 4/3] Data Router : Vérification de l'espace sur le headnode..."
HEADNODE_URL=${HEADNODE_URL:-"http://localhost:5000"}

# Interroger l'API du headnode pour connaître l'espace disque
SPACE_CHECK=$(curl -s "$HEADNODE_URL/check_space" || echo '{"sufficient": false, "error": "unreachable"}')
SUFFICIENT=$(echo "$SPACE_CHECK" | jq -r '.sufficient')

if [ "$SUFFICIENT" == "true" ]; then
    log_info "Espace suffisant sur le headnode. Synchronisation des artefacts..."
    if uv run dvc push; then
        python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-done "$TARGET_REPO"
        log_success "Synchronisation terminée."
    else
        log_error "Échec du dvc push. Le projet reste en attente de synchro."
        python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-pending "$TARGET_REPO"
    fi
else
    FREE_GB=$(echo "$SPACE_CHECK" | jq -r '.free_gb // "inconnu"')
    log_error "Espace insuffisant sur le headnode ($FREE_GB GB libres). Push annulé."
    log_info "Le projet est marqué 'en attente de synchro' pour un transfert ultérieur."
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-pending "$TARGET_REPO"
fi

echo "=========================================================================="
log_success "CLUSTER-CI: Exécution GitOps terminée avec succès."
echo "=========================================================================="

# Tronquer log à 2000 lignes max (efface le tout début pour ne garder que la fin)
if [ -f "$LOG_FILE" ]; then
    tail -n 2000 "$LOG_FILE" > "${LOG_FILE}.tmp"
    mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
