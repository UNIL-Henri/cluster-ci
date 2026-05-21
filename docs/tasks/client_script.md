# Client Installation Script

## 1. Context & Discussion (Narrative)
> *Handover*: Developer experience (or rather "agent experience") must be impeccable. To simplify the integration of the new Cluster CI across multiple existing research projects, we decided to provide an express (minimalist) installer.

This client script will be executed in research repositories (those Joules works on) via `curl -sSL ... | bash`. It must auto-generate the `.github/workflows/` directory and files containing a standard `.github/workflows/execute_on_ubuntu.yml` workflow calibrated to target the self-hosted tags of our new test machine.

## 2. Affected Files
- `install.sh` (at the root of the cluster-ci repository)
- `templates/execute_on_ubuntu.yml` (template for the distributed CI workflow)

## 3. Objectives (Definition of Done)
- A distributed and robust `install.sh` file.
- The script detects the git root and silently injects the `.github/workflows/` folder.
- The injected `execute_on_ubuntu.yml` payload is a valid, ready-to-use GitHub Actions workflow.
- The injected client workflow is configured to listen for "Push" or "Pull Request" events, use the "self-hosted" runner tag, and call the orchestration layer (which will be pre-deployed on said runner).
- Log instructions informing Joules of what to do next if the installation succeeds or fails.
