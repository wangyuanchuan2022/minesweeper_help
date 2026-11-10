@echo off
rem ============================================================
rem ISOLATED TRIAL build: outputs to utils\trial\ only.
rem NEVER writes utils\mscore.cp310-win_amd64.pyd (production baseline).
rem Defines MSW_TRIAL_DECIDE so decide() is exported for parity testing.
rem Module name stays "mscore" (PyInit_mscore); load via sys.path trick.
rem ============================================================
setlocal
set PYHOME=D:\anaconda3\envs\py310
set VCVARS=D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
set PYBIND11_INC=%~dp0inc
set BASE=utils\mscore.cp310-win_amd64.pyd
set BASE_HASH=5B22EC2BDE70ACC8E64DAD8A8CBF9305970DF8EB228A744F06ED22AAC50219CD
set OUTDIR=%~dp0..\utils\trial_decide

if not exist "%VCVARS%" ( echo [ERROR] vcvars64.bat missing & exit /b 1 )
call "%VCVARS%" >nul
if errorlevel 1 ( echo [ERROR] vcvars64 failed & exit /b 1 )

cd /d "%~dp0.."
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

rem Guard 1: refuse to build if production baseline is already wrong.
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash '%BASE%' -Algorithm SHA256).Hash"') do set NOW=%%H
if not "%NOW%"=="%BASE_HASH%" (
    echo [ERROR] production baseline SHA mismatch: %NOW%
    echo         Expected %BASE_HASH%. Restore before any trial build.
    exit /b 1
)

cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD /LD ^
    /D MSW_TRIAL_DECIDE ^
    /I "%PYBIND11_INC%" ^
    /I "%PYHOME%\include" ^
    cpp\mscore.cpp ^
    /Fe:%OUTDIR%\mscore.cp310-win_amd64.pyd ^
    /Fo:cpp\mscore_trial.obj ^
    /link /LIBPATH:"%PYHOME%\libs" python310.lib

if errorlevel 1 ( echo [ERROR] trial build failed & exit /b 1 )

rem Guard 2: confirm production baseline is STILL untouched after compile.
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash '%BASE%' -Algorithm SHA256).Hash"') do set AFTER=%%H
if not "%AFTER%"=="%BASE_HASH%" (
    echo [ERROR] production baseline was modified during build: %AFTER%
    echo         Restore from utils\mscore.cp310-win_amd64.pyd.bak_baseline
    exit /b 1
)

del /q cpp\mscore_trial.obj 2>nul
echo [OK] trial build done: %OUTDIR%\mscore.cp310-win_amd64.pyd
echo [OK] production baseline verified unchanged: %AFTER%
endlocal
