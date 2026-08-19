@echo off
setlocal
cd /d "%~dp0"
call conda activate Auto_test
if errorlevel 1 exit /b %errorlevel%
python launcher.py %*
exit /b %errorlevel%
