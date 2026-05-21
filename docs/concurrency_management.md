# Concurrency Management & Signal Propagation

This document describes how Cluster-CI handles GitHub Actions job cancellations and ensures that compute resources on Workers are correctly freed.

## Problem Statement

When a new commit is pushed to a branch, GitHub Actions may cancel existing runs for that same branch if the `concurrency` key is used. GitHub sends a `SIGTERM` signal to the runner process.

In a distributed architecture:
1. The **Headnode** receives the `SIGTERM`.
2. The **Worker** (Lenovo) is executing the actual DVC pipeline.

If the Headnode simply dies, the Worker continues the computation, creating "zombie" jobs that waste RAM and CPU.

## Solution: Signal Propagation

We implemented a propagation mechanism to ensure the Worker is notified when a job is cancelled on the Headnode.

### 1. Granular Concurrency Groups

In `.github/workflows/cluster-ci.yml`, the concurrency group is set to:
```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```
This ensures that only jobs on the same branch/PR are cancelled, allowing multiple researchers to work on different branches simultaneously without interrupting each other.

### 2. Signal Interception on Headnode

The `src/scheduler/submit_job.py` script (which runs on the Headnode) intercepts `SIGINT` and `SIGTERM` signals. When a signal is received:
- It queries the Headnode API to find the assigned Worker's `service_url`.
- It sends a POST request to the Worker's `/cancel/<job_id>` endpoint.
- It updates the job status to `failed` with exit code `-15` (SIGTERM).

### 3. Process Tree Termination on Worker

The `src/scheduler/worker_agent.py` exposes the `/cancel/<job_id>` route. When called:
- It verifies that the `job_id` matches the currently running job.
- It uses `psutil` to recursively kill the entire process tree of the CI job (including DVC and all sub-processes).
- This immediately frees the reserved RAM for other jobs.

## Flow Diagram

```text
GitHub Actions -> [SIGTERM] -> Headnode (submit_job.py)
                                     |
                                     v
                          Worker (/cancel/<job_id>)
                                     |
                                     v
                          [Kill Process Tree] -> RAM Free
```
