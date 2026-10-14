@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - One-Click Installer
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - One-Click Installer
echo    (No PowerShell Digital Signature Required)
echo ==========================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "MANIFEST_SRC=%SCRIPT_DIR%RhinoToRevitTransfer.addin"
set "ADDINS_ROOT=%APPDATA%\Autodesk\Revit\Addins"

if not exist "%MANIFEST_SRC%" (
    echo [ERROR] Manifest template not found: %MANIFEST_SRC%
    pause
    exit /b 1
)

if not exist "%ADDINS_ROOT%" (
    echo [ERROR] Revit Addins directory not found at:
    echo   %ADDINS_ROOT%
    echo Please make sure Autodesk Revit is installed.
    echo.
    pause
    exit /b 1
)

echo Scanning for installed Autodesk Revit versions...
set "FOUND=0"

for /d %%D in ("%ADDINS_ROOT%\20*") do (
    set "FOUND=1"
    set "YEAR=%%~nxD"
    set "TARGET_DIR=%%D\RhinoToRevitTransfer"
    set "TARGET_ADDIN=%%D\RhinoToRevitTransfer.addin"
    
    if not exist "!TARGET_DIR!" mkdir "!TARGET_DIR!" >nul 2>&1
    
    copy /y "%MANIFEST_SRC%" "!TARGET_ADDIN!" >nul 2>&1
    
    set "HAS_DLL=0"
    if exist "%SCRIPT_DIR%..\csharp\bin\Release\net48\RhinoToRevitTransfer.dll" set "HAS_DLL=1"
    
    if "!HAS_DLL!"=="1" (
        copy /y "%SCRIPT_DIR%..\csharp\bin\Release\net48\RhinoToRevitTransfer.dll" "!TARGET_DIR!\RhinoToRevitTransfer.dll" >nul 2>&1
        echo   -^> Revit !YEAR! : [SUCCESS] Installed DLL and registered manifest [.addin]
    )
    if "!HAS_DLL!"=="0" (
        echo   -^> Revit !YEAR! : [SUCCESS] Registered manifest [.addin]
    )
)

if "!FOUND!"=="0" (
    echo [WARNING] No existing Revit year directories found in %ADDINS_ROOT%.
    echo Creating default deployment folders for Revit 2024 and 2025...
    
    for %%Y in (2024 2025) do (
        mkdir "%ADDINS_ROOT%\%%Y\RhinoToRevitTransfer" >nul 2>&1
        copy /y "%MANIFEST_SRC%" "%ADDINS_ROOT%\%%Y\RhinoToRevitTransfer.addin" >nul 2>&1
        echo   -^> Revit %%Y : [SUCCESS] Registered manifest [.addin]
    )
)

echo.
echo ==========================================================
echo                INSTALLATION COMPLETE!
echo ==========================================================
echo.
echo How to use:
echo   1. Launch Autodesk Revit.
echo   2. Launch Rhino.Inside from the 'Rhino.Inside' ribbon tab.
echo   3. You will now see the new 'R2R Transfer' ribbon tab!
echo   4. Click 'One-Click Transfer' to start.
echo.
echo Press any key to exit...
pause >nul
