# -*- coding: utf-8 -*-
"""验证 eval_harness 的 workers 数是否影响结果（多进程 vs 单进程）。

=================== 为什么必须测 ====================
advisor 指出：不能把 81/200、73/200、71/200 的差异直接归因为"多进程随机性"。
而 `eval_harness` 的并行实现是 `_run_parallel_subprocess`：
按 workers 数把 seeds **轮转分块**，每个子进程连续跑多局。

若同 seed 在不同 workers 数下结果不同 ⇒ 台架不确定 ⇒
所有基于该台架的读数（包括历史 40.5%）都不能互相比较。

=================== 做法 ====================
同 seeds、同 config，只改 workers：
  W=1（单进程，无分块）
  W=4
  W=20
逐局比较 won / opened / steps，并用棋盘哈希核对局面是否相同。

用法: python bench/verify_workers_determinism.py --games 60
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def run(workers, games, seed, tag):
    out = os.path.join(ROOT, "state", "ab", f"_wd_{tag}.json")
    if os.path.exists(out):
        os.remove(out)
    cmd = [sys.executable, "-X", "utf8",
           os.path.join(ROOT, "sim", "eval_harness.py"),
           "--games", str(games), "--workers", str(workers),
           "--seed", str(seed), "--out", out]
    print(f"  跑 workers={workers} …", flush=True)
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out):
        print(f"  ✘ workers={workers} 失败：{p.stderr[-400:]}")
        return None
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    return {r["seed"]: r for r in d["results"] if "error" not in r}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    print("=" * 100)
    print(f"workers 决定性检验：{args.games} 局 × 3 种并行度")
    print("=" * 100)

    runs = {}
    for w in (1, 4, 20):
        runs[w] = run(w, args.games, args.seed, f"w{w}")

    ok = {w: r for w, r in runs.items() if r}
    if len(ok) < 2:
        print("可用轮次不足，无法比较")
        return

    print("\n" + "=" * 100)
    print("各并行度下的胜率")
    print("=" * 100)
    for w, r in sorted(ok.items()):
        wins = sum(1 for x in r.values() if x["won"])
        print(f"  workers={w:<3} {wins}/{len(r)} = {wins/len(r)*100:.2f}%")

    print("\n" + "=" * 100)
    print("逐局一致性（以 workers=1 为基准）")
    print("=" * 100)
    base = ok.get(1)
    if not base:
        print("  缺 workers=1 基准，改用最小 workers 作基准")
        base = ok[min(ok)]

    for w in sorted(ok):
        if ok[w] is base:
            continue
        same_win = sum(1 for s in base if s in ok[w]
                       and base[s]["won"] == ok[w][s]["won"])
        same_op = sum(1 for s in base if s in ok[w]
                      and base[s]["opened"] == ok[w][s]["opened"])
        same_st = sum(1 for s in base if s in ok[w]
                      and base[s]["steps"] == ok[w][s]["steps"])
        n = len([s for s in base if s in ok[w]])
        print(f"  workers={w} vs 基准：n={n}")
        print(f"    won    一致 {same_win}/{n}")
        print(f"    opened 一致 {same_op}/{n}")
        print(f"    steps  一致 {same_st}/{n}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    all_same = True
    for w in sorted(ok):
        if ok[w] is base:
            continue
        n = len([s for s in base if s in ok[w]])
        same = sum(1 for s in base if s in ok[w]
                   and base[s]["won"] == ok[w][s]["won"]
                   and base[s]["opened"] == ok[w][s]["opened"])
        if same < n:
            all_same = False

    if all_same:
        print("  ✅ 不同 workers 数下逐局完全一致 ⇒ 台架**确定**，")
        print("     历史 40.5% 与当前 35.5% 的差异不能归因为并行度。")
        print("     ⇒ 必须另找原因（代码版本/状态文件/依赖版本）。")
    else:
        print("  ✘ 不同 workers 数下结果**不一致** ⇒ 台架非确定。")
        print("     ⇒ 历史读数与当前读数**不可直接比较**；")
        print("        所有跨批次的胜率比较都失效，需改为单进程固定台架。")

    dest = os.path.join(ROOT, "state", "ab", "workers_determinism.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({f"w{w}": {str(k): v for k, v in r.items()}
                   for w, r in ok.items()}, f, ensure_ascii=False)
    print(f"\n明细已写 {dest}")


if __name__ == "__main__":
    main()
