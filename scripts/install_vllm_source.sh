#!/bin/bash
# Script running inside the docker container on the worker to compile vLLM from source

set -e
echo "Starting vLLM compilation from source..."

# Set build concurrency
export MAX_JOBS=32
echo "Using MAX_JOBS=$MAX_JOBS"

# Set CUDA architectures to compile for Blackwell (compute 10.0) or Hopper (9.0) or Ampere (8.0/8.6)
# Blackwell is 10.0. To support it, let's set TORCH_CUDA_ARCH_LIST.
# Let's inspect the GPU compute capability first or compile specifically for the detected GPU.
GPU_CC=$(python3 -c "import torch; print(torch.cuda.get_device_properties(0).major)")
GPU_SUB=$(python3 -c "import torch; print(torch.cuda.get_device_properties(0).minor)")
export TORCH_CUDA_ARCH_LIST="${GPU_CC}.${GPU_SUB}"
echo "Detected GPU Compute Capability: $TORCH_CUDA_ARCH_LIST"

# Clean build directory if exists
cd /home/user
rm -rf vllm

echo "Cloning vLLM repository..."
# Let's use a stable release tag, e.g., v0.7.3 or v0.6.3.
# v0.7.3 is recent, let's clone it.
git clone --depth 1 --branch v0.7.3 https://github.com/vllm-project/vllm.git
cd vllm

# Run use_existing_torch.py if it exists
if [ -f "use_existing_torch.py" ]; then
    echo "Running use_existing_torch.py to patch requirements..."
    python3 use_existing_torch.py
else
    echo "use_existing_torch.py not found. Patching setup.py manually..."
    # Fallback patching just in case
    sed -i 's/"torch==.*"/"torch"/g' setup.py || true
fi

echo "Installing build-time dependencies..."
pip install --break-system-packages cmake ninja setuptools-scm

echo "Building and installing vLLM..."
# Use --no-build-isolation to use the system's PyTorch instead of downloading a new one
pip install --break-system-packages --no-build-isolation -e .

echo "vLLM successfully compiled from source!"
