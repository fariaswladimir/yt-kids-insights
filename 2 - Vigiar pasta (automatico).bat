@echo off
chcp 65001 >nul
title Analise YouTube Infantil - vigiando a pasta entrada
python -I -X utf8 "%~dp0sistema\processar.py" --monitorar
pause
