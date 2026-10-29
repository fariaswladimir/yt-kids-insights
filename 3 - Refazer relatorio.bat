@echo off
chcp 65001 >nul
title Analise YouTube Infantil - refazer relatorio
python -I -X utf8 "%~dp0sistema\processar.py" --refazer
echo.
pause
