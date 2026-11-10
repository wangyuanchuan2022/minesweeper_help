# -*- coding: utf-8 -*-
"""端到端探针分析器：读 state/e2e/probe.jsonl，出「性能 / 准确性 / 功能 / 内存」四份账。

==================== 输入 ====================
`utils/e2e_probe.py` 落盘的 JSONL（MSW_E2E_PROBE=1 时产生），事件类型：
    game_begin / game_end / span / decision / click / mem / mark / error

==================== 输出四份账 ====================
① **性能**：每个 span 的 n / 中位 / p90 / p99 / 合计 / 占整局比；
   按「整局墙钟」归一，指出最大热点。
② **准确性**：decide 的 reason 分布、耗时分布、world_upper/situations 分布、
   以及**概率守恒**（Σ P(雷) 是否等于剩余雷数，用 click 事件的 conf 与
   game_end 的真实胜负交叉验证置信度校准）。
③ **功能**：mark/error 计数，覆盖「哪些功能真的被调用到了」——
   这是回答「功能是否实现、有没有死代码」的**运行时**证据（静态审计见
   bench/audit_dead_code.py）。
④ **内存**：mem 事件序列，按局分段的 commit 增量趋势（区分「锯齿=系统回收」
   与「单调增长=真泄漏」）。

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/analyze_e2e.py
    ... --jsonl state/e2e/probe.jsonl --md state/e2e/report.md
"""
import argparse
import collections
import io
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load(path):
    rows = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def q(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def fmt_ms(v):
    return f"{v:.2f}"


def select_main_run(rows, gap_s=60.0):
    """切出**本轮主跑数据**，返回 (rows, wall_s, n_segments)。

    ⚠️ 253 局实测踩到的两个仪器坑，这里一并修掉：

    ① **跨轮残留**：probe.jsonl 是追加写，可能含上一轮冒烟/失败运行的残留段
       （实测 141214 事件里前 1221 条属于 18:51 的旧运行，与 19:19 的主跑
        之间有 1106s 空档）。不过滤会把两段混在一起算。
       做法：按 >gap_s 的时间空档切段，取 **game_end 最多** 的那一段。

    ② **wall_ms 口径错**：game_begin 在 while 顶部、game_end 在循环中部的
       胜负分支，且该分支不 continue ⇒ wall_ms 只覆盖「上一局最后一轮决策」，
       实测中位 0.46s，而真实单局周期是 9.16s/局。
       做法：真实墙钟 = 该段 **首个 game_begin → 最后一个 game_end** 的 ts 跨度。
    """
    with_ts = [r for r in rows if isinstance(r.get("ts"), (int, float))]
    if not with_ts:
        return rows, 0.0, 0
    ts = sorted(r["ts"] for r in with_ts)
    # 切段边界
    bounds = [ts[0]]
    for i in range(len(ts) - 1):
        if ts[i + 1] - ts[i] > gap_s:
            bounds.append(ts[i + 1])
    bounds.append(ts[-1] + 1.0)
    segs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        sub = [r for r in rows
               if isinstance(r.get("ts"), (int, float)) and a <= r["ts"] < b]
        ge = sum(1 for r in sub if r.get("type") == "game_end")
        segs.append((ge, a, b, sub))
    if not segs:
        return rows, 0.0, 0
    segs.sort(key=lambda s: -s[0])
    ge, a, b, sub = segs[0]
    wall_s = 0.0
    if ge:
        bgs = [r["ts"] for r in sub if r.get("type") == "game_begin"]
        eds = [r["ts"] for r in sub if r.get("type") == "game_end"]
        if bgs and eds:
            wall_s = max(eds) - min(bgs)
    return sub, wall_s, len(segs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=os.path.join(ROOT, "state", "e2e", "probe.jsonl"))
    ap.add_argument("--md", default=os.path.join(ROOT, "state", "e2e", "report.md"))
    ap.add_argument("--json", default=os.path.join(ROOT, "state", "e2e", "report.json"))
    args = ap.parse_args()

    if not os.path.exists(args.jsonl):
        print(f"⚠️ 找不到探针文件：{args.jsonl}")
        print("   先以 MSW_E2E_PROBE=1 启动 main.py 跑几局，或用 run_e2e_900.cmd")
        return 0

    all_rows = load(args.jsonl)
    if not all_rows:
        print(f"⚠️ 探针文件为空：{args.jsonl}")
        return 0
    rows, wall_s, n_segments = select_main_run(all_rows)
    if n_segments > 1:
        print(f"⚠️ 检测到 {n_segments} 个时间不连续的段（跨轮残留），"
              f"已自动只取主跑段：{len(all_rows)} → {len(rows)} 事件")

    by_type = collections.Counter(r.get("type") for r in rows)
    print("=" * 100)
    print(f"端到端探针分析：{args.jsonl}")
    print("=" * 100)
    print(f"事件总数 {len(rows)}：" + "  ".join(
        f"{k}={v}" for k, v in sorted(by_type.items())))

    # ------------------------------------------------------------------
    # ① 性能
    # ------------------------------------------------------------------
    spans = collections.defaultdict(list)
    for r in rows:
        if r.get("type") == "span":
            spans[r.get("name") or "?"].append(float(r.get("ms") or 0.0))

    game_ends = [r for r in rows if r.get("type") == "game_end"]
    game_walls = [float(g.get("wall_ms") or 0.0) for g in game_ends]
    n_games = len(game_ends)
    # 真实墙钟 = 主跑段首个 game_begin → 最后 game_end 的 ts 跨度
    total_wall_s = wall_s
    if total_wall_s <= 0 and game_ends:
        total_wall_s = sum(game_walls) / 1000.0

    print("\n" + "-" * 100)
    print("① 性能：span 耗时分解")
    print("-" * 100)
    if not spans:
        print("  无 span 事件")
    else:
        hdr = (f"  {'span':<28}{'层':<6}{'n':>7}{'中位ms':>10}{'p90ms':>10}"
               f"{'p99ms':>10}{'合计s':>10}{'占墙钟':>8}")
        print(hdr)
        tot_all = 0.0
        stat_rows = []
        for name, vals in sorted(spans.items(), key=lambda kv: -sum(kv[1])):
            s = sum(vals) / 1000.0
            tot_all += s
            layer = "外层" if name.startswith("play.") else "内层"
            pct = (round(s / total_wall_s * 100, 1)
                   if total_wall_s else None)
            stat_rows.append({
                "name": name, "layer": layer, "n": len(vals),
                "median_ms": round(statistics.median(vals), 3),
                "p90_ms": round(q(vals, 0.9), 3),
                "p99_ms": round(q(vals, 0.99), 3),
                "sum_s": round(s, 2),
                "pct_of_wall": pct,
            })
            print(f"  {name:<28}{layer:<6}{len(vals):>7}{statistics.median(vals):>10.2f}"
                  f"{q(vals, 0.9):>10.2f}{q(vals, 0.99):>10.2f}{s:>10.2f}"
                  f"{(pct if pct is not None else 0):>7.1f}%")
        print(f"  {'（span 合计）':<28}{'':>7}{'':>10}{'':>10}{'':>10}"
              f"{tot_all:>10.2f}")
        # ⚠️ 嵌套 span 不能相加：外层 span（play.number5_1 等）**包含**内层
        #   （click / grab_board / ui.render_grid / decision），两层相加会 >100%。
        #   实测 253 局数据里外层合计 61.8% + 内层合计 62.9% = 124.7%，
        #   就是重复计的假象。真实归因必须只取一层，或按 game_end→game_end
        #   周期对事件索引切片（见 bench/e2e_900.py 的周期分析口径）。
        nested = [n for n in spans if n.startswith("play.")]
        if nested:
            print(f"\n  ⚠️ 嵌套说明：{', '.join(nested)} 是外层 span，其耗时**包含**")
            print(f"     内层（click.* / vision.* / ui.* / decision）。上面的「span 合计」")
            print(f"     是两层相加的重复计数，不能当作占整局比例使用。")
            print(f"     外层单独合计 {sum(sum(spans[n]) for n in nested) / 1000:.1f}s；")
            print(f"     内层单独合计 {(tot_all - sum(sum(spans[n]) for n in nested) / 1000):.1f}s。")
        print(f"\n  真实墙钟 {total_wall_s:.1f}s / {n_games} 局"
              f"（均值 {total_wall_s / max(1, n_games):.2f}s/局）")
        # ⚠️ game_end.wall_ms 的口径坑（253 局实测发现）：
        #   game_begin 在 while 顶部、game_end 在循环中部的胜负分支，且该分支
        #   **不 continue**（判完胜负+点开新局后，同一轮继续往下做一次完整决策）。
        #   ⇒ wall_ms 只覆盖「上一局最后一轮决策」，实测中位 0.46s，而真实
        #     game_end→game_end 周期是 9.16s/局。真实墙钟已改用 ts 跨度。
        if game_walls and total_wall_s:
            print(f"  ⚠️ game_end.wall_ms 中位 {statistics.median(game_walls) / 1000:.2f}s/局，"
                  f"仅作诊断（不是单局时长）；真实周期 "
                  f"{total_wall_s / max(1, n_games):.2f}s/局。")

    # ------------------------------------------------------------------
    # ② 准确性
    # ------------------------------------------------------------------
    decs = [r for r in rows if r.get("type") == "decision"]
    clicks = [r for r in rows if r.get("type") == "click"]
    print("\n" + "-" * 100)
    print("② 准确性：决策与点击")
    print("-" * 100)
    if decs:
        reasons = collections.Counter(str(d.get("reason")) for d in decs)
        ms = [float(d.get("ms") or 0.0) for d in decs]
        print(f"  decide 次数 {len(decs)}（每局 {len(decs) / max(1, n_games):.1f}）")
        print(f"  耗时 中位 {statistics.median(ms):.2f}ms  p90 {q(ms, 0.9):.1f}ms  "
              f"p99 {q(ms, 0.99):.1f}ms  max {max(ms):.1f}ms")
        print(f"  合计 {sum(ms) / 1000:.1f}s"
              + (f"（占整局墙钟 {sum(ms) / 1000 / total_wall_s * 100:.1f}%）"
                 if total_wall_s else ""))
        print("\n  reason 分布：")
        for k, v in reasons.most_common():
            print(f"    {k:<28}{v:>7}  {v / len(decs) * 100:>6.2f}%")
        # world/situations 一致性（恒等式 A 的运行时校验）
        wc_checked = wc_bad = 0
        for d in decs:
            wu = d.get("world_upper")
            sit = d.get("situations")
            if wu is None or sit is None:
                continue
            wc_checked += 1
        # ⚠️ situations 的三种取值语义（253 局实测，勿混）：
        #   -1  = 未计数（粗筛直接拒绝，g_last_situations 保持 -1）
        #   DSTAR+1 = **超限哨兵**（cap 早退返回 cap+1），不是真实 ‖S‖
        #   0..DSTAR = 真实精确 ‖S‖
        # 首版把 257 当真实值，得出「‖S‖ 中位 257」的假结论；
        # 实测 6164 次决策里 3813 次是 257 哨兵（61.9% 被拒），
        # 只有 1664 次 optimal 决策的 situations 才是真实可比值。
        dstar_vals = [int(d["dstar"]) for d in decs
                      if isinstance(d.get("dstar"), int) and d["dstar"] > 0]
        dstar = dstar_vals[0] if dstar_vals else None
        sentinel = -1
        capped = 0
        real_sits = []
        for d in decs:
            s = d.get("situations")
            if not isinstance(s, int):
                continue
            if s == sentinel:
                continue
            if dstar is not None and s == dstar + 1:
                capped += 1
                continue
            if 0 <= s <= (dstar or s):
                real_sits.append(s)
        print(f"\n  situations 语义拆分（DSTAR={dstar}）：")
        print(f"    有值 {wc_checked}/{len(decs)}；-1 未计数 "
              f"{sum(1 for d in decs if d.get('situations') == -1)}；"
              f"超限哨兵(={dstar + 1 if dstar else '?'}) {capped} "
              f"({capped / max(1, len(decs)) * 100:.1f}%)；"
              f"真实 ‖S‖ {len(real_sits)}")
        if real_sits:
            print(f"    真实 ‖S‖：中位 {statistics.median(real_sits):.0f}  "
                  f"p90 {q(real_sits, 0.9):.0f}  max {max(real_sits)}")
            # 恒等式 A：optimal 决策的 situations 必须 <= DSTAR
            opt_sits = [d.get("situations") for d in decs
                        if d.get("reason") == "optimal"
                        and isinstance(d.get("situations"), int)
                        and 0 <= d["situations"] <= (dstar or d["situations"])]
            if opt_sits and dstar:
                bad = sum(1 for s in opt_sits if s > dstar)
                print(f"    OPTIMAL 决策 ‖S‖ 全部 ≤ DSTAR："
                      f"{'✅' if bad == 0 else f'❌ {bad} 次越界'}"
                      f"（{len(opt_sits)} 次检查，max {max(opt_sits)}）")
    else:
        print("  无 decision 事件")

    if clicks:
        confs = [c.get("conf") for c in clicks if isinstance(c.get("conf"), (int, float))]
        print(f"\n  点击事件 {len(clicks)}（每局 {len(clicks) / max(1, n_games):.1f}）")
        if confs:
            guess = [c for c in confs if c < 0.999]
            print(f"    自评安全率：中位 {statistics.median(confs):.4f}  "
                  f"确定(=1) {len(confs) - len(guess)}/{len(confs)}"
                  f"（{(len(confs) - len(guess)) / len(confs) * 100:.1f}%）")
            if guess:
                print(f"    非确定点击（猜测）{len(guess)} 次，"
                      f"中位 conf {statistics.median(guess):.4f}")
        srcs = collections.Counter(str(c.get("source")) for c in clicks)
        print(f"    来源：{dict(srcs)}")

    # ------------------------------------------------------------------
    # ③ 功能覆盖（运行时证据）
    # ------------------------------------------------------------------
    marks = [r for r in rows if r.get("type") == "mark"]
    errs = [r for r in rows if r.get("type") == "error"]
    print("\n" + "-" * 100)
    print("③ 功能覆盖：运行时打点（回答「哪些功能真的被调用到」）")
    print("-" * 100)
    if marks:
        mcnt = collections.Counter(str(m.get("name")) for m in marks)
        for k, v in mcnt.most_common():
            print(f"  {k:<34}{v:>8}")
    else:
        print("  无 mark 事件")
    if errs:
        print("\n  错误/告警事件：")
        ecnt = collections.Counter(str(e.get("category")) for e in errs)
        for k, v in ecnt.most_common():
            print(f"  {k:<34}{v:>8}")
            sample = next((e for e in errs if str(e.get("category")) == k), None)
            if sample:
                print(f"      例：{str(sample.get('text'))[:100]}")
    else:
        print("\n  无 error 事件（无决策失败/线程异常）")

    # ------------------------------------------------------------------
    # ④ 内存
    # ------------------------------------------------------------------
    mems = [r for r in rows if r.get("type") == "mem"]
    print("\n" + "-" * 100)
    print("④ 内存：commit 序列趋势")
    print("-" * 100)
    mem_stat = {}
    if mems:
        commits = [float(m.get("commit_mb") or 0.0) for m in mems]
        rss = [float(m.get("rss_mb") or 0.0) for m in mems]
        print(f"  采样 {len(mems)} 点")
        print(f"  commit: 首 {commits[0]:.0f}MB  末 {commits[-1]:.0f}MB  "
              f"峰 {max(commits):.0f}MB  谷 {min(commits):.0f}MB  "
              f"净增 {commits[-1] - commits[0]:+.0f}MB")
        print(f"  rss   : 首 {rss[0]:.0f}MB  末 {rss[-1]:.0f}MB  "
              f"峰 {max(rss):.0f}MB")
        # ⚠️ 内存泄漏判定不能只看「分段段末是否单调」——首版据此报了
        #   「疑似真泄漏」，但 253 局实测：逐局峰值中位 880→919MB（+39MB），
        #   中间段5 931MB → 段6 906MB **回落**，且 13.1% 采样点相邻下降 >1MB。
        #   ⇒ 这是 Windows 分配器高水位 / 懒释放，不是单调泄漏。
        #   判据改为三条同时成立才报泄漏：
        #     ① 逐局峰值分 10 段，段中位单调不降且总增幅 > 200MB
        #     ② 相邻下降 >1MB 的采样点占比 < 3%
        #     ③ 末值 - 首值 > 200MB
        n = len(commits)
        drops = sum(1 for i in range(1, n) if commits[i] < commits[i - 1] - 1)
        drop_pct = drops / max(1, n - 1) * 100.0
        seg = max(1, n // 5)
        seg_ends = [commits[min(n - 1, (i + 1) * seg - 1)] for i in range(5)]
        print(f"  分 5 段的段末 commit："
              + "  ".join(f"{v:.0f}" for v in seg_ends))
        # 逐局峰值分段（更抗噪）
        ends = [r for r in rows if r.get("type") == "game_end"]
        peaks = [float(e.get("peak_mem_mb") or 0) for e in ends]
        peak_seg = []
        if len(peaks) >= 10:
            k = len(peaks) // 10
            peak_seg = [round(statistics.median(peaks[i * k:(i + 1) * k]), 1)
                        for i in range(10)]
            print(f"  逐局峰值分 10 段中位："
                  + "  ".join(f"{v:.0f}" for v in peak_seg))
        peak_mono = (len(peak_seg) >= 2
                     and all(peak_seg[i] <= peak_seg[i + 1] for i in range(len(peak_seg) - 1)))
        peak_rise = (peak_seg[-1] - peak_seg[0]) if peak_seg else 0.0
        net_rise = commits[-1] - commits[0]
        leak = bool(peak_mono and peak_rise > 200 and drop_pct < 3.0
                    and net_rise > 200)
        print(f"  相邻下降 >1MB：{drops}/{n - 1} 次 ({drop_pct:.1f}%)  "
              f"逐局峰值段差 {peak_rise:+.0f}MB  净增 {net_rise:+.0f}MB")
        print(f"  形态判定：{'❌ 疑似真泄漏（三条判据同时成立）' if leak else '✅ 非单调泄漏（Windows 分配器高水位/懒释放，或有回落）'}")
        mem_stat = {
            "samples": len(mems), "first_mb": round(commits[0], 1),
            "last_mb": round(commits[-1], 1), "peak_mb": round(max(commits), 1),
            "min_mb": round(min(commits), 1),
            "net_mb": round(net_rise, 1),
            "segment_ends": [round(v, 1) for v in seg_ends],
            "peak_segment_medians": peak_seg,
            "peak_rise_mb": round(peak_rise, 1),
            "drop_pct": round(drop_pct, 2),
            "monotonic_growth": leak,
        }
    else:
        print("  无 mem 事件（MSW_E2E_MEM_HZ=0 或未启动采样器）")

    # ------------------------------------------------------------------
    # 总结
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("总结")
    print("=" * 100)
    print(f"  局数 {n_games}  决策 {len(decs)}  点击 {len(clicks)}  "
          f"真实墙钟 {total_wall_s:.1f}s"
          + (f"（{total_wall_s / n_games:.2f}s/局）" if n_games else ""))
    if game_ends:
        wins = sum(1 for g in game_ends if g.get("won"))
        print(f"  胜局 {wins}/{n_games} = {wins / n_games * 100:.2f}%")
    if spans:
        top = max(spans.items(), key=lambda kv: sum(kv[1]))
        print(f"  最大耗时 span：{top[0]}（合计 {sum(top[1]) / 1000:.1f}s）")

    os.makedirs(os.path.dirname(args.md), exist_ok=True)
    payload = {
        "jsonl": args.jsonl, "events": len(rows),
        "by_type": dict(by_type), "games": n_games,
        "total_wall_s": round(total_wall_s, 1),
        "spans": stat_rows,
        "reasons": dict(collections.Counter(
            str(d.get("reason")) for d in decs)),
        "memory": mem_stat,
        "marks": dict(collections.Counter(str(m.get("name")) for m in marks)),
        "errors": dict(collections.Counter(str(e.get("category")) for e in errs)),
    }
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    lines = ["# 端到端测量报告", "",
             f"- 数据源：`{args.jsonl}`",
             f"- 事件 {len(rows)}，局数 {n_games}，真实墙钟 {total_wall_s:.1f}s "
             f"（{total_wall_s / max(1, n_games):.2f}s/局）",
             ""]
    if spans:
        lines += ["## ① 性能（span 分解）", "",
                  "| span | 层 | n | 中位 ms | p90 ms | p99 ms | 合计 s | 占墙钟 |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in stat_rows:
            pct = "—" if r["pct_of_wall"] is None else f"{r['pct_of_wall']}%"
            lines.append(f"| `{r['name']}` | {r.get('layer', '')} | {r['n']} | "
                         f"{r['median_ms']} | {r['p90_ms']} | {r['p99_ms']} | "
                         f"{r['sum_s']} | {pct} |")
        lines += ["", "> ⚠️ 外层 span 与内层 span **不可相加**（外层包含内层）；",
                  "> 真实墙钟按主跑段 game_begin→game_end 的 ts 跨度计算。"]
        lines.append("")
    lines += ["## ② 准确性", ""]
    if decs:
        lines += ["| reason | 次数 | 占比 |", "|---|---|---|"]
        for k, v in collections.Counter(str(d.get("reason")) for d in decs).most_common():
            lines.append(f"| `{k}` | {v} | {v / len(decs) * 100:.2f}% |")
        lines.append("")
    if mem_stat:
        lines += ["## ④ 内存", "",
                  f"- 采样 {mem_stat['samples']} 点，commit 首 {mem_stat['first_mb']}MB "
                  f"→ 末 {mem_stat['last_mb']}MB（净 {mem_stat['net_mb']:+.0f}MB），"
                  f"峰 {mem_stat['peak_mb']}MB",
                  f"- 分段段末：{mem_stat['segment_ends']}",
                  f"- 单调增长：{mem_stat['monotonic_growth']}",
                  ""]
    lines += ["## ③ 功能覆盖（运行时打点）", ""]
    for k, v in collections.Counter(str(m.get("name")) for m in marks).most_common():
        lines.append(f"- `{k}` × {v}")
    lines.append("")
    if errs:
        lines += ["## 错误事件", ""]
        for k, v in collections.Counter(str(e.get("category")) for e in errs).most_common():
            lines.append(f"- `{k}` × {v}")
        lines.append("")
    with io.open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n落盘：{args.json}")
    print(f"落盘：{args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
