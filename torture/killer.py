"""
Immediate process killer for torture testing.
Guarantees uncatchable, instantaneous termination without cleanup handlers:
- TerminateProcess on Windows
- SIGKILL (kill -9) on Unix / Linux / macOS
"""

import os
import sys
import signal
import subprocess


def hard_kill(proc: subprocess.Popen) -> None:
    """
    Terminates the process immediately without running exit handlers,
    atexit hooks, or cleanup code.
    """
    pid = proc.pid
    if sys.platform == "win32":
        try:
            # Use taskkill /F /T /PID to aggressively kill process tree
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5
            )
        except Exception:
            pass

        # Also call TerminateProcess via ctypes as extra assurance
        try:
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 9)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    try:
        proc.wait(timeout=2)
    except Exception:
        pass

