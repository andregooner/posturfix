@echo off
title Posture Guardian
echo ========================================================
echo  Posture Guardian - 100%% Offline Posture Monitor
echo ========================================================
call .venv\Scripts\activate.bat
python app.py
pause
