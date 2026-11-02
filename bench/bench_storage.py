# -*- coding: utf-8 -*-
"""存储方案实测：现有 5 个持久化落点的读写成本与增长趋势。

落点：
  data_time.json      实测耗时样本（当前 1748 条 / 153 KB），每次 _ps_record_ms 全量重写
  state/time_budget.json  模型状态（小，每次 _ps_observe 重写）
  state/game_stats.json   对局统计（小）
  data.json           按置信度分桶的胜负统计（当前 ~60 桶）
  cfg.json            配置（只读）

本脚本量化"全量重写"vs"追加写"vs"SQLite"的成本差，给存储改造提供依据。

用法: python bench/bench_storage.py
"""
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def timeit(fn, n=20):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    src = os.path.join(ROOT, "data_time.json")
    with open(src, encoding="utf-8") as f:
        rows = json.load(f)
    print(f"data_time.json: {len(rows)} 条, {os.path.getsize(src)/1024:.1f} KB")
    tmpd = os.path.join(ROOT, "bench", "_tmp_storage")
    os.makedirs(tmpd, exist_ok=True)
    dst = os.path.join(tmpd, "dt.json")
    dstl = os.path.join(tmpd, "dt.jsonl")
    dst_db = os.path.join(tmpd, "dt.db")

    # A 现状：读-改-写全量 JSON 数组 + 原子替换
    def a_rewrite():
        with open(src, encoding="utf-8") as f:
            cur = json.load(f)
        cur.append({"ts": "2026-11-02T00:00:00", "size": 33, "ms": 2.5, "native": True})
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)

    # B 追加式 JSONL（只写一行）
    def b_append():
        with open(dstl, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-11-02T00:00:00", "size": 33,
                                "ms": 2.5, "native": True}) + "\n")

    # C SQLite（WAL，单行插入）
    conn = sqlite3.connect(dst_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t(ts TEXT, size INT, ms REAL, native INT)")
    conn.commit()

    def c_sqlite():
        conn.execute("INSERT INTO t VALUES(?,?,?,?)",
                     ("2026-11-02T00:00:00", 33, 2.5, 1))
        conn.commit()

    ta = timeit(a_rewrite)
    tb = timeit(b_append)
    tc = timeit(c_sqlite)

    print("=" * 66)
    print(f"A 现状：全量读改写 JSON   {ta:8.3f} ms/次")
    print(f"B 追加式 JSONL           {tb:8.3f} ms/次   提速 {ta/tb:6.1f}x")
    print(f"C SQLite(WAL) 单行插入    {tc:8.3f} ms/次   提速 {ta/tc:6.1f}x")
    print("=" * 66)
    print("增长性：A 随样本数线性变慢（每局数十次调用）；B/C 基本恒定。")

    # 读回成本（启动时要读全量样本做拟合）
    def read_json():
        with open(src, encoding="utf-8") as f:
            json.load(f)

    def read_sqlite():
        list(conn.execute("SELECT size, ms, native FROM t"))

    print(f"\nA 启动读全量 JSON         {timeit(read_json, 10):8.3f} ms")
    print(f"C 启动读 SQLite 全量      {timeit(read_sqlite, 10):8.3f} ms")

    # 拟合：全量 vs 200 条窗口
    sys.path.insert(0, ROOT)
    from utils import probability as P
    print(f"\n_ps_fit_k(全量 {len(rows)})      {timeit(lambda: P._ps_fit_k(rows, True), 3):8.1f} ms")
    print(f"_ps_fit_k(最近 200)       {timeit(lambda: P._ps_fit_k(rows[-200:], True), 3):8.1f} ms")

    conn.close()
    import shutil
    shutil.rmtree(tmpd, ignore_errors=True)

    print("\n建议（按收益排序）：")
    print("  1. 拟合真正截窗到 200 条（注释已承诺，代码没做）→ 省 500+ ms/次")
    print("  2. 样本改追加式（JSONL）→ 写成本从线性降为常数")
    print("  3. 若要更强：SQLite 单表存样本，按 ts 定期裁剪，查询/拟合都下标化")


if __name__ == "__main__":
    main()
