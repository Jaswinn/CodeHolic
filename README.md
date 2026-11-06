# kvdb — Crash-Safe Key-Value Database Engine

> Built from scratch for the **"Kill It and Prove It"** 24-Hour Engineering Hackathon.  
> A zero-dependency, single-file embedded key-value storage engine engineered for provable correctness under instantaneous process termination (`kill -9`).

---

## 🚀 One-Command Build

No external dependencies, storage libraries, or third-party packages required. Uses the Python 3 standard library in full.

```bash
# On Linux / macOS / Git Bash:
chmod +x ./kvdb ./torture

# On Windows:
# Uses kvdb.bat and torture.bat directly
```

To verify the test suite:
```bash
# Linux / macOS:
python3 -m unittest discover tests/

# Windows:
python -m unittest discover tests/
# (or with explicit interpreter):
"C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe" -m unittest discover tests/
```

---

## 🛠️ Command-Line Interface (All 8 Mandatory Commands)

You can run commands using `./kvdb` on POSIX or `.\kvdb.bat` on Windows.

### 1. Open / Recover Database
Opens or creates the database file, recovers from any previous crash, and reports key count and recovery time:
```bash
./kvdb open data.db [--max-bytes 50000000]
# Output: Key count: 42, recovery time: 0.0032s
```

### 2. Store Key-Value (`put`)
Stores the value under the key. Overwrites if existing. Durable upon return:
```bash
./kvdb put data.db "user:101" "Alice Smith"
```

### 3. Retrieve Value (`get`)
Retrieves the current value for key, or prints `NOTFOUND`:
```bash
./kvdb get data.db "user:101"
# Output: Alice Smith
```

### 4. Delete Key (`del`)
Removes the key. Deleting a missing key is not an error. Durable upon return:
```bash
./kvdb del data.db "user:101"
```

### 5. Atomic Batch (`batch`)
Executes a file containing one operation per line (`put k v` or `del k`) atomically:
```bash
./kvdb batch data.db batch_ops.txt
```
*Format of `batch_ops.txt`:*
```
put user:101 Alice
put user:102 Bob
del user:100
```

### 6. Dump All Keys (`dump`)
Prints all active keys and values sorted alphabetically by key:
```bash
./kvdb dump data.db
# Output:
# user:101=Alice
# user:102=Bob
```

### 7. Integrity Check (`check`)
Verifies file structure, header magic, record framing, trailers, and CRC32 checksums:
```bash
./kvdb check data.db
# Output: OK
```

### 8. Daemon Serve Mode (`serve`)
Long-running streaming mode used by the torture tool and live judge testing. Reads commands from stdin and emits `ACK <op-id>` only after the data has been fsync'd to disk:
```bash
./kvdb serve data.db [--max-bytes 50000000]
```
*Stdin protocol:*
- `put <op-id> <key> <value>` &rarr; `ACK <op-id>`
- `del <op-id> <key>` &rarr; `ACK <op-id>`
- `batch <op-id> put:k1:v1;del:k2` &rarr; `ACK <op-id>`
- `begin <op-id>` ... `commit <op-id>` &rarr; `ACK <op-id>`
- `compact [op-id]` &rarr; `ACK <op-id>`
- `dump` &rarr; dumps all keys and values
- `check` &rarr; outputs `OK`
- `exit` &rarr; closes cleanly

---

## 💾 On-Disk Format

The database is authoritative in **exactly one file**.

```
+-------------------------------------------------------------------------------+
| FILE HEADER (64 Bytes, Fixed at Offset 0)                                     |
|   - Magic (8B): b"KVDB_WAL"                                                   |
|   - Format Version (uint32, 4B): 1                                            |
|   - Max Bytes Cap (uint64, 8B): 0 (unlimited) or user configured ceiling      |
|   - Status Flags (uint32, 4B): Status bitflags                               |
|   - Compaction Generation (uint64, 8B): Monotonic compaction generation count |
|   - Reserved (32B): Zero-padded reserved block                                |
+-------------------------------------------------------------------------------+
| RECORD 1                                                                      |
|   - Magic Header (4B): 0xDEADBEEF                                             |
|   - CRC32 Checksum (uint32, 4B): IEEE 802.3 CRC of (Type..Value)              |
|   - Record Type (uint8, 1B): 1=PUT, 2=DEL, 3=BATCH_COMMIT                     |
|   - Batch ID (uint64, 8B): 0 for single op; unique ID for batch operations    |
|   - Sequence Number (uint64, 8B): Monotonic sequence counter                  |
|   - Key Length (uint32, 4B): 1..1024 bytes                                    |
|   - Value Length (uint32, 4B): 0..1,048,576 bytes (1 MB)                     |
|   - Key Payload (Key Length bytes)                                            |
|   - Value Payload (Value Length bytes)                                        |
|   - Magic Trailer (4B): 0xBEEFDEAD                                            |
+-------------------------------------------------------------------------------+
| RECORD 2 ... RECORD N                                                         |
+-------------------------------------------------------------------------------+
```

---

## 🔄 Recovery Algorithm (Plain English)

When `open(path)` is called:
1. **Remove Dangling Temp Files**: Any abandoned `.compact.*.tmp` files from a crashed compaction run are unlinked.
2. **Header Validation**: The 64-byte file header is read and validated. If the file was killed before the 64-byte header finished writing, a fresh valid header is initialized and fsync'd.
3. **Sequential Log Replay**: Beginning at offset 64, records are parsed in 1 MB buffered blocks using binary struct unpacking.
4. **Framing & Checksum Verification**: Every record is verified against `MAGIC_HEADER (0xDEADBEEF)`, payload CRC32 checksum, and `MAGIC_TRAILER (0xBEEFDEAD)`.
5. **Handling Torn Writes**: If a record has an incomplete header, truncated payload, corrupted byte, or invalid trailer, the scanner halts immediately. Any bytes beyond the last valid record are torn writes.
6. **Two-Phase Batch Tracking**: Records with a non-zero `batch_id` are held in an in-memory dictionary `pending_batches`. They are **only** applied if and when their matching `OP_BATCH_COMMIT` record is encountered. If EOF is reached without a commit marker, the uncommitted batch records are discarded.
7. **Atomic Tail Truncation**: The file is truncated back to `last_committed_offset` with `file.truncate()` followed by `os.fsync()`, physically eliminating any torn tails or uncommitted operations.
8. **Ready**: The in-memory hash index is fully restored, sequence numbers are initialized, and the database is open for queries.

---

## ⚡ The Torture Tool

The torture tool starts the database in `serve` mode, spawns concurrent writers, records every `ACK` in an independent Oracle, and randomly terminates the process with `kill -9` (`TerminateProcess` on Windows) at unpredictable millisecond intervals.

```bash
# Standard 500-iteration random kill test:
./torture --iterations 500 --seed 12345

# Targeted mode: Kills during large 1 MB values
./torture --iterations 100 --seed 999 --mode large-values

# Targeted mode: Kills during log compaction
./torture --iterations 100 --seed 7 --mode compaction

# Targeted mode: Kills during open() recovery
./torture --iterations 100 --seed 42 --mode recovery-crash

# Targeted mode: Kills during large 10,000 op batches
./torture --iterations 100 --seed 777 --mode large-batches

# Targeted mode: Disk full simulation with size ceiling
./torture --iterations 100 --seed 888 --mode disk-full
```

