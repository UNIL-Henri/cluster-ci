#!/bin/bash
set -e

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <owner/repo> <branch_name>"
    echo "Exemple: $0 hjamet/llm-as-recommender main"
    exit 1
fi

CLI_TARGET_REPO=$1
CLI_TARGET_BRANCH=$2
CLI_GH_TOKEN="${3:-$GH_TOKEN}"

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

TARGET_REPO=${CLI_TARGET_REPO:-$TARGET_REPO}
TARGET_BRANCH=${CLI_TARGET_BRANCH:-$TARGET_BRANCH}

# Prioritize local GITHUB_PAT or GH_TOKEN from local environment files over the CLI token argument
if [ -n "$GITHUB_PAT" ]; then
    GH_TOKEN="$GITHUB_PAT"
elif [ -z "$GH_TOKEN" ]; then
    GH_TOKEN="$CLI_GH_TOKEN"
fi
JOB_ID=${JOB_ID:-"manual-$(date +%s)"}

# Robust Container Naming & Labeling
SAFE_JOB_ID=$(echo "$JOB_ID" | tr '/' '-')
MAIN_CONTAINER_NAME="cluster-job-${SAFE_JOB_ID}"
VIEWER_CONTAINER_NAME="cluster-viewer-${SAFE_JOB_ID}"
COMMON_LABELS="--label cluster-ci-job=${JOB_ID} --label cluster-ci-repo=${TARGET_REPO}"

# Delegation mode: If not explicitly in executor mode,
# delegate the task to the scheduler via submit_job.py
if [ "$CLUSTER_CI_MODE" != "executor" ]; then
    echo "🌐 Delegation Mode enabled. Submitting job to scheduler..."
if [ -n "$GH_TOKEN" ]; then
        python3 -u "$BASE_DIR/src/scheduler/submit_job.py" "$TARGET_REPO" "$TARGET_BRANCH" --gh-token "$GH_TOKEN"
    else
        python3 -u "$BASE_DIR/src/scheduler/submit_job.py" "$TARGET_REPO" "$TARGET_BRANCH"
    fi
    exit $?
fi

REPO_WORK_DIR="repositories/$TARGET_REPO"

# Log files are captured directly by the worker agent at the process level.
# Avoiding global bash tee redirection prevents any buffering delays.

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

echo "===STAGE:setup:BEGIN==="

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
if [ -n "$JOB_ID" ]; then
    SAFE_JOB_ID=$(echo "$JOB_ID" | tr -dc 'a-zA-Z0-9_-')
    log_info "Preventive purge of containers for job $SAFE_JOB_ID..."
    docker rm -f "cluster-job-$SAFE_JOB_ID" "cluster-viewer-$SAFE_JOB_ID" 2>/dev/null || true
fi
log_info "Scanning for zombie containers (JIT Zombie GC)..."
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" run-zombie-gc
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" run-gc
python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-running "$TARGET_REPO"

function cleanup_job_resources() {
    log_info "Cleaning up job resources for ${JOB_ID}..."
    # Graceful stop then force remove
    docker stop "${MAIN_CONTAINER_NAME}" "${VIEWER_CONTAINER_NAME}" 2>/dev/null || true
    docker rm -f "${MAIN_CONTAINER_NAME}" "${VIEWER_CONTAINER_NAME}" 2>/dev/null || true

    log_info "Updating metadata (idle status)..."
    if [ -n "$SAFE_JOB_ID" ]; then
        docker stop "cluster-viewer-$SAFE_JOB_ID" 2>/dev/null || true
        docker rm -f "cluster-viewer-$SAFE_JOB_ID" 2>/dev/null || true
        rm -f "/tmp/tmate_${SAFE_JOB_ID}.sock" "/tmp/tmate_${SAFE_JOB_ID}.conf" 2>/dev/null || true
    fi
    [ -n "$DVC_VIEWER_PID" ] && kill -9 "$DVC_VIEWER_PID" 2>/dev/null || true
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" update-idle "$TARGET_REPO" "$BASE_DIR/repositories/$TARGET_REPO"
    log_info "Running post-flight Maintenance GC (Lazy Transfer)..."
    python3 "$BASE_DIR/src/runner/gc_orchestrator.py" run-transfer-gc
}
# Trap EXIT, SIGINT, and SIGTERM to ensure cleanup
trap cleanup_job_resources EXIT SIGINT SIGTERM

# 2. Preventive Purge & Git State Management
log_info "[Step 2/3] Preventive purge of residual containers and processes..."
# 2.1 Cleanup containers for this specific job ID
# This ensures that if a previous attempt of the SAME job failed/crashed, we clean it up.
docker rm -f "${MAIN_CONTAINER_NAME}" "${VIEWER_CONTAINER_NAME}" 2>/dev/null || true

# 2.2 Cleanup legacy dvc-viewer processes (fallback for non-dockerized viewers)
for pid in $(pgrep -f "dvc-viewer" || true); do
    if pwdx "$pid" 2>/dev/null | grep -q ": $BASE_DIR/$REPO_WORK_DIR$"; then
        log_info "Cleaning up ghost legacy dvc-viewer process (PID: $pid)..."
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

# Extract RAM limit from .cluster-ci (--ram 16 or REQUIRED_RAM=16GB)
RAM_LIMIT=$(grep -oE -e 'REQUIRED_RAM=[0-9.]+' .cluster-ci | cut -d= -f2 | head -n 1)
[ -z "$RAM_LIMIT" ] && RAM_LIMIT=$(grep -oE -e '--ram [0-9.]+' .cluster-ci | awk '{print $2}' | head -n 1)
[ -z "$RAM_LIMIT" ] && RAM_LIMIT="2"
log_info "RAM limit detected: ${RAM_LIMIT}GB"


# Configuration Docker
DOCKER_IMAGE=${DOCKER_BASE_IMAGE:-"nvcr.io/nvidia/pytorch:26.04-py3"}
ENV_FILE_FLAG=""
if [ -f "$BASE_DIR/.env.secrets" ]; then
    ENV_FILE_FLAG="--env-file $BASE_DIR/.env.secrets"
fi

if [ -n "$CLUSTER_CI_SECRETS_FILE" ] && [ -f "$CLUSTER_CI_SECRETS_FILE" ]; then
    log_info "Injecting secure job secrets from $CLUSTER_CI_SECRETS_FILE"
    ENV_FILE_FLAG="$ENV_FILE_FLAG --env-file $CLUSTER_CI_SECRETS_FILE"
fi

# Create a volume for the user's home to avoid redownloading dvc every time and to keep uv/pip caches
HOME_CACHE_VOLUME="cluster-ci-home-$(echo "$TARGET_REPO" | tr '/' '-')"
if ! docker volume inspect "$HOME_CACHE_VOLUME" >/dev/null 2>&1; then
    docker volume create "$HOME_CACHE_VOLUME" >/dev/null
fi

# Ensure a clean state
docker rm -f "${MAIN_CONTAINER_NAME}" 2>/dev/null || true

# Launch the persistent main container
DOCKER_PORT_MAPPING=""

log_info "Searching for a free port for web interface..."
# Use EXPOSED_PORT if defined in .cluster-ci, otherwise find a free port
EXPOSED_PORT=$(grep -oE -e 'EXPOSED_PORT=[0-9]+' .cluster-ci | cut -d= -f2 | head -n 1)
if [ -n "$EXPOSED_PORT" ]; then
    VIEWER_PORT=$EXPOSED_PORT
    log_info "Using explicit EXPOSED_PORT from .cluster-ci: $VIEWER_PORT"
    DOCKER_PORT_MAPPING="-p 0.0.0.0:$VIEWER_PORT:$VIEWER_PORT"
    log_info "Main container will expose port $VIEWER_PORT (Web Application mode)"
else
    VIEWER_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
    log_info "No EXPOSED_PORT found. Dynamic port selected for dvc-viewer: $VIEWER_PORT"
    DOCKER_PORT_MAPPING=""
fi
echo "$VIEWER_PORT" > .cluster-ci-viewer-port

docker run -d \
    --name "${MAIN_CONTAINER_NAME}" \
    $COMMON_LABELS \
    $DOCKER_PORT_MAPPING \
    --entrypoint "tail" \
    --gpus all \
    -v "$(pwd):/workspace" \
    -v "$HOME_CACHE_VOLUME:/home/user" \
    -v "$BASE_DIR:/cluster-ci:ro" \
    -v /etc/passwd:/etc/passwd:ro \
    -v /etc/group:/etc/group:ro \
    -w /workspace \
    --ipc=host \
    --user "$(id -u):$(id -g)" \
    -e HOME=/home/user \
    $ENV_FILE_FLAG \
    -e HEADNODE_URL="$HEADNODE_URL" \
    -e CLUSTER_CI_MODE=executor \
    -e CLUSTER_CI_GPU_REQUIRED="$CLUSTER_CI_GPU_REQUIRED" \
    "$DOCKER_IMAGE" -f /dev/null

# Ensure the volume is owned by the current user (must be run as root)
docker exec --user root "${MAIN_CONTAINER_NAME}" bash -c "chown -R $(id -u):$(id -g) /home/user"

# Detect Docker image change: if the cached image marker differs from the
# current image, purge stale tool binaries to force a clean reinstall.
MARKER_CMD="cat /home/user/.cluster-ci-image-marker 2>/dev/null || echo 'none'"
CACHED_IMAGE=$(docker exec "${MAIN_CONTAINER_NAME}" bash -c "$MARKER_CMD")
if [ "$CACHED_IMAGE" != "$DOCKER_IMAGE" ]; then
    log_info "Docker image changed ($CACHED_IMAGE → $DOCKER_IMAGE). Purging stale tool cache..."
    docker exec --user "$(id -u):$(id -g)" "${MAIN_CONTAINER_NAME}" \
        bash -c "rm -rf /home/user/.local /home/user/.cache/uv /home/user/.cluster-ci-deps-hash 2>/dev/null; echo '$DOCKER_IMAGE' > /home/user/.cluster-ci-image-marker"
fi

function docker_exec() {
docker exec \
        -e HEADNODE_URL="$HEADNODE_URL" \
        -e CLUSTER_CI_MODE=executor \
        -e CLUSTER_CI_GPU_REQUIRED="$CLUSTER_CI_GPU_REQUIRED" \
        "${MAIN_CONTAINER_NAME}" bash -c "export PATH=/home/user/shims:\$PATH:/home/user/.local/bin && $1"
}

log_info "Image used: $DOCKER_IMAGE"

log_info "GPU Hardware Validation..."
# We check CUDA but only fail if CLUSTER_CI_GPU_REQUIRED is set to 1.
# This prevents breaking CPU-only environments (local debug, etc.) while keeping
# enforcement on production workers if desired.
GPU_REQ_CMD="import torch, os;
avail=torch.cuda.is_available();
print(f'CUDA available: {avail}');
if avail:
    props=torch.cuda.get_device_properties(0);
    free,total=torch.cuda.mem_get_info(0);
    print(f'GPU Device: {props.name}');
    print(f'GPU Memory (CUDA reports): {total/(1024**3):.1f} GB total, {free/(1024**3):.1f} GB free');
    print(f'Compute Capability: {props.major}.{props.minor}');
required=os.environ.get('CLUSTER_CI_GPU_REQUIRED', '0') == '1';
if required and not avail:
    print('❌ Error: GPU required but not found!');
    exit(1)"
docker_exec "python3 -c \"$GPU_REQ_CMD\""

log_info "Preparing smart environment shims (uv/poetry)..."
docker exec --user "$(id -u):$(id -g)" "${MAIN_CONTAINER_NAME}" bash -c 'SHIM_DIR=/home/user/shims && mkdir -p $SHIM_DIR &&

# UV Shim
cat > $SHIM_DIR/uv << '"'"'SHIMEOF'"'"'
#!/bin/bash
if [ "$1" = "run" ]; then
    shift
    # Collect --with packages and strip uv-specific flags
    WITH_PKGS=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --with) WITH_PKGS="$WITH_PKGS $2"; shift 2 ;;
            --python) shift 2 ;;
            --no-project|--no-sync) shift ;;
            *) break ;;
        esac
    done
    if [ -n "$WITH_PKGS" ]; then
        pip install --quiet --break-system-packages $WITH_PKGS 2>/dev/null || true
    fi
    echo "🚀 [Cluster-CI Shim] Intercepting uv run, executing natively: $@"
    exec "$@"
elif [ "$1" = "sync" ]; then
    echo "ℹ️  [Cluster-CI Shim] Ignoring uv sync, dependencies are pre-installed in system."
    exit 0
else
    # Fallback to real uv — strip shim dir from PATH to avoid infinite recursion
    if [ -x "/home/user/.local/bin/uv" ]; then
        exec /home/user/.local/bin/uv "$@"
    else
        REAL_UV=$(PATH=${PATH#/home/user/shims:} command -v uv 2>/dev/null || true)
        if [ -n "$REAL_UV" ]; then
            exec "$REAL_UV" "$@"
        else
            echo "❌ [Cluster-CI Shim] uv not found. Install it first." >&2
            exit 1
        fi
    fi
fi
SHIMEOF
chmod +x $SHIM_DIR/uv &&

# Poetry Shim
cat > $SHIM_DIR/poetry << '"'"'SHIMEOF'"'"'
#!/bin/bash
if [ "$1" = "run" ]; then
    shift
    echo "🚀 [Cluster-CI Shim] Intercepting poetry run, executing natively: $@"
    exec "$@"
elif [ "$1" = "install" ] || [ "$1" = "sync" ]; then
    echo "ℹ️  [Cluster-CI Shim] Ignoring poetry install, dependencies are pre-installed."
    exit 0
else
    if [ -x "/home/user/.local/bin/poetry" ]; then
        exec /home/user/.local/bin/poetry "$@"
    else
        REAL_POETRY=$(PATH=${PATH#/home/user/shims:} command -v poetry 2>/dev/null || true)
        if [ -n "$REAL_POETRY" ]; then
            exec "$REAL_POETRY" "$@"
        else
            echo "❌ [Cluster-CI Shim] poetry not found. Install it first." >&2
            exit 1
        fi
    fi
fi
SHIMEOF
chmod +x $SHIM_DIR/poetry'

log_info "Installing base dependencies in persistent volume..."
# Bootstrap commands MUST bypass shims — use a raw docker run without /home/user/shims in PATH.
# Shims are only for user pipeline execution, not for installing the tools themselves.
function docker_exec_bootstrap() {
    docker exec \
        "${MAIN_CONTAINER_NAME}" bash -c "export PATH=\$PATH:/home/user/.local/bin && $1"
}
docker_exec_bootstrap "uv --version >/dev/null 2>&1 || python3 -m pip install uv --user >/dev/null 2>&1"
docker_exec_bootstrap "dvc version >/dev/null 2>&1 || uv tool install dvc >/dev/null 2>&1"
docker_exec_bootstrap "uv tool upgrade dvc-viewer >/dev/null 2>&1 || uv tool install git+https://github.com/UNIL-DESI/dvc-viewer.git >/dev/null 2>&1"

log_info "Reading DVC parameters from .cluster-ci..."
# Clean comments, remove internal flags like --ram, filter out KEY=VALUE env variables, and put arguments on a single line
DVC_ARGS=$(grep -v '^\s*#' .cluster-ci | sed 's/--ram [0-9.]*//g' | grep -v '=' | tr '\n' ' ' | xargs)

if [ -z "$DVC_ARGS" ]; then
    log_info "No arguments specified in .cluster-ci. Executing full pipeline."
else
    log_info "Arguments detected: $DVC_ARGS"
fi

if [ -n "$DVC_REMOTE_P2P_URL" ]; then
    log_info "Data Plane: Configuring dynamic P2P remote to $DVC_REMOTE_P2P_URL..."
    PEER_REMOTE_URL="$DVC_REMOTE_P2P_URL/$TARGET_REPO/.dvc/cache/files/md5"

    docker_exec "dvc remote add -f peer_remote '$PEER_REMOTE_URL' --local"

    log_info "Fetching data from peer (best-effort P2P pull)..."
    if docker_exec "dvc pull --force -r peer_remote" 2>/dev/null; then
        log_success "P2P transfer successful."
    else
        log_info "⚠️  P2P pull incomplete (some cache files missing on peer). dvc repro will regenerate missing stages."
    fi
fi

log_info "AST analysis via dvc-viewer..."
docker_exec "dvc-viewer hash"

if [ -n "$EXPOSED_PORT" ]; then
    log_info "Skipping secondary dvc-viewer container (Main container handles web application on port $VIEWER_PORT)."
else
    log_info "Launching live dvc-viewer server on port $VIEWER_PORT..."
    # Pour le viewer en background, on expose le port
    # IMPORTANT: On utilise --pid=container:${MAIN_CONTAINER_NAME} pour voir les processus du job principal
    docker rm -f "$VIEWER_CONTAINER_NAME" 2>/dev/null || true
    docker run --rm \
    --name "$VIEWER_CONTAINER_NAME" \
        $COMMON_LABELS \
        --entrypoint "" \
        -v "$(pwd):/workspace" -w /workspace \
        -v "$HOME_CACHE_VOLUME:/home/user" \
        -p "0.0.0.0:$VIEWER_PORT:$VIEWER_PORT" \
        --ipc=host \
        --pid="container:${MAIN_CONTAINER_NAME}" \
        --user "$(id -u):$(id -g)" -e HOME=/home/user \
        -e CLUSTER_CI_MODE=executor \
        $ENV_FILE_FLAG \
        $DOCKER_IMAGE \
        bash -c "export PATH=/home/user/shims:\$PATH:/home/user/.local/bin && dvc-viewer --port $VIEWER_PORT" > "dvc-viewer.log" 2>&1 &
fi

log_info "Pre-flight Validation..."
# Run the validation script using uv to ensure dependencies (tomlkit) are present
docker_exec "uv run --with tomlkit python3 /cluster-ci/src/runner/validate_pyproject.py --ci"

echo "===STAGE:setup:END==="
echo "===STAGE:dvc_repro:BEGIN==="

log_info "Launching: dvc repro $DVC_ARGS via Docker"
# Smart dependency installation: only re-install if pyproject.toml/uv.lock changed.
# The smart_install.sh script hashes dependency files and caches the result in the
# persistent Docker volume. Skips entirely if nothing changed → saves ~3GB bandwidth.
if [ -f "pyproject.toml" ]; then
    EXEC_CMD="bash /cluster-ci/src/runner/smart_install.sh && dvc repro --force $DVC_ARGS"
else
    EXEC_CMD="dvc repro --force $DVC_ARGS"
fi

# 0. Detect or install tmate static binary dynamically
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMATE_BIN="tmate"

# Check if system tmate works, or if local tmate works, otherwise download the correct architecture
if command -v tmate &> /dev/null && tmate -V &>/dev/null; then
    TMATE_BIN="tmate"
    log_info "Using working system tmate: $(which tmate)"
elif [ -f "$REPO_DIR/bin/tmate" ] && "$REPO_DIR/bin/tmate" -V &>/dev/null; then
    TMATE_BIN="$REPO_DIR/bin/tmate"
    log_info "Found working local tmate static binary: $TMATE_BIN"
else
    log_info "tmate not found or not executable. Attempting to download correct static binary..."
    # Dynamically detect architecture (arm64v8 for aarch64/arm64, amd64 for others)
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        TMATE_ARCH="arm64v8"
    else
        TMATE_ARCH="amd64"
    fi
    log_info "Downloading static binary for $TMATE_ARCH ($ARCH)..."
    mkdir -p "$REPO_DIR/bin"
    # Remove any broken/incompatible local binary first to avoid format conflicts
    rm -f "$REPO_DIR/bin/tmate"
    
    # Download and extract the official static tarball for detected architecture
    if wget -q -O "$REPO_DIR/bin/tmate.tar.xz" "https://github.com/tmate-io/tmate/releases/download/2.4.0/tmate-2.4.0-static-linux-${TMATE_ARCH}.tar.xz" || curl -s -L -o "$REPO_DIR/bin/tmate.tar.xz" "https://github.com/tmate-io/tmate/releases/download/2.4.0/tmate-2.4.0-static-linux-${TMATE_ARCH}.tar.xz"; then
        tar -xf "$REPO_DIR/bin/tmate.tar.xz" -C "$REPO_DIR/bin" --strip-components=1
        rm -f "$REPO_DIR/bin/tmate.tar.xz"
        chmod +x "$REPO_DIR/bin/tmate"
        TMATE_BIN="$REPO_DIR/bin/tmate"
        log_success "tmate static binary ($TMATE_ARCH) installed successfully!"
    else
        log_error "Failed to download tmate static binary."
    fi
fi

if command -v "$TMATE_BIN" &> /dev/null; then
    log_info "🚀 Live Terminal Streaming enabled. Initializing tmate..."
    
    # 1. Clean up socket and create temp config to bypass tmate welcome/help screen
    TMATE_SOCKET="/tmp/tmate_${SAFE_JOB_ID}.sock"
    TMATE_CONF="/tmp/tmate_${SAFE_JOB_ID}.conf"
    rm -f "$TMATE_SOCKET" "$TMATE_CONF"
    echo "set -g tmate-display-help off" > "$TMATE_CONF"
    
    # 2. Start detached session running the execute command (write logs/exit code on host runner)
    TMATE_CMD="echo '⏱️ Introducing 2s startup delay...'; sleep 2; docker exec -it ${MAIN_CONTAINER_NAME} bash -c \"export PATH=/home/user/shims:\\\$PATH:/home/user/.local/bin && ${EXEC_CMD}\" 2>&1 | stdbuf -oL -eL tee tmate_execution.log; echo \${PIPESTATUS[0]} > tmate_exit_code"
    "$TMATE_BIN" -S "$TMATE_SOCKET" -f "$TMATE_CONF" new-session -d "$TMATE_CMD"
    
    # Send 'q' key as an extra safeguard
    sleep 0.5
    "$TMATE_BIN" -S "$TMATE_SOCKET" send-keys "q"
    
    # 3. Wait for tmate to connect and generate URL
    log_info "Generating live terminal SSH and Web URLs..."
    TMATE_SSH=""
    TMATE_WEB=""
    for attempt in {1..10}; do
        sleep 1.5
        TMATE_SSH=$("$TMATE_BIN" -S "$TMATE_SOCKET" display -p '#{tmate_ssh}' 2>/dev/null || true)
        TMATE_WEB=$("$TMATE_BIN" -S "$TMATE_SOCKET" display -p '#{tmate_web}' 2>/dev/null || true)
        if [ -n "$TMATE_SSH" ] && [ -n "$TMATE_WEB" ]; then
            break
        fi
    done
    
    if [ -n "$TMATE_SSH" ] && [ -n "$TMATE_WEB" ]; then
        log_success "Live terminal available! Connect via:"
        log_success "👉 Web: $TMATE_WEB"
        log_success "👉 SSH: $TMATE_SSH"
        
        # 4. Publish the URL to the GitHub Commit Status
        COMMIT_SHA=$(cat .cluster-ci-commit 2>/dev/null || git rev-parse HEAD || true)
        if [ -n "$GH_TOKEN" ] && [ -n "$COMMIT_SHA" ]; then
            log_info "Publishing tmate URL to GitHub commit status for commit $COMMIT_SHA..."
            resp=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
                -H "Authorization: token $GH_TOKEN" \
                -H "Accept: application/vnd.github.v3+json" \
                "https://api.github.com/repos/$TARGET_REPO/statuses/$COMMIT_SHA" \
                -d "{\"state\": \"pending\", \"target_url\": \"${TMATE_WEB}\", \"description\": \"SSH: ${TMATE_SSH}\", \"context\": \"tmate\"}")
            status_code=$(echo "$resp" | grep "HTTP_STATUS" | cut -d: -f2)
            body=$(echo "$resp" | grep -v "HTTP_STATUS")
            if [ "$status_code" -ne 201 ]; then
                log_error "Failed to publish commit status (HTTP $status_code): $body"
            else
                log_success "GitHub commit status published successfully."
            fi
        fi
    else
        log_error "Failed to generate tmate URL. Execution will continue silently."
    fi
    
    # 5. Wait for the session to finish and stream the log file in real-time
    set +e
    LAST_LINE=1
    while "$TMATE_BIN" -S "$TMATE_SOCKET" has-session 2>/dev/null; do
        if [ -f "tmate_execution.log" ]; then
            while read -r line || [ -n "$line" ]; do
                echo "$line"
                LAST_LINE=$((LAST_LINE + 1))
            done < <(tail -n +$LAST_LINE "tmate_execution.log" 2>/dev/null)
        fi
        sleep 2
    done
    
    # Print any remaining lines
    if [ -f "tmate_execution.log" ]; then
        while read -r line || [ -n "$line" ]; do
            echo "$line"
        done < <(tail -n +$LAST_LINE "tmate_execution.log" 2>/dev/null)
    fi
    set -e
    
    # 6. Retrieve the exit code
    if [ -f "tmate_exit_code" ]; then
        EXEC_RET=$(cat tmate_exit_code)
        rm -f tmate_exit_code
    else
        EXEC_RET=1
    fi
    
    # 7. Print the final log to standard stdout so GitHub Actions log has the permanent copy
    if [ -f "tmate_execution.log" ]; then
        rm -f tmate_execution.log
    fi
    
    # 8. Clean up temporary files
else
    log_info "⚠️ tmate not installed on runner host. Falling back to silent execution."
    set +e
    docker_exec "$EXEC_CMD"
    EXEC_RET=$?
    set -e
fi

echo "===STAGE:dvc_repro:END==="
echo "===STAGE:sync:BEGIN==="

if [ $EXEC_RET -ne 0 ]; then
    log_error "Execution interrupted or failed (Exit code: $EXEC_RET). Forcing DVC sync before exiting..."
fi

# Step 4 (Data Router) removed in favor of Post-Flight Lazy Transfer GC.

echo "=========================================================================="
log_success "CLUSTER-CI: GitOps execution completed successfully."
echo "=========================================================================="

echo "===STAGE:sync:END==="

# Truncate log to max 2000 lines (erases beginning to keep the end)
if [ -f "$LOG_FILE" ]; then
    tail -n 2000 "$LOG_FILE" > "${LOG_FILE}.tmp"
    mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

if [ $EXEC_RET -ne 0 ]; then
    log_error "Exiting with error code $EXEC_RET due to previous failure."
    exit $EXEC_RET
fi
