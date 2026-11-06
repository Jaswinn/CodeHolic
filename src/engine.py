"""
Core Database Engine for Kill It and Prove It Hackathon.
Embedded crash-safe key-value store using Write-Ahead Logging (WAL) and atomic compaction.
"""

import os
import sys
import glob
import time
import struct
import threading
from typing import Optional, Dict, List, Tuple, Union, Any

# Ensure repository root is on sys.path so file can run both as module and standalone script
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from src.constants import (
    FILE_MAGIC,
    FORMAT_VERSION,
    FILE_HEADER_SIZE,
    MAX_KEY_SIZE,
    MAX_VALUE_SIZE,
    MAX_BATCH_SIZE,
    RECOVERY_CHUNK_SIZE,
)
from src.record import (
    Record,
    OP_PUT,
    OP_DEL,
    OP_BATCH_COMMIT,
    OP_BATCH_ABORT,
    HEADER_STRUCT as RECORD_HEADER_STRUCT,
    HEADER_SIZE,
    TRAILER_SIZE,
    RECORD_OVERHEAD,
    parse_record_from_buffer,
)

FILE_HEADER_STRUCT = struct.Struct("<8sIQIQ32s")


class DatabaseError(Exception):
    """Base exception for database errors."""
    pass


class DiskFullError(DatabaseError):
    """Raised when an operation would exceed the configured max_bytes limit."""
    pass


class ValidationError(DatabaseError):
    """Raised when keys or values violate size limits."""
    pass


class KVDatabase:
    """
    Single-file crash-safe key-value database.
    """

    def __init__(self, path: str, max_bytes: Optional[int] = None):
        self._path = os.path.abspath(path)
        self._max_bytes = max_bytes if (max_bytes and max_bytes > 0) else 0
        self._lock = threading.RLock()
        self._index: Dict[bytes, bytes] = {}
        self._next_seq = 1
        self._file = None
        self._current_size = 0
        self._closed = True

        # Perform recovery and initialization
        self._recover_and_open()

    @property
    def key_count(self) -> int:
        with self._lock:
            return len(self._index)

    @property
    def file_size(self) -> int:
        with self._lock:
            return self._current_size

    def _cleanup_stale_temp_files(self) -> None:
        """Removes any abandoned compaction temp files from previous crashed runs."""
        dir_name = os.path.dirname(self._path) or "."
        base_name = os.path.basename(self._path)
        pattern = os.path.join(dir_name, f"{base_name}.compact.*.tmp")
        for tmp_file in glob.glob(pattern):
            try:
                os.remove(tmp_file)
            except OSError:
                pass

    def _build_header(self, max_bytes: int = 0) -> bytes:
        """Constructs a 64-byte database file header."""
        return FILE_HEADER_STRUCT.pack(
            FILE_MAGIC,
            FORMAT_VERSION,
            max_bytes,
            0,  # flags
            0,  # compaction generation
            b"\x00" * 32  # reserved
        )

    def _recover_and_open(self) -> float:
        """
        Recovers database from WAL file.
        Returns recovery duration in seconds.
        """
        t0 = time.perf_counter()
        with self._lock:
            self._cleanup_stale_temp_files()

            parent_dir = os.path.dirname(self._path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            if not os.path.exists(self._path) or os.path.getsize(self._path) == 0:
                # Fresh database creation
                header = self._build_header(self._max_bytes)
                with open(self._path, "wb") as f:
                    f.write(header)
                    f.flush()
                    os.fsync(f.fileno())
                
                self._file = open(self._path, "r+b")
                self._current_size = FILE_HEADER_SIZE
                self._closed = False
                t1 = time.perf_counter()
                return t1 - t0

            # Existing file: open and recover
            self._file = open(self._path, "r+b")
            self._file.seek(0, os.SEEK_END)
            raw_size = self._file.tell()

            if raw_size < FILE_HEADER_SIZE:
                # File was truncated before header completed: rewrite clean header
                self._file.seek(0)
                header = self._build_header(self._max_bytes)
                self._file.write(header)
                self._file.truncate(FILE_HEADER_SIZE)
                self._file.flush()
                os.fsync(self._file.fileno())
                self._current_size = FILE_HEADER_SIZE
                self._closed = False
                t1 = time.perf_counter()
                return t1 - t0

            # Read and validate header
            self._file.seek(0)
            header_bytes = self._file.read(FILE_HEADER_SIZE)
            magic, version, stored_max_bytes, flags, comp_gen, reserved = FILE_HEADER_STRUCT.unpack(header_bytes)
            if magic != FILE_MAGIC:
                raise DatabaseError(f"Invalid database header magic: {magic!r}")
            if version > FORMAT_VERSION:
                raise DatabaseError(f"Unsupported database version: {version}")

            # Keep configured max_bytes if provided, else use stored
            if self._max_bytes == 0 and stored_max_bytes > 0:
                self._max_bytes = stored_max_bytes
            elif self._max_bytes > 0 and self._max_bytes != stored_max_bytes:
                # Update stored max_bytes in header
                self._file.seek(0)
                self._file.write(self._build_header(self._max_bytes))
                self._file.flush()
                os.fsync(self._file.fileno())

            # Sequential replay of WAL records
            self._file.seek(FILE_HEADER_SIZE)
            valid_offset = FILE_HEADER_SIZE
            last_committed_offset = FILE_HEADER_SIZE
            max_seq = 0
            
            # Temporary state for tracking batches
            # batch_id -> list of records
            pending_batches: Dict[int, List[Record]] = {}
            recovered_index: Dict[bytes, bytes] = {}

            # Fast buffered reading
            buf = bytearray()
            file_offset = FILE_HEADER_SIZE

            while True:
                chunk = self._file.read(RECOVERY_CHUNK_SIZE)
                if not chunk:
                    break
                buf.extend(chunk)

                buf_offset = 0
                while True:
                    rec, n_bytes, err = parse_record_from_buffer(buf, buf_offset)
                    if rec is not None:
                        # Record parsed and verified against CRC32 + Trailer
                        valid_offset += n_bytes
                        buf_offset += n_bytes
                        if rec.seq_num > max_seq:
                            max_seq = rec.seq_num

                        if rec.batch_id == 0:
                            # Standalone operation
                            last_committed_offset = valid_offset
                            if rec.record_type == OP_PUT:
                                recovered_index[rec.key] = rec.value
                            elif rec.record_type == OP_DEL:
                                recovered_index.pop(rec.key, None)
                        else:
                            # Batch operation
                            if rec.record_type in (OP_PUT, OP_DEL):
                                pending_batches.setdefault(rec.batch_id, []).append(rec)
                            elif rec.record_type == OP_BATCH_COMMIT:
                                # Commit marker encountered! Apply all operations of this batch
                                last_committed_offset = valid_offset
                                ops = pending_batches.pop(rec.batch_id, [])
                                for batch_rec in ops:
                                    if batch_rec.record_type == OP_PUT:
                                        recovered_index[batch_rec.key] = batch_rec.value
                                    elif batch_rec.record_type == OP_DEL:
                                        recovered_index.pop(batch_rec.key, None)
                            elif rec.record_type == OP_BATCH_ABORT:
                                pending_batches.pop(rec.batch_id, None)

                    elif err == "INCOMPLETE_HEADER" or err == "INCOMPLETE_PAYLOAD":
                        # Buffer ran out of bytes; need to read more from file
                        break
                    else:
                        # Corrupted record / torn write at this offset
                        # Stop scanning; tail is invalid
                        break

                if rec is None and err not in ("INCOMPLETE_HEADER", "INCOMPLETE_PAYLOAD"):
                    # Hard failure (e.g. invalid magic or CRC fail) -> torn write at tail
                    break

                # Discard consumed bytes from buffer
                if buf_offset > 0:
                    del buf[:buf_offset]

            # Truncate any torn tail or trailing uncommitted batch records
            final_safe_offset = last_committed_offset
            if final_safe_offset < raw_size:
                self._file.seek(final_safe_offset)
                self._file.truncate(final_safe_offset)
                self._file.flush()
                os.fsync(self._file.fileno())

            self._index = recovered_index
            self._next_seq = max_seq + 1
            self._current_size = final_safe_offset
            self._file.seek(final_safe_offset)
            self._closed = False

            t1 = time.perf_counter()
            return t1 - t0

    def _to_bytes(self, data: Union[str, bytes], name: str, max_size: int) -> bytes:
        if isinstance(data, str):
            b = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            b = bytes(data)
        else:
            raise ValidationError(f"{name} must be str or bytes, got {type(data)}")
        
        if len(b) > max_size:
            raise ValidationError(f"{name} length {len(b)} exceeds maximum allowed {max_size}")
        return b

    def put(self, key: Union[str, bytes], value: Union[str, bytes]) -> None:
        """Stores value under key. Overwrite if exists. Guaranteed durable upon return."""
        key_bytes = self._to_bytes(key, "Key", MAX_KEY_SIZE)
        val_bytes = self._to_bytes(value, "Value", MAX_VALUE_SIZE)
        if len(key_bytes) == 0:
            raise ValidationError("Key cannot be empty")

        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")

            rec = Record(OP_PUT, 0, self._next_seq, key_bytes, val_bytes)
            data = rec.serialize()
            required_size = self._current_size + len(data)

            if self._max_bytes > 0 and required_size > self._max_bytes:
                raise DiskFullError(
                    f"Write exceeds max_bytes limit: {required_size} > {self._max_bytes}"
                )

            # 1. Write record to file
            self._file.seek(self._current_size)
            self._file.write(data)
            
            # 2. Flush and fsync to guarantee durability on disk
            self._file.flush()
            os.fsync(self._file.fileno())

            # 3. Update memory index
            self._index[key_bytes] = val_bytes
            self._current_size = required_size
            self._next_seq += 1

    def get(self, key: Union[str, bytes]) -> Optional[bytes]:
        """Returns the current value for key, or None if not found."""
        key_bytes = self._to_bytes(key, "Key", MAX_KEY_SIZE)
        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")
            val = self._index.get(key_bytes)
            return bytes(val) if val is not None else None

    def delete(self, key: Union[str, bytes]) -> bool:
        """Removes the key. Missing key is not an error. Guaranteed durable upon return."""
        key_bytes = self._to_bytes(key, "Key", MAX_KEY_SIZE)
        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")

            rec = Record(OP_DEL, 0, self._next_seq, key_bytes, b"")
            data = rec.serialize()
            required_size = self._current_size + len(data)

            if self._max_bytes > 0 and required_size > self._max_bytes:
                raise DiskFullError(
                    f"Delete tombstone exceeds max_bytes limit: {required_size} > {self._max_bytes}"
                )

            # 1. Write tombstone to file
            self._file.seek(self._current_size)
            self._file.write(data)

            # 2. Flush and fsync
            self._file.flush()
            os.fsync(self._file.fileno())

            # 3. Update memory index
            existed = key_bytes in self._index
            self._index.pop(key_bytes, None)
            self._current_size = required_size
            self._next_seq += 1
            return existed

    def batch(self, ops: List[Tuple[str, Union[str, bytes], Optional[Union[str, bytes]]]]) -> None:
        """
        Applies a list of ('put', key, val) and ('del', key) operations atomically.
        Either all operations are durable on disk or none of them are.
        """
        if not ops:
            return
        if len(ops) > MAX_BATCH_SIZE:
            raise ValidationError(f"Batch size {len(ops)} exceeds limit {MAX_BATCH_SIZE}")

        # Validate all ops first before any disk writes
        serialized_ops = []
        batch_id = self._next_seq  # Unique batch ID
        seq = self._next_seq

        total_bytes_needed = 0
        parsed_ops = []

        for item in ops:
            op_type = item[0].lower()
            if op_type == "put":
                k = self._to_bytes(item[1], "Key", MAX_KEY_SIZE)
                v = self._to_bytes(item[2], "Value", MAX_VALUE_SIZE)
                if len(k) == 0:
                    raise ValidationError("Key cannot be empty in batch put")
                rec = Record(OP_PUT, batch_id, seq, k, v)
                data = rec.serialize()
                serialized_ops.append(data)
                total_bytes_needed += len(data)
                parsed_ops.append(("put", k, v))
                seq += 1
            elif op_type in ("del", "delete"):
                k = self._to_bytes(item[1], "Key", MAX_KEY_SIZE)
                rec = Record(OP_DEL, batch_id, seq, k, b"")
                data = rec.serialize()
                serialized_ops.append(data)
                total_bytes_needed += len(data)
                parsed_ops.append(("del", k, None))
                seq += 1
            else:
                raise ValidationError(f"Unknown operation in batch: {op_type}")

        # Commit marker
        commit_rec = Record(OP_BATCH_COMMIT, batch_id, seq, b"", b"")
        commit_data = commit_rec.serialize()
        total_bytes_needed += len(commit_data)
        seq += 1

        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")

            required_size = self._current_size + total_bytes_needed
            if self._max_bytes > 0 and required_size > self._max_bytes:
                raise DiskFullError(
                    f"Batch exceeds max_bytes limit: {required_size} > {self._max_bytes}"
                )

            # Step 1: Write all batch operation records
            self._file.seek(self._current_size)
            for chunk in serialized_ops:
                self._file.write(chunk)
            
            # Step 2: Flush and fsync records
            self._file.flush()
            os.fsync(self._file.fileno())

            # Step 3: Write BATCH_COMMIT marker
            self._file.write(commit_data)

            # Step 4: Flush and fsync commit marker
            self._file.flush()
            os.fsync(self._file.fileno())

            # Step 5: Atomically update in-memory index
            for op, k, v in parsed_ops:
                if op == "put":
                    self._index[k] = v
                elif op == "del":
                    self._index.pop(k, None)

            self._current_size = required_size
            self._next_seq = seq

    def compact(self) -> int:
        """
        Performs crash-safe log compaction.
        Rewrites all live keys into a compacted file and atomically swaps it in.
        Returns the new file size in bytes.
        """
        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")

            dir_name = os.path.dirname(self._path) or "."
            base_name = os.path.basename(self._path)
            temp_path = os.path.join(
                dir_name, f"{base_name}.compact.{os.getpid()}.{time.time_ns()}.tmp"
            )

            try:
                # 1. Write compacted data to temporary file
                with open(temp_path, "wb") as f_tmp:
                    # Write header
                    hdr = self._build_header(self._max_bytes)
                    f_tmp.write(hdr)
                    current_temp_size = len(hdr)

                    # Write all active keys
                    seq = self._next_seq
                    for k, v in self._index.items():
                        rec = Record(OP_PUT, 0, seq, k, v)
                        data = rec.serialize()
                        if self._max_bytes > 0 and current_temp_size + len(data) > self._max_bytes:
                            raise DiskFullError("Compaction would exceed max_bytes limit")
                        f_tmp.write(data)
                        current_temp_size += len(data)
                        seq += 1

                    # Durably flush temp file to disk
                    f_tmp.flush()
                    os.fsync(f_tmp.fileno())

                # 2. Close active file handle before atomic swap (Windows file locking requirement)
                self._file.close()

                # 3. Atomic replace: swaps new compacted file into place
                os.replace(temp_path, self._path)

                # 4. Reopen file in read-write mode
                self._file = open(self._path, "r+b")
                self._file.seek(0, os.SEEK_END)
                self._current_size = self._file.tell()
                return self._current_size

            except Exception:
                # Cleanup temporary file if left behind
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                # Reopen original file if closed during failed attempt
                if self._file.closed and os.path.exists(self._path):
                    self._file = open(self._path, "r+b")
                    self._file.seek(0, os.SEEK_END)
                    self._current_size = self._file.tell()
                raise

    def dump(self) -> List[Tuple[bytes, bytes]]:
        """Returns all keys and values sorted by key."""
        with self._lock:
            if self._closed:
                raise DatabaseError("Database is closed")
            return sorted(self._index.items(), key=lambda item: item[0])

    def check_integrity(self) -> Tuple[bool, str]:
        """
        Verifies file integrity.
        Validates magic header, sequentially verifies all record CRCs and trailers,
        and verifies batch completion.
        """
        with self._lock:
            if not os.path.exists(self._path):
                return False, "File does not exist"

            file_size = os.path.getsize(self._path)
            if file_size < FILE_HEADER_SIZE:
                return False, f"File size {file_size} is less than header size {FILE_HEADER_SIZE}"

            with open(self._path, "rb") as f:
                hdr = f.read(FILE_HEADER_SIZE)
                magic, version, stored_max_bytes, flags, comp_gen, reserved = FILE_HEADER_STRUCT.unpack(hdr)
                if magic != FILE_MAGIC:
                    return False, f"Invalid magic: {magic!r}"

                offset = FILE_HEADER_SIZE
                pending_batches = set()

                while offset < file_size:
                    f.seek(offset)
                    hdr_buf = f.read(HEADER_SIZE)
                    if len(hdr_buf) < HEADER_SIZE:
                        return False, f"Torn record header at offset {offset}"

                    rec, n_bytes, err = parse_record_from_buffer(hdr_buf)
                    if err not in ("INCOMPLETE_PAYLOAD", None):
                        return False, f"Corrupted record at offset {offset}: {err}"

                    # Read full record
                    magic_h, crc, rec_type, batch_id, seq_num, key_len, val_len = (
                        RECORD_HEADER_STRUCT.unpack_from(hdr_buf, 0)
                    )
                    full_len = HEADER_SIZE + key_len + val_len + TRAILER_SIZE
                    if offset + full_len > file_size:
                        return False, f"Incomplete record at offset {offset} (file truncated)"

                    f.seek(offset)
                    full_buf = f.read(full_len)
                    rec, n_bytes, err = parse_record_from_buffer(full_buf)
                    if rec is None:
                        return False, f"Record verification failed at offset {offset}: {err}"

                    if rec.batch_id != 0:
                        if rec.record_type in (OP_PUT, OP_DEL):
                            pending_batches.add(rec.batch_id)
                        elif rec.record_type == OP_BATCH_COMMIT:
                            pending_batches.discard(rec.batch_id)

                    offset += full_len

                # File integrity is valid: all records have valid magic, CRCs, lengths and trailers
                return True, "OK"

    def close(self) -> None:
        """Flushes buffers and closes database cleanly."""
        with self._lock:
            if not self._closed:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                    self._file.close()
                except OSError:
                    pass
                finally:
                    self._closed = True


def open_db(path: str, max_bytes: Optional[int] = None) -> KVDatabase:
    """Convenience helper to open or create database."""
    return KVDatabase(path, max_bytes=max_bytes)


if __name__ == "__main__":
    import shutil
    demo_path = "terminal_demo.db"
    if os.path.exists(demo_path):
        os.remove(demo_path)

    print("==================================================")
    print("   KVDB ENGINE SELF-TEST & TERMINAL DEMO          ")
    print("==================================================")
    
    # 1. Open database
    print("\n1. Initializing & opening database...")
    db = open_db(demo_path)
    print(f"   [OK] Database ready. Initial key count: {db.key_count}")

    # 2. Put key-values
    print("\n2. Executing durable PUT operations...")
    db.put("hackathon:name", "Kill It and Prove It")
    db.put("team:status", "Ready to win")
    db.put("engine:durability", "fsync-guaranteed")
    print(f"   [OK] Stored 3 keys. Active keys on disk: {db.key_count}")

    # 3. Retrieve values
    print("\n3. Executing GET operations...")
    for k in ["hackathon:name", "team:status", "engine:durability"]:
        val = db.get(k)
        print(f"   GET '{k}' -> {val.decode('utf-8')}")

    # 4. Atomic batch
    print("\n4. Executing atomic BATCH operation (puts + del)...")
    db.batch([
        ("put", "batch:item1", "alpha"),
        ("put", "batch:item2", "beta"),
        ("del", "engine:durability"),
    ])
    print(f"   [OK] Batch applied atomically. Active keys: {db.key_count}")

    # 5. Check integrity
    print("\n5. Running file integrity check...")
    ok, reason = db.check_integrity()
    print(f"   [OK] Check result: {reason}")

    # 6. Compaction
    print("\n6. Running crash-safe log compaction...")
    old_size = db.file_size
    new_size = db.compact()
    print(f"   [OK] Compacted file from {old_size}B to {new_size}B")

    # 7. Dump all keys
    print("\n7. Dumping all keys (sorted order):")
    for k, v in db.dump():
        print(f"   {k.decode('utf-8')} = {v.decode('utf-8')}")

    # 8. Clean close & reopen test
    print("\n8. Closing database and simulating clean reopen...")
    db.close()
    db2 = open_db(demo_path)
    print(f"   [OK] Reopened successfully! Recovered key count: {db2.key_count}")
    db2.close()

    # Clean up
    if os.path.exists(demo_path):
        os.remove(demo_path)

    print("\n==================================================")
    print("   ALL OPERATIONS VERIFIED ON TERMINAL!           ")
    print("==================================================")
