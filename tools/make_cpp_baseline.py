# -*- coding: utf-8 -*-
"""建立 C++ decide 的删改前基线（用于重构后逐字段对拍）。

输出：
  state/audit/cpp_baseline_decisions.json  400 个真实盘面 × 自动/帮助两种模式的完整输出
  state/audit/cpp_baseline_exports.json    当前 pyd 的导出面快照

对拍要求：重构后 decide 的这些字段必须与基线**逐字段完全一致**：
  x, y, confidence, reason, path, prob(逐格), tied(集合), situations, dstar,
  world_upper, limitation, transitions(帮助模式)
"""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np

import utils.native as native


def _norm_probs(prob):
    """prob 可能是 dict 或 list，统一成按坐标排序的 [[x,y,p], ...]。"""
    if not prob:
        return []
    items = []
    if isinstance(prob, dict):
        for k, v in prob.items():
            items.append([int(k[0]), int(k[1]), round(float(v), 10)])
    else:
        # list of (coord, p) 或纯 list
        for e in prob:
            if isinstance(e, (list, tuple)) and len(e) == 2 and hasattr(e[0], "__len__"):
                items.append([int(e[0][0]), int(e[0][1]), round(float(e[1]), 10)])
            elif isinstance(e, (list, tuple)) and len(e) == 3:
                items.append([int(e[0]), int(e[1]), round(float(e[2]), 10)])
    return sorted(items)


def _norm_tied(tied):
    if not tied:
        return []
    out = []
    for c in tied:
        try:
            out.append([int(c[0]), int(c[1])])
        except (TypeError, IndexError, ValueError):
            continue
    return sorted(out)


def _norm_transitions(trans):
    """帮助模式转移：按 outcome key 排序，逐分支记录 board 摘要与概率。"""
    if not trans:
        return {}
    out = {}
    items = trans.items() if isinstance(trans, dict) else enumerate(trans)
    for k, v in items:
        key = str(k)
        if isinstance(v, dict):
            board = v.get("board")
            bsum = None
            if isinstance(board, list):
                bsum = [[int(x) if isinstance(x, (int, float)) else 9
                         for x in row] for row in board]
            out[key] = {
                "p": round(float(v.get("p") or v.get("prob") or 0.0), 10),
                "board": bsum,
            }
        else:
            out[key] = {"raw": str(v)[:80]}
    return dict(sorted(out.items()))


def snapshot(r):
    if r is None:
        return {"available": False}
    return {
        "available": True,
        "x": r.get("x"), "y": r.get("y"),
        "confidence": round(float(r.get("confidence") or 0.0), 10),
        "reason": r.get("reason"),
        "path": r.get("path"),
        "situations": r.get("situations"),
        "dstar": r.get("dstar"),
        "world_upper": r.get("world_upper"),
        "limitation": round(float(r.get("limitation") or 0.0), 8),
        "prob": _norm_probs(r.get("prob")),
        "tied": _norm_tied(r.get("tied")),
        "weighted": _norm_tied(r.get("weighted")),
        "transitions": _norm_transitions(r.get("transitions")),
    }


def main():
    cases = json.loads(
        (ROOT / "bench/data/midgame_cases.json").read_text(encoding="utf-8"))["cases"]
    print("cases:", len(cases))

    # 导出面快照
    exports = sorted([x for x in dir(native.mscore) if not x.startswith("__")])
    (ROOT / "state/audit").mkdir(parents=True, exist_ok=True)
    (ROOT / "state/audit/cpp_baseline_exports.json").write_text(
        json.dumps({"exports": exports}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("exports:", exports)

    out = []
    for i, c in enumerate(cases):
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        a = c["a"]
        auto = snapshot(native.decide(cv, a, 30, 16, is_play=True))
        # 帮助模式（DSTAR x3，返回 transitions）
        help_ = snapshot(native.decide(cv, a, 30, 16, is_play=False))
        out.append({"idx": i, "a": a, "auto": auto, "help": help_})
        if (i + 1) % 100 == 0:
            print("  done", i + 1)

    dst = ROOT / "state/audit/cpp_baseline_decisions.json"
    dst.write_text(json.dumps({"cases": out}, ensure_ascii=False, indent=0),
                   encoding="utf-8")
    print("wrote", dst, dst.stat().st_size // 1024, "KB")
    # 摘要
    from collections import Counter
    print("auto reasons:", dict(Counter(x["auto"].get("reason") for x in out)))
    print("help reasons:", dict(Counter(x["help"].get("reason") for x in out)))


if __name__ == "__main__":
    main()
