@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Installer
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - pyRevit Ribbon Installer
echo    (Zero Compilation - Runs Pure Python inside Revit)
echo ==========================================================
echo.

set "PYREVIT_EXT_ROOT=%APPDATA%\pyRevit\Extensions"
set "EXT_DIR=%PYREVIT_EXT_ROOT%\R2R.extension"
set "LIB_DIR=%EXT_DIR%\lib"
set "BUTTON_DIR=%EXT_DIR%\R2R.tab\Transfer.panel\OneClick.pushbutton"
set "SCRIPT_FILE=%BUTTON_DIR%\script.py"
set "BUNDLE_YAML=%BUTTON_DIR%\bundle.yaml"
pushd "%~dp0.." && set "PKG_DIR=%CD%" && popd

if not exist "%APPDATA%\pyRevit" (
    echo [ERROR] pyRevit installation not found at %APPDATA%\pyRevit
    pause
    exit /b 1
)

echo Creating pyRevit extension and lib directories...
if not exist "%LIB_DIR%" mkdir "%LIB_DIR%" >nul 2>&1
if not exist "%BUTTON_DIR%" mkdir "%BUTTON_DIR%" >nul 2>&1

echo Copying R2R core library and profile data into %LIB_DIR%...
copy /Y "%PKG_DIR%\__init__.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\scanner.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\classifier.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\ai_assistant.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\profile_manager.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\conversion_router.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\graphics_validator.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\sync_engine.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\reporting.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\ui_dialog.py" "%LIB_DIR%\" >nul 2>&1
copy /Y "%PKG_DIR%\main_launcher.py" "%LIB_DIR%\" >nul 2>&1
if exist "%PKG_DIR%\Transfer_Profile.json" (
    copy /Y "%PKG_DIR%\Transfer_Profile.json" "%LIB_DIR%\" >nul 2>&1
)

echo Generating pyRevit script launcher...
(
    echo # -*- coding: utf-8 -*-
    echo """Rhino-to-Revit One-Click Transfer Launcher"""
    echo import sys
    echo import os
    echo _this_dir = os.path.dirname^(os.path.abspath^(__file__^)^)
    echo lib_dir = os.path.normpath^(os.path.join^(_this_dir, '..', '..', '..', 'lib'^)^)
    echo if lib_dir not in sys.path:
    echo     sys.path.insert^(0, lib_dir^)
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
echo The 'R2R' extension with self-contained 'lib' folder and 'R2R.tab'
echo have been installed in pyRevit at:
echo   Extension Root : %EXT_DIR%
echo   Library Folder : %LIB_DIR%
echo   Ribbon Button  : %BUTTON_DIR%
echo.
echo How to use:
echo   1. Launch Autodesk Revit (or click pyRevit -> Reload).
echo   2. You will see the 'R2R' ribbon tab!
echo   3. Launch Rhino from the 'Rhino.Inside' ribbon tab.
echo   4. Click 'One-Click Transfer' to open the transfer dashboard!
echo.
echo Press any key to exit...
pause >nul
