# -*- coding: utf-8 -*-
"""点击顺序优化：按最小生成树路径排序点击坐标，减少鼠标移动距离。

自 utils/util.py 拆分而来。
"""
from collections import defaultdict

import networkx as nx


def sort_clicks(clicks, cs):
    if len(cs) <= 1:
        return clicks

    G = nx.Graph()
    for i in range(len(cs)):
        x0, y0 = cs[i]
        for j in range(i + 1, len(cs)):
            x1, y1 = cs[j]
            if abs(x0 - x1) <= 2 and abs(y0 - y1) <= 2:
                G.add_edge((x0, y0), (x1, y1), weight=abs(x0 - x1) + abs(y0 - y1))
    mst = nx.minimum_spanning_tree(G)

    flag = defaultdict(bool)

    def dfs(pos, mst, sorted_clicks):
        if flag[pos]:
            return sorted_clicks
        flag[pos] = True
        i, j = pos
        for u in range(i - 1, i + 2):
            for v in range(j - 1, j + 2):
                if (u, v) in clicks and (u, v) not in sorted_clicks:
                    sorted_clicks.append((u, v))

        if len(sorted_clicks) == len(clicks):
            return sorted_clicks

        for _p in mst.neighbors(pos):
            sorted_clicks = dfs(_p, mst, sorted_clicks)
        return sorted_clicks

    degree_one_nodes = [node for node in mst.nodes()
                        if mst.degree(node) == 1 or mst.degree(node) == 0]
    # 不连通/全孤立图里 degree_one_nodes 可能为空；且只从第一个连通分量
    # 出发会漏掉其它分量的格子。逐个分量起点跑 dfs，最后按原序补齐残余，
    # 保证返回值始终是 clicks 的一个排列（不丢格、不改算法排序语义）。
    sorted_clicks = []
    starts = degree_one_nodes or list(mst.nodes())
    for start in starts:
        if not flag.get(start):
            sorted_clicks = dfs(start, mst, sorted_clicks)
    for c in clicks:
        if c not in sorted_clicks:
            sorted_clicks.append(c)
    return sorted_clicks
