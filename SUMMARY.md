# Project Overview & Execution Guide

A minimalist summary of the **Kill It and Prove It** crash-safe key-value store, its architecture, file layout, and step-by-step instructions.

---

## 1. What This Project Is
A single-file embedded key-value database built in pure Python (standard library only). It is engineered to maintain 100% data integrity when the process is killed at any instruction via `kill -9` or Windows `TerminateProcess`.

### Core Invariants Guaranteed
1. **Durability**: Every acknowledged operation (`ACK`) is on disk and survives crashes.
2. **Atomicity**: Batches are all-or-nothing (never partially applied).
3. **Clean Recovery**: `open()` always recovers cleanly, automatically truncating torn writes.
4. **Zero Bitrot / Corrupt Reads**: Magic bytes (`0xDEADBEEF`), CRC32, and trailer framing (`0xBEEFDEAD`) reject torn sectors.

---

## 2. File & Directory Structure

| Path | Purpose |
|---|---|
| `kvdb.bat` / `kvdb` | Executable CLI wrappers for Windows and POSIX systems. |
| `kvdb.py` | CLI entrypoint mapping the 8 mandatory database subcommands. |
| `src/engine.py` | Storage engine: WAL writing, double-fsync batching, compaction, recovery. |
| `src/record.py` | Binary serialization, CRC32 checksum calculation, and torn-write framing. |
| `src/constants.py` | File header magic, versioning, and size thresholds. |
| `src/cli.py` | Implementation of `open`, `put`, `get`, `del`, `batch`, `dump`, `check`, `serve`. |
| `torture.bat` / `torture.py` | Torture tool running random millisecond hard-kills against concurrent writers. |
| `torture/runner.py` | Multithreaded driver managing server child processes and kills. |
| `torture/oracle.py` | Independent ground-truth tracker verifying correctness of database dumps. |
| `torture/modes.py` | Workload generators for large values, large batches, compaction, and disk-full. |
| `tests/test_scenarios.py` | Unit tests for torn writes, 1-byte hand truncation (F4), and recovery. |
| `tests/test_concurrency.py`| 10-thread concurrent writer & reader atomicity tests. |
| `ARCHITECTURE.md` | Formal design doc, 32-scenario proof table (A1–F4), and durability proofs. |
| `BREAKING.md` | Attack document: 12-row crash points table and architectural weaknesses. |
| `PROMPTS.md` | Chronological log of AI prompts used during development. |
| `DEMO_CHEATSHEET.md` | Quick reference for the live 15-minute judge questions. |

---

## 3. Step-by-Step Instructions

All commands can be executed in PowerShell or Command Prompt from `D:\Srinathon 3.0`.

### A. Run the Engine Self-Test Demo
Runs a complete lifecycle test (put, get, batch, compaction, reopen) directly:
```cmd
& "C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe" src/engine.py
```

### B. Run the Automated Unit Tests
Runs the test suite verifying torn-write recovery, uncommitted batch rollback, and concurrency:
```cmd
& "C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe" -m unittest discover tests/
```

### C. Run the 8 Mandatory CLI Commands
```cmd
:: 1. Open / recover database
.\kvdb.bat open test.db

:: 2. Store key-value
.\kvdb.bat put test.db "user:101" "Alice"

:: 3. Retrieve value
.\kvdb.bat get test.db "user:101"

:: 4. Check file integrity (prints OK)
.\kvdb.bat check test.db

:: 5. Execute an atomic batch from file
:: (File format: lines of 'put <key> <val>' or 'del <key>')
.\kvdb.bat batch test.db batch.txt

:: 6. Dump all keys and values sorted
.\kvdb.bat dump test.db

:: 7. Trigger crash-safe log compaction
.\kvdb.bat compact test.db

:: 8. Delete a key
.\kvdb.bat del test.db "user:101"
```

### D. Run the Torture Test Tool
The torture tool streams operations with concurrent writers and kills the process mid-write:

```cmd
:: Standard 50-iteration random kill run (seed is required):
.\torture.bat --iterations 50 --seed 12345

:: Targeted mode: Kills during 1 MB value writes
.\torture.bat --iterations 20 --seed 999 --mode large-values

:: Targeted mode: Kills during log compaction
.\torture.bat --iterations 20 --seed 7 --mode compaction

:: Targeted mode: Kills during recovery (open)
.\torture.bat --iterations 20 --seed 42 --mode recovery-crash

:: Targeted mode: Small disk-full limit simulation
.\torture.bat --iterations 20 --seed 888 --mode disk-full
```

---

## 4. Judging Demo Quick Reference (15-Minute Session)

### Minutes 1–8: Execution & Torture
1. **Build**: Run unit tests (`unittest discover tests/`).
2. **Torture**: Run `.\torture.bat --iterations 200 --seed <judge_choice>`.
3. **Manual kills**: The judge runs `serve`, types commands, kills with Task Manager, then runs `check` (prints `OK`) and `dump`.

### Minutes 8–12: "Crash-Here" Questions
If the judge points to a line and asks *"The process dies right here, what is on disk and what does open() do?"*:
- **During single `put`**: A torn/partial record may be at the file tail. `open()` scans sequentially, detects an invalid CRC32 or incomplete header, calls `truncate()` to strip the torn bytes, and recovers the last valid record.
- **Between batch writes and commit marker**: All batch operation records are on disk, but the `OP_BATCH_COMMIT` record is absent. `open()` collects batch records in `pending_batches`; seeing no commit marker before EOF, it truncates the file back to `last_committed_offset`. **0 operations are visible**.
- **During compaction `os.replace`**: Filesystem directory rename is atomic. Either the old or the new compacted file is active. Both are complete and valid; lingering `.tmp` files are cleaned up on `open()`.

### Minutes 12–15: Design Extension Question (Adding TTL / Expiry)
- **Format**: Add an 8-byte uint64 `expire_ts` to the record header and CRC.
- **Read Path**: `get()` and `dump()` check `current_time >= expire_ts` and return `NOTFOUND`.
- **Compaction**: During compaction, skip any key where `current_time >= expire_ts`.
- **Subtle Gotchas**:
  1. *Clock skew/NTP backward jumps*: Wall clock can jump; store monotonic offsets or creation timestamps.
  2. *Deterministic recovery*: Recovery must reconstruct expiration timestamps faithfully without dropping keys based on local clock during replay.

