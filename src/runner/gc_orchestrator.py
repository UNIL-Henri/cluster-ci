import os
import json
import shutil
import sys
import time
import fcntl
import subprocess
from pathlib import Path

# Config
DEFAULT_FREE_SPACE_THRESHOLD_GB = 100
FREE_SPACE_THRESHOLD_GB = int(os.environ.get("GC_FREE_SPACE_THRESHOLD_GB", DEFAULT_FREE_SPACE_THRESHOLD_GB))
FREE_SPACE_THRESHOLD_BYTES = FREE_SPACE_THRESHOLD_GB * 1024 * 1024 * 1024
REGISTRY_FILENAME = "registry.json"
LARGE_FILE_THRESHOLD_BYTES = 500 * 1024 * 1024

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

def mark_sync_status(project_name, status):
    validate_project_name(project_name)
    registry_path = get_registry_path()

    with open(registry_path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            registry = load_registry(f)
            if project_name not in registry:
                registry[project_name] = {}
            registry[project_name]["sync_status"] = status
            save_registry(f, registry)
            print(f"Project {project_name} sync_status marked as {status}.")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def cleanup_level_1(project_path):
    """Level 1: Purge DVC history (keep only the last 2 commits)."""
    print(f"  [Level 1] Purging DVC history for {project_path}")
    try:
        # dvc gc -w (workspace) --keep-experiments --rev HEAD --rev HEAD~1
        # Note: we use -f (force) to avoid interactive prompt
        subprocess.run(
            ["dvc", "gc", "-w", "-f", "--keep-experiments", "--rev", "HEAD", "--rev", "HEAD~1"],
            cwd=project_path,
            capture_output=True,
            text=True
        )
    except Exception as e:
        print(f"  Error in level 1 cleanup: {e}")

def cleanup_level_2(project_path):
    """Level 2: Delete large untracked files (> 500Mo) in working dirs, excluding .git and .dvc."""
    print(f"  [Level 2] Deleting large untracked files in {project_path}")
    try:
        for root, dirs, files in os.walk(project_path):
            # Skip .git and .dvc directories
            if ".git" in dirs:
                dirs.remove(".git")
            if ".dvc" in dirs:
                dirs.remove(".dvc")

            for file in files:
                file_path = Path(root) / file
                try:
                    if not file_path.is_symlink() and file_path.stat().st_size > LARGE_FILE_THRESHOLD_BYTES:
                        print(f"    Deleting large file: {file_path}")
                        file_path.unlink()
                except OSError:
                    pass
    except Exception as e:
        print(f"  Error in level 2 cleanup: {e}")

def cleanup_level_3(project_path):
    """Level 3: Delete local DVC cache."""
    print(f"  [Level 3] Deleting DVC cache for {project_path}")
    cache_path = project_path / ".dvc" / "cache"
    if cache_path.exists():
        try:
            shutil.rmtree(cache_path)
        except Exception as e:
            print(f"  Error in level 3 cleanup: {e}")

def cleanup_level_4(project_path):
    """Level 4: Delete the entire project directory."""
    print(f"  [Level 4] Deleting entire directory {project_path}")
    try:
        shutil.rmtree(project_path)
    except Exception as e:
        print(f"  Error in level 4 cleanup: {e}")

def get_free_space():
    repo_dir = get_repositories_dir()
    if not repo_dir.exists():
        return 0
    usage = shutil.disk_usage(repo_dir)
    return usage.free

def run_gc():
    repo_dir = get_repositories_dir()
    if not repo_dir.exists():
        print("Repositories directory does not exist. No GC needed.")
        return

    free_space = get_free_space()
    print(f"Free space: {free_space / (1024**3):.2f} GB (Threshold: {FREE_SPACE_THRESHOLD_GB} GB)")

    if free_space < FREE_SPACE_THRESHOLD_BYTES:
        print(f"Free space below threshold. Starting tiered cleanup...")
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

                any_cleanup_done = False
                for project_name, data in idle_projects:
                    if get_free_space() >= FREE_SPACE_THRESHOLD_BYTES:
                        break

                    validate_project_name(project_name)
                    project_path = repo_dir / project_name
                    if not project_path.exists():
                        continue

                    print(f"Cleaning project: {project_name}")

                    # Tiered cleanup levels
                    cleanup_levels = [
                        (cleanup_level_1, "Level 1"),
                        (cleanup_level_2, "Level 2"),
                        (cleanup_level_3, "Level 3"),
                        (cleanup_level_4, "Level 4")
                    ]

                    for cleanup_func, level_name in cleanup_levels:
                        cleanup_func(project_path)
                        any_cleanup_done = True

                        current_free_space = get_free_space()
                        print(f"  After {level_name}, free space: {current_free_space / (1024**3):.2f} GB")

                        if current_free_space >= FREE_SPACE_THRESHOLD_BYTES:
                            break

                        if level_name == "Level 4": # Project is gone
                            data["status"] = "deleted"
                            data["size_bytes"] = 0
                            break

                    # Update size in registry after partial or full cleanup
                    if project_path.exists():
                        data["size_bytes"] = get_dir_size(project_path)
                    else:
                        data["size_bytes"] = 0
                        data["status"] = "deleted"

                save_registry(f, registry)

                if any_cleanup_done and get_free_space() >= FREE_SPACE_THRESHOLD_BYTES:
                    # Notify headnode to trigger drain on workers
                    headnode_url = os.environ.get("HEADNODE_URL", "http://localhost:5000")
                    print(f"Threshold met. Notifying headnode at {headnode_url}...")
                    try:
                        import requests
                        requests.post(f"{headnode_url}/notify_cleanup", timeout=5)
                    except Exception as e:
                        print(f"Failed to notify cleanup: {e}")
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
        elif command == "get-free-space":
            print(get_free_space())
        elif command == "mark-sync-pending":
            mark_sync_status(sys.argv[2], "pending")
        elif command == "mark-sync-done":
            mark_sync_status(sys.argv[2], "done")
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    except Exception as e:
        print(f"Error in {command}: {e}")
        sys.exit(1)
