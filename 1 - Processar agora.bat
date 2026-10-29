@echo off
chcp 65001 >nul
title Analise YouTube Infantil - processar
python -I -X utf8 "%~dp0sistema\processar.py"
echo.
pause
