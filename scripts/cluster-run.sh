#!/bin/bash
set -e

# Cluster-CI Run CLI
# Helps researchers submit jobs via "Shadow Push" to a draft branch.

show_help() {
    echo "Usage: cluster-run [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  (default)           Package changes and submit a shadow run"
    echo "  list                List recent cluster runs"
    echo "  view <run_id>       View details/logs of a specific run"
    echo "  cancel <run_id>     Cancel a specific run"
    echo ""
    echo "Options:"
    echo "  --background, -b    Submit the run and exit without watching logs"
    echo "  --help, -h          Show this help message"
    echo ""
}

check_dependencies() {
    if ! command -v gh &> /dev/null; then
        echo "❌ Error: github-cli (gh) is not installed."
        echo "Please install it: https://cli.github.com/"
        exit 1
    fi
    if ! git rev-parse --is-inside-work-tree &> /dev/null; then
        echo "❌ Error: Not in a git repository."
        exit 1
    fi
}

check_gh_auth() {
    if ! gh auth status >/dev/null 2>&1; then
        echo "🔐 GitHub CLI not authenticated. Starting login..."
        gh auth login
    fi
}

get_current_user() {
    gh api user -q .login
}

delete_remote_branch() {
    local branch=$1
    git push origin --delete "$branch" --quiet >/dev/null 2>&1 || true
}

cleanup() {
    local run_id=$1
    local branch=$2
    echo -e "\n🛑 Interruption detected. Cancelling run $run_id and cleaning up..."
    gh run cancel "$run_id" >/dev/null 2>&1 || true
    delete_remote_branch "$branch"
    exit 130
}

shadow_run() {
    local background=$1
    check_gh_auth
    local user=$(get_current_user)
    local branch="cluster-draft/$user"

    echo "🏗️  Preparing shadow push for user: $user"

    # 1. Create a commit representing the current state (including uncommitted changes)
    # git stash create returns a commit hash that is NOT reachable from any branch but contains everything.
    local stash_hash=$(git stash create)
    local commit_to_push=""

    if [ -z "$stash_hash" ]; then
        commit_to_push=$(git rev-parse HEAD)
        echo "✨ No uncommitted changes. Using HEAD ($commit_to_push)."
    else
        commit_to_push=$stash_hash
        echo "📦 Included uncommitted changes (commit: $commit_to_push)."
    fi

    # 2. Push to the shadow branch
    echo "🚀 Shadow pushing to origin/$branch..."
    git push origin "$commit_to_push:refs/heads/$branch" --force --quiet

    if [ "$background" = true ]; then
        echo "✅ Run submitted in background. You can watch it with: cluster-run list"
        return
    fi

    # 3. Find and watch the run
    echo "⏳ Waiting for GitHub Actions to trigger..."
    sleep 3
    local run_id=""
    for i in {1..15}; do
        run_id=$(gh run list --branch "$branch" --limit 1 --json databaseId,status -q '.[0] | select(.status != "completed") | .databaseId')
        [ -n "$run_id" ] && break
        sleep 2
    done

    if [ -z "$run_id" ]; then
        # Check if it already finished (very fast run?)
        run_id=$(gh run list --branch "$branch" --limit 1 --json databaseId -q '.[0].databaseId')
    fi

    if [ -z "$run_id" ]; then
        echo "❌ Error: Could not find the triggered workflow run."
        exit 1
    fi

    echo "📺 Streaming logs for run $run_id (Ctrl+C to cancel)..."

    # Setup cleanup on SIGINT
    trap "cleanup $run_id $branch" SIGINT

    gh run watch "$run_id"

    # Check final status
    local conclusion=$(gh run view "$run_id" --json conclusion -q .conclusion)
    if [ "$conclusion" == "success" ]; then
        echo "✅ Cluster-CI run completed successfully."
    else
        echo "❌ Cluster-CI run finished with status: $conclusion"
    fi

    delete_remote_branch "$branch"
}

# --- CLI Entry Point ---
check_dependencies

COMMAND=$1
case "$COMMAND" in
    list)
        gh run list --workflow "Cluster-CI Execution"
        ;;
    view)
        shift
        if [ -z "$1" ]; then
            # If no ID provided, try to find the last run for this user
            check_gh_auth
            USER=$(get_current_user)
            RUN_ID=$(gh run list --branch "cluster-draft/$USER" --limit 1 --json databaseId -q '.[0].databaseId')
            if [ -z "$RUN_ID" ]; then
                echo "Usage: cluster-run view <run_id>"
                exit 1
            fi
            gh run view "$RUN_ID" --log
        else
            gh run view "$@"
        fi
        ;;
    cancel)
        shift
        if [ -z "$1" ]; then
            check_gh_auth
            USER=$(get_current_user)
            RUN_ID=$(gh run list --branch "cluster-draft/$USER" --limit 1 --json databaseId -q '.[0].databaseId')
             if [ -z "$RUN_ID" ]; then
                echo "Usage: cluster-run cancel <run_id>"
                exit 1
            fi
            gh run cancel "$RUN_ID"
            delete_remote_branch "cluster-draft/$USER"
        else
            gh run cancel "$@"
        fi
        ;;
    --help|-h)
        show_help
        ;;
    *)
        BG=false
        if [[ "$1" == "--background" || "$1" == "-b" ]]; then
            BG=true
        fi
        shadow_run "$BG"
        ;;
esac
