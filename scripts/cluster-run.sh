#!/bin/bash
set -e

# Cluster-CI Run CLI
# Helps researchers submit jobs via "Shadow Push" to a draft branch.

# Global variables for cleanup
RUN_ID=""
BRANCH=""
USER_INTERRUPTED="false"

trap_ctrl_c() {
    USER_INTERRUPTED="true"
    exit 130
}
trap trap_ctrl_c SIGINT

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
        # If we have a run_id, try to cancel it ONLY if the user manually interrupted (Ctrl+C)
        if [ -n "$RUN_ID" ] && [ "$USER_INTERRUPTED" = "true" ]; then
             local raw_status=$(gh run view "$RUN_ID" --json status -q .status < /dev/null 2>/dev/null || echo "completed"); local status=$(echo "$raw_status" | tr -cd 'a-zA-Z')
             if [[ "$status" != "completed" && "$status" != "success" && "$status" != "failure" && "$status" != "cancelled" ]]; then
                 echo -e "\n🛑 Cancelling GitHub run $RUN_ID..."
                 gh run cancel "$RUN_ID" < /dev/null 2>&1 || true
             fi
        fi
        echo "🧹 Cleaning up remote branch $BRANCH..."
        git push origin --delete "$BRANCH" --quiet >/dev/null 2>&1 || true
    fi
}

stream_logs() {
    local run_id=$1
    local commit_sha=$2
    local last_line_count=0
    local tmate_connected=false

    # If we have a commit SHA, let's poll for a tmate status
    if [ -n "$commit_sha" ]; then
        echo "🔍 Polling for live terminal connection (timeout ~2 mins)..."
        for attempt in {1..60}; do
            # Check if run has already completed (no need to connect if done)
            local run_status
            run_status=$(gh run view "$run_id" --json status -q '.status' 2>/dev/null || echo "completed")
            if [ "$run_status" == "completed" ]; then
                break
            fi

            # Fetch statuses via GitHub API
            local repo_full_name
            repo_full_name=$(git config --get remote.origin.url 2>/dev/null | sed -E 's/.*github.com[:\/](.*)\.git/\1/' 2>/dev/null)
            [ -z "$repo_full_name" ] && repo_full_name="UNIL-DESI/cluster-ci"

            local status_json
            status_json=$(gh api "repos/$repo_full_name/commits/$commit_sha/statuses" 2>/dev/null || true)
            
            if [ -n "$status_json" ]; then
                # Search for a status with context: "tmate" via python3 to be robust and independent of jq
                local tmate_url
                tmate_url=$(echo "$status_json" | python3 -c "import sys, json; data = json.load(sys.stdin); item = next((x for x in data if x.get('context') == 'tmate'), None); print(item['target_url'] if item else '')" 2>/dev/null || echo "")
                local tmate_ssh
                tmate_ssh=$(echo "$status_json" | python3 -c "import sys, json; data = json.load(sys.stdin); item = next((x for x in data if x.get('context') == 'tmate'), None); print(item['description'].replace('SSH: ', '') if item else '')" 2>/dev/null || echo "")
                
                if [ -n "$tmate_ssh" ] && [[ "$tmate_ssh" == ssh* ]]; then
                    echo "🟢 Live terminal stream found!"
                    echo "🔗 Web: $tmate_url"
                    echo "🔌 SSH: $tmate_ssh"
                    echo "⚡ Connecting to runner via SSH (exit SSH or let the job finish to complete)..."
                    echo "=========================================================================="
                    
                    # Execute SSH directly to connect the user to the tmate session
                    eval "$tmate_ssh -o StrictHostKeyChecking=no"
                    
                    echo "=========================================================================="
                    echo "🔌 Disconnected from live terminal. Fetching final logs..."
                    tmate_connected=true
                    break
                fi
            fi
            sleep 2
        done
    fi

    # Fallback/Final Logs
    while true; do
        local logs
        logs=$(gh run view "$run_id" --log 2>/dev/null || true)
        
        if [ -n "$logs" ]; then
            local current_line_count
            current_line_count=$(echo "$logs" | wc -l)
            
            if [ "$current_line_count" -gt "$last_line_count" ]; then
                # Print new lines and format beautifully
                echo "$logs" | tail -n +"$((last_line_count + 1))" | awk -F'\t' '{
                    step=$2; 
                    log_line=$3;
                    gsub(/^\xEF\xBB\xBF/, "", log_line); # Strip BOM if present
                    gsub(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z /, "", log_line);
                    gsub(/##\[group\]/, "▶️  ", log_line);
                    gsub(/##\[endgroup\]/, "", log_line);
                    
                    if (log_line != "") {
                        printf "\033[90m[%s]\033[0m %s\n", step, log_line;
                    }
                }'
                last_line_count=$current_line_count
            fi
        fi

        # Check run status
        local info
        info=$(gh run view "$run_id" --json status -q '.status' 2>/dev/null || echo "completed")
        if [ "$info" == "completed" ]; then
            # Final log flush
            logs=$(gh run view "$run_id" --log 2>/dev/null || true)
            current_line_count=$(echo "$logs" | wc -l)
            if [ "$current_line_count" -gt "$last_line_count" ]; then
                echo "$logs" | tail -n +"$((last_line_count + 1))" | awk -F'\t' '{
                    step=$2; 
                    log_line=$3;
                    gsub(/^\xEF\xBB\xBF/, "", log_line);
                    gsub(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z /, "", log_line);
                    gsub(/##\[group\]/, "▶️  ", log_line);
                    gsub(/##\[endgroup\]/, "", log_line);
                    if (log_line != "") {
                        printf "\033[90m[%s]\033[0m %s\n", step, log_line;
                    }
                }'
            fi
            break
        fi
        sleep 2
    done
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

    # Get last run ID before push to prevent matching stale run
    local last_known_run_id
    last_known_run_id=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || echo "")

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
    sleep 4
    for i in {1..15}; do
        RUN_ID=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId,status -q '.[0] | select(.status != "completed") | .databaseId' 2>/dev/null || true)
        if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "$last_known_run_id" ]; then
            break
        fi
        RUN_ID=""
        sleep 2
    done

    if [ -z "$RUN_ID" ]; then
        # Check if it already finished (very fast run?)
        RUN_ID=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)
        if [ "$RUN_ID" == "$last_known_run_id" ]; then
            RUN_ID=""
        fi
    fi

    if [ -z "$RUN_ID" ]; then
        echo "❌ Error: Could not find the triggered workflow run."
        exit 1
    fi

    echo "📺 Streaming logs for run $RUN_ID (Ctrl+C to cancel)..."

    stream_logs "$RUN_ID" "$commit_to_push"

    # Check final status with robust retry to handle GitHub API latency
    local conclusion=""
    for i in {1..5}; do
        conclusion=$(gh run view "$RUN_ID" --json conclusion -q .conclusion < /dev/null 2>/dev/null || echo "")
        [ -n "$conclusion" ] && [ "$conclusion" != "null" ] && break
        sleep 1
    done

    if [ "$conclusion" == "success" ]; then
        echo "✅ Cluster-CI run completed successfully."
    else
        echo "❌ Cluster-CI run finished with status: ${conclusion:-unknown}"
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
