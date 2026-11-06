"""
Workload generators for different torture testing modes.
"""

import random
import string
from typing import List, Tuple, Any


def random_string(length: int) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def generate_default_op(op_id_counter: int) -> Tuple[str, str, Any, str]:
    """
    Generates a normal random operation:
    Returns (op_type, op_id, details, command_string_for_serve)
    """
    op_id = f"op_{op_id_counter}"
    r = random.random()

    if r < 0.60:
        # Single PUT
        key = f"key_{random.randint(1, 100)}"
        val = f"val_{random_string(random.randint(5, 50))}"
        return "put", op_id, (key, val), f"put {op_id} {key} {val}\n"

    elif r < 0.75:
        # Single DEL
        key = f"key_{random.randint(1, 100)}"
        return "del", op_id, key, f"del {op_id} {key}\n"

    else:
        # Atomic BATCH of 2 to 10 operations
        batch_size = random.randint(2, 8)
        batch_ops = []
        batch_str_parts = []
        for i in range(batch_size):
            if random.random() < 0.8:
                bk = f"bkey_{op_id_counter}_{i}"
                bv = f"bval_{random_string(10)}"
                batch_ops.append(("put", bk, bv))
                batch_str_parts.append(f"put:{bk}:{bv}")
            else:
                bk = f"bkey_{op_id_counter}_{i}"
                batch_ops.append(("del", bk))
                batch_str_parts.append(f"del:{bk}")

        cmd = f"batch {op_id} {';'.join(batch_str_parts)}\n"
        return "batch", op_id, batch_ops, cmd


def generate_large_value_op(op_id_counter: int) -> Tuple[str, str, Any, str]:
    """Generates a PUT operation with a large value (up to 1 MB)."""
    op_id = f"lv_op_{op_id_counter}"
    key = f"large_key_{random.randint(1, 10)}"
    # 256 KB to 1 MB value
    size_kb = random.choice([256, 512, 1024])
    val = random_string(size_kb * 1024)
    return "put", op_id, (key, val), f"put {op_id} {key} {val}\n"


def generate_large_batch_op(op_id_counter: int, batch_size: int = 1000) -> Tuple[str, str, Any, str]:
    """Generates an atomic batch with many operations (up to thousands)."""
    op_id = f"lb_op_{op_id_counter}"
    batch_ops = []
    batch_str_parts = []
    for i in range(batch_size):
        bk = f"lbk_{op_id_counter}_{i}"
        bv = f"v_{i}"
        batch_ops.append(("put", bk, bv))
        batch_str_parts.append(f"put:{bk}:{bv}")

    cmd = f"batch {op_id} {';'.join(batch_str_parts)}\n"
    return "batch", op_id, batch_ops, cmd

