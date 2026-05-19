#!/bin/bash
set -e

# Disable Python output buffering globally
export PYTHONUNBUFFERED=1


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
                 # Cancel silently to avoid scary terminal output
                 gh run cancel "$RUN_ID" < /dev/null >/dev/null 2>&1 || true
             fi
        fi
        # Silence branch deletion
        git push origin --delete "$BRANCH" --quiet >/dev/null 2>&1 || true
    fi
}

stream_logs() {
    local run_id=$1
    local commit_sha=$2
    local last_line_count=0
    local tmate_connected=false
    local repo_full_name
    repo_full_name=$(git config --get remote.origin.url 2>/dev/null | sed -E 's/.*github.com[:\/](.*)\.git/\1/' 2>/dev/null)
    [ -z "$repo_full_name" ] && repo_full_name="UNIL-DESI/cluster-ci"

    # 1. Try to fetch job_id from Headnode via SSH to stream real-time worker logs
    local job_id=""
    if command -v sshpass &> /dev/null; then
        echo "🔍 Connecting to Headnode scheduler to capture unbuffered worker logs..."
        for attempt in {1..20}; do
            # Check if run has already completed in GitHub
            local run_status
            run_status=$(gh run view "$run_id" --json status -q '.status' 2>/dev/null || echo "completed")
            if [ "$run_status" == "completed" ]; then
                break
            fi

            if [ -n "$commit_sha" ]; then
                job_id=$(SSHPASS='9wE1Ry^6JUK*1zxX5Aa3' sshpass -e ssh -q -o ConnectTimeout=3 -o StrictHostKeyChecking=no henri@130.223.73.209 "sqlite3 /home/henri/cluster-ci/cluster_scheduler.db \"SELECT job_id FROM jobs WHERE repo = '$repo_full_name' AND commit_hash = '$commit_sha' LIMIT 1;\"" 2>/dev/null || echo "")
            else
                job_id=$(SSHPASS='9wE1Ry^6JUK*1zxX5Aa3' sshpass -e ssh -q -o ConnectTimeout=3 -o StrictHostKeyChecking=no henri@130.223.73.209 "sqlite3 /home/henri/cluster-ci/cluster_scheduler.db \"SELECT job_id FROM jobs WHERE repo = '$repo_full_name' AND branch = '$BRANCH' ORDER BY created_at DESC LIMIT 1;\"" 2>/dev/null || echo "")
            fi
            if [ -n "$job_id" ]; then
                echo "🟢 Connected to job: $job_id"
                break
            fi
            sleep 2
        done
    fi

    # 2. If job_id was found, stream logs directly and in real-time from the worker API
    if [ -n "$job_id" ]; then
        local log_offset=0
        local dots=""
        local is_first_progress=true
        local last_job_status=""
        
        while true; do
            # Fetch job status from SQLite to handle queuing animations and termination
            local job_status
            job_status=$(SSHPASS='9wE1Ry^6JUK*1zxX5Aa3' sshpass -e ssh -q -o ConnectTimeout=3 -o StrictHostKeyChecking=no henri@130.223.73.209 "sqlite3 /home/henri/cluster-ci/cluster_scheduler.db \"SELECT status FROM jobs WHERE job_id = '$job_id';\"" 2>/dev/null || echo "completed")
            
            if [ "$job_status" != "$last_job_status" ]; then
                if [ "$job_status" == "pending" ]; then
                    echo "⏳ Job is in scheduler queue (waiting for a worker slot)..."
                elif [ "$job_status" == "running" ]; then
                    if [ "$last_job_status" == "pending" ]; then echo ""; fi
                    echo "🏃 Job has started on worker slot..."
                fi
                last_job_status=$job_status
                dots=""
            fi

            # Handle pending animation
            if [ "$job_status" == "pending" ]; then
                dots="${dots}."
                if [ ${#dots} -gt 5 ]; then dots="."; fi
                printf "\r⏳ Waiting in queue%s     " "$dots"
                sleep 2
                continue
            fi

            # Fetch unbuffered logs in real-time from Headnode REST API proxy
            local resp_json
            resp_json=$(SSHPASS='9wE1Ry^6JUK*1zxX5Aa3' sshpass -e ssh -q -o ConnectTimeout=3 -o StrictHostKeyChecking=no henri@130.223.73.209 "curl -s --connect-timeout 3 http://localhost:5000/api/jobs/$job_id/logs?offset=$log_offset" 2>/dev/null || echo "")
            
            if [ -n "$resp_json" ] && [[ "$resp_json" == *'"logs"'* ]]; then
                local new_logs
                new_logs=$(echo "$resp_json" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('logs', ''))" 2>/dev/null || echo "")
                if [ -n "$new_logs" ]; then
                    # Clear carriage return booting line if present
                    if [ "$is_first_progress" == "true" ]; then
                        echo ""
                        is_first_progress=false
                    fi
                    # Stream logs out instantly to terminal
                    printf "%s" "$new_logs"
                    
                    # Update offset
                    log_offset=$(echo "$resp_json" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('offset', '$log_offset'))" 2>/dev/null || echo "$log_offset")
                fi
            elif [ "$is_first_progress" == "true" ]; then
                dots="${dots}."
                if [ ${#dots} -gt 5 ]; then dots="."; fi
                printf "\r⏱️  Booting job environment%s     " "$dots"
            fi

            # Check for completion
            if [[ "$job_status" != "running" && "$job_status" != "pending" ]]; then
                # Perform one last logs flush
                resp_json=$(SSHPASS='9wE1Ry^6JUK*1zxX5Aa3' sshpass -e ssh -q -o ConnectTimeout=3 -o StrictHostKeyChecking=no henri@130.223.73.209 "curl -s --connect-timeout 3 http://localhost:5000/api/jobs/$job_id/logs?offset=$log_offset" 2>/dev/null || echo "")
                if [ -n "$resp_json" ] && [[ "$resp_json" == *'"logs"'* ]]; then
                    local final_logs
                    final_logs=$(echo "$resp_json" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('logs', ''))" 2>/dev/null || echo "")
                    [ -n "$final_logs" ] && printf "%s" "$final_logs"
                fi
                if [ "$is_first_progress" == "true" ]; then
                    echo ""
                fi
                break
            fi
            sleep 2
        done
        return 0
    fi

    # 3. Fallback: Classic GitHub CLI log extraction (for completed logs only, since GHA buffers running logs)
    echo "⚠️  Worker API connection unavailable or job not found. Falling back to GitHub CLI logs..."
    local last_status=""
    local dots=""
    local is_first_progress=true
    
    while true; do
        # 1. Fetch current status of the GitHub Actions run
        local info
        info=$(gh run view "$run_id" --json status -q '.status' 2>/dev/null || echo "completed")
        
        # 2. Print user-friendly queue and progress states
        if [ "$info" != "$last_status" ]; then
            if [ "$info" == "queued" ]; then
                echo "⏳ Job is in GitHub queue (waiting for a runner to pick it up)..."
            elif [ "$info" == "in_progress" ]; then
                # Clear carriage return line if we were printing dots
                if [ "$last_status" == "queued" ]; then
                    echo ""
                fi
                echo "🏃 Job has started and is now in progress..."
            fi
            last_status=$info
            dots=""
        fi
        
        # 3. Handle queue animation
        if [ "$info" == "queued" ]; then
            dots="${dots}."
            if [ ${#dots} -gt 5 ]; then dots="."; fi
            printf "\r⏳ Waiting in queue%s     " "$dots"
            sleep 2
            continue
        fi

        # 4. Fetch logs if run is active or completed
        local logs
        logs=$(gh run view "$run_id" --log 2>/dev/null || true)
        
        if [ -n "$logs" ]; then
            # Clear carriage return booting line if present
            if [ "$is_first_progress" == "true" ]; then
                echo ""
                is_first_progress=false
            fi
            
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
        elif [ "$is_first_progress" == "true" ]; then
            # Logs are still empty but job is in progress
            dots="${dots}."
            if [ ${#dots} -gt 5 ]; then dots="."; fi
            printf "\r⏱️  Booting job environment%s     " "$dots"
        fi

        # 5. Check if run is done
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
            # Add newline if we finished on a booting status line
            if [ "$is_first_progress" == "true" ]; then
                echo ""
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
