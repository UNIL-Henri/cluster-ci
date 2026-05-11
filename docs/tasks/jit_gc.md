# JIT Garbage Collector (LRU)

## 1. Context & Why
To preserve disk space on computing machines while benefiting from local cache speed, we dynamically manage workspaces. The chosen approach is Just-In-Time (JIT) deletion before new jobs are executed.

## 2. Operation
The system relies on a local metadata registry (`repositories/registry.json`) that tracks:
- Project name (owner/repo).
- Last execution date (Timestamp).
- Disk size (bytes).
- Status (`running`, `idle`, or `deleted`).

### Cleanup Policy (LRU)
Before each job, the orchestrator checks the available disk space on the repository partition.
- **Safety Threshold:** 100 GB.
- If free space is below this threshold, the GC identifies projects marked as `idle`.
- It deletes local folders starting from the oldest (Least Recently Used) until the 100 GB threshold is met.
- Active projects (`running`) are never deleted.

## 3. Components
- `src/runner/gc_orchestrator.py`: Python script managing the GC logic and registry.
- `src/runner/run_research_pipeline.sh`: Shell orchestrator integrating GC calls.

## 4. Integration
The orchestrator calls `gc_orchestrator.py` at key moments:
1. **Before the job:** `run-gc` to free space and `update-running` to mark the project as active.
2. **After the job (via EXIT trap):** `update-idle` to update the occupied size and mark the project as inactive.
