# -*- coding: utf-8 -*-
"""注入必红负向自测：对新测试文件施加生产侧变异体，逐个验证「必红」。

在隔离副本根目录执行：python _mutate_check.py
每个变异体只改一处生产语义；目标用例必须变红（否则测试是假绿）。
结果写入 _mut_report.md（UTF-8，避免 GBK 控制台编码问题）。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROB = os.path.join(ROOT, "utils", "probability.py")
NATIVE = os.path.join(ROOT, "utils", "native.py")

MUTANTS = [
    ("M1", PROB, "点击坐标 off-by-one：(x-1)*cell_width",
     "self.bx + cx * self.cell_width,\n                                self.by + cy * self.cell_width",
     "self.bx + (cx - 1) * self.cell_width,\n                                self.by + (cy - 1) * self.cell_width",
     "test_single_click_is_one_based_not_zero_based"),
    ("M2", PROB, "批量分支只点 tied[0]（不是全量批量）",
     "_to_click = tied[:64]",
     "_to_click = tied[:1]",
     "test_certain_safe_batch_clicks_all_tied_cells"),
    ("M3", PROB, "热力图契约退化成扁平短键",
     '                        self._last_heatmap = {\n'
     '                            "prob": _prob_hm,\n'
     '                            "best": (rx, ry),\n'
     '                            "best_conf": round(confidence, 5),\n'
     '                            "total": float(r.get("world_upper", 0) or 0),\n'
     '                        }\n'
     '                        # 本轮要点的格子',
     '                        self._last_heatmap = {\n'
     '                            "p": _prob_hm,\n'
     '                            "b": (rx, ry),\n'
     '                            "c": round(confidence, 5),\n'
     '                            "t": float(r.get("world_upper", 0) or 0),\n'
     '                        }\n'
     '                        # 本轮要点的格子',
     "test_heatmap_contract_is_last_round_of_loop"),
    ("M4", PROB, "热力图只保留首轮（第二轮不覆盖）",
     '                        self._last_heatmap = {\n'
     '                            "prob": _prob_hm,\n'
     '                            "best": (rx, ry),\n'
     '                            "best_conf": round(confidence, 5),\n'
     '                            "total": float(r.get("world_upper", 0) or 0),\n'
     '                        }\n'
     '                        # 本轮要点的格子',
     '                        if not getattr(self, "_last_heatmap", None):\n'
     '                            self._last_heatmap = {\n'
     '                                "prob": _prob_hm,\n'
     '                                "best": (rx, ry),\n'
     '                                "best_conf": round(confidence, 5),\n'
     '                                "total": float(r.get("world_upper", 0) or 0),\n'
     '                            }\n'
     '                        # 本轮要点的格子',
     "test_heatmap_contract_is_last_round_of_loop"),
    ("M5", PROB, "帮助模式漏掉 appended_pos 去重",
     "if (rx, ry) not in self.appended_pos:",
     "if True:",
     "test_help_mode_single_decision_no_click_and_dedup"),
    ("M6", PROB, "帮助模式被错误送进自动点击分支",
     '                    if not self.is_play:\n'
     '                        # ---- 帮助模式：单次 decide（原逻辑原样保留）----',
     '                    if False:\n'
     '                        # ---- mutated: 帮助模式误入自动分支 ----',
     "test_help_mode_single_decision_no_click_and_dedup"),
    ("M7", PROB, "无进展守卫删除",
     "if _num9_after >= _num9_before:",
     "if False:",
     "test_loop_breaks_when_rescan_shows_no_progress"),
    ("M8", PROB, "连续循环退化为「一轮即返回」",
     "                        if _num9_after == 0:\n                            break",
     "                        break",
     "test_heatmap_contract_is_last_round_of_loop"),
    ("M9", PROB, "循环内第二轮 decide 参数漂移",
     "                            r = native.decide(\n"
     "                                cell_value, self.a, w, h,\n"
     "                                wr_threshold=(6 + native.LIMIT_BONUS_WIN_RATE + 6.0)\n"
     "                                if _help_mode else (6 + native.LIMIT_BONUS_WIN_RATE),\n"
     "                                node_cap=5_000_000 if _help_mode else 2_000_000,\n"
     "                                is_play=self.is_play,\n"
     "                            )",
     "                            r = native.decide(\n"
     "                                cell_value, self.a, w, h,\n"
     "                                wr_threshold=6.0,\n"
     "                                node_cap=2_000_000,\n"
     "                                is_play=self.is_play,\n"
     "                            )",
     "test_heatmap_contract_is_last_round_of_loop"),
    ("M10", PROB, "帮助模式预算乘数失效（恒 30s）",
     "return DECISION_TIME_BUDGET * _DECISION_BUDGET_MULT",
     "return DECISION_TIME_BUDGET",
     "test_budget_multiplier_roundtrip"),
    ("M11", PROB, "点击后不 rescan（重扫被漏掉）",
     "cell_value = self.rescan_after_click(cell_value)",
     "pass",
     "test_certain_safe_batch_clicks_all_tied_cells"),
    ("M12", NATIVE, "is_play 未透传（新 pyd 恒收到 True）",
     '    kwargs = {"is_play": bool(is_play)}',
     '    kwargs = {"is_play": True}',
     "test_native_decide_new_pyd_passes_is_play"),
    ("M13", NATIVE, "旧 pyd 回退静默（不打 WARNING）",
     '        logger.warning("native.decide 旧签名兼容回退（pyd 无 is_play 形参）："\n'
     '                       "帮助模式放宽参数（DSTAR×3 等）本次不生效")',
     "        pass  # mutated: 静默回退",
     "test_native_decide_old_pyd_fallback"),
    ("M14", PROB, "帮助模式不放宽 wr_threshold（少 +6）",
     "wr_threshold=(6 + native.LIMIT_BONUS_WIN_RATE + 6.0)\n"
     "                    if _help_mode else (6 + native.LIMIT_BONUS_WIN_RATE),",
     "wr_threshold=(6 + native.LIMIT_BONUS_WIN_RATE)\n"
     "                    if _help_mode else (6 + native.LIMIT_BONUS_WIN_RATE),",
     "test_help_mode_passes_relaxed_params"),
]


def run_tests(target):
    cmd = [sys.executable, "-m", "unittest",
           "tests.test_cpp_decide_wiring.TestCppDecideWiring." + target]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       errors="replace")
    return p.returncode


def main():
    paths = sorted({m[1] for m in MUTANTS})
    backups = {}
    for path in paths:
        bak = path + ".mutbak"
        shutil.copyfile(path, bak)
        backups[path] = bak
    results = []
    try:
        for mid, path, desc, old, new, target in MUTANTS:
            shutil.copyfile(backups[path], path)  # 每个变异体从干净副本起
            src = open(path, encoding="utf-8").read()
            hits = src.count(old)
            if hits != 1:
                results.append((mid, desc, target, -1, False,
                                "原文匹配 %d 处（需恰好 1 处）" % hits))
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            rc = run_tests(target)
            results.append((mid, desc, target, rc, rc != 0, ""))
    finally:
        for path, bak in backups.items():
            shutil.copyfile(bak, path)
            os.remove(bak)

    base_rc = run_tests("test_heatmap_contract_is_last_round_of_loop")

    lines = ["| 变异体 | 变异内容 | 目标用例 | 退出码 | 判定 |",
             "|---|---|---|---|---|"]
    bad = 0
    for mid, desc, target, rc, red, note in results:
        if note:
            bad += 1
            lines.append("| %s | %s | `%s` | - | 变异体未施加：%s |"
                         % (mid, desc, target, note))
            continue
        bad += 0 if red else 1
        lines.append("| %s | %s | `%s` | %d | %s |"
                     % (mid, desc, target, rc,
                        "变红 OK" if red else "假绿 BAD"))
    lines.append("| BASELINE | 还原生产代码后重跑同一用例 | "
                 "`test_heatmap_contract_is_last_round_of_loop` | %d | %s |"
                 % (base_rc, "变绿 OK" if base_rc == 0 else "基线不绿 BAD"))
    lines.append("")
    lines.append("变异体检出 %d / %d；基线恢复绿 = %s"
                 % (len(MUTANTS) - bad, len(MUTANTS), base_rc == 0))
    report = "\n".join(lines) + "\n"
    with open(os.path.join(ROOT, "_mut_report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(ROOT, "_mut_status.txt"), "w", encoding="ascii") as f:
        f.write("detected=%d/%d baseline_green=%s bad=%d\n"
                % (len(MUTANTS) - bad, len(MUTANTS), base_rc == 0, bad))
    return 1 if (bad or base_rc != 0) else 0


if __name__ == "__main__":
    sys.exit(main())
