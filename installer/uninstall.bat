@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller
echo ==========================================================
echo.

set "ADDINS_ROOT=%APPDATA%\Autodesk\Revit\Addins"

if not exist "%ADDINS_ROOT%" (
    echo [ERROR] Revit Addins directory not found.
    pause
    exit /b 0
)

echo Removing Rhino-to-Revit Transfer add-in from all Revit versions...

for /d %%D in ("%ADDINS_ROOT%\20*") do (
    set "YEAR=%%~nxD"
    set "TARGET_DIR=%%D\RhinoToRevitTransfer"
    set "TARGET_ADDIN=%%D\RhinoToRevitTransfer.addin"
    
    if exist "!TARGET_ADDIN!" (
        del /f /q "!TARGET_ADDIN!" >nul 2>&1
        echo   -^> Removed manifest from Revit !YEAR!
    )
    if exist "!TARGET_DIR!" (
        rd /s /q "!TARGET_DIR!" >nul 2>&1
        echo   -^> Removed plugin folder from Revit !YEAR!
    )
)

echo.
echo ==========================================================
echo              UNINSTALLATION COMPLETE!
echo ==========================================================
echo RhinoToRevitTransfer has been cleanly removed from Revit.
echo.
echo Press any key to exit...
pause >nul
