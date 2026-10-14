@echo off
title Rhino-to-Revit Transfer (R2R) Add-in Uninstaller
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller
echo ==========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"

echo.
echo Press any key to exit...
pause >nul
