# JIT Garbage Collector (LRU)

## 1. Context & Why
To preserve disk space on computing machines while benefiting from local cache speed, we dynamically manage workspaces. The chosen approach is Just-In-Time (JIT) deletion before new jobs are executed.

## 2. Operation
The system relies on a local metadata registry (`repositories/registry.json`) that tracks:
- Project name (owner/repo).
- Last execution date (Timestamp).
- Disk size (bytes).
- Status (`running`, `idle`, or `deleted`).

### Cleanup Policy (Tiered LRU)
Before each job, the orchestrator checks the available disk space on the repository partition.
- **Safety Threshold:** 100 GB (configurable via `GC_FREE_SPACE_THRESHOLD_GB`).
- If free space is below this threshold, the GC identifies projects marked as `idle`.
- It executes a **Tiered Cleanup** (Levels 1 to 5) starting from the oldest (Least Recently Used) projects until the threshold is met.

#### Cleanup Levels:
1. **Level 1 (DVC GC):** Purges DVC history, keeping only the last 2 commits to save space while keeping recent history.
2. **Level 2 (Large Files):** Deletes untracked files larger than 500 MB in the workspace.
3. **Level 3 (Docker Volume):** Deletes the project-specific Docker volume (`cluster-ci-home-<owner-repo>`) used for Python dependencies (`~/.local` and `uv` cache).
4. **Level 4 (DVC Cache):** Deletes the entire local `.dvc/cache` directory.
5. **Level 5 (Full Wipe):** Deletes the entire project repository directory.

Active projects (`running`) are never targeted by the GC.

## 3. Environment Isolation
To prevent dependency collisions between different projects running on the same worker, each project uses a dedicated Docker volume for its home directory. This ensures that `uv` or `pip` installations in one project do not overwrite system packages or caches of another project.

Volume naming convention: `cluster-ci-home-<owner-repo>` (where `/` is replaced by `-`).

## 4. Components
- `src/runner/gc_orchestrator.py`: Python script managing the GC logic and registry.
- `src/runner/run_research_pipeline.sh`: Shell orchestrator integrating GC calls.

## 5. Integration
The orchestrator calls `gc_orchestrator.py` at key moments:
1. **Before the job:** `run-gc` to free space and `update-running` to mark the project as active.
2. **After the job (via EXIT trap):** `update-idle` to update the occupied size and mark the project as inactive.
