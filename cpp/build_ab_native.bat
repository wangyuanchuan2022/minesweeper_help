@echo off
rem Merged build: mscore.cpp + ab_native.cpp -> one pyd (module name: mscore)
rem
rem Why merged: ab_native.cpp must call mscore.cpp internal PartSolveCtx / wr_f
rem (user-approved B1-a-1) instead of a self-written enumerator.
rem   - define AB_NATIVE_MERGED  -> mscore.cpp skips its PYBIND11_MODULE
rem   - ab_native.cpp provides the single PYBIND11_MODULE(mscore)
rem   - ab_native.cpp calls extern "C" abnative_decide() from mscore.cpp
rem
rem Output: utils\ab_native.cp310-win_amd64.pyd  (module name is still mscore)
rem Production build still uses cpp\build.bat (no macro), unchanged.
rem ============================================================
setlocal
set PYHOME=D:\anaconda3\envs\py310
set VCVARS=D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
set PYBIND11_INC=%~dp0inc

if not exist "%VCVARS%" ( echo [ERROR] vcvars64.bat missing & exit /b 1 )
call "%VCVARS%" >nul
if errorlevel 1 ( echo [ERROR] vcvars64 failed & exit /b 1 )

cd /d "%~dp0.."

cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD /LD ^
    /D AB_NATIVE_MERGED ^
    /I "%PYBIND11_INC%" ^
    /I "%PYHOME%\include" ^
    cpp\mscore.cpp cpp\ab_native.cpp ^
    /Fe:utils\ab_native.cp310-win_amd64.pyd ^
    /link /LIBPATH:"%PYHOME%\libs" python310.lib

if errorlevel 1 ( echo [ERROR] merged build failed & exit /b 1 )
del /q *.obj 2>nul
del /q cpp\*.obj 2>nul
echo [OK] merged build done: utils\ab_native.cp310-win_amd64.pyd
endlocal
