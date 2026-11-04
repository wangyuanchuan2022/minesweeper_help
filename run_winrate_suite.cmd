@echo off
rem ============================================================
rem  Win-rate 50%% goal : full verification suite  (v2)
rem
rem  This file MUST live in the project root (same folder as bench\).
rem  The working dir is set from %~dp0; if this file were moved under
rem  bench\, every "bench\x.py" reference would resolve to
rem  "bench\bench\x.py" and every stage would fail.
rem
rem  ------------------------------------------------------------------
rem  GATE POLICY (v2, changed from v1)
rem  ------------------------------------------------------------------
rem  S0/S1/S2 are HARD GATES: they verify the instruments (probes,
rem  oracle, enumerator) and the harness determinism. If any gate
rem  fails, S3-S7 are SKIPPED and the failed log is kept. With a broken
rem  instrument or a non-deterministic harness, every downstream number
rem  is untrustworthy, so continuing would only burn 2.5-3 hours
rem  producing results that look complete but cannot be used.
rem
rem  S3-S7 are independent downstream experiments: a failure in one does
rem  not stop the others.
rem  ------------------------------------------------------------------
rem
rem  RESULTS ARE NEVER OVERWRITTEN. Each run creates
rem      state\ab\runs\<stamp>\   (all JSON + config + code version)
rem      state\suite\<stamp>\     (all stage logs)
rem  so re-running cannot clobber a previous run's per-game data.
rem
rem  Approximate wall time (32 cores / 32 GB), from the dry run:
rem     S0 ~1 min | S1 ~2 min | S2 ~6 min | S3 ~35 min
rem     S4 ~35 min | S5 ~30 min | S6 ~60-90 min | S7 ~60-90 min
rem  Total roughly 4-5 hours.
rem ============================================================
setlocal enabledelayedexpansion

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

rem ---- timestamped run directory (never overwrite a previous run) ----
rem  Do NOT use wmic: absent on Windows 11. When missing, STAMP stays
rem  empty, mkdir fails silently, every log redirect fails, yet the
rem  suite still exits 0 -- a false green. Use Python for the stamp.
rem  Avoid nested quotes inside for/f: cmd pre-parses the command part,
rem  so '"%PY%" -c "..."' breaks (the inner quote terminates early).
rem  Write the stamp to a temp file, then read it with set /p.
set STAMP_TMP=%TEMP%\_wr_stamp_%RANDOM%.txt
"%PY%" -c "import datetime;print(datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'))" > "%STAMP_TMP%" 2>nul
set /p STAMP=<"%STAMP_TMP%"
del "%STAMP_TMP%" 2>nul
if "!STAMP!"=="" (
    echo [ERROR] could not build a run timestamp ^(python unavailable?^)
    pause
    exit /b 1
)
set RUN=state\ab\runs\!STAMP!
set OUT=state\suite\!STAMP!
mkdir "!RUN!" 2>nul
mkdir "!OUT!" 2>nul
rem  Hard sanity check: if these dirs cannot be created, every stage
rem  would report ok while writing nowhere (false green). Abort.
if not exist "!RUN!\" (
    echo [ERROR] cannot create run dir: !RUN!
    pause
    exit /b 1
)
if not exist "!OUT!\" (
    echo [ERROR] cannot create log dir: !OUT!
    pause
    exit /b 1
)

set FAILED=0
set LAST_RC=0

echo ============================================================
echo  Win-rate verification suite  (v2)
echo  started %DATE% %TIME%
echo  cwd   = %CD%
echo  run   = !RUN!
echo  logs  = !OUT!
echo ============================================================
echo.

echo [prep] recording code version and config ...
"%PY%" -X utf8 bench\record_run_manifest.py --out "!RUN!" > "!OUT!\manifest.log" 2>&1
echo       done
echo.

rem ============================================================
rem  HARD GATES  (any failure stops the suite)
rem ============================================================
echo ---------- HARD GATES ----------
echo.

echo [S0] smoke: every script runnable ...
call :stage S0 bench\smoke_all_checks.py "" S0_smoke.log
if not "!LAST_RC!"=="0" goto :gate_failed

echo [S1] instrument self-checks ...
call :stage S1a bench\verify_first_click_wiring.py "" S1a_first_click_wiring.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1b bench\verify_flood_chain.py "" S1b_flood_chain.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1c bench\verify_probe_chain.py "" S1c_probe_chain.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1d bench\verify_endgame_safety.py "" S1d_endgame_pairing.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1e bench\verify_expand_score_degenerate.py "" S1e_expand_score.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1f bench\verify_first_click_wiring.py "" S1f_first_click_wiring.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S1g bench\atomic_decision_events.py "--selftest" S1g_atomic_selftest.log
if not "!LAST_RC!"=="0" goto :gate_failed

echo [S2] harness determinism ...
call :stage S2a bench\verify_seed_determinism.py "--games 60" S2a_seed_determinism.log
if not "!LAST_RC!"=="0" goto :gate_failed
call :stage S2b bench\verify_workers_determinism.py "--games 60" S2b_workers_determinism.log
if not "!LAST_RC!"=="0" goto :gate_failed

echo.
echo ---------- GATES PASSED, running experiments ----------
echo.

rem ============================================================
rem  DOWNSTREAM EXPERIMENTS (independent; one failure does not stop others)
rem ============================================================
echo [S3] rule gap, 600 games paired ...
call :stage S3 bench\rule_gap.py "--games 600 --seed 20261102 --out !RUN!\rule_gap_600.json" S3_rule_gap.log

echo [S4] first-move quality, 1000 games ...
rem  1000 (not 600): confirming the +3.39pp first-move gap needs ~1000 games.
rem  At n=1000 near p=0.21 the 95%% CI half-width is about 2.5pp. Any
rem  conclusion must be phrased "not detected at this sample size" unless
rem  the effect clears the MDE printed by the script.
call :stage S4 bench\analyze_first_move_quality.py "--games 1000 --seed 20265000 --out !RUN!\first_move_1000.json" S4_first_move.log

echo [S5] endgame quality (truth + uniform prior), 400 games ...
rem  NOTE: the old analyze_endgame_quality.py is RETIRED. It paired
rem  flush pending with prob_map across calls, which is structurally
rem  wrong (free-inference clicks get mixed in; _last_heatmap is
rem  overwritten). See review/endgame measurement doc.
rem  This version records clicks atomically at click time and judges
rem  quality by true mine hits vs the uniform-prior rate R/U.
call :stage S5 bench\measure_endgame_stratified.py "--games 400 --seed 20267000 --out !RUN!\endgame_stratified_400.json" S5_endgame.log
call :stage S5b bench\diag_pm_coverage.py "--games 30" S5b_pm_coverage.log

echo [S6] first-click position A/B, 400 games ...
rem  The only untested high-leverage variable left. Under Arbiter deferred
rem  placement only the first CELL is guaranteed safe, so a corner has just
rem  3 neighbours and P(first number == 0) = 49.85%%, versus 15.45%% at the
rem  centre -- which is what production hardcodes.
call :stage S6 bench\ab_first_click.py "--games 400 --seed 20269000 --out !RUN!\first_click_400.json" S6_first_click.log

echo [S6b] first-click replication, independent seeds ...
call :stage S6b bench\ab_first_click.py "--games 500 --seed 20270000 --arms center,corner01 --out !RUN!\first_click_repl500.json" S6b_first_click_repl.log

echo [S6c] pool both first-click rounds ...
call :stage S6c bench\pool_first_click.py "" S6c_pool_first_click.log

echo [S7] tiebreak A/B, 400 games ...
call :stage S7 bench\ab_tiebreak.py "--games 400 --seed 20268000 --arms baseline,open_plus_frontier --label suite" S7_tiebreak.log

echo.
echo ============================================================
echo  finished %DATE% %TIME%   stages_failed=!FAILED!
echo ============================================================
echo.
echo Run directory : !RUN!
echo Stage logs    : !OUT!
echo.
if not "!FAILED!"=="0" (
    echo [WARN] !FAILED! experiment^(s^) failed. Other stages already wrote
    echo        their JSON into !RUN!; re-run the failed one individually.
)
echo JSON written in !RUN! :
dir /b "!RUN!\*.json" 2>nul
echo.
pause
exit /b 0

rem ============================================================
:gate_failed
echo.
echo ============================================================
echo  HARD GATE FAILED -- suite stopped on purpose
echo ============================================================
echo.
echo  A gate stage (S0/S1/S2) failed. Those stages verify the
echo  instruments and the test harness, not the algorithm.
echo.
echo  Continuing would run S3-S7 for ~4-5 hours and produce numbers
echo  that cannot be trusted: a broken probe or a non-deterministic
echo  harness invalidates every downstream reading.
echo.
echo  The failed stage log is kept in !OUT!\
echo  Fix the instrument first, then re-run this file.
echo  A fresh run directory will be created; this one is kept.
echo.
pause
exit /b 1

rem ============================================================
:stage
rem %1=id  %2=script  %3=extra args (quoted string)  %4=log name
rem Sets LAST_RC to the child's exit code for the caller to gate on.
set SID=%~1
set SCR=%~2
set EXTRA=%~3
set LOG=%~4
echo      running %SID% %SCR% %EXTRA%
"%PY%" -X utf8 %SCR% %EXTRA% > "!OUT!\%LOG%" 2>&1
set LAST_RC=!ERRORLEVEL!
if not "!LAST_RC!"=="0" (
    set /a FAILED+=1
    echo      FAILED exit=!LAST_RC!  ^(see !OUT!\%LOG%^)
) else (
    echo      ok
)
exit /b 0
