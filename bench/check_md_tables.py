# -*- coding: utf-8 -*-
"""Markdown 表格列数一致性自检（格式闸门的本地替代）。

用法: python bench/check_md_tables.py CHANGELOG.md review/算法更新评估.md
"""
import sys


def check(path):
    bad = 0
    rows = 0
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    in_tbl = False
    expect = None
    for ln, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cols = s.count("|") - 1
            if not in_tbl:
                in_tbl = True
                expect = cols
                rows += 1
            else:
                # 分隔行（|---|）也要同列数
                if cols != expect:
                    print(f"  ❌ {path}:{ln} 列数 {cols} ≠ 表头 {expect}")
                    bad += 1
                rows += 1
        else:
            in_tbl = False
            expect = None
    print(f"  {path}: {rows} 行表格，{'✅ 全部同列数' if bad == 0 else f'{bad} 行不一致'}")
    return bad


def main():
    paths = sys.argv[1:] or ["CHANGELOG.md"]
    print("=" * 62)
    print("Markdown 表格列数自检")
    print("=" * 62)
    total = sum(check(p) for p in paths)
    print("-" * 62)
    print("✅ PASS" if total == 0 else f"❌ FAIL（{total} 处）")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
