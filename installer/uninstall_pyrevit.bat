@echo off
title Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Uninstaller
echo ==========================================================
echo   Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Uninstaller
echo ==========================================================
echo.

set "EXTENSION_DIR=%APPDATA%\pyRevit\Extensions\R2R.extension"

if exist "%EXTENSION_DIR%" (
    echo Removing pyRevit extension: %EXTENSION_DIR%
    rd /s /q "%EXTENSION_DIR%" >nul 2>&1
    echo [SUCCESS] R2R ribbon tab removed from pyRevit.
) else (
    echo R2R pyRevit extension was not found or already removed.
)

echo.
echo Press any key to exit...
pause >nul
