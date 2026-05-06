#!/bin/bash
set -e

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <owner/repo> <branch_name>"
    echo "Exemple: $0 hjamet/llm-as-recommender main"
    exit 1
fi

TARGET_REPO=$1
TARGET_BRANCH=$2
GH_TOKEN="${3:-$GH_TOKEN}"

# Go to cluster-ci project root
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

# Delegation mode: If not explicitly in executor mode,
# delegate the task to the scheduler via submit_job.py
if [ "$CLUSTER_CI_MODE" != "executor" ]; then
    echo "🌐 Delegation Mode enabled. Submitting job to scheduler..."
    python3 "$BASE_DIR/src/scheduler/submit_job.py" "$TARGET_REPO" "$TARGET_BRANCH"
    exit $?
fi

REPO_WORK_DIR="repositories/$TARGET_REPO"

# Pipe all output (stdout and stderr) to console AND to a local log file
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
log_info "CLUSTER-CI: GitOps Runner Orchestration Start"
log_info "   Target Repo   : $TARGET_REPO"
log_info "   Target Branch : $TARGET_BRANCH"
log_info "   Run Folder    : $BASE_DIR/$REPO_WORK_DIR"
echo "=========================================================================="

# 1. Creation / switch to repositories/
log_info "[Step 1/3] Initializing local cache..."
mkdir -p "$BASE_DIR/repositories/$(dirname "$TARGET_REPO")"
cd "$BASE_DIR/repositories/$(dirname "$TARGET_REPO")"

# Extract just the final repo name for the folder (e.g., llm-as-recommender)
REPO_BASENAME=$(basename "$TARGET_REPO")

if [ -n "$GH_TOKEN" ]; then
    # Silent https authentication for GitHub Actions
    REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${TARGET_REPO}.git"
else
    REPO_URL="https://github.com/${TARGET_REPO}.git"
fi

# 1.5 JIT Garbage Collection & Metadata update
log_info "[Step 1.5/3] JIT Garbage Collection (GC) Management..."
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" run-gc
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-running "$TARGET_REPO"

function update_status_idle() {
    log_info "Updating metadata (idle status)..."
    [ -n "$DVC_VIEWER_PID" ] && kill -9 "$DVC_VIEWER_PID" 2>/dev/null || true
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-idle "$TARGET_REPO" "$BASE_DIR/repositories/$TARGET_REPO"
}
trap update_status_idle EXIT

# 2. Preventive Purge & Git State Management
log_info "[Step 2/3] Preventive purge of residual dvc-viewer processes..."
# Look for dvc-viewer processes whose CWD matches the project working directory
for pid in $(pgrep -f "dvc-viewer" || true); do
    if pwdx "$pid" 2>/dev/null | grep -q ": $BASE_DIR/$REPO_WORK_DIR$"; then
        log_info "Cleaning up ghost dvc-viewer process (PID: $pid)..."
        kill -9 "$pid" 2>/dev/null || true
    fi
done

if [ ! -d "$REPO_BASENAME/.git" ]; then
    log_info "[Step 2.1/3] First repository fetch. Cloning in progress..."
    git clone "$REPO_URL" "$REPO_BASENAME"
else
    log_info "[Step 2.1/3] Existing repository found. Updating..."
fi

cd "$REPO_BASENAME"

# Force remote URL in case it changed (ephemeral token)
git remote set-url origin "$REPO_URL"

# Force fetching latest references (explicitly specify branch mapping to origin/branch
# as GitHub Actions conditional fetch sometimes omits it)
log_info "Synchronizing remote reference origin/$TARGET_BRANCH..."
git fetch origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"

# Security validation: does the branch exist on remote?
if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
    log_error "Branch origin/$TARGET_BRANCH does not exist or was not found."
    exit 1
fi

# Switch and hard reset to ensure clean Git tree
log_info "Forced branch checkout and re-synchronization..."
git checkout -f -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
git reset --hard "origin/$TARGET_BRANCH"

# Register current commit hash for traceability
git rev-parse HEAD > .cluster-ci-commit

log_success "Git tree synchronized. Artifacts (.dvc/cache etc.) preserved for reuse."

# Register current commit hash for traceability
git rev-parse HEAD > .cluster-ci-commit

# 3. Launch Dockerized Execution
log_info "[Step 3/3] Preparing Dockerized execution..."

if [ ! -f ".cluster-ci" ]; then
    log_error ".cluster-ci file not found at repository root. Execution aborted."
    exit 1
fi

# Extract RAM limit from .cluster-ci (--ram 16)
RAM_LIMIT=$(grep -oE -e '--ram [0-9.]+' .cluster-ci | awk '{print $2}' | head -n 1)
[ -z "$RAM_LIMIT" ] && RAM_LIMIT="2"
log_info "RAM limit detected: ${RAM_LIMIT}GB"

# Configuration Docker
DOCKER_IMAGE=${DOCKER_BASE_IMAGE:-"nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3"}
ENV_FILE_FLAG=""
if [ -f "$BASE_DIR/.env.secrets" ]; then
    ENV_FILE_FLAG="--env-file $BASE_DIR/.env.secrets"
fi

# Create a volume for the user's home to avoid redownloading dvc every time and to keep uv/pip caches
HOME_CACHE_VOLUME="cluster-ci-home-cache"
if ! docker volume inspect "$HOME_CACHE_VOLUME" >/dev/null 2>&1; then
    docker volume create "$HOME_CACHE_VOLUME" >/dev/null
fi
# Ensure the volume is owned by the current user
docker run --rm -v "$HOME_CACHE_VOLUME:/home/user" "$DOCKER_IMAGE" chown -R "$(id -u):$(id -g)" /home/user

function docker_exec() {
    docker run --rm \
        --gpus all \
        -v "$(pwd):/workspace" \
        -v "$HOME_CACHE_VOLUME:/home/user" \
        -v /etc/passwd:/etc/passwd:ro \
        -v /etc/group:/etc/group:ro \
        -w /workspace \
        --ipc=host \
        --user "$(id -u):$(id -g)" \
        -e HOME=/home/user \
        --memory="${RAM_LIMIT}g" \
        $ENV_FILE_FLAG \
        -e HEADNODE_URL="$HEADNODE_URL" \
        -e CLUSTER_CI_MODE=executor \
        "$DOCKER_IMAGE" bash -c "export PATH=\$PATH:/home/user/.local/bin && $1"
}

log_info "Image used: $DOCKER_IMAGE"

log_info "Installing base dependencies in persistent volume..."
docker_exec "if ! command -v uv &> /dev/null; then python3 -m pip install uv --user >/dev/null 2>&1; fi"
docker_exec "if ! command -v dvc &> /dev/null; then uv tool install dvc >/dev/null 2>&1; fi"
docker_exec "if ! command -v dvc-viewer &> /dev/null; then uv tool install git+https://github.com/UNIL-DESI/dvc-viewer.git >/dev/null 2>&1; fi"

log_info "Reading DVC parameters from .cluster-ci..."
# Clean comments, remove internal flags like --ram, and put arguments on a single line
DVC_ARGS=$(grep -v '^\s*#' .cluster-ci | sed 's/--ram [0-9.]*//g' | tr '\n' ' ' | xargs)

if [ -z "$DVC_ARGS" ]; then
    log_info "No arguments specified in .cluster-ci. Executing full pipeline."
else
    log_info "Arguments detected: $DVC_ARGS"
fi

log_info "AST analysis via dvc-viewer..."
docker_exec "dvc-viewer hash"

log_info "Searching for a free port for dvc-viewer..."
VIEWER_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
log_info "Port selected: $VIEWER_PORT"
echo "$VIEWER_PORT" > .cluster-ci-viewer-port

log_info "Launching live dvc-viewer server on port $VIEWER_PORT..."
# Pour le viewer en background, on expose le port
docker run --rm \
    --gpus all \
    -v "$(pwd):/workspace" -w /workspace \
    -v "$HOME_CACHE_VOLUME:/home/user" \
    -p "$VIEWER_PORT:$VIEWER_PORT" \
    --user "$(id -u):$(id -g)" -e HOME=/home/user \
    $ENV_FILE_FLAG \
    $DOCKER_IMAGE \
    bash -c "export PATH=\$PATH:/home/user/.local/bin && dvc-viewer --port $VIEWER_PORT" > "$BASE_DIR/dvc-viewer.log" 2>&1 &
DVC_VIEWER_PID=$!

if [ -n "$DVC_REMOTE_P2P_URL" ]; then
    log_info "Data Plane: Configuring dynamic P2P remote to $DVC_REMOTE_P2P_URL..."
    PEER_REMOTE_URL="$DVC_REMOTE_P2P_URL/$TARGET_REPO/.dvc/cache/files/md5"

    docker_exec "dvc remote add peer_remote '$PEER_REMOTE_URL' --local"

    log_info "Fetching data from peer (strict P2P pull with retries)..."
    MAX_RETRIES=3
    RETRY_DELAY=5
    RETRY_COUNT=0
    PULL_SUCCESS=false

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if docker_exec "dvc pull -r peer_remote"; then
            PULL_SUCCESS=true
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
            log_info "P2P pull failed (attempt $RETRY_COUNT/$MAX_RETRIES). Retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
        fi
    done

    if [ "$PULL_SUCCESS" = false ]; then
        log_error "Critical P2P transfer failure from $PEER_REMOTE_URL after $MAX_RETRIES attempts. Aborting."
        exit 1
    fi
    log_success "P2P transfer successful."
fi

log_info "Launching: dvc repro $DVC_ARGS via Docker"
# Execution of repro and uv dependencies if present
if [ -f "pyproject.toml" ]; then
    EXEC_CMD="(command -v uv || pip install uv --user >/dev/null 2>&1) && uv sync && uv run dvc repro $DVC_ARGS"
else
    EXEC_CMD="dvc repro $DVC_ARGS"
fi

docker_exec "$EXEC_CMD"

# 4. Data Router: Verification before Push
log_info "[Step 4/3] Data Router: Checking headnode space..."
HEADNODE_URL=${HEADNODE_URL:-"http://localhost:5000"}

# Query headnode API for disk space
SPACE_CHECK=$(curl -s "$HEADNODE_URL/check_space" || echo '{"sufficient": false, "error": "unreachable"}')
SUFFICIENT=$(echo "$SPACE_CHECK" | jq -r '.sufficient')

if [ "$SUFFICIENT" == "true" ]; then
    log_info "Sufficient space on headnode. Synchronizing artifacts..."
    # Check if a default DVC remote is configured by checking the config files
    HAS_REMOTE=false
    if [ -f ".dvc/config" ] && grep -E -q "^\s*remote\s*=" .dvc/config; then
        HAS_REMOTE=true
    elif [ -f ".dvc/config.local" ] && grep -E -q "^\s*remote\s*=" .dvc/config.local; then
        HAS_REMOTE=true
    fi

    if [ "$HAS_REMOTE" = "true" ]; then
        if docker_exec "dvc push"; then
            python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-done "$TARGET_REPO"
            log_success "Synchronization complete."
        else
            log_error "dvc push failed. Project remains pending sync."
            python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-pending "$TARGET_REPO"
        fi
    else
        log_info "No default DVC remote configured. Skipping dvc push."
        python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-done "$TARGET_REPO"
        log_success "Synchronization complete (nothing to push)."
    fi
else
    FREE_GB=$(echo "$SPACE_CHECK" | jq -r '.free_gb // "unknown"')
    log_error "Insufficient space on headnode ($FREE_GB GB free). Push cancelled."
    log_info "Project marked 'pending sync' for later transfer."
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" mark-sync-pending "$TARGET_REPO"
fi

echo "=========================================================================="
log_success "CLUSTER-CI: GitOps execution completed successfully."
echo "=========================================================================="

# Truncate log to max 2000 lines (erases beginning to keep the end)
if [ -f "$LOG_FILE" ]; then
    tail -n 2000 "$LOG_FILE" > "${LOG_FILE}.tmp"
    mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
