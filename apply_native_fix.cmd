@echo off
REM ============================================================
REM Install the rebuilt C++ native module
REM
REM This build contains the help-page preview fix:
REM   *) Help mode (is_play=False) now returns REAL state
REM      transitions for EVERY decision layer (certain_safe /
REM      win_rate / pbs / limitation / optimal), computed by
REM      exact world enumeration + ExactEngine::click_outcomes.
REM      Each observable branch carries its true conditional
REM      probability AND a full 7x7 outcome board, so the popup
REM      can draw several reachable local boards with the
REM      probability labelled above each one.
REM   *) When the world set is too large to enumerate exactly
REM      (midgame), NO transition is computed at all: a precise
REM      reason is returned instead of any sampling or estimate.
REM      Per user decision, midgame does not compute state transitions.
REM   *) OPTIMAL layer already returned per-cell safety prob
REM      and real transitions in the previous build.
REM   *) Auto mode is untouched (no transitions, heatmap only).
REM   *) decide() now also returns the real "limitation" value
REM      (decision difficulty) so Python can drive the adaptive
REM      win_rate threshold from the 30s budget. Install this
REM      build or the adaptive chain stays disconnected.
REM   *) transitions_for(cv,a,w,h,x,y): per-hovered-cell transitions.
REM      Reuses the world set cached by the last decide() call.
REM   *) 7x7 branch boards now use the REAL cell_value for opened
REM      cells (digits are NOT recomputed from a consistent world,
REM      which used to show wrong digits) and preserve user flags
REM      (10 = flagged mine, 11 = deduced safe).
REM   *) FIX (2026-11-08): digit 8 was excluded from frontier
REM      GROUPING (0<v<8) while run_part_solve built constraints
REM      with 1<=v<=8. Digit 8 means "all 8 neighbours are mines"
REM      -- the STRONGEST local constraint -- so dropping it made
REM      any frontier cell whose only neighbouring digit is 8
REM      form a single-cell group with an unsatisfiable constraint
REM      => part_solve found zero solutions => decide returned
REM      reason=empty_group, x=y=-1. Real-machine symptom was the
REM      endgame log repeating "decide: decision unavailable"
REM      with no recommended cell at all. Midgame hid the bug
REM      (frontier cells usually touch several digits 1..7 and
REM      still merged); only the sparse endgame frontier exposed
REM      it. Fixed by unifying ALL grouping predicates to
REM      1<=v<=8 in both decide_impl and py_transitions.
REM
REM utils\mscore.cp310-win_amd64.pyd is locked while the app is
REM running, so run this script AFTER closing the program.
REM
REM Usage: close the app -> double-click this file -> restart
REM ============================================================
setlocal
cd /d "%~dp0"

set SRC=cpp\mscore_new.pyd
set DST=utils\mscore.cp310-win_amd64.pyd

if not exist "%SRC%" (
    echo [ERROR] Rebuilt module not found: %SRC%
    echo         Rebuild first: cpp\build.bat
    exit /b 1
)

if not exist "%DST%" (
    copy /y "%SRC%" "%DST%" >nul
    echo [OK] Installed native module
    exit /b 0
)

copy /y "%DST%" "%DST%.bak_before_transitions" >nul

copy /y "%SRC%" "%DST%" >nul
if errorlevel 1 (
    echo [ERROR] Replace failed: target is still locked.
    echo         Make sure the app ^(python.exe^) is fully closed.
    exit /b 1
)

echo [OK] Native module installed
echo      Help mode: real state transitions on all decision layers
echo      Auto mode: limitation returned for adaptive threshold
echo      Backup: %DST%.bak_before_transitions
endlocal
