@echo off
rem Build check_mscore_exports.cpp (pure C++ verification tool)
setlocal
set PYHOME=D:\anaconda3\envs\py310
set VCVARS=D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
call "%VCVARS%" >nul
if errorlevel 1 ( echo [ERROR] vcvars64 failed & exit /b 1 )
cd /d "%~dp0.."
cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD ^
   /I "%PYHOME%\include" ^
   cpp\check_mscore_exports.cpp ^
   /Fe:cpp\check_mscore_exports.exe ^
   /Fo:cpp\check_mscore_exports.obj ^
   /link /LIBPATH:"%PYHOME%\libs" python310.lib
if errorlevel 1 ( echo [ERROR] build failed & exit /b 1 )
del /q cpp\check_mscore_exports.obj 2>nul
echo [OK] cpp\check_mscore_exports.exe
endlocal
