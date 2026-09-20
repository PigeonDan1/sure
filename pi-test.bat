@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "POWERSHELL_EXE=powershell.exe"

where node >nul 2>nul
if errorlevel 1 (
	>&2 echo node was not found on PATH. Install Node 22.19 or newer ^(see .nvmrc^).
	exit /b 1
)

where %POWERSHELL_EXE% >nul 2>nul
if errorlevel 1 (
	>&2 echo powershell.exe not found. Install PowerShell or run pi-test.ps1 directly.
	exit /b 1
)

%POWERSHELL_EXE% -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%pi-test.ps1" %*
exit /b %ERRORLEVEL%
