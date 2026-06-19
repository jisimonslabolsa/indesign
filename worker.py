"""
idml2banner — RQ Worker
Arranca un worker que consume la cola de Redis.
"""

import os
from redis import Redis
from rq import Worker, Queue

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

if __name__ == "__main__":
    conn = Redis.from_url(REDIS_URL)
    q = Queue(connection=conn)
    worker = Worker([q], connection=conn)
    print("Worker started, waiting for jobs...")
    worker.work()
