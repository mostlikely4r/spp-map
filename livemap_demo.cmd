@echo off
cd /d "%~dp0"
set PY=python
if exist "%~dp0runtime\python.exe" set PY="%~dp0runtime\python.exe"
%PY% -m livemap demo --open
pause
