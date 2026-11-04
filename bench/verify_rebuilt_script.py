# -*- coding: utf-8 -*-
"""验收重建的 measure_endgame_stratified.py：确认关键逻辑都在。

该文件未被 Git 跟踪，且刚被误删后重建，必须重新验收，
不能假定它等价于原实现。
"""
import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(ROOT, "bench", "measure_endgame_stratified.py")
s = io.open(p, encoding="utf-8").read()

print("=" * 96)
print("验收重建的 measure_endgame_stratified.py")
print("=" * 96)

# 语法
try:
    ast.parse(s)
    print("\n✔ 语法通过")
except SyntaxError as e:
    print(f"\n✘ 语法错误：{e}")
    sys.exit(1)

# 关键逻辑（逐项，缺一不可）
checks = {
    "原子写盘 _atomic_json_dump": "_atomic_json_dump" in s,
    "写入 complete 标记": '"complete": True' in s,
    "按局 bootstrap CI": "cluster_bootstrap_ci" in s,
    "覆盖率门线 COVERAGE_MIN": "COVERAGE_MIN" in s,
    "残局阈值 ENDGAME=343": "ENDGAME = 343" in s,
    "复用生产快照 ProdSnapshotRecorder": "ProdSnapshotRecorder" in s,
    "不自行重算概率 build_full_probs": "build_full_probs" in s,
    "内部格用 confidence 补全": "snap[\"confidence\"]" in s,
    "未使用外框污染的 n_opened": "cell_value[1:H + 1" not in s,
    "按局聚类报告": "局级" in s,
}
print("\n关键逻辑检查：")
miss = []
for k, v in checks.items():
    print(f"  {'✔' if v else '✘'} {k}")
    if not v:
        miss.append(k)

# 必须**不**存在的东西（旧错误做法的痕迹）
bad_patterns = {
    "自行维护内部格公式（漏 mine_num）": "1.0 - R / U" in s,
    "用 flush pending 做配对": "flush_clicks" in s,
}
print("\n旧错误做法残留检查（应全为 False）：")
for k, v in bad_patterns.items():
    print(f"  {'✘ 存在' if v else '✔ 无'} {k}")
    if v:
        miss.append(k)

print("\n" + "=" * 96)
if miss:
    print(f"✘ 未通过：{miss}")
    sys.exit(1)
print("✅ 重建脚本逻辑完整：原子写盘 + complete 标记 + 按局 bootstrap +")
print("   覆盖率门线 + 复用生产快照（不自行算概率）+ 无外框污染 + 无旧错误做法")
