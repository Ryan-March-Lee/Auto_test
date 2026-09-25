@echo off
setlocal
cd /d "%~dp0"
set "AUTO_TEST_PYTHON=D:\Anaconda\envs\Auto_test\python.exe"
if /I "%CONDA_DEFAULT_ENV%"=="Auto_test" if exist "%CONDA_PREFIX%\python.exe" set "AUTO_TEST_PYTHON=%CONDA_PREFIX%\python.exe"
if not exist "%AUTO_TEST_PYTHON%" (
    echo Auto_test Python was not found: "%AUTO_TEST_PYTHON%"
    exit /b 4
)
"%AUTO_TEST_PYTHON%" launcher.py %*
exit /b %errorlevel%
