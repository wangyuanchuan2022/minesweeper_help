import json
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
