@echo off
REM ===========================================================================
REM  Baseline win-rate measurement for each policy arm -- 2000 games per arm
REM
REM  Usage: double-click this file, or:
REM      cd /d D:\python\projects\minesweeper_help
REM      run_baselines.cmd
REM
REM  Rough time (0.5s/game, 12 workers): 8-15 min per arm, about 1-1.5h total.
REM  For a quick smoke run, lower GAMES below (e.g. 100).
REM
REM  NOTE: this does NOT measure memory. Run verify_node_cap.cmd first to
REM        confirm the memory guard is active.
REM ===========================================================================
setlocal
cd /d "%~dp0"

set PY=D:\anaconda3\envs\py310\python.exe
set GAMES=2000
set WORKERS=12

set QT_QPA_PLATFORM=offscreen
set PYTHONIOENCODING=utf-8

if not exist "%~dp0state\ab" mkdir "%~dp0state\ab"

echo ============================================================
echo  Baseline win rate per arm (%GAMES% games each, %WORKERS% workers)
echo ============================================================
echo.

for %%A in (baseline safety expand ply2 ply3 exact) do (
    echo ------------------------------------------------------------
    echo [arm] %%A  start %TIME%
    echo ------------------------------------------------------------
    "%PY%" bench\ab_policy.py --games %GAMES% --workers %WORKERS% --arm %%A --out state\ab\arm_%%A.json
    if errorlevel 1 (
        echo [WARN] arm %%A failed, continuing
    )
)

echo.
echo ============================================================
echo  All done %TIME%
echo  Results: state\ab\arm_*.json
echo  Next step: summarize
echo     "%PY%" bench\compare_all_arms.py
echo ============================================================
pause
