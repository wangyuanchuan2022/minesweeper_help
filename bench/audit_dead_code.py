# -*- coding: utf-8 -*-
"""死代码 / 冗余审计（静态，只读，不需要跑游戏）。

==================== 为什么要单独做 ====================
用户问题：「有没有没调用到的代码、死代码，哪些可以清理合并」。
凭印象列清单不可信（本项目已出现过「以为是死代码、其实是测试夹具入口」
和「以为是活代码、其实生产零调用」两类误判）。本脚本用 AST 建立三份账：

  ① **定义账**：生产代码（main.py / utils / ui / setting.py / logger.py /
     color.py）里所有 模块级函数、类、类方法、模块级常量。
  ② **引用账**：把 生产 + tests + bench + sim + tools 全部 .py 的标识符出现
     次数统计出来（AST Name/Attribute 节点 + 字符串里的 getattr 形式）。
     定义处本身不计入引用。
  ③ **导出账**：C++ `m.def("name"` 的每个导出符号，在 Python 侧的消费点
     （`mscore.name` / `native.<wrapper>` 里的 `mscore.name`）。

判定（四档，互斥）：
  DEAD_ANYWHERE   生产里定义，但生产/测试/bench/sim/tools 全都没有任何引用
  DEAD_PROD       生产里定义，只有 tests/bench 引用（生产不调用）
  TEST_ONLY       仅 tests 引用（允许保留，但要在报告里标出来源）
  LIVE            生产里有 ≥1 处真实引用

⚠️ 误判防护（本脚本已内置）：
  · 装饰器/属性访问（`obj.method`）也算引用——只看 `Name` 节点会漏掉
    方法调用，把活代码误判成死代码。
  · `__all__` / `import *` 导出的名字算「被外部引用」，不判死。
  · Qt 槽函数（`@pyqtSlot` / `connect(...)` 里出现的名字）与 `getattr` 字符串
    形式（`getattr(self, "name")`）也算引用。
  · `if __name__ == "__main__":` 块内的引用算生产引用（脚本可直接跑）。
  · 名字里带 `_` 前缀不算豁免——私有方法同样可能是死的。

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/audit_dead_code.py
    ... --json state/audit/dead_code.json    # 机读版（供 analyze_e2e 消费）
"""
import argparse
import ast
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# GBK 控制台防 UnicodeEncodeError（本脚本输出含中文）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROD_FILES = [
    "main.py", "setting.py", "logger.py", "color.py",
    "utils/__init__.py", "utils/solver.py", "utils/vision.py",
    "utils/probability.py", "utils/deduction.py", "utils/native.py",
    "utils/mm0.py", "utils/util.py", "utils/combinatorics.py",
    "utils/clicking.py", "utils/misc.py", "utils/decide_trace.py",
    "utils/analyze_decide_trace.py",
    "ui/__init__.py", "ui/window.py", "ui/preview_popup.py",
    "ui/edit_setting.py", "ui/screenshot.py",
]

SCAN_DIRS = ["", "utils", "ui", "tests", "bench", "sim", "tools"]
SKIP_DIR_PARTS = {".git", ".tools", "__pycache__", ".mypy_cache",
                  ".pytest_cache", ".ruff_cache", "vendor", "output",
                  ".code-review-graph", ".idea", "node_modules", "cpp"}


def iter_py(root):
    for base, dirs, files in os.walk(root):
        rel = os.path.relpath(base, root)
        parts = set() if rel == "." else set(rel.replace("\\", "/").split("/"))
        if parts & SKIP_DIR_PARTS:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_PARTS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def read(p):
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def bucket_of(rel):
    rel = rel.replace("\\", "/")
    if rel.startswith("tests/"):
        return "test"
    if rel.startswith(("bench/", "sim/", "tools/")):
        return "bench"
    if rel.startswith(("utils/", "ui/")) or rel in (
            "main.py", "setting.py", "logger.py", "color.py"):
        return "prod"
    return "other"


# ---------------------------------------------------------------------------
# ① 定义账
# ---------------------------------------------------------------------------
class DefCollector(ast.NodeVisitor):
    def __init__(self):
        self.defs = []   # (kind, name, lineno, qualname, decorators)
        self._cls = []

    def _add(self, kind, node, name):
        decs = []
        for d in getattr(node, "decorator_list", []) or []:
            try:
                decs.append(ast.unparse(d))
            except Exception:  # noqa: BLE001
                decs.append("?")
        self.defs.append((kind, name, node.lineno,
                          ".".join(self._cls + [name]), decs))

    def visit_ClassDef(self, node):
        self._add("class", node, node.name)
        self._cls.append(node.name)
        self.generic_visit(node)
        self._cls.pop()

    def visit_FunctionDef(self, node):
        self._add("method" if self._cls else "func", node, node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._add("method" if self._cls else "func", node, node.name)
        self.generic_visit(node)

    def visit_Assign(self, node):
        # 模块级常量（UPPER_CASE 或普通名）——只收模块顶层
        if self._cls:
            return
        for t in node.targets:
            if isinstance(t, ast.Name):
                self.defs.append(("const", t.id, node.lineno, t.id, []))


def collect_defs():
    out = {}
    for rel in PROD_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        tree = ast.parse(read(p), filename=p)
        c = DefCollector()
        # 只收模块顶层与类内（跳过函数内部嵌套定义）
        for node in tree.body:
            c.visit(node)
        out[rel] = c.defs
    return out


# ---------------------------------------------------------------------------
# ② 引用账
# ---------------------------------------------------------------------------
def count_refs(defs):
    """返回 {name: {"prod":n, "test":n, "bench":n, "other":n, "sites":[...]}}"""
    names = set()
    for rows in defs.values():
        for kind, name, _ln, _q, _d in rows:
            names.add(name)

    # 预先构造正则：标识符整词匹配（用于字符串里的 getattr/connect 形式）
    pats = {n: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])")
            for n in names}
    refs = {n: {"prod": 0, "test": 0, "bench": 0, "other": 0, "sites": []}
            for n in names}

    for p in iter_py(ROOT):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        b = bucket_of(rel)
        text = read(p)
        if not text:
            continue
        # AST 计数（Name / Attribute.attr / 字符串字面量）
        hit = {}
        try:
            tree = ast.parse(text, filename=p)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                nm = None
                if isinstance(node, ast.Name):
                    nm = node.id
                elif isinstance(node, ast.Attribute):
                    nm = node.attr
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # getattr(self, "x") / connect("x") / m.def 里的名字
                    s = node.value
                    if s in names:
                        nm = s
                    elif len(s) <= 80:
                        for cand in names:
                            if pats[cand].fullmatch(s):
                                nm = cand
                                break
                elif isinstance(node, ast.alias):
                    nm = (node.asname or node.name).split(".")[0]
                if nm in names:
                    hit.setdefault(nm, []).append(node.lineno)
        for nm, lns in hit.items():
            refs[nm][b] += len(lns)
            if b == "prod" and len(refs[nm]["sites"]) < 6:
                refs[nm]["sites"].append(f"{rel}:{lns[0]}")

    # 减去「定义处自己」那一次出现。
    # ⚠️ 只有**模块级常量赋值**会产出 ast.Name（`FOO = 1` 的 target），
    # `def foo` / `class Foo` 的 name 是 FunctionDef/ClassDef 的字符串字段，
    # 不会产生 Name 节点。所以这里只能对 kind=="const" 扣一次，且要按
    # (file, name) 去重——否则同名定义（如三个 closeEvent）会被重复扣减，
    # 把活方法误判成 DEAD_ANYWHERE（首版实测就踩了这个坑）。
    subtracted = set()
    for rel, rows in defs.items():
        b = bucket_of(rel)
        for kind, name, _ln, _q, _d in rows:
            if kind != "const":
                continue
            key = (rel, name)
            if key in subtracted:
                continue
            subtracted.add(key)
            if refs.get(name, {}).get(b, 0) > 0:
                refs[name][b] -= 1
    return refs


# ---------------------------------------------------------------------------
# ③ C++ 导出账
# ---------------------------------------------------------------------------
def cpp_exports():
    p = os.path.join(ROOT, "cpp", "mscore.cpp")
    if not os.path.exists(p):
        return []
    text = read(p)
    # m.def("name"  —— 允许跨行与空白
    return sorted(set(re.findall(r'\bm\.def\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', text)))


def python_consumers():
    """收集 Python 侧 `mscore.<sym>` / `td.<sym>` 的消费（含 hasattr 字符串）。"""
    out = {}
    for p in iter_py(ROOT):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if bucket_of(rel) == "other":
            continue
        text = read(p)
        for m in re.finditer(r"\b(?:mscore|td|_mscore_mod)\.([A-Za-z_][A-Za-z0-9_]*)", text):
            out.setdefault(m.group(1), []).append(rel)
        for m in re.finditer(r'hasattr\(\s*[^,]+,\s*"([A-Za-z_][A-Za-z0-9_]*)"', text):
            out.setdefault(m.group(1), []).append(rel)
    return out


# ---------------------------------------------------------------------------
# ④ 合并候选（重复/相似片段）
# ---------------------------------------------------------------------------
STYLE_RE = re.compile(r'QPushButton\{[^}]*\}')
CONST_STR_RE = re.compile(r'^\s{0,8}([A-Z][A-Z0-9_]{3,})\s*=\s*(.+)$')


def duplicate_candidates():
    """找出重复度高的常量/样式串（合并候选）。"""
    seen = {}
    for rel in PROD_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        text = read(p)
        for i, ln in enumerate(text.splitlines(), 1):
            for m in STYLE_RE.finditer(ln):
                s = re.sub(r"\s+", "", m.group(0))
                if len(s) > 40:
                    seen.setdefault(s, []).append(f"{rel}:{i}")
    dup = {k: v for k, v in seen.items() if len(v) >= 3}
    return dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "state", "audit", "dead_code.json"))
    ap.add_argument("--md", default=os.path.join(ROOT, "state", "audit", "dead_code.md"))
    args = ap.parse_args()

    defs = collect_defs()
    refs = count_refs(defs)

    rows = []
    for rel, ds in defs.items():
        for kind, name, ln, qual, decs in ds:
            r = refs.get(name) or {}
            prod, test, bench, other = (r.get("prod", 0), r.get("test", 0),
                                        r.get("bench", 0), r.get("other", 0))
            if prod > 0:
                verdict = "LIVE"
            elif test > 0 or bench > 0:
                verdict = "DEAD_PROD"
            elif other > 0:
                verdict = "OTHER_ONLY"
            else:
                verdict = "DEAD_ANYWHERE"
            # 装饰器强引用（Qt 槽等）视为活
            if verdict.startswith("DEAD") and any(
                    "slot" in d.lower() or "property" in d.lower() for d in decs):
                verdict = "LIVE_DECORATED"
            rows.append({
                "file": rel, "kind": kind, "name": name, "line": ln,
                "qualname": qual, "verdict": verdict,
                "prod": prod, "test": test, "bench": bench, "other": other,
                "sites": r.get("sites", []), "decorators": decs,
            })

    exports = cpp_exports()
    consumers = python_consumers()
    exp_rows = []
    for e in exports:
        sites = sorted(set(consumers.get(e, [])))
        prod_sites = [s for s in sites if bucket_of(s) == "prod"]
        exp_rows.append({
            "symbol": e,
            "prod": len(prod_sites),
            "all": len(sites),
            "sites": sites[:6],
            "verdict": "LIVE" if prod_sites else (
                "BENCH_ONLY" if sites else "EXPORTED_UNUSED"),
        })

    dup = duplicate_candidates()

    # ---- 控制台报告 ----
    order = {"DEAD_ANYWHERE": 0, "DEAD_PROD": 1, "OTHER_ONLY": 2,
             "LIVE_DECORATED": 3, "LIVE": 4}
    rows.sort(key=lambda r: (order[r["verdict"]], r["file"], r["line"]))
    cnt = {}
    for r in rows:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    print("=" * 96)
    print("死代码审计（静态 AST；引用 = 生产/测试/bench 全部 .py 的标识符出现）")
    print("=" * 96)
    print("定义总数 %d：" % len(rows)
          + "  ".join("%s=%d" % (k, v) for k, v in sorted(cnt.items())))
    print()
    for verdict in ("DEAD_ANYWHERE", "DEAD_PROD", "OTHER_ONLY"):
        sel = [r for r in rows if r["verdict"] == verdict]
        if not sel:
            continue
        print("-" * 96)
        print("[%s] %d 项" % (verdict, len(sel)))
        for r in sel:
            print("  %-34s %s:%d  (%s%s)" % (
                r["qualname"], r["file"], r["line"], r["kind"],
                "" if not r["decorators"] else " @" + ",".join(r["decorators"])))
            if r["sites"]:
                print("      prod 引用: " + ", ".join(r["sites"]))
            print("      计数 prod=%d test=%d bench=%d other=%d"
                  % (r["prod"], r["test"], r["bench"], r["other"]))
        print()

    print("-" * 96)
    print("C++ 导出符号消费账（%d 个导出）" % len(exp_rows))
    for r in exp_rows:
        flag = {"LIVE": "  ", "BENCH_ONLY": "!!", "EXPORTED_UNUSED": "XX"}[r["verdict"]]
        print("  %s %-26s prod=%-2d all=%-2d  %s"
              % (flag, r["symbol"], r["prod"], r["all"],
                 ", ".join(r["sites"][:4]) if r["sites"] else "（无 Python 消费）"))
    unused_exp = [r["symbol"] for r in exp_rows
                  if r["verdict"] == "EXPORTED_UNUSED"]
    if unused_exp:
        print("\n  XX = 导出但 Python 侧零消费：%s" % ", ".join(unused_exp))

    print()
    print("-" * 96)
    print("合并候选：重复 ≥3 次的 QSS 样式片段（%d 组）" % len(dup))
    for s, sites in sorted(dup.items(), key=lambda kv: -len(kv[1]))[:8]:
        print("  ×%d  %s" % (len(sites), s[:96]))
        print("        " + ", ".join(sites[:6]))

    # ---- 落盘 ----
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    payload = {
        "defs": rows,
        "counts": cnt,
        "cpp_exports": exp_rows,
        "duplicate_qss": {k: v for k, v in dup.items()},
    }
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    lines = ["# 死代码 / 冗余审计（静态 AST）", "",
             "定义总数 %d：%s" % (len(rows),
                              "  ".join("%s=%d" % (k, v) for k, v in sorted(cnt.items()))),
             ""]
    for verdict in ("DEAD_ANYWHERE", "DEAD_PROD", "OTHER_ONLY"):
        sel = [r for r in rows if r["verdict"] == verdict]
        if not sel:
            continue
        lines.append("## %s（%d 项）" % (verdict, len(sel)))
        lines.append("")
        lines.append("| 符号 | 位置 | 类型 | prod | test | bench |")
        lines.append("|---|---|---|---|---|---|")
        for r in sel:
            lines.append("| `%s` | %s:%d | %s | %d | %d | %d |"
                         % (r["qualname"], r["file"], r["line"], r["kind"],
                            r["prod"], r["test"], r["bench"]))
        lines.append("")
    lines.append("## C++ 导出消费账")
    lines.append("")
    lines.append("| 导出符号 | prod 消费 | 全部 | 判定 |")
    lines.append("|---|---|---|---|")
    for r in exp_rows:
        lines.append("| `%s` | %d | %d | %s |"
                     % (r["symbol"], r["prod"], r["all"], r["verdict"]))
    lines.append("")
    lines.append("## 合并候选（重复 QSS 片段）")
    lines.append("")
    for s, sites in sorted(dup.items(), key=lambda kv: -len(kv[1]))[:12]:
        lines.append("- ×%d `%s` — %s" % (len(sites), s[:80], ", ".join(sites[:5])))
    with io.open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n落盘：%s" % args.json)
    print("落盘：%s" % args.md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
