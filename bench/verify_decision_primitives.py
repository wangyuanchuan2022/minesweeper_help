"""Deterministic contract tests for the C++ decision-layer primitives.

This is deliberately a preflight-only test: it does not run a game and does
not change the production decision path.  The reference split mirrors the
production distance rule: a closed cell is frontier iff one of its 8-neighbors
is an opened number cell.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import cv2

# Running a script from bench/ puts that directory first on sys.path; make the
# repository package authoritative instead of an unrelated sibling utils.py.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from utils import native


MEM_BUDGET = 1536 * 1024 * 1024


def reference_split(board: np.ndarray):
    """True production semantics: cv2.distanceTransform on interior-only bg."""
    rows, cols = board.shape
    h, w = rows - 2, cols - 2
    bg = np.zeros((h, w), dtype=np.uint8)
    for j in range(h):
        for i in range(w):
            if int(board[j + 1, i + 1]) in (9, 10):
                bg[j, i] = 255
    res = cv2.distanceTransform(bg, cv2.DIST_L12, 0)
    frontier, interior = [], []
    num9 = num10 = 0
    for j in range(h):
        for i in range(w):
            v = int(board[j + 1, i + 1])
            if v == 9:
                num9 += 1
            if v == 10:
                num10 += 1
            if v != 9:
                continue
            (frontier if res[j, i] <= 1.5 else interior).append((i + 1, j + 1))
    return frontier, interior, num9, num10


def assert_mem_contract():
    small = native.estimate_mem(100, 20, MEM_BUDGET)
    assert small is not None and small["allowed"] is True
    assert small["bytes"] == 100 * 20 * 2304

    exact = native.estimate_mem(1, 1, 2304)
    assert exact["allowed"] is True
    over = native.estimate_mem(1, 1, 2303)
    assert over["allowed"] is False

    huge = native.estimate_mem(10**18, 10**18, MEM_BUDGET)
    assert huge["allowed"] is False
    assert huge["bytes"] == 2**63 - 1

    board = np.zeros((5, 6), dtype=np.int32)
    board[1:-1, 1:-1] = 9
    preflight = native.decision_preflight(board, 100, MEM_BUDGET)
    assert preflight["allowed"] is True
    assert preflight["estimated_bytes"] == 100 * preflight["candidates"] * 2304
    assert preflight["candidates"] == 1 + preflight["num9"]

    storage = native.packed_board_storage(576)
    assert storage["bits_per_cell"] == 4
    assert storage["payload_bytes"] == 288
    assert storage["active_words"] == 36
    assert storage["object_storage_bytes"] >= storage["payload_bytes"]

    try:
        native.estimate_mem(-1, 1, MEM_BUDGET)
    except Exception:
        pass
    else:
        raise AssertionError("negative worlds must be rejected")


def assert_split_contract():
    rng = np.random.default_rng(20261105)
    for _ in range(100):
        h = int(rng.integers(1, 20))
        w = int(rng.integers(1, 20))
        # Randomize the full array, including the sentinel border, so a C++
        # implementation that accidentally treats border 0 as an opened number
        # is caught (verified against real cv2: 0 mismatches on 500 cases).
        board = rng.choice([-1, 0, 1, 3, 8, 9, 10, 11],
                           size=(h + 2, w + 2)).astype(np.int32)
        expected = reference_split(board)
        actual = native.split_frontier(board)
        assert actual is not None
        actual = tuple([list(v) if i < 2 else int(v) for i, v in enumerate(actual)])
        assert actual == expected, (board.tolist(), expected, actual)

    # Negative control: changing a closed cell to an opened number must move
    # its neighboring closed cells from interior to frontier.
    board = np.zeros((9, 9), dtype=np.int32)
    board[1:-1, 1:-1] = 9
    before = native.split_frontier(board)
    board[4, 4] = 1
    after = native.split_frontier(board)
    assert (4, 4) in before[1]
    assert (4, 4) not in after[0] and (4, 4) not in after[1]
    assert len(after[0]) > len(before[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not native.available:
        raise SystemExit("C++ native module unavailable: decision primitives cannot be verified")
    assert_mem_contract()
    assert_split_contract()
    result = {"status": "PASS", "memory": "PASS", "split_frontier": "PASS"}
    print(json.dumps(result) if args.json else "PASS: estimate_mem and split_frontier contracts")


if __name__ == "__main__":
    main()
