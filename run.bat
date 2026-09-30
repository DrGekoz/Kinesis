@echo off
title Kinesis
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv
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
