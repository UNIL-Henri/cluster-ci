import os
import json
import shutil
import sys
import time
import fcntl
import subprocess
from pathlib import Path

# Config
DEFAULT_FREE_SPACE_THRESHOLD_GB = 200
FREE_SPACE_THRESHOLD_GB = int(os.environ.get("GC_FREE_SPACE_THRESHOLD_GB", DEFAULT_FREE_SPACE_THRESHOLD_GB))
FREE_SPACE_THRESHOLD_BYTES = FREE_SPACE_THRESHOLD_GB * 1024 * 1024 * 1024
REGISTRY_FILENAME = "registry.json"

def get_base_dir():
    # Assuming script is in src/runner/gc_orchestrator.py
    return Path(__file__).parent.parent.parent.resolve()

def get_repositories_dir():
    return get_base_dir() / "repositories"

def get_registry_path():
    return get_repositories_dir() / REGISTRY_FILENAME

def load_registry(f):
    try:
        f.seek(0)
        content = f.read()
        if not content:
            return {}
        return json.loads(content)
    except Exception as e:
        print(f"Error loading registry: {e}")
        return {}

def save_registry(f, registry):
    f.seek(0)
    f.truncate()
    json.dump(registry, f, indent=4)
    f.flush()
    os.fsync(f.fileno())

def get_dir_size(path):
    """Calculates directory size using 'du -sb' if available, otherwise os.walk."""
    try:
        # Use -s for summary and -b for bytes
        output = subprocess.check_output(["du", "-sb", str(path)], stderr=subprocess.DEVNULL)
        return int(output.split()[0])
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError):
        # Fallback to os.walk
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    try:
                        total_size += os.path.getsize(fp)
                    except OSError:
                        pass
        return total_size

def validate_project_name(project_name):
    """Simple validation to ensure project_name is a relative path and doesn't escape repositories/."""
    path = Path(project_name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid project name: {project_name}")

def update_running(project_name):
    validate_project_name(project_name)
    registry_path = get_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    with open(registry_path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            registry = load_registry(f)
            registry[project_name] = registry.get(project_name, {})
            registry[project_name].update({
                "last_execution": time.time(),
                "status": "running"
            })
            save_registry(f, registry)
            print(f"Project {project_name} marked as running.")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def update_idle(project_name, project_path):
    validate_project_name(project_name)
    registry_path = get_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    if os.path.exists(project_path):
        size = get_dir_size(project_path)

    with open(registry_path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            registry = load_registry(f)
            if project_name not in registry:
                registry[project_name] = {}

            registry[project_name].update({
                "status": "idle",
                "size_bytes": size,
                "last_execution": time.time()
            })
            save_registry(f, registry)
            print(f"Project {project_name} marked as idle. Size: {size} bytes.")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def run_gc():
    repo_dir = get_repositories_dir()
    if not repo_dir.exists():
        print("Repositories directory does not exist. No GC needed.")
        return

    usage = shutil.disk_usage(repo_dir)
    free_space = usage.free

    print(f"Free space: {free_space / (1024**3):.2f} GB (Threshold: {FREE_SPACE_THRESHOLD_GB} GB)")

    if free_space < FREE_SPACE_THRESHOLD_BYTES:
        print(f"Free space below threshold. Starting cleanup...")
        registry_path = get_registry_path()
        if not registry_path.exists():
            print("Registry file not found. Nothing to clean.")
            return

        with open(registry_path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                registry = load_registry(f)

                # Filter idle projects and sort by last_execution (oldest first)
                idle_projects = [
                    (name, data) for name, data in registry.items()
                    if data.get("status") == "idle"
                ]
                idle_projects.sort(key=lambda x: x[1].get("last_execution", 0))

                for project_name, data in idle_projects:
                    if free_space >= FREE_SPACE_THRESHOLD_BYTES:
                        break

                    # Double check project name for safety before deletion
                    validate_project_name(project_name)
                    project_path = repo_dir / project_name

                    if project_path.exists():
                        print(f"Deleting oldest idle project: {project_name} at {project_path}")
                        try:
                            shutil.rmtree(project_path)
                            # Update free space after deletion
                            usage = shutil.disk_usage(repo_dir)
                            free_space = usage.free
                            data["size_bytes"] = 0
                            data["status"] = "deleted"
                            print(f"Deleted {project_name}. New free space: {free_space / (1024**3):.2f} GB")
                        except Exception as e:
                            print(f"Error deleting {project_name}: {e}")

                save_registry(f, registry)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    else:
        print("Sufficient free space available. No GC needed.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python gc_orchestrator.py <command> [args]")
        sys.exit(1)

    command = sys.argv[1]

    try:
        if command == "update-running":
            update_running(sys.argv[2])
        elif command == "update-idle":
            update_idle(sys.argv[2], sys.argv[3])
        elif command == "run-gc":
            run_gc()
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    except Exception as e:
        print(f"Error in {command}: {e}")
        sys.exit(1)
