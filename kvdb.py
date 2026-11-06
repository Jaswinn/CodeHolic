#!/usr/bin/env python3
"""
CLI entry point for kvdb.
"""

import sys
import os

# Ensure repo root is on sys.path
repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())

