@echo off
cd /d "%~dp0"
call conda activate VISA_demo
python launcher.py
