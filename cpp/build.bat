@echo off
rem ============================================================
rem Build script for mscore.cpp (MSVC + pybind11, target cp310)
rem Usage (from repo root):  cpp\build.bat
rem Output: utils\mscore.cp310-win_amd64.pyd
rem ============================================================
setlocal

set PYHOME=D:\anaconda3\envs\py310
set VCVARS=D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
set PYBIND11_INC=%~dp0inc

if not exist "%VCVARS%" (
    echo [ERROR] vcvars64.bat not found: %VCVARS%
    exit /b 1
)
if not exist "%PYBIND11_INC%\pybind11\pybind11.h" (
    echo [ERROR] pybind11 headers not found: %PYBIND11_INC%
    echo         Run first: pip install pybind11 --target cpp\vendor --no-cache-dir
    exit /b 1
)

call "%VCVARS%" >nul
if errorlevel 1 (
    echo [ERROR] vcvars64 init failed
    exit /b 1
)

cd /d "%~dp0.."

cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD /LD ^
    /I "%PYBIND11_INC%" ^
    /I "%PYHOME%\include" ^
    cpp\mscore.cpp ^
    /Fe:utils\mscore.cp310-win_amd64.pyd ^
    /Fo:cpp\mscore.obj ^
    /link /LIBPATH:"%PYHOME%\libs" python310.lib

if errorlevel 1 (
    echo [ERROR] build failed
    exit /b 1
)

del /q cpp\mscore.obj 2>nul
echo [OK] build done: utils\mscore.cp310-win_amd64.pyd
endlocal
