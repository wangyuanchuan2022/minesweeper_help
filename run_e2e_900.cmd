@echo off
REM ===========================================================================
REM  REAL end-to-end 900-game measurement (full GUI + real Arbiter window)
REM
REM  WHAT THIS DOES
REM    Runs the production GUI (main.MyMainWindow, same class, same signal
REM    wiring), the production Solver QThread (play() main loop), the real
REM    Arbiter window, real screen capture, real template matching and real
REM    mouse clicks. Nothing is simulated.
REM
REM    Telemetry (utils/e2e_probe.py, enabled by MSW_E2E_PROBE=1) records:
REM      - span timings      : screen grab / template match / board scan /
REM                            rescan / C++ decide / real click / UI render
REM      - decision telemetry: reason, ms, world_upper, situations, DSTAR
REM      - click telemetry   : cell, self-assessed safety rate, source
REM      - memory series     : RSS + commit every 500 ms
REM      - runtime coverage  : which functions actually got called
REM                            (the runtime counterpart of the static dead-code
REM                            audit in bench\audit_dead_code.py)
REM
REM  PREREQUISITES (please check BEFORE running)
REM    1. Minesweeper Arbiter is already running and visible.
REM    2. Display / DPI match cfg.json (bx=12, by=141, cell_width=24).
REM       This project is calibrated on a 1600x2560 screen.
REM    3. The Arbiter window is NOT covered by other windows.
REM
REM  WALL TIME (measured, 2026-11-09): 6.30 s/game after the I/O
REM  optimizations (was 9.16 s/game before). 900 games ~95 min.
REM  Telemetry file cap is 128 MB (MSW_E2E_MAX_MB); the old 20 MB cap
REM  truncated at ~253 games and silently dropped the rest of the run.
REM  STOP ANYTIME (the mouse is driven by the program, so GUI buttons are
REM  hard to click while it runs):
REM    - Press ESC  : emergency stop hotkey (physical key, no mouse/focus needed)
REM    - Ctrl+C     : same cooperative cancel path
REM    Override key: set MSW_STOP_HOTKEY=0x7B for F12, or =0 to disable.
REM  Resumable: after stopping, re-run this file to continue.
REM
REM  OUTPUT
REM    state\e2e\probe.jsonl          raw telemetry events
REM    state\e2e\probe_summary.json   process summary
REM    state\e2e_900\games.jsonl      one line per finished game
REM    state\e2e_900\summary.json     aggregate (win rate, CI, wall time, mem)
REM    state\e2e_900\report.md        analysed report (4 ledgers)
REM ===========================================================================
setlocal

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set MSW_E2E_PROBE=1
set MSW_E2E_MEM_HZ=2
REM Event-driven end-of-game restart is the default. For an A/B comparison
REM against the old fixed sleep chain, set MSW_LEGACY_SLEEP=1 before running.
REM Emergency hotkey: ESC by default (set MSW_STOP_HOTKEY=0x7B for F12, 0=off).
set PY=D:\anaconda3\envs\py310\python.exe

cd /d "%~dp0"

if not exist "%PY%" (
    echo [ERROR] python not found: %PY%
    pause
    exit /b 1
)

echo ============================================================
echo  REAL end-to-end 900-game measurement
echo  started at %DATE% %TIME%
echo ============================================================
echo.
echo  [1/3] Preflight: Arbiter window, native module, board capture
echo.

"%PY%" bench\e2e_900.py --games 900 --tag e2e900 --resume --max-seconds 7200
set RC=%ERRORLEVEL%

echo.
echo  [2/3] Analysing telemetry
echo.
"%PY%" bench\analyze_e2e.py --jsonl state\e2e\probe.jsonl --md state\e2e_900\report.md --json state\e2e_900\report.json
set RC2=%ERRORLEVEL%

echo.
echo  [3/3] Static dead-code / redundancy audit
echo.
"%PY%" bench\audit_dead_code.py
set RC3=%ERRORLEVEL%

echo.
echo ============================================================
echo  finished at %DATE% %TIME%
echo  e2e runner  exit code=%RC%
echo  analyser    exit code=%RC2%
echo  audit       exit code=%RC3%
echo ============================================================
echo.
echo  Reports:
echo    state\e2e_900\report.md      (performance / accuracy / function / memory)
echo    state\audit\dead_code.md     (dead code + merge candidates)
echo.
if %RC% neq 0 (
    echo [WARN] runner exited non-zero. Finished games are already saved;
    echo        re-run this file to resume from where it stopped.
)
pause
exit /b %RC%
