"""
Binary record format and validation for crash-safe key-value database.

On-disk record layout:
[4B MAGIC] [4B CRC32] [1B TYPE] [8B BATCH_ID] [8B SEQ_NUM] [4B KEY_LEN] [4B VAL_LEN] [KEY] [VALUE] [4B TRAILER]
"""

import struct
import zlib
from typing import Optional, Tuple, NamedTuple

MAGIC_HEADER = b"\xDE\xAD\xBE\xEF"
MAGIC_TRAILER = b"\xBE\xEF\xDE\xAD"

# Record types
OP_PUT = 1
OP_DEL = 2
OP_BATCH_COMMIT = 3
OP_BATCH_ABORT = 4
OP_CHECKPOINT = 5

HEADER_STRUCT = struct.Struct("<4sIBQQII")
HEADER_SIZE = HEADER_STRUCT.size  # 4 + 4 + 1 + 8 + 8 + 4 + 4 = 33 bytes
TRAILER_STRUCT = struct.Struct("<4s")
TRAILER_SIZE = TRAILER_STRUCT.size  # 4 bytes
RECORD_OVERHEAD = HEADER_SIZE + TRAILER_SIZE  # 37 bytes


class Record(NamedTuple):
    record_type: int
    batch_id: int
    seq_num: int
    key: bytes
    value: bytes

    def serialize(self) -> bytes:
        """Serializes the record into durable bytes with CRC32 and framing."""
        key_bytes = self.key if isinstance(self.key, bytes) else self.key.encode("utf-8")
        val_bytes = self.value if isinstance(self.value, bytes) else self.value.encode("utf-8")
        
        key_len = len(key_bytes)
        val_len = len(val_bytes)
        
        # Payload for CRC calculation: type, batch_id, seq_num, key_len, val_len, key, value
        crc_payload = struct.pack(
            "<BQQII",
            self.record_type,
            self.batch_id,
            self.seq_num,
            key_len,
            val_len
        ) + key_bytes + val_bytes
        
        crc = zlib.crc32(crc_payload) & 0xFFFFFFFF
        
        header = HEADER_STRUCT.pack(
            MAGIC_HEADER,
            crc,
            self.record_type,
            self.batch_id,
            self.seq_num,
            key_len,
            val_len
        )
        
        trailer = TRAILER_STRUCT.pack(MAGIC_TRAILER)
        return header + key_bytes + val_bytes + trailer


def parse_record_from_buffer(buf: bytes, offset: int = 0) -> Tuple[Optional[Record], int, Optional[str]]:
    """
    Parses a record from a buffer starting at offset.
    Returns: (record, total_bytes_consumed, error_reason)
    If buffer does not have enough bytes for header, returns (None, 0, 'INCOMPLETE_HEADER').
    If magic header does not match, returns (None, 0, 'INVALID_MAGIC').
    If buffer does not have full payload, returns (None, 0, 'INCOMPLETE_PAYLOAD').
    If CRC or trailer fails, returns (None, 0, 'CORRUPT_CHECKSUM' or 'INVALID_TRAILER').
    """
    buf_len = len(buf) - offset
    if buf_len < HEADER_SIZE:
        return None, 0, "INCOMPLETE_HEADER"
    
    magic, crc, rec_type, batch_id, seq_num, key_len, val_len = HEADER_STRUCT.unpack_from(buf, offset)
    
    if magic != MAGIC_HEADER:
        return None, 0, "INVALID_MAGIC"
    
    total_record_size = HEADER_SIZE + key_len + val_len + TRAILER_SIZE
    if buf_len < total_record_size:
        return None, 0, "INCOMPLETE_PAYLOAD"
    
    # Extract key and value
    key_start = offset + HEADER_SIZE
    key_end = key_start + key_len
    val_start = key_end
    val_end = val_start + val_len
    trailer_start = val_end
    
    key_bytes = bytes(buf[key_start:key_end])
    val_bytes = bytes(buf[val_start:val_end])
    
    # Check trailer
    (trailer,) = TRAILER_STRUCT.unpack_from(buf, trailer_start)
    if trailer != MAGIC_TRAILER:
        return None, 0, "INVALID_TRAILER"
    
    # Verify CRC32
    crc_payload = struct.pack(
        "<BQQII",
        rec_type,
        batch_id,
        seq_num,
        key_len,
        val_len
    ) + key_bytes + val_bytes
    
    actual_crc = zlib.crc32(crc_payload) & 0xFFFFFFFF
    if actual_crc != crc:
        return None, 0, "CORRUPT_CHECKSUM"
    
    record = Record(
        record_type=rec_type,
        batch_id=batch_id,
        seq_num=seq_num,
        key=key_bytes,
        value=val_bytes
    )
    return record, total_record_size, None
