@echo off
title Rhino-to-Revit Transfer (R2R) Add-in Installer
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - One-Click Installer
echo ==========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

echo.
echo Press any key to exit...
pause >nul
