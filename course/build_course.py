# -*- coding: utf-8 -*-
"""组装 code2course 单文件课程（L3 精讲档重制版）：内联三件套 + 拼接所有分段 + 生成侧边栏。

外壳结构来自 resources/course-template.html（CSP meta / 主题防闪 / 滚动复位 / sidebar 骨架）；
样式与脚本来自 resources/base.css 与 resources/app.js 全文内联（零外部依赖）。
"""
import io
import os

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.expanduser("~/.agents/skills/code2course")
SEG = os.path.join(BASE, "segments")
OUT = os.path.join(BASE, "智能扫雷助手-交互式代码课.html")

CSS = io.open(os.path.join(SKILL, "resources", "base.css"), encoding="utf-8").read()
JS = io.open(os.path.join(SKILL, "resources", "app.js"), encoding="utf-8").read()

# 模块清单：(id, 侧边栏名) —— 与 segments/NN-mN.html 一一对应
MODULES = [
    ("m0", "0 · 封面"),
    ("m1", "1 · 模板仓库"),
    ("m2", "2 · 屏幕到棋盘"),
    ("m3", "3 · 增量重扫"),
    ("m4", "4 · 单格判定"),
    ("m5", "5 · 双格判定"),
    ("m6", "6 · 区域划分"),
    ("m7", "7 · 回溯枚举"),
    ("m8", "8 · 胜率与大局面"),
    ("m9", "9 · 帮助模式"),
    ("m10", "10 · 自动模式"),
    ("m11", "11 · 设置与校准"),
    ("m12", "12 · 工程化全景"),
]

nav = "\n".join(
    '        <a class="nav-item" href="#%s">%s</a>' % (mid, name)
    for mid, name in MODULES
)

seg_parts = []
for i, (mid, _name) in enumerate(MODULES):
    fname = "%02d-%s.html" % (i, mid)
    path = os.path.join(SEG, fname)
    if not os.path.exists(path):
        raise SystemExit("缺少分段文件: %s" % path)
    seg_parts.append(io.open(path, encoding="utf-8").read())

content = "\n\n".join(seg_parts)

head = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'">
  <title>智能扫雷助手 · 交互式代码课</title>
  <script>
    try {
      var t = localStorage.getItem('c2c-theme');
      if (t === 'dark' || t === 'light') document.documentElement.dataset.theme = t;
    } catch (e) {}
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    window.scrollTo(0, 0);
  </script>
  <style>
"""

body_top = """  </style>
</head>
<body>
  <button class="theme-toggle" type="button" aria-label="切换夜间模式">🌙</button>

  <div class="layout">
    <aside class="sidebar">
      <div class="brand">📦 智能扫雷助手</div>
      <nav class="module-nav">
""" + nav + """
      </nav>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="progress-label"><span id="prog-text">模块 1 / 13</span> · 用 ←/→ 键翻页</div>
    </aside>

    <main class="content">
"""

body_bottom = """
    </main>
  </div>

  <script>
"""

tail = """  </script>
</body>
</html>
"""

final = head + CSS + body_top + "\n" + content + "\n" + body_bottom + JS + tail

io.open(OUT, "w", encoding="utf-8").write(final)
print("written:", OUT)
print("size KB:", round(len(final.encode("utf-8")) / 1024, 1))
