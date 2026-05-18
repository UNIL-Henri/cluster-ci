#!/bin/bash
set -e

# Cluster-CI Run CLI
# Helps researchers submit jobs via "Shadow Push" to a draft branch.

# Global variables for cleanup
RUN_ID=""
BRANCH=""

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

cleanup() {
    # If we have a branch, try to delete it
    if [ -n "$BRANCH" ]; then
        # If we have a run_id, try to cancel it if it's not completed
        if [ -n "$RUN_ID" ]; then
             local status=$(gh run view "$RUN_ID" --json status -q .status 2>/dev/null || echo "completed")
             if [[ "$status" != "completed" && "$status" != "success" && "$status" != "failure" && "$status" != "cancelled" ]]; then
                 echo -e "\n🛑 Cancelling GitHub run $RUN_ID..."
                 gh run cancel "$RUN_ID"
             fi
        fi
        echo "🧹 Cleaning up remote branch $BRANCH..."
        git push origin --delete "$BRANCH" --quiet >/dev/null 2>&1 || true
    fi
}

shadow_run() {
    local background=$1
    check_gh_auth
    local user=$(get_current_user)
    BRANCH="cluster-draft/$user"

    # Register the cleanup trap to ensure the branch is deleted on exit
    trap cleanup EXIT

    echo "🏗️  Preparing shadow push for user: $user (including untracked files)"

    # Create a temporary index to include untracked files without affecting the current index
    local temp_index=$(mktemp)
    local commit_to_push=""

    # Use a subshell to keep the environment clean
    commit_to_push=$(
        export GIT_INDEX_FILE="$temp_index"
        git read-tree HEAD
        git add --all  # Includes tracked and untracked files
        local tree=$(git write-tree)
        git commit-tree "$tree" -p HEAD -m "Shadow commit for $user"
    )
    rm -f "$temp_index"

    if [ -z "$commit_to_push" ]; then
        echo "❌ Error: Failed to create shadow commit."
        exit 1
    fi

    echo "🚀 Shadow pushing to origin/$BRANCH..."
    git push origin "$commit_to_push:refs/heads/$BRANCH" --force --quiet

    if [ "$background" = true ]; then
        echo "✅ Run submitted in background. You can watch it with: cluster-run list"
        # In background mode, we DON'T want the EXIT trap to delete the branch immediately
        # because the GHA is still running. The researcher will have to delete it later or
        # it will be overwritten by next run.
        # Actually, for background runs, we should probably just untrap.
        trap - EXIT
        return
    fi

    # 3. Find and watch the run
    echo "⏳ Waiting for GitHub Actions to trigger..."
    sleep 3
    for i in {1..15}; do
        RUN_ID=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId,status -q '.[0] | select(.status != "completed") | .databaseId')
        [ -n "$RUN_ID" ] && break
        sleep 2
    done

    if [ -z "$RUN_ID" ]; then
        # Check if it already finished (very fast run?)
        RUN_ID=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId')
    fi

    if [ -z "$RUN_ID" ]; then
        echo "❌ Error: Could not find the triggered workflow run."
        exit 1
    fi

    echo "📺 Streaming logs for run $RUN_ID (Ctrl+C to cancel)..."

    gh run watch "$RUN_ID"

    # Check final status
    local conclusion=$(gh run view "$RUN_ID" --json conclusion -q .conclusion)
    if [ "$conclusion" == "success" ]; then
        echo "✅ Cluster-CI run completed successfully."
    else
        echo "❌ Cluster-CI run finished with status: $conclusion"
    fi

    # Final cleanup will be handled by the EXIT trap
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
            BRANCH="cluster-draft/$USER"
            RUN_ID=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId')
             if [ -z "$RUN_ID" ]; then
                echo "Usage: cluster-run cancel <run_id>"
                exit 1
            fi
            echo "🛑 Cancelling run $RUN_ID..."
            gh run cancel "$RUN_ID"
            echo "🧹 Deleting branch $BRANCH..."
            git push origin --delete "$BRANCH" --quiet >/dev/null 2>&1 || true
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
