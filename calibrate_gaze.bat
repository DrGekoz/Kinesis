@echo off
title Kinesis gaze calibration
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv
echo Kinesis gaze calibration.
echo Sit as you normally do. A dot will appear on each monitor in turn.
echo Keep your head still and just look at the dot. Do not move your head to follow it.
echo.
pause
".venv\Scripts\python.exe" -u calibrate_gaze.py %*
echo.
pause
goto :eof

:novenv
echo No .venv found in this folder - run check.bat first for setup instructions.
pause
