"""
Torture Test Runner orchestrating child process, concurrent writers, random kills,
and oracle verification.
"""

import os
import sys
import time
import random
import queue
import shutil
import subprocess
import threading
from typing import Optional, List, Tuple, Dict, Any

from torture.killer import hard_kill
from torture.oracle import Oracle
from torture.modes import (
    generate_default_op,
    generate_large_value_op,
    generate_large_batch_op,
)

PYTHON_EXE = sys.executable


def get_python_exe() -> str:
    """Returns the working Python executable."""
    if os.path.exists(r"C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe"):
        return r"C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe"
    return sys.executable


class TortureRunner:
    def __init__(
        self,
        db_path: str,
        iterations: int = 500,
        seed: int = 12345,
        mode: str = "default",
        max_bytes: Optional[int] = None,
    ):
        self.db_path = os.path.abspath(db_path)
        self.iterations = iterations
        self.seed = seed
        self.mode = mode
        self.max_bytes = max_bytes
        self.py_exe = get_python_exe()
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.kvdb_script = os.path.join(self.repo_root, "kvdb.py")

        random.seed(self.seed)

    def _run_cli_command(self, *args) -> Tuple[int, str, str]:
        """Runs kvdb CLI command synchronously."""
        cmd = [self.py_exe, self.kvdb_script] + list(args)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _start_server(self) -> subprocess.Popen:
        """Starts database in serve mode."""
        cmd = [self.py_exe, self.kvdb_script, "serve", self.db_path]
        if self.max_bytes:
            cmd += ["--max-bytes", str(self.max_bytes)]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        # Wait for READY banner
        line = proc.stdout.readline()
        if not line or "READY" not in line:
            raise RuntimeError(f"Server failed to start, output: {line!r}")
        return proc

    def run_single_iteration(self, iteration_num: int, oracle: Oracle) -> Tuple[bool, str]:
        """Runs a single torture iteration."""
        if self.mode == "recovery-crash":
            return self._run_recovery_crash_iteration(iteration_num, oracle)

        proc = self._start_server()
        stop_event = threading.Event()
        stdin_lock = threading.Lock()
        op_counter = [iteration_num * 10000]

        # Thread to read ACKs from server stdout
        def ack_reader():
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("ACK "):
                        parts = line.split(maxsplit=1)
                        if len(parts) > 1:
                            oracle.record_ack(parts[1])
            except Exception:
                pass

        reader_thread = threading.Thread(target=ack_reader, daemon=True)
        reader_thread.start()

        # Writer worker function
        def writer_worker(writer_id: int):
            while not stop_event.is_set():
                with stdin_lock:
                    op_counter[0] += 1
                    cid = op_counter[0]

                if self.mode == "large-values":
                    op_type, op_id, details, cmd = generate_large_value_op(cid)
                    oracle.record_sent(op_id, op_type, details)
                elif self.mode == "large-batches":
                    op_type, op_id, details, cmd = generate_large_batch_op(cid, batch_size=500)
                    oracle.record_sent(op_id, op_type, details)
                elif self.mode == "compaction":
                    # Alternate normal ops and explicit compact triggers
                    if random.random() < 0.2:
                        op_id = f"cmp_{cid}"
                        cmd = f"compact {op_id}\n"
                        oracle.record_sent(op_id, "compact", None)
                    else:
                        op_type, op_id, details, cmd = generate_default_op(cid)
                        oracle.record_sent(op_id, op_type, details)
                else:
                    op_type, op_id, details, cmd = generate_default_op(cid)
                    oracle.record_sent(op_id, op_type, details)

                try:
                    with stdin_lock:
                        if proc.poll() is not None:
                            break
                        proc.stdin.write(cmd)
                        proc.stdin.flush()
                except Exception:
                    break

                # Micro-jitter between operations
                time.sleep(random.uniform(0.0005, 0.003))

        # Launch at least two concurrent writers (Invariant D1/D2)
        writers = [
            threading.Thread(target=writer_worker, args=(1,), daemon=True),
            threading.Thread(target=writer_worker, args=(2,), daemon=True),
        ]
        for w in writers:
            w.start()

        # Random delay before kill in milliseconds (never fixed operations count!)
        if self.mode == "large-values":
            delay = random.uniform(0.020, 0.120)
        elif self.mode == "large-batches":
            delay = random.uniform(0.030, 0.150)
        elif self.mode == "compaction":
            delay = random.uniform(0.010, 0.080)
        else:
            delay = random.uniform(0.005, 0.060)

        time.sleep(delay)

        # KILL THE CHILD PROCESS WITH NO WARNING (kill -9)
        hard_kill(proc)
        stop_event.set()

        for w in writers:
            w.join(timeout=0.5)

        # 1. Restart database with open (recovers from crash, repairs torn tail)
        rc, out, err = self._run_cli_command("open", self.db_path)
        if rc != 0:
            return False, f"kvdb open recovery failed after crash: rc={rc}, stderr={err!r}"

        # 2. Run check on database
        rc, out, err = self._run_cli_command("check", self.db_path)
        if rc != 0 or "OK" not in out:
            return False, f"kvdb check failed after crash: rc={rc}, stdout={out!r}, stderr={err!r}"

        # 3. Run dump on database
        rc, out, err = self._run_cli_command("dump", self.db_path)
        if rc != 0:
            return False, f"kvdb dump failed after crash: rc={rc}, stderr={err!r}"

        dump_items = []
        for line in out.splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                dump_items.append((k, v))

        # 3. Compare dump against the Oracle
        ok, reason = oracle.verify_database_dump(dump_items)
        if not ok:
            return False, reason

        return True, "OK"

    def _run_recovery_crash_iteration(self, iteration_num: int, oracle: Oracle) -> Tuple[bool, str]:
        """
        Special mode: kills during recovery (open command) then restarts!
        Tests scenario C1, C2, F2 (recovery idempotence).
        """
        # First write some data
        proc = self._start_server()
        for i in range(10):
            k = f"rc_k_{iteration_num}_{i}"
            v = f"rc_v_{i}"
            op_id = f"rc_op_{iteration_num}_{i}"
            oracle.record_sent(op_id, "put", (k, v))
            proc.stdin.write(f"put {op_id} {k} {v}\n")
            proc.stdin.flush()
            ack = proc.stdout.readline()
            if "ACK" in ack:
                oracle.record_ack(op_id)
        hard_kill(proc)

        # Now start an open/recovery command as child process and kill it mid-execution!
        rec_proc = subprocess.Popen(
            [self.py_exe, self.kvdb_script, "open", self.db_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(random.uniform(0.001, 0.010))
        hard_kill(rec_proc)

        # Now run open again and verify it recovers successfully!
        rc, out, err = self._run_cli_command("check", self.db_path)
        if rc != 0 or "OK" not in out:
            return False, f"Check failed after kill during recovery: rc={rc}, out={out!r}"

        rc, out, err = self._run_cli_command("dump", self.db_path)
        dump_items = []
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                dump_items.append((k, v))

        ok, reason = oracle.verify_database_dump(dump_items)
        if not ok:
            return False, reason
        return True, "OK"

    def execute(self) -> int:
        """Runs full torture loop across requested iterations."""
        print(f"=== Starting Torture Run ===")
        print(f"Mode: {self.mode}")
        print(f"Iterations: {self.iterations}")
        print(f"Seed: {self.seed}")
        print(f"Target Database: {self.db_path}")
        print(f"============================")

        # Initialize clean test db and oracle
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

        oracle = Oracle()
        passed = 0
        failed = 0

        t_start = time.perf_counter()

        for i in range(1, self.iterations + 1):
            t0 = time.perf_counter()
            success, msg = self.run_single_iteration(i, oracle)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if success:
                passed += 1
                print(f"[{i:04d}/{self.iterations:04d}] PASS | {elapsed_ms:6.1f}ms | ACKed ops: {len(oracle.acked_ops)}")
            else:
                failed += 1
                print(f"[{i:04d}/{self.iterations:04d}] FAIL | {elapsed_ms:6.1f}ms | Reason: {msg}")
                print(f"\n[CRITICAL FAILURE] Stopped on iteration {i} (seed {self.seed}).")
                print(f"Details: {msg}")
                return 1

        total_time = time.perf_counter() - t_start
        print(f"\n=== Torture Run Completed ===")
        print(f"Total time: {total_time:.2f}s")
        print(f"Passed: {passed} / {self.iterations}")
        print(f"Failed: {failed}")
        print(f"Result: SUCCESS - ALL INVARIANTS HELD UNDER FAILURE")
        return 0
