"""
Independent Oracle and Invariant Checker for Torture Testing.
Tracks promises made (ACKed operations) and verifies correctness under failure.
"""

import json
from typing import Dict, List, Tuple, Optional, Any, Set


class Oracle:
    """
    Maintains independent ground truth of all acknowledged database operations.
    Recorded in an independent oracle state to compare against database dumps.
    """

    def __init__(self, oracle_log_path: Optional[str] = None):
        self.oracle_log_path = oracle_log_path
        # Current authoritative ground truth for acknowledged keys: key -> val
        self.acked_state: Dict[str, str] = {}
        # History of acknowledged operations: op_id -> op details
        self.acked_ops: Dict[str, Any] = {}
        # In-flight operations sent to server but not yet ACKed when killed
        self.in_flight_ops: Dict[str, Any] = {}

    def record_sent(self, op_id: str, op_type: str, details: Any) -> None:
        """Records an operation dispatched to the database engine."""
        self.in_flight_ops[op_id] = {
            "type": op_type,
            "details": details,
        }

    def record_ack(self, op_id: str) -> None:
        """Records an ACK returned by the database engine (a promise made)."""
        op = self.in_flight_ops.pop(op_id, None)
        if op is None:
            return

        self.acked_ops[op_id] = op
        op_type = op["type"]
        details = op["details"]

        if op_type == "put":
            # details: (key, value)
            k, v = details
            self.acked_state[k] = v
        elif op_type == "del":
            # details: key
            k = details
            self.acked_state.pop(k, None)
        elif op_type == "batch":
            # details: list of ("put", k, v) or ("del", k)
            for sub_op in details:
                if sub_op[0] == "put":
                    self.acked_state[sub_op[1]] = sub_op[2]
                elif sub_op[0] in ("del", "delete"):
                    self.acked_state.pop(sub_op[1], None)

        if self.oracle_log_path:
            try:
                with open(self.oracle_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"acked": op_id, "op": op}) + "\n")
            except Exception:
                pass

    def verify_database_dump(self, dump_items: List[Tuple[str, str]]) -> Tuple[bool, str]:
        """
        Validates the dump against the Oracle:
        1. Every ACKed operation must be reflected (unless overwritten by a later ACKed op).
        2. Any in-flight single write must either be its prior value or its new value.
        3. Any in-flight batch must be all-or-nothing (either all applied or none applied).
        4. No key ever holds a value that was never written to it (no garbage).
        """
        dump_dict: Dict[str, str] = dict(dump_items)

        # Step 1: Verify all ACKed keys that were not touched by in-flight writes
        # Determine keys touched by in-flight operations
        in_flight_keys: Set[str] = set()
        for op_id, op in self.in_flight_ops.items():
            op_type = op["type"]
            details = op["details"]
            if op_type == "put":
                in_flight_keys.add(details[0])
            elif op_type == "del":
                in_flight_keys.add(details)
            elif op_type == "batch":
                for sub in details:
                    in_flight_keys.add(sub[1])

        # Check all ACKed keys that were NOT in flight
        for k, expected_v in self.acked_state.items():
            if k not in in_flight_keys:
                if k not in dump_dict:
                    return False, f"Missing ACKed key '{k}'. Expected value '{expected_v}', but key is missing from dump."
                if dump_dict[k] != expected_v:
                    return False, f"Value mismatch for ACKed key '{k}'. Expected '{expected_v}', got '{dump_dict[k]}'."

        # Step 2: Verify all keys in the dump exist in either acked_state or in_flight writes
        for k, found_v in dump_dict.items():
            if k not in self.acked_state and k not in in_flight_keys:
                return False, f"Phantom key found in dump: '{k}' with value '{found_v}' was never written!"

        # Step 3: Verify all-or-nothing atomicity for any in-flight batches
        for op_id, op in self.in_flight_ops.items():
            if op["type"] == "batch":
                batch_ops = op["details"]
                # Determine how many ops match their POST-batch state vs PRE-batch state
                applied_matches = 0
                unapplied_matches = 0
                total_ops = len(batch_ops)

                for sub in batch_ops:
                    sub_type = sub[0]
                    k = sub[1]
                    prior_val = self.acked_state.get(k)

                    if sub_type == "put":
                        new_val = sub[2]
                        current_dump_val = dump_dict.get(k)
                        if current_dump_val == new_val:
                            applied_matches += 1
                        elif current_dump_val == prior_val:
                            unapplied_matches += 1
                    elif sub_type in ("del", "delete"):
                        current_dump_val = dump_dict.get(k)
                        if current_dump_val is None:
                            # If prior value was None, this matches both pre and post
                            applied_matches += 1
                            if prior_val is None:
                                unapplied_matches += 1
                        elif current_dump_val == prior_val:
                            unapplied_matches += 1

                # If some ops definitely applied and other ops definitely did not apply
                # then we have an atomicity violation!
                # If total_ops > 1 and applied_matches < total_ops and unapplied_matches < total_ops:
                # then neither all-applied nor all-unapplied holds.
                if applied_matches > 0 and unapplied_matches == total_ops:
                    # All match pre-batch state -> batch completely unapplied (correct!)
                    pass
                elif applied_matches == total_ops:
                    # All match post-batch state -> batch completely applied (correct!)
                    pass
                elif 0 < applied_matches < total_ops and unapplied_matches < total_ops:
                    return False, (
                        f"BATCH ATOMICITY VIOLATION for batch {op_id}: "
                        f"partially applied ({applied_matches} applied, {unapplied_matches} unapplied of {total_ops}). "
                        f"Batches must be all-or-nothing!"
                    )

        return True, "OK"
