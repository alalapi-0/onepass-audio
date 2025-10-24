@echo off
title PrivateTunnel One-Click (Round 1: Windows Only)
setlocal
set "PYTHON_CMD=%PYTHON_CMD%"
if "%PYTHON_CMD%"=="" set "PYTHON_CMD=python"

%PYTHON_CMD% --version || (echo 请先安装 Python 3.8+ 并将其添加到 PATH，或在运行前设置 PYTHON_CMD && pause && exit /b)
%PYTHON_CMD% -m pip install -r requirements.txt
%PYTHON_CMD% main.py
endlocal
pause
