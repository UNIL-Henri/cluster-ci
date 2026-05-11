import argparse
import os
import sys
import subprocess
import re
from pathlib import Path

try:
    import tomlkit
except ImportError:
    print("❌ Error: 'tomlkit' is required. Please install it with 'pip install tomlkit'.")
    sys.exit(1)

def log_info(msg):
    print(f"ℹ️  [Cluster-CI] {msg}")

def log_error(msg):
    print(f"❌ [Cluster-CI] {msg}")

def log_success(msg):
    print(f"✅ [Cluster-CI] {msg}")

def check_python_version(pyproject_path, target_version="3.12"):
    with open(pyproject_path, "r") as f:
        doc = tomlkit.parse(f.read())
    
    requires_python = doc.get("project", {}).get("requires-python", "")
    if not requires_python:
        return True, None

    # Simple check for compatibility with target_version (e.g. 3.12)
    # We look for <3.11, <3.12 etc.
    # Note: A real semver check would be better but requires packaging.specifiers
    # We'll use a simple regex for common problematic patterns mentioned in the issue
    if re.search(r"<3\.(?:11|10|[0-9])", requires_python):
        return False, requires_python
    
    return True, requires_python

def check_torch_pinning(pyproject_path):
    with open(pyproject_path, "r") as f:
        doc = tomlkit.parse(f.read())
    
    dependencies = doc.get("project", {}).get("dependencies", [])
    conflicts = []
    for dep in dependencies:
        if "torch" in dep.lower() and "==" in dep:
            conflicts.append(dep)
    
    return conflicts

def fix_pyproject(pyproject_path, fix_python=False, remove_torch_pin=False):
    with open(pyproject_path, "r") as f:
        content = f.read()
        doc = tomlkit.parse(content)
    
    modified = False
    if fix_python:
        if "project" in doc and "requires-python" in doc["project"]:
            old_val = doc["project"]["requires-python"]
            doc["project"]["requires-python"] = ">=3.12"
            log_success(f"Updated requires-python: {old_val} -> >=3.12")
            modified = True

    if remove_torch_pin:
        if "project" in doc and "dependencies" in doc["project"]:
            deps = doc["project"]["dependencies"]
            new_deps = []
            for dep in deps:
                if "torch" in dep.lower() and "==" in dep:
                    # Replace strict pinning with relaxed one
                    new_dep = re.sub(r"==[0-9.]+", ">=2.0", dep)
                    new_deps.append(new_dep)
                    log_success(f"Relaxed dependency: {dep} -> {new_dep}")
                    modified = True
                else:
                    new_deps.append(dep)
            doc["project"]["dependencies"] = new_deps

    if modified:
        with open(pyproject_path, "w") as f:
            f.write(tomlkit.dumps(doc))
        return True
    return False

def simulate_resolution(pyproject_path, constraints_path):
    if not os.path.exists(constraints_path):
        log_info(f"No constraints file found at {constraints_path}. Skipping full simulation.")
        return True
    
    log_info("Simulating ARM64 resolution with uv...")
    cmd = [
        "uv", "pip", "compile",
        "--python-platform", "aarch64-unknown-linux-gnu",
        "--python", "3.12",
        "-c", constraints_path,
        pyproject_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log_error("Resolution simulation failed!")
        print(result.stderr)
        return False
    
    log_success("Resolution simulation passed.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Cluster-CI Pre-flight Scanner")
    parser.add_argument("--ci", action="store_true", help="CI mode (fail-fast)")
    parser.add_argument("--interactive", action="store_true", help="Interactive mode (pre-commit)")
    parser.add_argument("--pyproject", default="pyproject.toml", help="Path to pyproject.toml")
    parser.add_argument("--constraints", default="cluster_constraints.txt", help="Path to constraints file")
    
    args = parser.parse_args()
    
    pyproject_path = Path(args.pyproject)
    if not pyproject_path.exists():
        if args.ci:
            log_info("No pyproject.toml found. Skipping check.")
            sys.exit(0)
        else:
            log_error(f"File not found: {args.pyproject}")
            sys.exit(1)

    # 1. Check Python Version
    compat, version = check_python_version(pyproject_path)
    if not compat:
        log_error(f"Incompatible Python version detected: {version}")
        log_error("The cluster requires Python 3.12. Your project excludes it.")
        if args.interactive:
            ans = input("❓ Would you like to fix it to '>=3.12' automatically? [Y/n]: ")
            if ans.lower() in ["", "y", "yes"]:
                fix_pyproject(pyproject_path, fix_python=True)
            else:
                log_info("Aborted by user.")
                sys.exit(1)
        else:
            sys.exit(1)

    # 2. Check Torch Pinning
    torch_conflicts = check_torch_pinning(pyproject_path)
    if torch_conflicts:
        log_error(f"Strict Torch pinning detected: {torch_conflicts}")
        log_info("Strict pinning on ARM64 may conflict with NVIDIA's native builds.")
        if args.interactive:
            ans = input("❓ Would you like to relax these constraints? [Y/n]: ")
            if ans.lower() in ["", "y", "yes"]:
                fix_pyproject(pyproject_path, remove_torch_pin=True)
            else:
                log_info("Aborted by user.")
                sys.exit(1)
        else:
            log_info("Warning: Strict pinning detected. This might fail during installation.")

    # 3. Simulate full resolution
    if not simulate_resolution(pyproject_path, args.constraints):
        if args.interactive:
            log_error("Automatic fix failed to resolve all conflicts. Manual intervention required.")
        sys.exit(1)

    log_success("Validation complete.")

if __name__ == "__main__":
    main()
