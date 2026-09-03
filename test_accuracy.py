import json
import math
import os
import random

import matplotlib.pyplot as plt

from logger import get_logger

logger = get_logger(__name__)

with open("data.json") as f:
    data = json.load(f)

x = []
y = []

for key, value in data.items():
    if value["lose"] + value["win"] > 200:
        x.append(int(key))
        y.append((value["win"] / (value["lose"] + value["win"])) * 100)
        win = value["win"]
        lose = value["lose"]
        p_star = win / (win + lose)
        # 二项分布的检验
        n = win + lose
        p = x[-1] / 100
        if p == 1 or p == 0:
            continue
        z = (p_star - p) / ((p * (1 - p) / n) ** 0.5)
        if abs(z) > 1.96:
            logger.info("The percentage of %s is not significant. z-score: %s, p: %s", key, z, p_star)
        else:
            logger.info("The percentage of %s is significant. z-score: %s, p: %s", key, z, p_star)

# 散点图
plt.scatter(x, y)

x = []
y = []
num = 200
for i in range(50, 101):
    _x = 0
    for j in range(num):
        if random.random() < i / 100:
            _x = _x + 1
    x.append(i)
    y.append(_x / num * 100)

plt.scatter(x, y, color="green", s=2)

# 绘制 y=x 线
plt.plot(x, x, color="red")
plt.xlabel("literal percentage")
plt.ylabel("actual percentage")
plt.title("Percentage of winning over literal percentage")
plt.show()


def stat_runtime():
    """附加工具：统计运算时间（part_solve 实测耗时，样本来自 data_time.json）。

    按 (模式, 组大小) 分组输出样本数/均值/中位数/最值，并用指数模型
    t(L) = base·k^(L-33) 拟合增长因子 k（大组样本对比值中位数，
    与 utils.probability._ps_fit_k 同一算法），绘制实测散点与拟合曲线
    （纵轴对数）。拟合结果可用于核对程序内的自动拟合值（state/time_budget.json）。
    """
    if not os.path.exists("data_time.json"):
        logger.info("stat_runtime: data_time.json 不存在，请先运行自动扫雷积累实测样本")
        return
    with open("data_time.json", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or not rows:
        logger.info("stat_runtime: data_time.json 无样本")
        return

    # 按 (模式, 组大小) 分组统计
    groups = {}
    for r in rows:
        try:
            groups.setdefault((bool(r["native"]), int(r["size"])), []).append(float(r["ms"]))
        except (KeyError, TypeError, ValueError):
            continue
    logger.info("stat_runtime: 样本总数 %d，分组数 %d", len(rows), len(groups))
    for (native, size), vals in sorted(groups.items()):
        vals.sort()
        n = len(vals)
        mean = sum(vals) / n
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        logger.info(
            "  size=%3d  n=%4d  mean=%8.2fms  median=%8.2fms  min=%7.2f  max=%8.2f  native=%s",
            size, n, mean, med, vals[0], vals[-1], native,
        )

    # 拟合 k：只用大组(size>=30)样本对——小组耗时被固定开销/形状噪声主导
    pts = [(int(r["size"]), float(r["ms"])) for r in rows
           if r.get("native") and r.get("ms") and r.get("size") and int(r["size"]) >= 30]
    ks = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d_l = pts[j][0] - pts[i][0]
            if abs(d_l) >= 3:
                ks.append(math.log(pts[j][1] / pts[i][1]) / d_l)
    if len(ks) < 3:
        logger.info("stat_runtime: 大组(size>=30)有效样本对仅 %d（需≥3），无法拟合 k", len(ks))
        return
    ks.sort()
    k = min(max(math.exp(ks[len(ks) // 2]), 1.05), 2.0)
    bases = sorted(ms / (k ** (size - 33)) for size, ms in pts)
    base = bases[len(bases) // 2]
    logger.info("stat_runtime: 拟合 k=%.4f（夹限[1.05,2.0]）  base=%.3fms@33格  样本对=%d",
                k, base, len(ks))

    # 图：实测散点（对数纵轴）+ 拟合曲线
    sizes = [s for s, _ in pts]
    times = [m for _, m in pts]
    plt.figure("part_solve runtime")
    plt.scatter(sizes, times, s=8, label="measured (size>=30)")
    xs = list(range(min(sizes), max(sizes) + 1))
    plt.plot(xs, [base * (k ** (x - 33)) for x in xs], color="red",
             label=f"fit: {base:.2f}·{k:.3f}^(L-33)")
    plt.yscale("log")
    plt.xlabel("group size L")
    plt.ylabel("part_solve ms (log)")
    plt.title("Runtime statistics")
    plt.legend()
    plt.grid(True, which="both", alpha=0.3)
    plt.show()


stat_runtime()
