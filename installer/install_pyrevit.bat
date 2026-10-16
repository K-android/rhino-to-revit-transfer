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

rem Canonical package directory resolution
for %%I in ("%~dp0..") do set "PKG_DIR=%%~fI"
if not exist "%PKG_DIR%\main_launcher.py" (
    for %%I in ("%~dp0.") do set "PKG_DIR=%%~fI"
)

if not exist "%PKG_DIR%\main_launcher.py" (
    echo [ERROR] Could not locate R2R source files in:
    echo   %PKG_DIR%
    echo Please make sure install_pyrevit.bat is inside the 'installer' subfolder
    echo of the rhino-to-revit-transfer repository.
    pause
    exit /b 1
)

if not exist "%APPDATA%\pyRevit" (
    echo [ERROR] pyRevit installation not found at %APPDATA%\pyRevit
    echo Please ensure pyRevit is installed before running this installer.
    pause
    exit /b 1
)

echo Source Directory: "%PKG_DIR%"
echo Target Extension: "%EXT_DIR%"
echo Target Library:   "%LIB_DIR%"
echo.

echo Creating pyRevit extension directories...
if not exist "%EXT_DIR%" mkdir "%EXT_DIR%" >nul 2>&1
if not exist "%LIB_DIR%" mkdir "%LIB_DIR%" >nul 2>&1
if not exist "%BUTTON_DIR%" mkdir "%BUTTON_DIR%" >nul 2>&1

echo.
echo Copying R2R Python modules into extension library...
copy /Y "%PKG_DIR%\*.py" "%LIB_DIR%\" >nul
if exist "%LIB_DIR%\test_pipeline.py" del /f /q "%LIB_DIR%\test_pipeline.py" >nul 2>&1
if exist "%LIB_DIR%\Transfer_Profile.json" del /f /q "%LIB_DIR%\Transfer_Profile.json" >nul 2>&1

set "USER_R2R_DATA=%APPDATA%\pyRevit\R2R"
if not exist "%USER_R2R_DATA%" mkdir "%USER_R2R_DATA%" >nul 2>&1
if exist "%PKG_DIR%\Transfer_Profile.json" (
    if not exist "%USER_R2R_DATA%\Transfer_Profile.json" (
        copy /Y "%PKG_DIR%\Transfer_Profile.json" "%USER_R2R_DATA%\" >nul
    )
)

echo.
echo Verifying installed files in %LIB_DIR%:
dir /b "%LIB_DIR%\*.py"
echo.

echo Deploying pyRevit button launcher and metadata...
if exist "%~dp0script.py" (
    copy /Y "%~dp0script.py" "%SCRIPT_FILE%" >nul
) else (
    (
        echo # -*- coding: utf-8 -*-
        echo """Rhino-to-Revit One-Click Transfer Launcher"""
        echo import sys
        echo import os
        echo import clr
        echo clr.AddReference^("System"^)
        echo import System
        echo loaded = [a.GetName^(^).Name for a in System.AppDomain.CurrentDomain.GetAssemblies^(^)]
        echo if "RhinoCommon" not in loaded:
        echo     try:
        echo         from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogIcon
        echo         td = TaskDialog^("R2R Transfer - Rhino.Inside Required"^)
        echo         td.MainInstruction = "Rhino.Inside.Revit is Not Started"
        echo         td.MainContent = "Rhino.Inside is not currently active in Revit.\n\nPlease go to the 'Rhino.Inside' ribbon tab and click 'Start' first."
        echo         td.MainIcon = TaskDialogIcon.TaskDialogIconWarning
        echo         td.CommonButtons = TaskDialogCommonButtons.Ok
        echo         td.Show^(^)
        echo     except Exception:
        echo         pass
        echo     sys.exit^(0^)
        echo _this_dir = os.path.dirname^(os.path.abspath^(__file__^)^)
        echo lib_dir = os.path.normpath^(os.path.join^(_this_dir, '..', '..', '..', 'lib'^)^)
        echo if os.path.exists^(lib_dir^) and lib_dir not in sys.path:
        echo     sys.path.insert^(0, lib_dir^)
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
)

if exist "%~dp0bundle.yaml" (
    copy /Y "%~dp0bundle.yaml" "%BUNDLE_YAML%" >nul
) else (
    (
        echo title: "One-Click\nTransfer"
        echo tooltip: "Launch the Rhino-to-Revit 6-stage transfer dashboard (SCAN -> CLASSIFY -> REVIEW -> TRANSFER -> VALIDATE -> SYNC)"
        echo author: "AEC Computational Design Team"
    ) > "%BUNDLE_YAML%"
)

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
echo   1. Launch Autodesk Revit ^(or click pyRevit -^> Reload^).
echo   2. You will see the 'R2R' ribbon tab!
echo   3. Launch Rhino from the 'Rhino.Inside' ribbon tab.
echo   4. Click 'One-Click Transfer' to open the transfer dashboard!
echo.
echo Press any key to exit...
pause >nul
