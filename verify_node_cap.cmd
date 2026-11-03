@echo off
REM ===========================================================================
REM  node_cap memory-safety gate -- run all three acceptance checks
REM
REM  Background: win_rate's memo is write-only (never evicted), so node count
REM  grows combinatorially and can blow up memory. A hard node_cap was added in
REM  C++; exceeding it throws, and Python degrades to the greedy branch.
REM
REM  All three gates must pass:
REM    1) verify_node_cap.py         default unchanged + must throw + message ok
REM    2) verify_node_cap_e2e.py     exception crosses call chain into safe
REM                                  degradation + counter observable + normal
REM                                  (non-cap) exceptions not swallowed
REM    3) verify_node_cap_default.py production default cap bit-identical
REM  Calibration (optional, slow): calibrate_node_cap.py
REM
REM  Usage: double-click this file, or cd to this dir and run verify_node_cap.cmd
REM ===========================================================================
setlocal
cd /d "%~dp0"

set PY=D:\anaconda3\envs\py310\python.exe
set QT_QPA_PLATFORM=offscreen
set PYTHONIOENCODING=utf-8

echo ============================================================
echo  node_cap memory-safety acceptance
echo ============================================================
echo.

"%PY%" -c "import utils.probability as p; print('default cap =', p._wr_node_cap())"

echo.
echo ------------------------------------------------------------
echo [1/3] default unchanged + must throw on exceed
echo ------------------------------------------------------------
"%PY%" bench\verify_node_cap.py
if errorlevel 1 goto FAILED

echo.
echo ------------------------------------------------------------
echo [2/3] end-to-end degradation + negative test
echo ------------------------------------------------------------
"%PY%" bench\verify_node_cap_e2e.py
if errorlevel 1 goto FAILED

echo.
echo ------------------------------------------------------------
echo [3/3] production default cap bit-identical to unlimited
echo ------------------------------------------------------------
"%PY%" bench\verify_node_cap_default.py
if errorlevel 1 goto FAILED

echo.
echo ============================================================
echo  All three gates completed
echo  To re-calibrate cap (about 1-2 min):
echo     "%PY%" bench\calibrate_node_cap.py
echo ============================================================
pause
exit /b 0

:FAILED
echo.
echo ============================================================
echo  [FAILED] a gate above did not pass; do not claim acceptance
echo ============================================================
pause
exit /b 1
