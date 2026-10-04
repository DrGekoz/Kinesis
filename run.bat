@echo off
title Kinesis
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv

REM -------------------------------------------------------------------------------------
REM Every run now does this, in this order:
REM   1. opens the settings window - pick the screens for this session, then Save
REM   2. runs the gaze calibration wizard on those screens
REM   3. starts tracking
REM
REM Both startup steps can be skipped for a fast start:
REM   run.bat --no-settings --no-calibrate
REM   run.bat --calibration-mode quick     one dot per screen instead of a full grid
REM
REM Any other Kinesis flag is passed straight through:
REM   run.bat --dry                          log every action, inject nothing
REM   run.bat --latency-report               print fps and ms every 5s
REM   run.bat --tune deadband_px=2.2 --save-config
REM -------------------------------------------------------------------------------------

".venv\Scripts\python.exe" -u kinesis.py %*
goto :eof

:novenv
echo No .venv found in this folder.
echo Create it first:
echo.
echo   python -m venv .venv
echo   .venv\Scripts\pip install mediapipe==0.10.20 opencv-contrib-python==4.10.0.84 numpy==1.26.4
echo.
pause
