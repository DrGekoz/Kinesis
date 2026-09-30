@echo off
title Kinesis aim calibration
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv
".venv\Scripts\python.exe" -u calibrate.py
pause
goto :eof

:novenv
echo No .venv found in this folder - run check.bat first for setup instructions.
pause
