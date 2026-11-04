# -*- coding: utf-8 -*-
"""计时协议（强制）：所有 C++ 耗时实验必须走这个模块。

=================== 为什么需要协议层 ====================
实测发现（bench/report_repeat_slowdown.py，可复现）：

  同进程内连续调用 win_rate，节点数完全相同，耗时却漂移：
    G6  4,626 节点      → 3,3,3,3 ms           稳定
    G8  78,282 节点     → 46,45,44,45 ms       稳定
    G9  313,940 节点    → 984,1003,999,1020 ms 稳定
    G10 1,241,184 节点  → 853,838,1642,4638 ms **5.44x 漂移**
    G11 4,081,650 节点  → 7898,17658,17506,18657 ms **2.36x 漂移**

  而**新进程**首次调用稳定：4 个独立进程 876ms，极差仅 52ms。

**根因未明**（已排除 GC / 算法路径 / 无差别堆碎片 / 后台负载 / CPU 频率，
未验证 C++ 分配机制）。但对实验的影响是确定的：

  ⇒ **同进程内的耗时对比不可信，不得用于策略排序。**

=================== 本模块提供的协议 ====================
  measure_new_process(fn_spec, repeats=3)
      每个配置在**独立新进程**里跑，返回中位数 / p90 / 极差。
      这是唯一可信的耗时测量方式。

  measure_interleaved(fn_specs, rounds=5)
      多配置**随机交替**在同一进程内跑，取每配置中位。
      仅用于"相对比较且配置数量多、无法全用新进程"的场景，
      且必须报极差；若极差 > 20%，结果作废。

用法（示例见文件末尾 __main__）。
"""
import json
import os
import statistics
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))


def _run_child(code, env_extra=None, timeout=1800):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["MSW_WR_PHASE"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable, "-u", "-c", code],
                          capture_output=True, text=True, env=env,
                          cwd=ROOT, timeout=timeout)
    return proc


def measure_new_process(setup_code, call_code, repeats=3, timeout=1800):
    """每个 repeat 起一个**全新进程**，返回耗时统计。

    setup_code: 在该进程里先执行的准备代码（构造用例等）
    call_code : 实际计时的那一次调用（只调用一次，避免同进程累积）
    """
    times = []
    phases = []
    for i in range(repeats):
        # ⚠️ 不能用 % 格式化：子进程代码里有 "__ELAPSED__%.3f" 会和外层 % 冲突。
        # 用 .format() 且把子进程内的 % 写成 %% 也不行（会与外层混），
        # 因此子进程内改用 f-string 拼接。
        code = (
            "import sys,os,time,json;"
            "sys.path.insert(0,{root!r});sys.path.insert(0,{bench!r});"
            "os.chdir({root!r});"
            "{setup}\n"
            "t0=time.perf_counter();"
            "{call}\n"
            "dt=(time.perf_counter()-t0)*1000.0;"
            "sys.stderr.write('__ELAPSED__'+repr(dt)+'\\n')"
        ).format(root=ROOT, bench=os.path.join(ROOT, "bench"),
                 setup=setup_code, call=call_code)
        proc = _run_child(code, timeout=timeout)
        got = None
        for line in (proc.stderr or "").splitlines():
            if line.startswith("__ELAPSED__"):
                got = float(line[len("__ELAPSED__"):])
            elif "[wr_phase]" in line:
                d = {}
                for tok in line.split("] ", 1)[1].split():
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        d[k] = v
                phases.append(d)
        if got is not None:
            times.append(got)
    if not times:
        return None
    ts = sorted(times)
    return {
        "n": len(ts),
        "times": times,
        "median": statistics.median(ts),
        "p90": ts[min(len(ts) - 1, int(len(ts) * 0.9))],
        "min": ts[0], "max": ts[-1],
        "spread_pct": (ts[-1] - ts[0]) / max(1e-9, ts[0]) * 100.0,
        "phases": phases,
    }


def measure_interleaved(setup_code, configs, rounds=5, timeout=1800):
    """多配置在同一进程内**随机交替**测量（仅用于相对比较）。

    configs: [(name, call_code), ...]
    返回 {name: stats}；若某配置极差 > 20%，其 stats 带 "unreliable": True
    """
    import random
    order = []
    for _ in range(rounds):
        idx = list(range(len(configs)))
        random.shuffle(idx)
        order.extend(idx)
    seq = "[" + ",".join(str(i) for i in order) + "]"
    calls = "[" + ",".join("lambda: (%s)" % c[1] for c in configs) + "]"
    code = (
        "import sys,os,time,json;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');os.chdir(r'%s');"
        "%s\n"
        "fns=%s;order=%s;res=[[] for _ in fns]\n"
        "for i in order:\n"
        "    t0=time.perf_counter();fns[i]();"
        "    res[i].append((time.perf_counter()-t0)*1000.0)\n"
        "sys.stderr.write('__RES__'+json.dumps(res)+'\\n')"
        % (ROOT, os.path.join(ROOT, "bench"), ROOT, setup_code, calls, seq)
    )
    proc = _run_child(code, timeout=timeout)
    raw = None
    for line in (proc.stderr or "").splitlines():
        if line.startswith("__RES__"):
            raw = json.loads(line[len("__RES__"):])
    if raw is None:
        return {}
    out = {}
    for (name, _), vals in zip(configs, raw):
        if not vals:
            continue
        vs = sorted(vals)
        spread = (vs[-1] - vs[0]) / max(1e-9, vs[0]) * 100.0
        out[name] = {
            "n": len(vs), "times": vals,
            "median": statistics.median(vs),
            "p90": vs[min(len(vs) - 1, int(len(vs) * 0.9))],
            "spread_pct": spread,
            "unreliable": spread > 20.0,
        }
    return out


def main():
    print("=" * 96)
    print("计时协议自检：演示「新进程稳定 vs 同进程漂移」")
    print("=" * 96)
    setup = ("from verify_adaptive_depth_synthetic import build_cases,call;"
             "cs=dict(build_cases())")
    print()
    print("1) 新进程测量（G10，ad=0，3 次独立进程）")
    st = measure_new_process(setup, "call(cs['G10'],20000000,0)", repeats=3)
    if st:
        print(f"   耗时 {[round(x) for x in st['times']]} ms")
        print(f"   中位 {st['median']:.0f}ms  p90 {st['p90']:.0f}ms  "
              f"极差 {st['spread_pct']:.1f}%")
        print(f"   ⇒ {'✔ 稳定（<20%）' if st['spread_pct'] <= 20 else '✘ 不稳定'}")

    print()
    print("2) 同进程交替测量（G10 ad=0 vs ad=1，5 轮随机顺序）")
    il = measure_interleaved(
        setup,
        [("ad0", "call(cs['G10'],20000000,0)"),
         ("ad1", "call(cs['G10'],20000000,1)")],
        rounds=5)
    for k, v in il.items():
        flag = "✘ 不可用（极差>20%）" if v["unreliable"] else "✔"
        print(f"   {k}: {[round(x) for x in v['times']]} ms  "
              f"中位 {v['median']:.0f}ms  极差 {v['spread_pct']:.1f}%  {flag}")
    print()
    print("   读法：同进程内即使随机交替，G10 这类大用例仍会漂移 ⇒")
    print("         大用例的耗时只能用「新进程」方式测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
