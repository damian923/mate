@echo off
title Instalando Mate
echo Instalando Mate... esto tarda unos minutos (baja ~2 GB la primera vez).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
echo Abriendo Mate...
start "" pythonw "%~dp0mate.py"
pause
