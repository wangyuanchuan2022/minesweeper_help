@echo off
rem ============================================================
rem ISOLATED WHOLE-GAME build: mscore.cpp + ab_native.cpp -> utils\trial\
rem NEVER writes utils\mscore.cp310-win_amd64.pyd (production baseline).
rem
rem Defines BOTH:
rem   AB_NATIVE_MERGED -> mscore.cpp skips its PYBIND11_MODULE; ab_native.cpp
rem                       provides the single module (run_ab entry).
rem   MSW_TRIAL_DECIDE -> compiles decision_layer::decide_impl so the whole-game
rem                       driver can call the SAME validated C++ decision logic
rem                       used by the 6/6 parity harness (no second impl).
rem ============================================================
setlocal
set PYHOME=D:\anaconda3\envs\py310
set VCVARS=D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
set PYBIND11_INC=%~dp0inc
set BASE=utils\mscore.cp310-win_amd64.pyd
set BASE_HASH=5B22EC2BDE70ACC8E64DAD8A8CBF9305970DF8EB228A744F06ED22AAC50219CD
set OUTDIR=%~dp0..\utils\trial_wholegame

if not exist "%VCVARS%" ( echo [ERROR] vcvars64.bat missing & exit /b 1 )
call "%VCVARS%" >nul
if errorlevel 1 ( echo [ERROR] vcvars64 failed & exit /b 1 )

cd /d "%~dp0.."
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

rem Guard 1: never build if production baseline is already wrong.
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash '%BASE%' -Algorithm SHA256).Hash"') do set NOW=%%H
if not "%NOW%"=="%BASE_HASH%" (
    echo [ERROR] production baseline SHA mismatch: %NOW%
    echo         Expected %BASE_HASH%. Restore before any trial build.
    exit /b 1
)

cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD /LD ^
    /D AB_NATIVE_MERGED /D MSW_TRIAL_DECIDE ^
    /I "%PYBIND11_INC%" ^
    /I "%PYHOME%\include" ^
    cpp\mscore.cpp cpp\ab_native.cpp ^
    /Fe:%OUTDIR%\mscore.cp310-win_amd64.pyd ^
    /link /LIBPATH:"%PYHOME%\libs" python310.lib

if errorlevel 1 ( echo [ERROR] whole-game trial build failed & exit /b 1 )

rem Guard 2: production baseline must be untouched after compile.
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash '%BASE%' -Algorithm SHA256).Hash"') do set AFTER=%%H
if not "%AFTER%"=="%BASE_HASH%" (
    echo [ERROR] production baseline modified during build: %AFTER%
    exit /b 1
)

del /q *.obj 2>nul
del /q cpp\*.obj 2>nul
echo [OK] whole-game trial build: %OUTDIR%\mscore.cp310-win_amd64.pyd
echo [OK] production baseline verified unchanged: %AFTER%
endlocal
