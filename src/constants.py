"""
Constants and configurations for the KV database engine.
"""

# Database file header magic
FILE_MAGIC = b"KVDB_WAL"
FORMAT_VERSION = 1

# Header size on disk in bytes
FILE_HEADER_SIZE = 64

# Maximum allowable key and value lengths per specification
MAX_KEY_SIZE = 1024            # 1 KB
MAX_VALUE_SIZE = 1024 * 1024   # 1 MB
MAX_BATCH_SIZE = 10000         # 10,000 operations per batch

# Compaction statuses
COMPACT_STATUS_NORMAL = 0
COMPACT_STATUS_IN_PROGRESS = 1

# Default buffer chunk size for recovery scanning
RECOVERY_CHUNK_SIZE = 1024 * 1024  # 1 MB chunk reads

