# -*- coding: utf-8 -*-
"""修启动器 :stage：按**产物完整性**判定阶段成败，而非只看 exit code。

受控验证（bench/verify_exit_code_semantics.py）已证明：
  成功     → exit 0        + 产物 complete=true
  普通失败 → exit 3        + 无产物
  被中断   → exit 0xC000013A + **产物可能仍完整**（complete=true）

⇒ 只看 exit code 会把"已完整产出、仅被中断"误判为失败
   （本次 S3 的实际情况：rule_gap_600.json 完整，启动器却跳 gate_failed）。

修法：`:stage` 增加可选第 5 参数（期望产物路径）。
  · exit 0                          → ok
  · exit≠0 但产物存在且 complete=true → ok-with-interrupt（不跳门）
  · exit≠0 且产物缺失/不完整          → FAILED（真失败）
并把真实退出码追加进 exit_codes.txt 供审计。
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(ROOT, "run_winrate_suite.cmd")
s = io.open(p, encoding="ascii").read()

old = (
    ':stage\n'
    'rem %1=id  %2=script  %3=extra args (quoted string)  %4=log name\n'
    "rem Sets LAST_RC to the child's exit code for the caller to gate on.\n"
    'set SID=%~1\n'
    'set SCR=%~2\n'
    'set EXTRA=%~3\n'
    'set LOG=%~4\n'
    'echo      running %SID% %SCR% %EXTRA%\n'
    '"%PY%" -X utf8 %SCR% %EXTRA% > "!OUT!\\%LOG%" 2>&1\n'
    'set LAST_RC=!ERRORLEVEL!\n'
    'if not "!LAST_RC!"=="0" (\n'
    '    set /a FAILED+=1\n'
    '    echo      FAILED exit=!LAST_RC!  ^(see !OUT!\\%LOG%^)\n'
    ') else (\n'
    '    echo      ok\n'
    ')\n'
    'exit /b 0\n'
)

new = (
    ':stage\n'
    'rem %1=id  %2=script  %3=extra args  %4=log name  %5=expected json (opt)\n'
    'rem Exit code alone cannot classify a stage: a stage may finish and\n'
    'rem write complete output, then be interrupted (exit 0xC000013A).\n'
    'rem So: success = exit 0, OR (output exists AND complete=true).\n'
    'set SID=%~1\n'
    'set SCR=%~2\n'
    'set EXTRA=%~3\n'
    'set LOG=%~4\n'
    'set EXPECT=%~5\n'
    'echo      running %SID% %SCR% %EXTRA%\n'
    '"%PY%" -X utf8 %SCR% %EXTRA% > "!OUT!\\%LOG%" 2>&1\n'
    'set LAST_RC=!ERRORLEVEL!\n'
    'echo !SID!=!LAST_RC!>> "!OUT!\\exit_codes.txt"\n'
    'if "!LAST_RC!"=="0" (\n'
    '    echo      ok\n'
    '    exit /b 0\n'
    ')\n'
    'set OUTPUT_OK=0\n'
    'if not "!EXPECT!"=="" if exist "!EXPECT!" (\n'
    '    "%PY%" -X utf8 -c "import json,sys;d=json.load(open(sys.argv[1],encoding=chr(117)+chr(116)+chr(102)+chr(45)+chr(56)));sys.exit(0 if d.get(chr(99)+chr(111)+chr(109)+chr(112)+chr(108)+chr(101)+chr(116)+chr(101)) is True else 1)" "!EXPECT!" >nul 2>&1\n'
    '    if "!ERRORLEVEL!"=="0" set OUTPUT_OK=1\n'
    ')\n'
    'if "!OUTPUT_OK!"=="1" (\n'
    '    echo      ok-with-interrupt exit=!LAST_RC!\n'
    '    echo             output COMPLETE ^(complete=true^) - treated as success\n'
    '    exit /b 0\n'
    ')\n'
    'set /a FAILED+=1\n'
    'echo      FAILED exit=!LAST_RC!  ^(see !OUT!\\%LOG%^)\n'
    'echo             output missing or incomplete - real failure\n'
    'exit /b 0\n'
)

assert old in s, ":stage block not found"
s = s.replace(old, new)

# 给下游实验补第 5 参数（期望产物）
pairs = [
    ('S3_rule_gap.log', '"!RUN!\\rule_gap_600.json"'),
    ('S4_first_move.log', '"!RUN!\\first_move_1000.json"'),
    ('S5_endgame.log', '"!RUN!\\endgame_stratified_400.json"'),
    ('S6_first_click.log', '"!RUN!\\first_click_400.json"'),
    ('S6b_first_click_repl.log', '"!RUN!\\first_click_repl500.json"'),
]
n = 0
lines = s.split("\n")
for i, ln in enumerate(lines):
    for logname, exp in pairs:
        if ln.startswith("call :stage ") and ln.rstrip().endswith(logname):
            lines[i] = ln.rstrip() + " " + exp
            n += 1
s = "\n".join(lines)

io.open(p, "w", encoding="ascii", newline="\r\n").write(s)
raw = open(p, "rb").read()
txt = raw.decode("ascii", errors="replace")
print("已修 :stage（按产物完整性判定）")
print(f"  给 {n}/5 个下游阶段补期望产物参数")
print("  含 ok-with-interrupt:", "ok-with-interrupt" in txt)
print("  含 exit_codes.txt  :", "exit_codes.txt" in txt)
print("  非 ASCII:", sum(1 for b in raw if b > 127))
print("  LF-only:", len(re.findall(chr(10) + "(?<!" + chr(13) + ")", txt)))
