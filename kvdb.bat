@echo off
setlocal enabledelayedexpansion

set "PY_EXE="

rem 1. Check QGIS Python first since it is a known working installation
if exist "C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe" (
    set "PY_EXE=C:\Program Files\QGIS 4.2.1\apps\Python312\python.exe"
    goto :run
)

rem 2. Check py launcher
py -3 -c "import sys" >nul 2>nul
if !ERRORLEVEL! equ 0 (
    set "PY_EXE=py -3"
    goto :run_py
)

rem 3. Check python.exe but ensure it is not the Windows Store 0-byte stub
for /f "tokens=*" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i /c:"WindowsApps" >nul
    if !ERRORLEVEL! neq 0 (
        "%%i" -c "import sys" >nul 2>nul
        if !ERRORLEVEL! equ 0 (
            set "PY_EXE=%%i"
            goto :run
        )
    )
)

:run
if not defined PY_EXE (
    echo ERROR: Working Python 3 installation not found! 1>&2
    exit /b 1
)
"%PY_EXE%" "%~dp0kvdb.py" %*
exit /b %ERRORLEVEL%

:run_py
py -3 "%~dp0kvdb.py" %*
exit /b %ERRORLEVEL%
