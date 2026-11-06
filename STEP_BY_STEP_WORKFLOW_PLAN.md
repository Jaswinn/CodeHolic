# STEP_BY_STEP_WORKFLOW_PLAN.md — Master Execution Plan for All 5 Workflows

This document provides a comprehensive, step-by-step action plan for understanding, verifying, and presenting each of the 5 core system workflows from the hackathon architecture diagrams.

---

## 🗺️ Master Workflow Index

| # | Workflow Name | Diagram Asset | Primary Source File | Primary Test / Verification |
|---|---|---|---|---|
| **1** | [Main Operational Lifecycle](#workflow-1-main-operational-lifecycle) | `diagrams/main_lifecycle.png` | `src/engine.py` | `kvdb.bat open`, `kvdb.bat serve` |
| **2** | [CRUD Client Operations](#workflow-2-crud-client-operations-put--get--delete) | `diagrams/crud_operations.png` | `src/engine.py`, `src/record.py` | `kvdb.bat put`, `get`, `del` |
| **3** | [Crash Recovery & Batch Atomicity](#workflow-3-crash-recovery--batch-atomicity-engine) | `diagrams/recovery_batch.png` | `src/engine.py` (`_recover_and_open`) | `tests/test_scenarios.py` (A1–B6, F4) |
| **4** | [Crash-Safe Compaction Flow](#workflow-4-crash-safe-compaction-flow) | `diagrams/compaction_flow.png` | `src/engine.py` (`compact`) | `kvdb.bat compact`, `test_scenarios.py` (C3–C5) |
| **5** | [Torture Harness & Oracle Verification](#workflow-5-torture-testing-harness--verification-oracle) | `diagrams/torture_tool.png` | `torture/runner.py`, `torture/oracle.py` | `torture.bat --iterations 200 --seed 12345` |

---

## Workflow 1: Main Operational Lifecycle

*Reference Diagram: [`diagrams/main_lifecycle.png`](file:///d:/Srinathon%203.0/diagrams/main_lifecycle.png)*

### Step-by-Step Execution Sequence
1. **Launch Database**: Caller invokes `engine.open_db(path)` or CLI command `kvdb.bat open <path>`.
2. **Open Database File**: Engine opens or creates the file path in `r+b` mode (`src/engine.py: line 125, 137`).
3. **Check Existence**:
   - **Path does not exist / 0 bytes**: Engine writes a clean 64-byte file header (`FILE_MAGIC = b"KVDB_WAL"`), flushes and fsyncs to disk, initializes an empty in-memory hash map (`self._index = {}`), and marks the database ready (`src/engine.py: lines 122-134`).
   - **File exists**: Engine reads the first 64 bytes and validates header magic and version (`src/engine.py: lines 154-161`).
4. **Trigger Recovery Engine**: Dispatches sequential recovery scan (`_recover_and_open()`) over the WAL log (`src/engine.py: lines 174-240`).
5. **Rebuild In-Memory Index**: Validated records are replayed into `self._index`, updating key offsets and discarding aborted/torn tails.
6. **Accept Client Operations**: Engine enters operational state, receiving `PUT`, `GET`, `DELETE`, `BATCH`, or `DUMP` requests.
7. **Clean Termination**: When `close()` is requested, engine flushes OS file buffers, runs `os.fsync()`, closes the file descriptor, and releases all internal locks (`src/engine.py: lines 483-492`).

### Step-by-Step Terminal Verification Commands
```bat
:: 1. Open a new database (creates fresh file with valid 64-byte header)
.\kvdb.bat open store.db

:: 2. Verify header integrity
.\kvdb.bat check store.db

:: 3. Run interactive serve mode
.\kvdb.bat serve store.db
```

---

## Workflow 2: CRUD Client Operations (PUT / GET / DELETE)

*Reference Diagram: [`diagrams/crud_operations.png`](file:///d:/Srinathon%203.0/diagrams/crud_operations.png)*

### Step-by-Step Execution Sequence
#### PUT Operation
1. **Validate Key & Value**:
   - Check key length ($1 \le \text{len} \le 1024$ bytes) and value length ($0 \le \text{len} \le 1\text{MB}$).
   - Reject empty keys or oversized payloads with `ValidationError` (`src/engine.py: lines 260-278`).
2. **Disk-Full Capacity Check**:
   - Verify `current_size + record_size <= max_bytes` (if cap configured) (`src/engine.py: lines 282-286`).
3. **Encode Binary Record**:
   - Pack record header with `MAGIC_HEADER (0xDEADBEEF)`, sequence number, lengths, calculate IEEE 802.3 CRC32 checksum over `(type + batch_id + seq + k_len + v_len + key + value)`, and append `MAGIC_TRAILER (0xBEEFDEAD)` (`src/record.py: lines 63-79`).
4. **Append & Synchronize to Disk**:
   - Write bytes to file: `self._file.write(data)`
   - Flush userspace buffer: `self._file.flush()`
   - Force OS dirty pages to physical non-volatile storage: `os.fsync(self._file.fileno())` (`src/engine.py: lines 290-293`).
5. **Update Hash Index**:
   - Update in-memory hash table: `self._index[key_bytes] = val_bytes`.
6. **Return Success**:
   - Fulfill client promise (`ACK <op_id>` or function return).

#### GET Operation
1. **Search Hash Index**:
   - Acquire reentrant lock: `with self._lock:`
   - Query in-memory index: `self._index.get(key_bytes)` (`src/engine.py: lines 315-320`).
2. **Return Value**:
   - Key found: Return byte string value directly from memory ($O(1)$ query, 0 disk I/O).
   - Key missing: Return `NOTFOUND` (or `None`).

#### DELETE Operation
1. **Validate Key**: Ensure key is valid byte sequence.
2. **Encode & Write Tombstone**: Append record with `OP_DEL (2)` to WAL file.
3. **Flush & Fsync**: Synchronously commit tombstone to disk.
4. **Remove from Index**: Evict key from `self._index`: `self._index.pop(key_bytes, None)`.
5. **Return Success**: Emits acknowledgment.

### Step-by-Step Terminal Verification Commands
```bat
:: Put a key-value pair
.\kvdb.bat put store.db user:101 "Alice"

:: Retrieve the stored value
.\kvdb.bat get store.db user:101

:: Delete the key
.\kvdb.bat del store.db user:101

:: Verify it is gone
.\kvdb.bat get store.db user:101
```

---

## Workflow 3: Crash Recovery & Batch Atomicity Engine

*Reference Diagram: [`diagrams/recovery_batch.png`](file:///d:/Srinathon%203.0/diagrams/recovery_batch.png)*

### Step-by-Step Execution Sequence
1. **Open File & Validate Header**:
   - File opened in `r+b` mode.
   - 64-byte header checked: `magic == b"KVDB_WAL"`, `version <= FORMAT_VERSION`.
2. **Sequential Buffered Scan**:
   - Reads 1 MB chunks starting at offset 64 (`src/engine.py: lines 184-192`).
   - Uses C-accelerated `struct.unpack` via `parse_record_from_buffer` (`src/record.py: lines 82-127`).
3. **Record Validation**:
   - Magic prefix must equal `0xDEADBEEF`.
   - Magic trailer must equal `0xBEEFDEAD`.
   - Computed CRC32 over payload must strictly match header CRC32.
4. **Operation Dispatching**:
   - **Standalone Single Record (`batch_id == 0`)**:
     - `OP_PUT`: Set `recovered_index[key] = value`.
     - `OP_DEL`: `recovered_index.pop(key, None)`.
     - Advance `last_committed_offset = valid_offset`.
   - **Batch Record (`batch_id != 0`)**:
     - `OP_PUT` / `OP_DEL`: Append operation to `pending_batches[batch_id]`. Do **not** apply to `recovered_index`!
     - `OP_BATCH_COMMIT`: Batch commit marker verified! Pop all operations from `pending_batches[batch_id]` and apply them to `recovered_index` in exact order. Advance `last_committed_offset = valid_offset`.
     - `OP_BATCH_ABORT`: Drop all buffered operations in `pending_batches[batch_id]`.
5. **Handling Incomplete / Torn Tails**:
   - When EOF is reached or any framing/CRC corruption is detected:
     - All uncommitted records remaining in `pending_batches` are discarded.
     - If `last_committed_offset < file_size`, call `self._file.truncate(last_committed_offset)`, `self._file.flush()`, and `os.fsync()`.
6. **Finalize State**:
   - Set active `self._index = recovered_index`, set `self._next_seq = max_seq + 1`, and return recovery elapsed time.

### Step-by-Step Terminal Verification Commands
```bat
:: 1. Write an atomic batch of 3 keys
echo put k1 v1 > batch.txt
echo put k2 v2 >> batch.txt
echo put k3 v3 >> batch.txt
.\kvdb.bat batch store.db batch.txt

:: 2. Verify all keys are present
.\kvdb.bat dump store.db

:: 3. Run automated scenario tests (includes torn write & batch atomicity)
"C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe" -m unittest tests/test_scenarios.py
```

---

## Workflow 4: Crash-Safe Compaction Flow

*Reference Diagram: [`diagrams/compaction_flow.png`](file:///d:/Srinathon%203.0/diagrams/compaction_flow.png)*

### Step-by-Step Execution Sequence
1. **Acquire Database Lock**:
   - `with self._lock:` ensures no concurrent writes interfere with compaction.
2. **Snapshot In-Memory Index**:
   - `active_items = list(self._index.items())` captures current live state.
3. **Create Isolated Shadow File**:
   - Open temporary file `<path>.compact.<pid>.<timestamp>.tmp` (`src/engine.py: lines 398-403`).
   - Write 64-byte header with incremented `compaction_generation`.
4. **Serialize Active Records**:
   - Write exactly one clean `OP_PUT` record per active key-value pair.
   - Obsolete prior versions and `OP_DEL` tombstones are excluded.
5. **Flush & Fsync Shadow File**:
   - Call `temp_file.flush()` followed by `os.fsync(temp_file.fileno())`.
   - Close temporary file handle.
6. **Atomic Replacement (`os.replace`)**:
   - Close active database file handle `self._file.close()` to satisfy Windows file sharing requirements.
   - Execute `os.replace(temp_path, self._path)`: atomic NTFS directory metadata swap.
7. **Reopen & Sweep Stale Temps**:
   - Reopen newly replaced file at `self._path` in `r+b` mode.
   - Run `_cleanup_stale_temp_files()` to delete any orphaned `.compact.*.tmp` files.
8. **Release Lock**:
   - Compaction complete; space reclaimed safely.

### Step-by-Step Terminal Verification Commands
```bat
:: Perform multiple overwrites and deletes to create reclaimable WAL garbage
.\kvdb.bat put store.db heavy_key "OldValue"
.\kvdb.bat put store.db heavy_key "NewValue"
.\kvdb.bat del store.db heavy_key

:: Execute compaction
.\kvdb.bat compact store.db

:: Verify integrity of compacted database
.\kvdb.bat check store.db
```

---

## Workflow 5: Torture Testing Harness & Verification Oracle

*Reference Diagram: [`diagrams/torture_tool.png`](file:///d:/Srinathon%203.0/diagrams/torture_tool.png)*

### Step-by-Step Execution Sequence
1. **Initialize Seed & State**:
   - Torture runner initializes pseudo-random generator with `--seed <int>` for 100% deterministic reproducibility (`torture/runner.py: line 44`).
2. **Spawn Database Process**:
   - Launches `kvdb serve <db_path>` as child process with piped stdin/stdout.
3. **Multi-Threaded Workload Injection**:
   - 2+ concurrent writer threads generate random `put`, `del`, and `batch` operations.
   - Each operation is registered with `oracle.record_sent(op_id, op_type, details)`.
4. **ACK Monitoring & Tracking**:
   - Background reader thread monitors stdout for `ACK <op_id>`.
   - Upon receipt: calls `oracle.record_acked(op_id)` (`torture/oracle.py: lines 34-45`).
5. **Hard Process Termination**:
   - Runner waits random delay ($1..50$ ms).
   - Calls `killer.hard_kill(proc.pid)` (`taskkill /F /T /PID` on Windows or `SIGKILL` on POSIX).
6. **Recovery & Verification Loop**:
   - Opens database: `engine.open_db(db_path)`.
   - Runs structural integrity check: `db.check_integrity()`.
   - Dumps all key-value pairs: `dump_data = db.dump()`.
   - Oracle verifies:
     - **Durability**: Every acknowledged operation is correctly reflected.
     - **Atomicity**: No half-applied batches exist.
     - **No Phantoms**: No unexpected keys appear.
7. **Iterate or Pass**:
   - Repeats for requested iterations; prints detailed summary report upon passing.

### Step-by-Step Terminal Verification Commands
```bat
:: Run 20 iterations of default random kill torture
.\torture.bat --iterations 20 --seed 12345

:: Run compaction crash torture
.\torture.bat --iterations 5 --seed 7 --mode compaction

:: Run recovery-time crash torture
.\torture.bat --iterations 5 --seed 42 --mode recovery-crash

:: Run large 1MB value torture
.\torture.bat --iterations 5 --seed 999 --mode large-values
```

---

## 🎤 15-Minute Judge Presentation Script

When walking the judge through the system, use this exact sequence:

1. **Minute 0–2 (Architecture & Workflows Overview)**:
   - Point to `ARCHITECTURE.md` and show the 5 workflow diagrams in `diagrams/`.
   - Explain: *"We use a structured append-only Write-Ahead Log with an in-memory hash index. Every mutating operation commits to disk with IEEE 802.3 CRC32 before emitting ACK. Readers query in-memory in $O(1)$ time with 0 disk I/O."*
2. **Minute 2–6 (Automated Torture Demo)**:
   - Run: `.\torture.bat --iterations 200 --seed <judge_seed>`
   - Explain the Oracle: *"The Oracle runs in the parent test process, completely isolated from the database. It tracks ACKed vs in-flight ops. Every iteration terminates the database with TerminateProcess at random millisecond offsets and verifies 100% durability, batch atomicity, and zero phantom keys."*
3. **Minute 6–9 (Manual Kill & CLI Demo)**:
   - Run `kvdb.bat put`, `get`, `batch`, and `dump`.
   - Demonstrate `kvdb.bat check` and show that partial tails are cleanly truncated upon `open()`.
4. **Minute 9–12 (Crash-Here Code Explanations)**:
   - Refer to `DEMO_CHEATSHEET.md` Section 1. Explain exact disk state and `open()` behavior for writes, batches, and compaction.
5. **Minute 12–15 (Design Extension: Expiry / TTL)**:
   - Refer to `DEMO_CHEATSHEET.md` Section 2. Explain on-disk header format change (`expire_ts`), query-time masking, compaction purge, and the 3 subtle pitfalls (wall-clock skew, batch overwrite, recovery determinism).

