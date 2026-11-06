#!/usr/bin/env python3
"""
Torture Test Tool CLI Entry Point for Kill It and Prove It Hackathon.
"""

import os
import sys
import argparse

repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from torture.runner import TortureRunner


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="torture",
        description="Torture test runner to attack and verify crash-safety of kvdb"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=500,
        help="Number of kill/restart iterations to run (default: 500)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Random seed for deterministic reproducibility (mandatory)"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="default",
        choices=["default", "large-values", "large-batches", "compaction", "recovery-crash", "disk-full"],
        help="Targeted torture mode"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="torture_test.db",
        help="Path for temporary database file used during torture run"
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Optional max-bytes limit (used in disk-full mode)"
    )

    args = parser.parse_args()

    # For disk-full mode, default to a tight limit if not specified
    max_bytes = args.max_bytes
    if args.mode == "disk-full" and max_bytes is None:
        max_bytes = 100_000  # 100 KB limit for disk-full simulation

    runner = TortureRunner(
        db_path=args.db_path,
        iterations=args.iterations,
        seed=args.seed,
        mode=args.mode,
        max_bytes=max_bytes,
    )
    return runner.execute()


if __name__ == "__main__":
    sys.exit(main())

