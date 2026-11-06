# DEMO_CHEATSHEET.md — Live 15-Minute Judge Demo Preparation

This guide prepares you for the live 15-minute demo (Section 11 & 12 of the Hackathon Guide).

---

## ⏱️ Demo Schedule Breakdown

| Minutes | Stage | What Judge Does | Expected Result |
|---|---|---|---|
| **0 to 1** | Build | Judge asks you to build from clean clone | Runs `.\kvdb.bat` or `python -m unittest discover tests/`. Must work first time! |
| **1 to 4** | Torture (Default) | Judge runs `.\torture.bat --iterations 200 --seed <judge's choice>` | **0 failures**. Judge watches output. |
| **4 to 6** | Torture (Targeted) | Judge runs compaction (`--mode compaction`) and recovery crash (`--mode recovery-crash`) | **0 failures**. |
| **6 to 8** | Manual Kills | Judge runs `.\kvdb.bat serve data.db`, inputs puts/batches by hand, and kills with `taskkill` 5-10 times. Then runs `check` and `dump`. | Invariant holds every time. `check` prints **OK**. |
| **8 to 12** | Crash-Here Questions | Judge points to 3 lines in source code: *"The process dies right here. What is on disk? What does the next open() do?"* | Precise answers from Section 1 below. |
| **12 to 15** | Design Extension | Judge asks to add key expiration (TTL). | Comprehensive answer from Section 2 below. |

---

## 🎯 Section 1: Crash-Here Questions (Minutes 8 to 12)

If the judge points to any line in your code, use these exact answers:

### Question 1: Pointing at `src/engine.py: put()`, line 283 (`self._file.write(data)`)
- **Judge asks**: *"The process dies right here. What is on disk? What does the next open() do?"*
- **Your Answer**:
  > *"A partial prefix of the record has entered the userspace/kernel file buffer, and some or none of the disk sectors may have been flushed. On the next `open()`, the recovery loop scans from offset 64. When it reaches this offset, `parse_record_from_buffer()` detects an incomplete header or payload, or a CRC32 mismatch. `open()` marks `last_committed_offset` at the end of the previous valid record, calls `self._file.truncate(last_committed_offset)`, and issues `os.fsync()`. The torn write is discarded. Because `put` never returned, no promise was made, and the invariant holds."*

### Question 2: Pointing at `src/engine.py: batch()`, line 369 (between operations write and commit marker)
- **Judge asks**: *"The process dies right here, after the batch operations are written, but before writing the commit marker. What is on disk? What does open() do?"*
- **Your Answer**:
  > *"All $N$ operation records belonging to this batch are on disk with valid CRCs and their shared `batch_id`. However, the `OP_BATCH_COMMIT` marker record was never written. On the next `open()`, the recovery scanner parses each operation record, sees a non-zero `batch_id`, and buffers them in `pending_batches[batch_id]`. Because it reaches EOF without ever encountering `OP_BATCH_COMMIT`, `last_committed_offset` was not advanced past the batch. `open()` truncates the file back to `last_committed_offset`, discarding all $N$ operations. Exactly 0 of $N$ operations are visible. A batch is never partially applied."*

### Question 3: Pointing at `src/engine.py: compact()`, line 427 (`os.replace(temp_path, self._path)`)
- **Judge asks**: *"The process dies right here, in the middle of os.replace. What is on disk? What does open() do?"*
- **Your Answer**:
  > *"Both the original database file and the temporary compacted file were fully fsync'd. On Windows NTFS, `os.replace` invokes `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`, which is an atomic directory metadata swap in the NTFS journal. The directory entry either points to the old file or the new file — never a half-state. Both files contain valid 64-byte headers and complete, valid records. On restart, `open()` opens whichever file is at `self._path`, and cleans up any lingering `.tmp` file in the directory. Zero data is lost."*

---

## 💡 Section 2: Design Extension Question (Minutes 12 to 15)

**Judge asks**:
> *"Add an expiry time to every key. Keys past expiry must be invisible and must be purged during compaction. Do not write code. Tell me what changes and what breaks."*

### Your Structured Answer:

#### 1. On-Disk Format Changes:
- In the record header (`src/record.py`), add an 8-byte uint64 field `expire_ts` (UNIX epoch timestamp in milliseconds, or `0` for no expiration).
- Update `RECORD_HEADER_STRUCT` from `<4sIBQQII` to `<4sIBQQQII` (adding 8 bytes to record overhead).
- Update the CRC32 calculation to include `expire_ts` so timestamps are protected against corruption.

#### 2. Recovery & Read Path:
- In `KVDatabase`, store `(value, expire_ts)` in the in-memory index.
- In `get(key)`: check `now = time.time_ms()`. If `expire_ts > 0 and now >= expire_ts`, treat the key as non-existent and return `NOTFOUND`.
- In `dump()`: skip any key whose `expire_ts > 0 and now >= expire_ts`.

#### 3. Compaction Changes:
- During `compact()`, when iterating through in-memory keys to write the new compacted file:
  Filter out any key where `expire_ts > 0 and now >= expire_ts`.
  This permanently purges expired records and reclaims disk space crash-safely.

#### 4. Subtle Problems & What Breaks (Crucial for Full Marks!):
1. **Wall-Clock Skew & Backward Time Travel (NTP jumps):**
   If the operating system clock jumps backward (e.g. NTP synchronization or Daylight Saving Time), an expired key could suddenly become "unexpired" and visible again! To solve this, expiration must be checked against a monotonic clock offset, or recorded as a creation time + relative TTL.
2. **Expired Keys Overwritten in Atomic Batches:**
   Suppose Key X expired 5 seconds ago, but an atomic batch overwrites Key X with a new value. If the batch crashes before commit, Key X must remain invisible/expired. The recovery replay must honor the exact sequence number so that a newer uncommitted write cannot revive an expired predecessor.
3. **Recovery Determinism:**
   During crash recovery replay, if `open()` checks `time.time()`, recovery would not be deterministic (replaying at 11:59 vs 12:01 produces different memory states). Therefore, the replay phase must faithfully reconstruct all records with their expiration timestamps intact, delegating visibility checks strictly to query time.

