#!/bin/bash
# Smart dependency installer for Cluster-CI
# Skips installation if dependency specs haven't changed since last successful install.
# Hash is stored in /home/user/.cluster-ci-deps-hash (persistent Docker volume).
set -e

HASH_FILE="/home/user/.cluster-ci-deps-hash"

# Compute a composite hash of all dependency specification files
compute_deps_hash() {
    local files="pyproject.toml"
    [ -f "uv.lock" ] && files="$files uv.lock"
    [ -f "requirements.txt" ] && files="$files requirements.txt"
    [ -f "setup.py" ] && files="$files setup.py"
    md5sum $files 2>/dev/null | md5sum | cut -d' ' -f1
}

DEPS_HASH=$(compute_deps_hash)
CACHED_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "none")

if [ "$DEPS_HASH" = "$CACHED_HASH" ]; then
    echo "✅ [Cluster-CI] Dependencies unchanged (cached). Skipping install."
    exit 0
fi

echo "📦 [Cluster-CI] Dependencies changed (hash: ${CACHED_HASH:0:8}… → ${DEPS_HASH:0:8}…). Installing..."

# Ensure uv is available
command -v uv >/dev/null || python3 -m pip install uv --user --break-system-packages >/dev/null 2>&1

# Install project with system packages, allowing pre-releases for NGC PyTorch
uv pip install --system --break-system-packages --prerelease allow --prefix /home/user/.local -e .

# Post-install: purge any PyPI-downloaded NVIDIA/PyTorch packages that would
# shadow the highly-optimized NGC system libraries in /usr/local/lib/python3.*/
# See: PyTorch/NVIDIA Library Shadowing Bug (memory ae4a85be)
rm -rf /home/user/.local/lib/python3.*/site-packages/torch \
       /home/user/.local/lib/python3.*/site-packages/torch-* \
       /home/user/.local/lib/python3.*/site-packages/nvidia* \
       /home/user/.local/lib/python3.*/site-packages/triton* \
       /home/user/.local/lib/python3.*/site-packages/xformers* 2>/dev/null || true

# Patch bitsandbytes for newer CUDA versions (e.g. 13.2) if missing
BNB_DIR=$(ls -d /home/user/.local/lib/python3.*/site-packages/bitsandbytes 2>/dev/null | head -n 1)
if [ -n "$BNB_DIR" ] && command -v nvcc >/dev/null; then
    SYS_CUDA=$(nvcc --version | grep 'release' | awk '{print $5}' | cut -d',' -f1 | tr -d '.')
    if [ -n "$SYS_CUDA" ]; then
        HIGHEST_SO=$(ls "$BNB_DIR"/libbitsandbytes_cuda*.so 2>/dev/null | grep -Eo 'cuda[0-9]+' | sed 's/cuda//' | sort -nr | head -n 1)
        if [ -n "$HIGHEST_SO" ] && [ "$SYS_CUDA" -gt "$HIGHEST_SO" ] && [ ! -f "$BNB_DIR/libbitsandbytes_cuda${SYS_CUDA}.so" ]; then
            echo "🔧 [Cluster-CI] Patching bitsandbytes for CUDA $SYS_CUDA (fallback to $HIGHEST_SO)"
            ln -s "libbitsandbytes_cuda${HIGHEST_SO}.so" "$BNB_DIR/libbitsandbytes_cuda${SYS_CUDA}.so"
        fi
    fi
fi

# Save hash only after successful install
echo "$DEPS_HASH" > "$HASH_FILE"
echo "✅ [Cluster-CI] Dependencies installed and cached."
