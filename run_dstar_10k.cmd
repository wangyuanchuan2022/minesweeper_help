@echo off
REM ===========================================================================
REM  DSTAR 10k-game discrimination test (400 / 800 / 1200 / 2000, serial)
REM
REM  Run this in YOUR OWN terminal (measured ~1.6x faster than the sandbox).
REM  Resumable: press Ctrl+C anytime; re-run this file to continue.
REM
REM  Expected wall time (terminal, extrapolated from measured rate):
REM      DSTAR=400  : ~45 min
REM      DSTAR=800  : ~68 min
REM      DSTAR=1200 : ~90 min
REM      DSTAR=2000 : ~143 min
REM      total      : ~5.8 hours
REM
REM  Progress: state\dstar_10k\arm_<tier>.json  (flushed every 500 games)
REM  Report:   McNemar paired test is printed automatically at the end.
REM ===========================================================================
setlocal
cd /d "%~dp0"

set PY=D:\anaconda3\envs\py310\python.exe
set PYTHONIOENCODING=utf-8
set QT_QPA_PLATFORM=offscreen

if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    pause
    exit /b 1
)

echo ============================================================
echo  DSTAR 1e4 games discrimination test (serial, resumable)
echo  Python : %PY%
echo  Progress: state\dstar_10k\
echo ============================================================
echo.
echo  Press Ctrl+C anytime to interrupt; re-run to resume.
echo.

"%PY%" bench\dstar_10k_serial.py --games 10000 --tiers 400 800 1200 2000 --resume

echo.
echo ============================================================
if errorlevel 1 (
    echo  [DONE WITH ERRORS] see output above
) else (
    echo  [OK] all tiers finished; McNemar report is above
)
echo ============================================================
pause
endlocal
