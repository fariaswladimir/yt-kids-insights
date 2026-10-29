@echo off
chcp 65001 >nul
title Analise YouTube Infantil - dados de exemplo
python -I -X utf8 "%~dp0sistema\gerar_demo.py"
echo.
echo Agora rode "1 - Processar agora.bat".
pause
