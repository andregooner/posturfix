@echo off
title PosturFix
echo ========================================================
echo  PosturFix - 100%% Offline Posture Monitor
echo ========================================================
call .venv\Scripts\activate.bat
python app.py
pause
