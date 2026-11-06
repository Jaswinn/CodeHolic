"""
Command-line interface implementing all required commands for kvdb.
"""

import os
import sys
import time
import argparse
from typing import List, Tuple

from src.engine import open_db, DatabaseError, DiskFullError, ValidationError


def cmd_open(args: argparse.Namespace) -> int:
    max_bytes = args.max_bytes
    t0 = time.perf_counter()
    db = open_db(args.path, max_bytes=max_bytes)
    t1 = time.perf_counter()
    recovery_time = t1 - t0
    count = db.key_count
    db.close()
    print(f"Key count: {count}, recovery time: {recovery_time:.4f}s")
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        db.put(args.key, args.value)
        db.close()
        return 0
    except DiskFullError as e:
        sys.stderr.write(f"DISK_FULL: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_get(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        val = db.get(args.key)
        db.close()
        if val is None:
            print("NOTFOUND")
            return 1
        else:
            try:
                print(val.decode("utf-8"))
            except UnicodeDecodeError:
                sys.stdout.buffer.write(val)
                sys.stdout.buffer.write(b"\n")
            return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_del(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        db.delete(args.key)
        db.close()
        return 0
    except DiskFullError as e:
        sys.stderr.write(f"DISK_FULL: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_batch(args: argparse.Namespace) -> int:
    batch_file = args.file
    if not os.path.exists(batch_file):
        sys.stderr.write(f"Batch file not found: {batch_file}\n")
        return 1

    ops: List[Tuple[str, str, str]] = []
    with open(batch_file, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=2)
            op = parts[0].lower()
            if op == "put":
                if len(parts) < 3:
                    sys.stderr.write(f"Invalid put at line {line_no}: {line}\n")
                    return 1
                ops.append(("put", parts[1], parts[2]))
            elif op in ("del", "delete"):
                if len(parts) < 2:
                    sys.stderr.write(f"Invalid del at line {line_no}: {line}\n")
                    return 1
                ops.append(("del", parts[1], ""))
            else:
                sys.stderr.write(f"Unknown op '{op}' at line {line_no}\n")
                return 1

    try:
        db = open_db(args.path)
        db.batch(ops)
        db.close()
        return 0
    except DiskFullError as e:
        sys.stderr.write(f"DISK_FULL: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_dump(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        items = db.dump()
        db.close()
        for k, v in items:
            try:
                k_str = k.decode("utf-8")
                v_str = v.decode("utf-8")
                sys.stdout.write(f"{k_str}={v_str}\n")
            except UnicodeDecodeError:
                sys.stdout.buffer.write(k + b"=" + v + b"\n")
        sys.stdout.flush()
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_check(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        ok, reason = db.check_integrity()
        db.close()
        if ok:
            print("OK")
            return 0
        else:
            print(f"CORRUPT: {reason}")
            return 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 1


def cmd_compact(args: argparse.Namespace) -> int:
    try:
        db = open_db(args.path)
        new_size = db.compact()
        db.close()
        print(f"OK {new_size}")
        return 0
    except DiskFullError as e:
        sys.stderr.write(f"DISK_FULL: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """
    Long-running serve mode.
    Reads operations from stdin, executes them, and prints ACK <op-id>
    only after the operation has been fsync'd and is durable on disk.
    """
    max_bytes = getattr(args, "max_bytes", None)
    db = open_db(args.path, max_bytes=max_bytes)

    # Let caller know serve mode is ready to receive commands
    sys.stdout.write("READY\n")
    sys.stdout.flush()

    batch_buffer = []
    current_batch_id = None
    expected_batch_count = None

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            parts = line.split(maxsplit=3)
            cmd = parts[0].lower()

            if cmd == "put":
                # put <op-id> <key> <value>
                if len(parts) < 4:
                    sys.stderr.write(f"Invalid put syntax: {line}\n")
                    continue
                op_id = parts[1]
                key = parts[2]
                val = parts[3]
                try:
                    db.put(key, val)
                    sys.stdout.write(f"ACK {op_id}\n")
                    sys.stdout.flush()
                except DiskFullError as e:
                    sys.stdout.write(f"ERR {op_id} DISK_FULL {e}\n")
                    sys.stdout.flush()
                except Exception as e:
                    sys.stdout.write(f"ERR {op_id} {e}\n")
                    sys.stdout.flush()

            elif cmd in ("del", "delete"):
                # del <op-id> <key>
                if len(parts) < 3:
                    sys.stderr.write(f"Invalid del syntax: {line}\n")
                    continue
                op_id = parts[1]
                key = parts[2]
                try:
                    db.delete(key)
                    sys.stdout.write(f"ACK {op_id}\n")
                    sys.stdout.flush()
                except DiskFullError as e:
                    sys.stdout.write(f"ERR {op_id} DISK_FULL {e}\n")
                    sys.stdout.flush()
                except Exception as e:
                    sys.stdout.write(f"ERR {op_id} {e}\n")
                    sys.stdout.flush()

            elif cmd == "begin":
                # begin <op-id>
                current_batch_id = parts[1] if len(parts) > 1 else "batch"
                batch_buffer = []

            elif cmd == "bput":
                # bput <key> <value> (inside begin..commit)
                if len(parts) >= 3:
                    batch_buffer.append(("put", parts[1], parts[2]))

            elif cmd == "bdel":
                # bdel <key> (inside begin..commit)
                if len(parts) >= 2:
                    batch_buffer.append(("del", parts[1], ""))

            elif cmd == "commit":
                # commit <op-id>
                op_id = parts[1] if len(parts) > 1 else current_batch_id
                try:
                    db.batch(batch_buffer)
                    sys.stdout.write(f"ACK {op_id}\n")
                    sys.stdout.flush()
                except DiskFullError as e:
                    sys.stdout.write(f"ERR {op_id} DISK_FULL {e}\n")
                    sys.stdout.flush()
                except Exception as e:
                    sys.stdout.write(f"ERR {op_id} {e}\n")
                    sys.stdout.flush()
                finally:
                    batch_buffer = []
                    current_batch_id = None

            elif cmd == "batch":
                # batch <op-id> <num_ops>
                # OR single-line batch: batch <op-id> put:k:v,del:k
                if len(parts) >= 3 and parts[2].isdigit():
                    current_batch_id = parts[1]
                    expected_batch_count = int(parts[2])
                    batch_buffer = []
                elif len(parts) >= 3:
                    # Inline format: batch <op-id> <ops_spec>
                    op_id = parts[1]
                    ops_str = parts[2]
                    parsed_ops = []
                    for item in ops_str.split(";"):
                        item = item.strip()
                        if not item: continue
                        sub = item.split(":", 2)
                        if sub[0].lower() == "put":
                            parsed_ops.append(("put", sub[1], sub[2] if len(sub) > 2 else ""))
                        elif sub[0].lower() in ("del", "delete"):
                            parsed_ops.append(("del", sub[1], ""))
                    try:
                        db.batch(parsed_ops)
                        sys.stdout.write(f"ACK {op_id}\n")
                        sys.stdout.flush()
                    except DiskFullError as e:
                        sys.stdout.write(f"ERR {op_id} DISK_FULL {e}\n")
                        sys.stdout.flush()
                    except Exception as e:
                        sys.stdout.write(f"ERR {op_id} {e}\n")
                        sys.stdout.flush()

            elif cmd == "get":
                # get <key>
                if len(parts) >= 2:
                    val = db.get(parts[1])
                    if val is None:
                        sys.stdout.write("NOTFOUND\n")
                    else:
                        sys.stdout.write(val.decode("utf-8", errors="replace") + "\n")
                    sys.stdout.flush()

            elif cmd == "compact":
                # compact [op-id]
                op_id = parts[1] if len(parts) > 1 else None
                try:
                    new_size = db.compact()
                    if op_id:
                        sys.stdout.write(f"ACK {op_id}\n")
                    else:
                        sys.stdout.write(f"COMPACTED {new_size}\n")
                    sys.stdout.flush()
                except DiskFullError as e:
                    if op_id:
                        sys.stdout.write(f"ERR {op_id} DISK_FULL {e}\n")
                    else:
                        sys.stdout.write(f"ERR DISK_FULL {e}\n")
                    sys.stdout.flush()
                except Exception as e:
                    if op_id:
                        sys.stdout.write(f"ERR {op_id} {e}\n")
                    else:
                        sys.stdout.write(f"ERR {e}\n")
                    sys.stdout.flush()

            elif cmd == "check":
                ok, reason = db.check_integrity()
                if ok:
                    sys.stdout.write("OK\n")
                else:
                    sys.stdout.write(f"CORRUPT: {reason}\n")
                sys.stdout.flush()

            elif cmd == "dump":
                for k, v in db.dump():
                    sys.stdout.write(f"{k.decode('utf-8', errors='replace')}={v.decode('utf-8', errors='replace')}\n")
                sys.stdout.flush()

            elif cmd in ("exit", "quit", "close"):
                break

    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        db.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kvdb", description="Crash-safe key-value store CLI")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # open <path> [--max-bytes N]
    p_open = subparsers.add_parser("open", help="Open or create database and report recovery")
    p_open.add_argument("path", help="Path to database file")
    p_open.add_argument("--max-bytes", type=int, default=None, help="Maximum database size in bytes")
    p_open.set_defaults(func=cmd_open)

    # put <path> <key> <value>
    p_put = subparsers.add_parser("put", help="Store a value under key")
    p_put.add_argument("path", help="Path to database file")
    p_put.add_argument("key", help="Key")
    p_put.add_argument("value", help="Value")
    p_put.set_defaults(func=cmd_put)

    # get <path> <key>
    p_get = subparsers.add_parser("get", help="Retrieve value for key")
    p_get.add_argument("path", help="Path to database file")
    p_get.add_argument("key", help="Key")
    p_get.set_defaults(func=cmd_get)

    # del <path> <key>
    p_del = subparsers.add_parser("del", help="Delete key")
    p_del.add_argument("path", help="Path to database file")
    p_del.add_argument("key", help="Key")
    p_del.set_defaults(func=cmd_del)

    # batch <path> <file>
    p_batch = subparsers.add_parser("batch", help="Execute atomic batch of operations from file")
    p_batch.add_argument("path", help="Path to database file")
    p_batch.add_argument("file", help="Path to batch instructions file")
    p_batch.set_defaults(func=cmd_batch)

    # dump <path>
    p_dump = subparsers.add_parser("dump", help="Print all keys and values sorted by key")
    p_dump.add_argument("path", help="Path to database file")
    p_dump.set_defaults(func=cmd_dump)

    # check <path>
    p_check = subparsers.add_parser("check", help="Verify database integrity")
    p_check.add_argument("path", help="Path to database file")
    p_check.set_defaults(func=cmd_check)

    # compact <path>
    p_compact = subparsers.add_parser("compact", help="Trigger log compaction")
    p_compact.add_argument("path", help="Path to database file")
    p_compact.set_defaults(func=cmd_compact)

    # serve <path> [--max-bytes N]
    p_serve = subparsers.add_parser("serve", help="Run in daemon serve mode over stdin/stdout")
    p_serve.add_argument("path", help="Path to database file")
    p_serve.add_argument("--max-bytes", type=int, default=None, help="Maximum database size in bytes")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(args=None) -> int:
    parser = build_parser()
    parsed_args = parser.parse_args(args)
    return parsed_args.func(parsed_args)

