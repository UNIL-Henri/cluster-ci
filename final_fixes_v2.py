import os
import subprocess
import uuid

def fix_headnode_service():
    filepath = 'src/scheduler/headnode_service.py'
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Fix historical artifact extraction: No fallback to worker for historical revisions, and cleanup tmp files.
    # We'll use a wrapper to handle cleanup after response.

    new_artifacts_code = """
@app.route('/artifacts/<repo_owner>/<repo_name>/<rev>/<path:file_path>', methods=['GET'])
def artifacts(repo_owner, repo_name, rev, file_path):
    repo_slug = f"{repo_owner}/{repo_name}"
    repo_url = f"https://github.com/{repo_slug}"

    # --- Case 1: REAL DVC EXTRACTION (Historical Integrity) ---
    # We extract the file exactly as it was at the given revision
    request_id = str(uuid.uuid4())
    tmp_dir = os.path.join(REPOS_DIR, "_tmp_artifacts", request_id)
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        local_repo_path = os.path.join(REPOS_DIR, repo_slug)
        source = local_repo_path if os.path.exists(local_repo_path) else repo_url
        cmd = ["dvc", "get", source, file_path, "--rev", rev, "--out", tmp_dir]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            filename = os.path.basename(file_path)
            full_path = os.path.join(tmp_dir, filename)

            def generate():
                try:
                    with open(full_path, 'rb') as f:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            yield chunk
                finally:
                    # Robust cleanup: delete the whole tmp directory for this request
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            return Response(generate(), mimetype='application/octet-stream',
                            headers={"Content-Disposition": f"attachment; filename={filename}"})

        # If DVC get failed, we DO NOT fallback to worker for historical revisions (rev looks like a hash)
        # However, if rev is a branch name, Case 2 might still be valid for the 'latest' of that branch.
        # But per requirements: "Supprime ce repli en cascade pour les requêtes historiques"
        # We consider any request with a 'rev' as a historical request needing integrity.
        return jsonify({"error": f"Failed to extract historical artifact: {result.stderr}"}), 404

    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": f"Internal error during extraction: {str(e)}"}), 500
"""

    # Find start and end of current artifacts function to replace it
    import re
    pattern = r"@app\.route\('/artifacts/.*?def artifacts\(.*?\):.*?return jsonify\({\"error\": \"Artifact not found.*?\}\), 404"
    # This regex is a bit complex due to nested try/except, let's use a simpler marker replacement

    start_marker = "@app.route('/artifacts/<repo_owner>/<repo_name>/<rev>/<path:file_path>', methods=['GET'])"
    end_marker = "return jsonify({\"error\": \"Artifact not found locally or on any known worker for this branch\"}), 404"

    if start_marker in content and end_marker in content:
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker) + len(end_marker)
        content = content[:start_idx] + new_artifacts_code.strip() + content[end_idx:]
        print("Replaced artifacts route")
    else:
        print("Could not find artifacts markers for replacement")

    with open(filepath, 'w') as f:
        f.write(content)

def fix_dashboard():
    filepath = 'src/scheduler/templates/dashboard.html'
    with open(filepath, 'r') as f:
        content = f.read()

    # 2. Fix client-side observability in catch block
    old_error_handler = "let error = 'Internal Server Error'; try { if (contentType && contentType.includes('application/json')) { const data = await resp.json(); error = data.error || data.message || error; } } catch(e) {}"
    new_error_handler = "let error = 'Internal Server Error'; try { if (contentType && contentType.includes('application/json')) { const data = await resp.json(); error = data.error || data.message || error; } else { error = 'Server returned ' + resp.status + ' (' + resp.statusText + ')'; } } catch(e) { console.error('Error parsing failure response:', e); error = 'Parse Error: ' + e.message; }"

    content = content.replace(old_error_handler, new_error_handler)

    with open(filepath, 'w') as f:
        f.write(content)

fix_headnode_service()
fix_dashboard()
