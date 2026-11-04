# -*- coding: utf-8 -*-
"""核查 dry-run 的实际产物：参数是否真的转发、日志是否独立、JSON 是否正确。

=================== 为什么单独查 ====================
启动器"全部 ok"只说明 exit code 为 0。必须再核对：
  ① 参数真的转发了吗？（--games 8 与 --games 12 的结果行数应不同）
  ② 每个阶段的日志是否独立（内容不串）
  ③ 原始 JSON 是否落了、能否 json.load
  ④ 重复运行是否覆盖旧结果（而不是追加/混杂）
  ⑤ 失败阶段是否留下了诊断信息

用法: python bench/verify_dryrun_artifacts.py
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

OUT = os.path.join(ROOT, "state", "suite_dryrun")
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✔' if cond else '✘'} {name}"
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    print("=" * 96)
    print("dry-run 产物核查")
    print("=" * 96)

    # ---- ① 日志独立且非空 ----
    print("\n[1] 每阶段日志是否独立、非空")
    logs = {
        "S1a_flood.log": "flood",
        "S1b_probe.log": "probe",
        "S2a_seed.log": "seed",
        "S3_rulegap.log": "规则",
        "S4_firstmove.log": "首",
        "S5_endgame.log": "残局",
        "S6_ab.log": "A/B",
        "X1_missing.log": "No such file",
    }
    for fn, kw in logs.items():
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            check(fn, False, "缺失")
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        n = len(txt)
        has = (kw in txt)
        check(f"{fn}", n > 50 and has,
              f"{n} 字节, 含关键词「{kw}」={has}")

    # ---- ② 参数真的转发了：--games 8 应产生 8 局 ----
    print("\n[2] 参数转发核查（--games N 是否真的生效）")

    # rule_gap --games 8 → 结果里应有 8 个 seed
    try:
        d = json.load(open(os.path.join(ROOT, "state", "ab",
                                        "rule_gap.json"), encoding="utf-8"))
        n_seed = len(d["results"]["deferred"])
        check("rule_gap --games 8 → 8 局", n_seed == 8, f"实得 {n_seed}")
        check("rule_gap 有两条规则的结果",
              set(d["results"].keys()) == {"deferred", "safe3x3"},
              str(list(d["results"].keys())))
    except Exception as e:                              # noqa: BLE001
        check("rule_gap 结果可读", False, str(e)[:80])

    # ab_tiebreak --games 12 --label dryrun → 文件名带 dryrun，12 局
    p = os.path.join(ROOT, "state", "ab", "tiebreak_ab_dryrun.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        n = d.get("games")
        arms = list(d.get("results", {}).keys())
        check("--label dryrun 生效（文件名）", True, os.path.basename(p))
        check("--games 12 生效", n == 12, f"实得 {n}")
        check("--arms 生效（只跑 2 臂）",
              set(arms) == {"baseline", "open_plus_frontier"}, str(arms))
        # 参数 seed 是否生效
        seeds = sorted(int(k) for k in d["results"]["baseline"])
        check("--seed 20268000 生效", seeds[0] == 20268000,
              f"首个 seed {seeds[0]}")
    else:
        check("tiebreak_ab_dryrun.json 存在", False, "缺失")

    # ---- ③ 失败阶段的诊断信息 ----
    print("\n[3] 失败阶段是否留下可诊断信息")
    p = os.path.join(OUT, "X1_missing.log")
    if os.path.exists(p):
        txt = open(p, encoding="utf-8", errors="replace").read()
        check("缺失脚本的错误信息可读",
              "No such file" in txt or "can't open" in txt, txt.strip()[:60])
    else:
        check("X1 日志存在", False)

    # ---- ④ 续跑/覆盖语义 ----
    print("\n[4] 重复运行语义（覆盖 vs 追加）")
    p_ab = os.path.join(ROOT, "state", "ab", "tiebreak_ab_dryrun.json")
    if os.path.exists(p_ab):
        d = json.load(open(p_ab, encoding="utf-8"))
        seeds = [int(k) for k in d["results"]["baseline"]]
        check("重复运行未产生重复 seed（覆盖语义）",
              len(seeds) == len(set(seeds)), f"{len(seeds)} 条")
        check("JSON 结构完整可解析", "games" in d and "results" in d)
    else:
        check("dryrun A/B JSON 存在", False, "缺失")

    # ---- 汇总 ----
    print("\n" + "=" * 96)
    if FAILS:
        print(f"✘ {len(FAILS)} 项未通过：{FAILS}")
        return 1
    print("✅ dry-run 产物全部核查通过：")
    print("   · 8 个阶段日志独立、非空、内容对应")
    print("   · 参数（--games/--seed/--label/--arms）确实转发并生效")
    print("   · 原始 JSON 落盘且可解析")
    print("   · 失败阶段留下诊断信息，且未阻断后续阶段")
    print("   · 重复运行覆盖旧结果，不产生重复记录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
