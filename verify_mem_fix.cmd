@echo off
REM ===========================================================================
REM  Memory-safety acceptance -- 2000-game OOM root-cause fixes
REM
REM  Background (real incident): running 2000 games consumed 20+ GB and OOM'd.
REM  Four root causes were found and fixed (see CHANGELOG):
REM    1) monkey-patch closure chain: wrappers nested per game, never restored
REM       => every finished game's Solver/Board stayed alive (O(N) leak +
REM          O(N^2) call overhead)
REM    2) world enumeration had NO cap and built set[tuple] per world:
REM       114,870 worlds x ~4KB = ~460 MB just for construction
REM    3) world-count check happened AFTER construction => never effective
REM    4) illegal positions (U=471/num10=0 from recognition failure) produced
REM       10^148 worlds; must be rejected as invalid before any math
REM
REM  Gates below must be green:
REM    A) verify_world_cap.py     cap enforced BEFORE allocation; illegal
REM                               positions rejected; unlimited path unchanged
REM    B) verify_mem_safety.py    real RSS: single-game peak under budget,
REM                               no cross-game accumulation
REM    C) oracle_bruteforce.py    numeric parity unchanged (3 impls agree)
REM    D) verify_world_enum.py    conservation assertions still hold
REM
REM  Usage: double-click this file, or run verify_mem_fix.cmd
REM ===========================================================================
setlocal
cd /d "%~dp0"

set PY=D:\anaconda3\envs\py310\python.exe
set QT_QPA_PLATFORM=offscreen
set PYTHONIOENCODING=utf-8

echo ============================================================
echo  Memory-safety acceptance (2000-game OOM fixes)
echo ============================================================
echo.

echo ------------------------------------------------------------
echo [A] world cap + illegal-position gate
echo ------------------------------------------------------------
"%PY%" bench\verify_world_cap.py
if errorlevel 1 goto FAILED

echo.
echo ------------------------------------------------------------
echo [B] real-RSS memory safety (25 games)
echo ------------------------------------------------------------
"%PY%" bench\verify_mem_safety.py --games 25 --arm safety
if errorlevel 1 goto FAILED

echo.
echo ------------------------------------------------------------
echo [C] numeric parity unchanged (3 implementations)
echo ------------------------------------------------------------
"%PY%" bench\oracle_bruteforce.py
if errorlevel 1 goto FAILED

echo.
echo ------------------------------------------------------------
echo [D] world enumeration conservation
echo ------------------------------------------------------------
"%PY%" bench\verify_world_enum.py
if errorlevel 1 goto FAILED

echo.
echo ============================================================
echo  All gates completed
echo ============================================================
pause
exit /b 0

:FAILED
echo.
echo ============================================================
echo  [FAILED] a gate above did not pass; do not claim the fix
echo ============================================================
pause
exit /b 1
