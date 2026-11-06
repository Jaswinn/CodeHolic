"""
Unit test suite covering failure, recovery, atomicity, disk-full, and compaction scenarios.
"""

import os
import sys
import shutil
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.engine import KVDatabase, open_db, DiskFullError, DatabaseError
from src.record import Record, OP_PUT, OP_DEL, OP_BATCH_COMMIT


class TestScenarios(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath("scratch/unit_tests")
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

    def test_single_write_and_recovery(self):
        """Scenario A1-A5: Standard durable put/delete survive reopen."""
        db = open_db(self.db_path)
        db.put("k1", "v1")
        db.put("k2", "v2")
        db.delete("k1")
        db.close()

        # Reopen
        db2 = open_db(self.db_path)
        self.assertIsNone(db2.get("k1"))
        self.assertEqual(db2.get("k2"), b"v2")
        db2.close()

    def test_torn_write_recovery(self):
        """Scenario A2 & F4: Truncate file tail by 1 byte or partial record."""
        db = open_db(self.db_path)
        db.put("committed_key", "committed_val")
        db.close()

        orig_size = os.path.getsize(self.db_path)
        # Append some garbage torn bytes simulating process killed mid-write
        with open(self.db_path, "ab") as f:
            f.write(b"\xDE\xAD\xBE\xEF\x00\x01\x02") # Partial header
        
        # open() must succeed and recover to committed state
        db2 = open_db(self.db_path)
        self.assertEqual(db2.get("committed_key"), b"committed_val")
        self.assertEqual(db2.key_count, 1)
        self.assertEqual(os.path.getsize(self.db_path), orig_size)
        db2.close()

    def test_f4_truncate_by_one_byte(self):
        """Scenario F4: Database file truncated by 1 byte at the end by hand."""
        db = open_db(self.db_path)
        db.put("k1", "v1")
        db.put("k2", "v2")
        db.close()

        # Truncate by 1 byte
        size = os.path.getsize(self.db_path)
        with open(self.db_path, "r+b") as f:
            f.truncate(size - 1)

        db2 = open_db(self.db_path)
        # k1 must be present, k2 was torn so truncated
        self.assertEqual(db2.get("k1"), b"v1")
        self.assertIsNone(db2.get("k2"))
        db2.close()

    def test_batch_atomicity_uncommitted_discarded(self):
        """Scenario B1, B2: Batch without commit marker is completely invisible."""
        db = open_db(self.db_path)
        db.put("base_key", "base_val")
        db.close()

        # Simulate batch written but killed before commit marker
        # Manually append 3 batch records without OP_BATCH_COMMIT
        batch_id = 9999
        r1 = Record(OP_PUT, batch_id, 10, b"batch_1", b"val_1").serialize()
        r2 = Record(OP_PUT, batch_id, 11, b"batch_2", b"val_2").serialize()
        r3 = Record(OP_DEL, batch_id, 12, b"base_key", b"").serialize()

        with open(self.db_path, "ab") as f:
            f.write(r1 + r2 + r3)
            f.flush()
            os.fsync(f.fileno())

        # Reopen: None of the batch operations must be visible
        db2 = open_db(self.db_path)
        self.assertEqual(db2.get("base_key"), b"base_val")
        self.assertIsNone(db2.get("batch_1"))
        self.assertIsNone(db2.get("batch_2"))
        self.assertEqual(db2.key_count, 1)
        db2.close()

    def test_batch_ordering_same_key(self):
        """Scenario B5: A batch that puts key X and deletes key X."""
        db = open_db(self.db_path)
        db.batch([
            ("put", "keyX", "val1"),
            ("del", "keyX"),
        ])
        self.assertIsNone(db.get("keyX"))

        db.batch([
            ("del", "keyY"),
            ("put", "keyY", "valFinal"),
        ])
        self.assertEqual(db.get("keyY"), b"valFinal")
        db.close()

        # Survives reopen
        db2 = open_db(self.db_path)
        self.assertIsNone(db2.get("keyX"))
        self.assertEqual(db2.get("keyY"), b"valFinal")
        db2.close()

    def test_crash_during_compaction_temp_file(self):
        """Scenario C3, C5: Temp files left over from compaction are cleaned up."""
        db = open_db(self.db_path)
        db.put("k1", "v1")
        db.close()

        # Create dummy abandoned compaction temp files
        tmp1 = f"{self.db_path}.compact.1234.5678.tmp"
        with open(tmp1, "wb") as f:
            f.write(b"garbage temp data")

        # Open db: must clean up temp file and open cleanly
        db2 = open_db(self.db_path)
        self.assertEqual(db2.get("k1"), b"v1")
        self.assertFalse(os.path.exists(tmp1))
        db2.close()

    def test_disk_full_scenarios(self):
        """Scenario E1-E5: File size cap enforcement."""
        # 64 byte header + room for ~1-2 small records
        max_bytes = 200
        db = open_db(self.db_path, max_bytes=max_bytes)
        db.put("k1", "v1") # ~53 bytes, total ~117 bytes

        # Next put fits
        db.put("k2", "v2") # ~53 bytes, total ~170 bytes

        # Third put should exceed 200 bytes limit and fail cleanly
        with self.assertRaises(DiskFullError):
            db.put("k3", "v3_too_large_to_fit_in_the_database_limit")

        # Previous data remains 100% intact and readable
        self.assertEqual(db.get("k1"), b"v1")
        self.assertEqual(db.get("k2"), b"v2")
        self.assertIsNone(db.get("k3"))
        db.close()

        # Reopen after disk-full error: succeeds and intact (E3)
        db2 = open_db(self.db_path, max_bytes=max_bytes)
        self.assertEqual(db2.get("k1"), b"v1")
        self.assertEqual(db2.get("k2"), b"v2")
        db2.close()


if __name__ == "__main__":
    unittest.main()

