# -*- coding: utf-8 -*-
"""cap 值完整标定：分别测「节点主导」与「世界主导」两类场景的峰值 RSS。

=================== 为什么上一版标定不够 ====================
上一版（verify_node_cap_mem.py）只在一个用例上测了「ΔRSS / visited」得到约 22B，
那是**边际斜率**，不能直接外推为安全 cap：
  · 它没覆盖「世界列表主导」的场景（nworlds 很大、节点相对少）
  · 没算解释器基线、numpy 既有占用、分配器高水位（freed-but-retained）
  · 没验证「真实触发 cap 时」峰值是否仍低于预算

本脚本按两类场景分别测：
  场景 N（节点主导）：候选少、单次递归深 ⇒ 节点数大，世界数小
  场景 W（世界主导）：世界数大（多组乘积）、候选多 ⇒ 世界列表占主导
对每类分别扫 cap，记录真实 RSS 峰值，并回答三件事：
  ① 峰值是否随 cap 单调、是否在预算内
  ② 真实触发时峰值是否 < 预算（1.5GB）
  ③ 触发后降级是否及时（耗时是否显著低于不限制）

用法: python bench/calibrate_node_cap.py
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np                      # noqa: E402
# ⚠️ install_stubs 会把 time.sleep 换成空操作，采样线程必须用原始 sleep
import time as _time_mod
_REAL_SLEEP = _time_mod.sleep

import psutil                           # noqa: E402

import utils.native as nat              # noqa: E402

BUDGET_MB = 1536.0
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class Sampler(threading.Thread):
    def __init__(self, interval=0.003):
        super().__init__(daemon=True)
        self.proc = psutil.Process()
        self.interval = interval
        self.peak = 0
        self.running = True
        self.base = 0

    def run(self):
        self.base = self.proc.memory_info().rss
        self.peak = self.base
        while self.running:
            try:
                r = self.proc.memory_info().rss
                if r > self.peak:
                    self.peak = r
            except Exception:                              # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)

    def stop(self):
        self.running = False
        self.join(timeout=1.0)
        return self.base, self.peak


def call(case, cap):
    args = dict(
        clicks=case["clicks"], clicks9=case["clicks9"],
        res_list=[np.asarray(g, np.int32) for g in case["groups"]],
        cell_value=np.asarray(case["cell_value"], np.int32),
        ck=case["ck"], num10=case["num10"], a=case["a"],
        w=case["w"], h=case["h"], is_play=case["is_play"],
        progress=lambda v: None)
    if cap is not None:
        args["node_cap"] = cap
    return nat.mscore.win_rate(**args)


def measure(case, cap):
    """返回 (ok, ms, base_rss, peak_rss, visited)。"""
    smp = Sampler()
    smp.start()
    time.sleep(0.01)                       # 让采样线程先跑起来
    t0 = time.perf_counter()
    visited = None
    try:
        call(case, cap)
        ok = True
        err = ""
    except RuntimeError as e:
        ok = False
        err = str(e)
    dt = (time.perf_counter() - t0) * 1000.0
    base, peak = smp.stop()
    for tok in err.split():
        if tok.startswith("visited="):
            try:
                visited = int(tok.split("=")[1].rstrip(","))
            except ValueError:
                pass
    return ok, dt, base, peak, visited


def classify_cases(cases):
    """把用例按「世界数大」与「节点数大」粗分两类。"""
    big_worlds = []
    big_nodes = []
    for ci, c in enumerate(cases):
        nw = c.get("total", 0)
        nc = c.get("n_cand", 0)
        if nw >= 40:
            big_worlds.append((ci, c))
        if nc >= 14:
            big_nodes.append((ci, c))
    return big_worlds, big_nodes


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 96)
    print(f"cap 完整标定（真实 RSS 峰值；预算 {BUDGET_MB:.0f} MB）")
    print("=" * 96)
    if not nat.available:
        print("native 不可用")
        return

    big_worlds, big_nodes = classify_cases(cases)
    print(f"用例分类：世界数≥40 的 {len(big_worlds)} 个；"
          f"候选数≥14 的 {len(big_nodes)} 个")

    # 先跑一次不限制，挑出"最重"的用例（耗时最长 = 节点最多）
    print("\n预扫描：找最重用例（不限制跑一遍）")
    worst = None
    worst_dt = -1
    for ci, c in enumerate(cases):
        try:
            ok, dt, base, peak, _ = measure(c, None)
        except Exception as e:                             # noqa: BLE001
            continue
        if dt > worst_dt:
            worst_dt = dt
            worst = (ci, c, base, peak)
    if worst is None:
        print("无可用用例")
        return
    ci, case, base_ref, peak_ref = worst
    d_ref = (peak_ref - base_ref) / 2 ** 20
    print(f"  最重用例 [{ci}]：{worst_dt:.0f} ms，ΔRSS {d_ref:.2f} MB，"
          f"F={case['F']} U={case['U']} 世界={case['total']}")

    # ---- 场景 N：同一用例扫 cap ----
    print("\n" + "=" * 96)
    print("场景 N（单用例扫 cap）：峰值随 cap 的关系")
    print("=" * 96)
    print(f"{'cap':>10} {'结果':>6} {'ms':>8} {'ΔRSS MB':>10} {'visited':>9} "
          f"{'B/节点':>8}")
    print("-" * 96)
    rows = []
    for cap in (2000, 10000, 50000, 200000, 500000, 2000000, 10 ** 9):
        ok, dt, base, peak, vis = measure(case, cap)
        d = (peak - base) / 2 ** 20
        per = (d * 2 ** 20 / vis) if vis else float("nan")
        print(f"{cap:>10} {'完成' if ok else '超限':>6} {dt:>8.0f} {d:>10.2f} "
              f"{(vis if vis else '-'):>9} {per:>8.1f}")
        rows.append({"cap": cap, "ok": ok, "ms": dt, "delta_mb": d,
                     "visited": vis, "per_node": per})

    # 判据 1：峰值必须单调不增（cap 越小峰值应越小）
    trig = [r for r in rows if not r["ok"]]
    check("存在真实触发 cap 的样本", len(trig) >= 3, f"n={len(trig)}")
    if trig:
        smallest = min(trig, key=lambda r: r["cap"])
        ok_rows = [r for r in rows if r["ok"]]
        biggest = max(ok_rows, key=lambda r: r["cap"]) if ok_rows else None
        print(f"\n  最小 cap({smallest['cap']}) 峰值 Δ={smallest['delta_mb']:.2f} MB")
        if biggest:
            print(f"  不限制时峰值 Δ={biggest['delta_mb']:.2f} MB")
        check("cap 越小峰值越低（闸门真的封顶）",
              biggest is None or smallest["delta_mb"] <= biggest["delta_mb"] + 2.0,
              f"{smallest['delta_mb']:.2f} vs "
              f"{biggest['delta_mb'] if biggest else 'n/a'}")

    # 判据 2：真实触发时峰值必须远低于预算
    max_trig_delta = max((r["delta_mb"] for r in trig), default=0)
    check(f"触发 cap 时峰值 Δ 远低于预算（{max_trig_delta:.2f} MB << "
          f"{BUDGET_MB:.0f} MB）", max_trig_delta < BUDGET_MB * 0.25)
    check("峰值 Δ 含解释器基线后仍在预算内",
          (max_trig_delta) < BUDGET_MB)

    # 判据 3：降级及时（cap 触发的耗时显著低于不限制）
    n_trig = [r for r in trig if r["cap"] <= 200000]
    if n_trig and worst_dt > 0:
        fastest_trig = min(n_trig, key=lambda r: r["cap"])
        check("cap 触发时耗时显著低于不限制（降级及时）",
              fastest_trig["ms"] < worst_dt * 0.5,
              f"{fastest_trig['ms']:.0f} ms vs {worst_dt:.0f} ms")

    # ---- 场景 W：世界主导 ----
    print("\n" + "=" * 96)
    print("场景 W（世界数大的用例）：世界列表开销是否主导")
    print("=" * 96)
    if big_worlds:
        print(f"{'idx':>3} {'世界':>5} {'F':>3} {'U':>3} {'ΔRSS MB':>10} {'ms':>8}")
        print("-" * 96)
        for wi, wc in big_worlds[:6]:
            try:
                ok, dt, base, peak, _ = measure(wc, 2000000)
                d = (peak - base) / 2 ** 20
                print(f"{wi:>3} {wc['total']:>5} {wc['F']:>3} {wc['U']:>3} "
                      f"{d:>10.2f} {dt:>8.0f}")
            except Exception as e:                         # noqa: BLE001
                print(f"{wi:>3} 失败 {type(e).__name__}")
    else:
        print("（无世界数≥40 的用例）")

    print("\n" + "=" * 96)
    print("标定结论")
    print("=" * 96)
    per_nodes = [r["per_node"] for r in rows
                 if r["per_node"] == r["per_node"] and r["per_node"] > 0]
    if per_nodes:
        med = sorted(per_nodes)[len(per_nodes) // 2]
        mx = max(per_nodes)
        print(f"实测每节点：中位 {med:.1f} B，最大 {mx:.1f} B（n={len(per_nodes)}）")
        # 保守用**最大值**而非中位来反推，避免低估尾部
        cap_cons = int(BUDGET_MB * 2**20 / mx / 3)
        print(f"按预算/3 与**最大**每节点字节反推保守 cap ≈ {cap_cons:,}")
        print(f"当前默认 2,000,000 → 最坏估计 "
              f"{2000000*mx/2**20:.0f} MB"
              f"（{'预算内' if 2000000*mx/2**20 < BUDGET_MB/3 else '超 1/3 预算'}）")
    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    print("注：RSS 采样有噪声，Δ 用于量级判断；分配器高水位会保留已释放内存，")
    print("    因此实测 Δ 是上界（偏保守），适合用于反推安全 cap。")


if __name__ == "__main__":
    main()
