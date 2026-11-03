@echo off
rem ============================================================
rem 2000-game win rate test (baseline arm, production path)
rem
rem Double-click to run. Results are written incrementally, so an
rem interruption does not lose completed games; re-run to resume.
rem
rem Output:
rem   state\ab\sweep_node_baseline\node_2000000.jsonl   (one line per game)
rem   state\ab\sweep_node_baseline\sweep_summary.json  (summary)
rem   console: win rate, 95%% CI, median/p90/p99 time, peak RSS
rem ============================================================
setlocal

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set QT_QPA_PLATFORM=offscreen
set PY=D:\anaconda3\envs\py310\python.exe

cd /d "%~dp0"

if not exist "%PY%" (
    echo [ERROR] python not found: %PY%
    pause
    exit /b 1
)

echo ============================================================
echo  2000-game win rate test (arm=baseline, node_cap=2000000)
echo  started at %DATE% %TIME%
echo ============================================================
echo.

rem --time-budget-s 0 = no per-game time cap (required, else 2000 games truncates)
"%PY%" bench\sweep_cap.py --axis node --values 2000000 --games 2000 --arm baseline --time-budget-s 0 --resume

set RC=%ERRORLEVEL%
echo.
echo ============================================================
echo  finished at %DATE% %TIME%  exit code=%RC%
echo ============================================================
if %RC% neq 0 (
    echo [WARN] non-zero exit. Completed games are already saved;
    echo        re-run this file to resume from where it stopped.
)
pause
exit /b %RC%
