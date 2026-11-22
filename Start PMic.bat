@echo off
title PMic
cd /d "%~dp0"
python server.py --port=8787
pause
