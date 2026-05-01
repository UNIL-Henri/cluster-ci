from flask import Flask, request, jsonify
from persistence import init_db, get_db_conn
import uuid
import datetime

app = Flask(__name__)

@app.route('/register_worker', methods=['POST'])
def register_worker():
    data = request.json
    worker_id = data.get('worker_id')
    hostname = data.get('hostname')
    total_ram_gb = data.get('total_ram_gb')
    # We ignore the worker's reported available RAM for scheduling
    # to avoid the race condition where physical RAM isn't yet claimed by jobs.
    # available_ram_gb = data.get('available_ram_gb')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        # On first registration, we initialize available_ram_gb to total_ram_gb
        cursor.execute('''
            INSERT INTO workers (worker_id, hostname, total_ram_gb, available_ram_gb, last_seen, status)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 'online')
            ON CONFLICT(worker_id) DO UPDATE SET
                last_seen = CURRENT_TIMESTAMP,
                status = 'online'
        ''', (worker_id, hostname, total_ram_gb, total_ram_gb))
        conn.commit()

    return jsonify({"status": "ok"})

@app.route('/submit_job', methods=['POST'])
def submit_job():
    data = request.json
    repo = data.get('repo')
    branch = data.get('branch')
    ram_required_gb = data.get('ram_required_gb', 0)
    job_id = str(uuid.uuid4())

    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (job_id, repo, branch, ram_required_gb, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (job_id, repo, branch, ram_required_gb))
        conn.commit()

    return jsonify({"job_id": job_id, "status": "pending"})

@app.route('/job_status/<job_id>', methods=['GET'])
def job_status(job_id):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
        job = cursor.fetchone()
        if job:
            return jsonify(dict(job))
        else:
            return jsonify({"error": "Job not found"}), 404

@app.route('/worker_poll/<worker_id>', methods=['GET'])
def worker_poll(worker_id):
    # This endpoint is for workers to check if they have a job assigned
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs
            WHERE worker_id = ? AND status = 'assigned'
            ORDER BY created_at ASC LIMIT 1
        ''', (worker_id,))
        job = cursor.fetchone()
        if job:
            return jsonify(dict(job))
        else:
            return jsonify({"status": "no_job"})

@app.route('/update_job_status', methods=['POST'])
def update_job_status():
    data = request.json
    job_id = data.get('job_id')
    status = data.get('status')
    exit_code = data.get('exit_code')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        if status == 'running':
            cursor.execute('UPDATE jobs SET status = ?, started_at = CURRENT_TIMESTAMP WHERE job_id = ?', (status, job_id))
        elif status in ['completed', 'failed']:
            # Restore RAM to the worker
            cursor.execute('SELECT worker_id, ram_required_gb FROM jobs WHERE job_id = ?', (job_id,))
            job = cursor.fetchone()
            if job and job['worker_id']:
                cursor.execute('''
                    UPDATE workers
                    SET available_ram_gb = available_ram_gb + ?
                    WHERE worker_id = ?
                ''', (job['ram_required_gb'], job['worker_id']))

            cursor.execute('UPDATE jobs SET status = ?, finished_at = CURRENT_TIMESTAMP, exit_code = ? WHERE job_id = ?', (status, exit_code, job_id))
        else:
            cursor.execute('UPDATE jobs SET status = ? WHERE job_id = ?', (status, job_id))
        conn.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
