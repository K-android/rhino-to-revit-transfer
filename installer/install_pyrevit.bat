@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Installer
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Installer
echo    (Zero Compilation - Runs Pure Python inside Revit)
echo ==========================================================
echo.

set "PYREVIT_EXT_ROOT=%APPDATA%\pyRevit\Extensions"
set "BUTTON_DIR=%PYREVIT_EXT_ROOT%\R2R.extension\R2R.tab\Transfer.panel\OneClick.pushbutton"
set "SCRIPT_FILE=%BUTTON_DIR%\script.py"
set "BUNDLE_YAML=%BUTTON_DIR%\bundle.yaml"
pushd "%~dp0.." && set "PKG_DIR=%CD%" && popd

if not exist "%APPDATA%\pyRevit" (
    echo [ERROR] pyRevit installation not found at %APPDATA%\pyRevit
    pause
    exit /b 1
)

echo Creating pyRevit extension directory...
if not exist "%BUTTON_DIR%" mkdir "%BUTTON_DIR%" >nul 2>&1

echo Generating pyRevit script launcher...
(
    echo # -*- coding: utf-8 -*-
    echo """Rhino-to-Revit One-Click Transfer Launcher"""
    echo import sys
    echo import os
    echo pkg_path = r"%PKG_DIR%"
    echo if pkg_path not in sys.path:
    echo     sys.path.insert^(0, pkg_path^)
    echo for _mod in ['scanner', 'classifier', 'ai_assistant', 'profile_manager', 'conversion_router', 'graphics_validator', 'sync_engine', 'reporting', 'ui_dialog', 'main_launcher']:
    echo     if _mod in sys.modules:
    echo         del sys.modules[_mod]
    echo import main_launcher
    echo doc = None
    echo uidoc = None
    echo try:
    echo     if '__revit__' in globals^(^):
    echo         uidoc = __revit__.ActiveUIDocument
    echo         doc = uidoc.Document if uidoc else None
    echo except Exception:
    echo     pass
    echo main_launcher.launch_transfer^(revit_doc=doc, revit_uidoc=uidoc^)
) > "%SCRIPT_FILE%"

echo Generating button configuration...
(
    echo title: "One-Click\nTransfer"
    echo tooltip: "Launch the Rhino-to-Revit 6-stage transfer dashboard (SCAN -> CLASSIFY -> REVIEW -> TRANSFER -> VALIDATE -> SYNC)"
    echo author: "AEC Computational Design Team"
) > "%BUNDLE_YAML%"

echo.
echo ==========================================================
echo              PYREVIT RIBBON BUTTON INSTALLED!
echo ==========================================================
echo.
echo The 'R2R' tab and 'One-Click Transfer' ribbon button have been
echo registered in pyRevit at:
echo   %BUTTON_DIR%
echo.
echo How to use:
echo   1. Launch Autodesk Revit.
echo   2. You will now see the 'R2R' ribbon tab!
echo   3. Launch Rhino from the 'Rhino.Inside' ribbon tab.
echo   4. Click 'One-Click Transfer' to open the transfer dashboard!
echo.
echo Press any key to exit...
pause >nul
