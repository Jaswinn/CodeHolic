"""
Concurrency test: multiple concurrent writer threads and reader threads.
Verifies no data corruption, no half-applied batches, and consistent reads.
"""

import os
import sys
import time
import random
import shutil
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.engine import open_db


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath("scratch/concurrency_tests")
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir)
            except OSError:
                pass

    def test_concurrent_writers_and_readers(self):
        db = open_db(self.db_path)
        stop_event = threading.Event()
        errors = []

        # Writer 1: single puts and deletes
        def writer1():
            try:
                for i in range(100):
                    k = f"w1_{i}"
                    db.put(k, f"val_{i}")
                    time.sleep(0.001)
                    if i % 3 == 0:
                        db.delete(k)
            except Exception as e:
                errors.append(f"Writer1 error: {e}")

        # Writer 2: atomic batches
        def writer2():
            try:
                for i in range(50):
                    batch_ops = [
                        ("put", f"batch_a_{i}", f"va_{i}"),
                        ("put", f"batch_b_{i}", f"vb_{i}"),
                    ]
                    db.batch(batch_ops)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(f"Writer2 error: {e}")

        # Reader: checks atomicity of batch_a and batch_b
        # Invariant D4: A reader calling get during a batch sees state before or after, never in between
        def reader():
            try:
                while not stop_event.is_set():
                    # Pick a recent batch index
                    for i in range(50):
                        va = db.get(f"batch_a_{i}")
                        vb = db.get(f"batch_b_{i}")
                        # Both must be present or both must be None
                        if (va is None and vb is not None) or (va is not None and vb is None):
                            errors.append(f"Atomicity violation for batch {i}: va={va}, vb={vb}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"Reader error: {e}")

        threads = [
            threading.Thread(target=writer1),
            threading.Thread(target=writer2),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()

        threads[0].join()
        threads[1].join()
        stop_event.set()
        threads[2].join()
        threads[3].join()

        self.assertEqual(errors, [])
        
        # Verify check_integrity says OK
        ok, reason = db.check_integrity()
        self.assertTrue(ok, f"Integrity check failed: {reason}")
        db.close()

        # Reopen and check integrity again
        db2 = open_db(self.db_path)
        ok2, reason2 = db2.check_integrity()
        self.assertTrue(ok2, f"Reopen integrity check failed: {reason2}")
        db2.close()


if __name__ == "__main__":
    unittest.main()

