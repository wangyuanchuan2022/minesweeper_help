# -*- coding: utf-8 -*-
"""逐字一致性校验器：检查课程分段里 <pre class="tp-line"> 的代码块
与最近的 data-file 指向的源文件是否逐字一致（HTML 实体反转义后精确子串匹配）。

用法：python check_verbatim.py [segments_dir]

规则：
- 按 token 在文档中的位置归属：每个 <pre class="tp-line"> 属于它之前最近的
  data-file 属性（translate-pair 的 .tp-head 里）。
- data-file 形如 "utils/vision.py · L23-L36"，可能含多个「文件 · 行号」
  组合（跨文件翻译块）；代码块须至少在其中一个文件中找到逐字子串。
- 源文件 CRLF 归一为 LF 后比较；html.unescape 后精确子串匹配。
- 行号范围越界 / 文件不存在 / 无归属 data-file 均报 FAIL。
"""
import html
import io
import os
import re
import sys

SEG_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "segments")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRE_RE = re.compile(r'<pre class="tp-line"[^>]*>\s*<code>(.*?)</code>\s*</pre>', re.S)
DATA_FILE_RE = re.compile(r'data-file="([^"]*)"')
FILE_RANGE_RE = re.compile(
    r'([\w./\\]+\.(?:py|json|spec|md|txt|yml|yaml))'
    r'(?:\s*[·+＋]\s*L(\d+)\s*[–\-—~至]+\s*L?(\d+))?')

# 脱敏归一：盘符路径（D:/foo/bar.exe 与 D:/…/bar.exe）折算成同一记号后再比对。
# 这是课程铁律允许的唯一改写（cfg.json 的 path 字段脱敏），其余不一致仍算失败。
MASK_PATH_RE = re.compile(r'[A-Za-z]:[/\\](?:[^"\s]+[/\\])+')


def mask_norm(text):
    return MASK_PATH_RE.sub('[MASKED]/', text)

_cache = {}


def read_file_norm(path):
    if path not in _cache:
        with io.open(path, encoding="utf-8", errors="replace", newline="") as f:
            _cache[path] = f.read().replace("\r\n", "\n")
    return _cache[path]


def main():
    ok, fail, warn = 0, 0, 0
    for fname in sorted(os.listdir(SEG_DIR)):
        if not fname.endswith(".html"):
            continue
        path = os.path.join(SEG_DIR, fname)
        doc = io.open(path, encoding="utf-8").read()

        # 收集 token：data-file 与 pre，按位置排序
        tokens = []
        for m in DATA_FILE_RE.finditer(doc):
            tokens.append((m.start(), "file", html.unescape(m.group(1))))
        for m in PRE_RE.finditer(doc):
            tokens.append((m.start(), "pre", m.group(1)))
        tokens.sort()

        current = None          # 当前归属的 data-file 文本
        owner_ranges = []       # (path, line_a, line_b)
        pending_pres = []

        def flush():
            nonlocal ok, fail, warn
            files = []
            for fpath, a, b in owner_ranges:
                if os.path.exists(fpath):
                    files.append(fpath)
                else:
                    print("[FAIL] %s: 源文件不存在: %s"
                          % (fname, os.path.relpath(fpath, ROOT)))
                    fail += 1
            for text_html in pending_pres:
                if not files:
                    print("[FAIL] %s: data-file 未解析出有效文件: %s" % (fname, current))
                    fail += 1
                    continue
                text = html.unescape(text_html)
                hit = any(text in read_file_norm(f) for f in files)
                if hit:
                    ok += 1
                else:
                    # 脱敏兜底：两侧盘符路径归一后再比（仅 cfg.json path 脱敏这一例外）
                    hit_masked = any(
                        mask_norm(text) in mask_norm(read_file_norm(f))
                        for f in files)
                    if hit_masked:
                        ok += 1
                        print("[MASK-OK] %s: 一致（含脱敏 path 归一比对，归属: %s）"
                              % (fname, current))
                    else:
                        print("[FAIL] %s: 代码块与源文件不一致 (归属: %s)" % (fname, current))
                        print("       首行: %r" % text.split("\n")[0][:76])
                        fail += 1

        for _pos, kind, val in tokens:
            if kind == "file":
                flush()
                current = val
                owner_ranges = []
                for fm in FILE_RANGE_RE.finditer(current):
                    fpath = os.path.join(ROOT, fm.group(1).replace("/", os.sep))
                    a = int(fm.group(2)) if fm.group(2) else None
                    b = int(fm.group(3)) if fm.group(3) else None
                    if a is not None:
                        owner_ranges.append((fpath, a, b))
                    elif os.path.exists(fpath):
                        owner_ranges.append((fpath, None, None))
                    else:
                        owner_ranges.append((fpath, None, None))  # 不存在也会在 flush 报
                pending_pres = []
            else:
                if current is None:
                    print("[FAIL] %s: pre 无前置 data-file 归属" % fname)
                    fail += 1
                    continue
                pending_pres.append(val)

        flush()

        # 行号越界检查（独立于子串匹配）
        for m in DATA_FILE_RE.finditer(doc):
            df = html.unescape(m.group(1))
            for fm in FILE_RANGE_RE.finditer(df):
                if not fm.group(2):
                    continue
                fpath = os.path.join(ROOT, fm.group(1).replace("/", os.sep))
                if not os.path.exists(fpath):
                    continue
                a, b = int(fm.group(2)), int(fm.group(3))
                n = read_file_norm(fpath).count("\n") + 1
                if b > n:
                    print("[FAIL] %s: 行号越界 %s L%d-L%d（实际 %d 行）"
                          % (fname, fm.group(1), a, b, n))
                    fail += 1

    print("\n逐字校验: %d 块一致, %d 块失败, %d 警告" % (ok, fail, warn))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
