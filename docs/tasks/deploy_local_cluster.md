# Local Cluster Deployment and Testing

## 1. Context & Discussion (Narrative)
> *Handover*: To validate the proper operation of Cluster-CI, we decided to transform the current development machine into a target "Cluster". This allows testing the entire chain in real conditions (Push to Github -> CI Trigger -> Local Runner -> Orchestrator -> `dvc repro`).

The objective is to actually install a GitHub Actions Self-Hosted Runner on this machine, attached either to the `cluster-ci` repository or the entire organization, and to validate that the local loop executes correctly without conflicts.

## 2. Affected Files
- `src/cluster/install_runner.sh` (New host-specific installation script)
- `.github/workflows/test_runner.yml` (Internal validation CI for `cluster-ci`)

## 3. Objectives (Definition of Done)
- A script capable of downloading and building the GitHub Actions Runner locally.
- The installation includes `uv` verification/installation.
- Configure the runner in system service mode to listen for jobs with the "self-hosted" tag.
- Trigger a `git push` that initiates a basic test CI via the `.github/workflows/test_runner.yml` workflow, ensuring the local runner responds and correctly executes the orchestration script in the `workspaces/` folder.
