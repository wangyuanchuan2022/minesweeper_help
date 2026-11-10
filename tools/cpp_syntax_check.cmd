@echo off
REM ============================================================
REM Syntax-only check of cpp/mscore.cpp (NO .obj/.pyd produced).
REM Same includes/defines as cpp/build.bat but /Zs instead of /LD.
REM Run BEFORE rebuilding the production pyd.
REM
REM Required env vars (no machine-specific paths are hardcoded here):
REM   PYHOME  = Python 3.10 env root (must contain include\Python.h)
REM   VCVARS  = full path to vcvars64.bat (MSVC 2022)
REM
REM Example (run in your own terminal):
REM   set PYHOME=YOUR_PY310_ENV_ROOT
REM   set VCVARS=YOUR_MSVC_VCVARS64_BAT
REM   tools\cpp_syntax_check.cmd
REM ============================================================
setlocal
set PYBIND11_INC=%~dp0..\cpp\inc

if "%PYHOME%"=="" goto no_pyhome
if "%VCVARS%"=="" goto no_vcvars
if not exist "%VCVARS%" goto no_vcvars_file
if not exist "%PYBIND11_INC%\pybind11\pybind11.h" goto no_pybind
if not exist "%PYHOME%\include\Python.h" goto no_python_h

call "%VCVARS%" >nul
if errorlevel 1 goto vcvars_fail
cd /d "%~dp0.."
cl /nologo /Zs /utf-8 /std:c++17 /EHsc /MD ^
   /D MSW_TRIAL_DECIDE ^
   /I "%PYBIND11_INC%" ^
   /I "%PYHOME%\include" ^
   cpp\mscore.cpp
echo CL_EXIT=%ERRORLEVEL%
exit /b %ERRORLEVEL%

:no_pyhome
echo [ERROR] PYHOME is not set. Point it to your Python 3.10 env root.
exit /b 1
:no_vcvars
echo [ERROR] VCVARS is not set. Point it to vcvars64.bat (MSVC 2022).
exit /b 1
:no_vcvars_file
echo [ERROR] vcvars64.bat not found.
exit /b 1
:no_pybind
echo [ERROR] vendored pybind11 headers not found.
exit /b 1
:no_python_h
echo [ERROR] Python.h not found under PYHOME include dir.
exit /b 1
:vcvars_fail
echo [ERROR] vcvars64 init failed.
exit /b 1
