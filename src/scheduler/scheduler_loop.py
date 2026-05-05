import time
import json
import requests
from persistence import get_db_conn
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def schedule_jobs():
    """
    Loop to assign PENDING jobs to available Workers using First-Fit (Bin-Packing).
    """
    while True:
        try:
                with get_db_conn() as conn:
                    cursor = conn.cursor()

                    # 1. Fetch pending jobs ordered by creation time
                    cursor.execute('SELECT * FROM jobs WHERE status = "pending" ORDER BY created_at ASC')
                    pending_jobs = [dict(row) for row in cursor.fetchall()]

                    if not pending_jobs:
                        time.sleep(5)
                        continue

                    # 2. Fetch online workers
                    cursor.execute('''
                        SELECT * FROM workers
                        WHERE status = "online"
                        AND last_seen >= datetime('now', '-60 seconds')
                        ORDER BY available_ram_gb DESC
                    ''')
                    workers = [dict(row) for row in cursor.fetchall()]

                if not workers:
                    logger.warning("No online workers available.")
                    time.sleep(5)
                    continue

                for job in pending_jobs:
                    job_id = job['job_id']
                    ram_required = job['ram_required_gb']
                    repo = job['repo']
                    required_hashes = json.loads(job.get('required_hashes') or '[]')

                    # Hard Constraint: Filter workers by RAM
                    candidates = [w for w in workers if w['available_ram_gb'] >= ram_required]

                    if not candidates:
                        logger.info(f"Could not find worker for job {job_id} requiring {ram_required} GB")
                        continue

                    # Soft Constraint: Data Locality (P2P Discovery)
                    worker_scores = []
                    for worker in candidates:
                        score = 0
                        if required_hashes and worker['service_url']:
                            try:
                                resp = requests.post(f"{worker['service_url']}/check_cache",
                                                     json={"repo": repo, "hashes": required_hashes},
                                                     timeout=2)
                                if resp.status_code == 200:
                                    found_hashes = resp.json()
                                    score = len(found_hashes)
                            except Exception as e:
                                logger.warning(f"Failed to check cache on worker {worker['worker_id']}: {e}")
                        worker_scores.append((worker, score))

                    # Sort by score descending (Data Locality)
                    worker_scores.sort(key=lambda x: x[1], reverse=True)
                    assigned_worker, winner_score = worker_scores[0]

                    # Injection du Data Plane (P2P URL)
                    p2p_url = None
                    if winner_score < len(required_hashes) and len(worker_scores) > 1:
                        peers = [ws for ws in worker_scores if ws[0]['worker_id'] != assigned_worker['worker_id']]
                        if peers:
                            best_peer, peer_score = peers[0]
                            if peer_score > 0:
                                p2p_url = f"{best_peer['service_url']}/fetch_artifact"

                    logger.info(f"Assigning job {job_id} to worker {assigned_worker['worker_id']} (Score: {winner_score}, P2P: {p2p_url})")

                    # Update Job status
                    with get_db_conn() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            UPDATE jobs
                            SET status = 'assigned', worker_id = ?, p2p_url = ?
                            WHERE job_id = ? AND status = 'pending'
                        ''', (assigned_worker['worker_id'], p2p_url, job_id))

                        if cursor.rowcount > 0:
                            # Optimistically decrease available RAM on worker for subsequent jobs in this loop
                            new_available_ram = assigned_worker['available_ram_gb'] - ram_required
                            cursor.execute('''
                                UPDATE workers
                                SET available_ram_gb = ?
                                WHERE worker_id = ?
                            ''', (new_available_ram, assigned_worker['worker_id']))
                            conn.commit()
                            assigned_worker['available_ram_gb'] = new_available_ram

        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        time.sleep(5)

if __name__ == '__main__':
    schedule_jobs()
