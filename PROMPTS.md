# PROMPTS.md — AI Interaction & Prompt Log

This document records the prompts sent to AI assistants during the 24-hour engineering hackathon in chronological order, documenting the design discussions, bug analysis, verification steps, and engineering challenges.

---

### Prompt 1: Initial Problem Analysis & Architecture Selection
- **Tool**: Claude Opus / Gemini Assistant
- **Approximate Time**: 2026-11-06 11:52:00
- **Prompt**:
  > "analyse this and i want to work on that [Attached: Kill It and Prove It Hackathon Problem Statement and Team Guide PDF]"

---

### Prompt 2: Durability Architecture & Write Ordering Verification
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:05:00
- **Prompt**:
  > "We need to design a single-file append-only Write-Ahead Log (WAL) with an in-memory hash index. To guarantee durability under kill -9 without exception, what is the exact sequence of system calls needed for single writes versus multi-operation atomic batches? Specifically, if we write a commit marker for a batch, why is a single fsync insufficient, and what happens if a crash occurs between the operations write and the commit marker?"

---

### Prompt 3: Python Struct Unpacking Performance for 1,000,000 Keys
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:11:00
- **Prompt**:
  > "The hackathon specification requires that open() with 1,000,000 keys must complete in under 2 seconds on restart. In Python, a naive line-by-line read will be too slow. Can we achieve sub-second recovery using struct.Struct unpack_from on a contiguous memory buffer or large buffered chunks? Write a benchmark to verify the throughput of unpacking 1,000,000 36-byte binary record headers."

---

### Prompt 4: Investigating Batch Atomicity False Violation in Oracle
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:36:00
- **Prompt**:
  > "Our torture tool just failed on iteration 1 with: 'BATCH ATOMICITY VIOLATION: 2 of 8 operations are visible in dump'. However, inspecting the database file shows that the batch was never committed and none of the put operations are in the dump! Why did the Oracle think 2 operations were applied? Is it because the batch contained delete operations on keys that did not exist in the database, and checking 'if k not in dump' returned True? How should the Oracle properly distinguish between an unapplied delete and an applied delete?"

---

### Prompt 5: WAL Recovery with In-Flight Uncommitted Batch Tail
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:40:00
- **Prompt**:
  > "In iteration 4 of the torture test, the child process died while writing a batch. When open() ran, the uncommitted batch records were present on disk without a commit marker. In a WAL database, what should recovery do with uncommitted batch records at the very end of the file? Should we track last_committed_offset and truncate the file back to the last committed record on restart so that check and dump see a clean, consistent log tail?"

---

### Prompt 6: NTFS os.replace Atomic Compaction Semantics
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:44:00
- **Prompt**:
  > "On Windows, os.replace raises PermissionError if the target file is currently open with non-shared access. During compaction, how can we guarantee crash-safe atomic swapping on Windows without leaving the database unopenable if killed right before or during the swap? Walk through the exact file handle lifecycle."

---

### Prompt 7: Live Demo Preparation — Answering the Design Extension Question
- **Tool**: Antigravity AI
- **Approximate Time**: 2026-11-06 12:46:00
- **Prompt**:
  > "Section 11 of the guide states that in minutes 12 to 15, the judge will ask: 'Add an expiry time to every key. Keys past expiry must be invisible and must be purged during compaction. Do not write code. Tell me what changes and what breaks.' How should we structure our answer covering format changes, recovery scan, clock skew / monotonic time, and what happens if an expired key is overwritten inside an atomic batch?"

