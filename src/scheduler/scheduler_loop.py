import time
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
                # We consider a worker online if it has been seen in the last 60 seconds
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

                    # Bin-Packing: Find the first worker that can fit the job
                    assigned_worker = None
                    for worker in workers:
                        if worker['available_ram_gb'] >= ram_required:
                            assigned_worker = worker
                            break

                    if assigned_worker:
                        logger.info(f"Assigning job {job_id} to worker {assigned_worker['worker_id']}")

                        # Update Job status
                        cursor.execute('''
                            UPDATE jobs
                            SET status = 'assigned', worker_id = ?
                            WHERE job_id = ?
                        ''', (assigned_worker['worker_id'], job_id))

                        # Optimistically decrease available RAM on worker for subsequent jobs in this loop
                        new_available_ram = assigned_worker['available_ram_gb'] - ram_required
                        cursor.execute('''
                            UPDATE workers
                            SET available_ram_gb = ?
                            WHERE worker_id = ?
                        ''', (new_available_ram, assigned_worker['worker_id']))

                        # Update local workers list for the next job in the loop
                        assigned_worker['available_ram_gb'] = new_available_ram

                        conn.commit()
                    else:
                        logger.info(f"Could not find worker for job {job_id} requiring {ram_required} GB")

        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        time.sleep(5)

if __name__ == '__main__':
    schedule_jobs()
