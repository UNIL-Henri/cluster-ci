# Orchestrator Runner Setup

## 1. Context & Discussion (Narrative)
> *Handover*: Replacing the SlurmRay "push" architecture with a GitOps "pull" model. Agents had difficulty maintaining active context during long research job executions. The idea is to move away from active waiting to a GitHub Actions Self-Hosted Runner as a systemd service on our Ubuntu machine.

The goal is to implement the `run_research_pipeline.sh` orchestration script (or equivalent) that will be called by the runner at each CI event. This script must manage its workspaces in an absolute persistent directory (`workspaces/$REPO_NAME`) on the machine to ensure reuse of the local `.dvc/cache`, optimize downloads, and perform `uv sync && uv run dvc repro`.

## 2. Affected Files
- `src/runner/run_research_pipeline.sh`
- (potentially a systemd service template if advanced supervision is needed, though the standard GitHub Runner already integrates this)

## 3. Objectives (Definition of Done)
- A bash pipeline script capable of receiving the repository path and/or target PR branch from the runner.
- The script must orchestrate:
  - Verification/creation of the persistent `workspaces/$REPO_NAME` directory.
  - Clean and isolated git clone/pull on the specific branch.
  - Configuration and launch of execution with `uv sync` then `uv run dvc repro`.
- Standard output and error must be clearly pushed back to the GitHub Actions output so that Joules can analyze the traces.
