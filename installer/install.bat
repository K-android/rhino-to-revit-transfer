@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - One-Click Installer
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - Multi-Version Installer
echo    Supported Revit Versions: 2024, 2025, 2026, 2027
echo ==========================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "MANIFEST_SRC=%SCRIPT_DIR%RhinoToRevitTransfer.addin"
set "USER_ADDINS=%APPDATA%\Autodesk\Revit\Addins"
set "PROGRAM_ADDINS=%ProgramData%\Autodesk\Revit\Addins"

if not exist "%MANIFEST_SRC%" (
    echo [ERROR] Manifest template not found: %MANIFEST_SRC%
    pause
    exit /b 1
)

echo Registering add-in across Revit versions 2024 to 2027...
echo.

rem 1. Deploy to User AppData for 2024, 2025, 2026, 2027 and any detected versions
set "ALL_YEARS=2024 2025 2026 2027"

rem Check for other existing directories like 2022, 2023
if exist "%USER_ADDINS%" (
    for /d %%D in ("%USER_ADDINS%\20*") do (
        set "Y=%%~nxD"
        echo !ALL_YEARS! | findstr /i "!Y!" >nul || set "ALL_YEARS=!ALL_YEARS! !Y!"
    )
)

for %%Y in (%ALL_YEARS%) do (
    set "TARGET_DIR=%USER_ADDINS%\%%Y\RhinoToRevitTransfer"
    set "TARGET_ADDIN=%USER_ADDINS%\%%Y\RhinoToRevitTransfer.addin"
    
    if not exist "!TARGET_DIR!" mkdir "!TARGET_DIR!" >nul 2>&1
    copy /y "%MANIFEST_SRC%" "!TARGET_ADDIN!" >nul 2>&1

    set "DLL_SRC="
    if %%Y GEQ 2025 (
        if exist "%SCRIPT_DIR%RhinoToRevitTransfer\net8.0\RhinoToRevitTransfer.dll" set "DLL_SRC=%SCRIPT_DIR%RhinoToRevitTransfer\net8.0\RhinoToRevitTransfer.dll"
        if not defined DLL_SRC (
            if exist "%SCRIPT_DIR%..\csharp\bin\Release\net8.0-windows\RhinoToRevitTransfer.dll" set "DLL_SRC=%SCRIPT_DIR%..\csharp\bin\Release\net8.0-windows\RhinoToRevitTransfer.dll"
        )
    )
    if not defined DLL_SRC (
        if exist "%SCRIPT_DIR%RhinoToRevitTransfer\net48\RhinoToRevitTransfer.dll" set "DLL_SRC=%SCRIPT_DIR%RhinoToRevitTransfer\net48\RhinoToRevitTransfer.dll"
    )
    if not defined DLL_SRC (
        if exist "%SCRIPT_DIR%..\csharp\bin\Release\net48\RhinoToRevitTransfer.dll" set "DLL_SRC=%SCRIPT_DIR%..\csharp\bin\Release\net48\RhinoToRevitTransfer.dll"
    )
    if not defined DLL_SRC (
        if exist "%SCRIPT_DIR%RhinoToRevitTransfer\RhinoToRevitTransfer.dll" set "DLL_SRC=%SCRIPT_DIR%RhinoToRevitTransfer\RhinoToRevitTransfer.dll"
    )

    if defined DLL_SRC (
        copy /y "!DLL_SRC!" "!TARGET_DIR!\RhinoToRevitTransfer.dll" >nul 2>&1
        echo   -^> Revit %%Y : [SUCCESS] Installed DLL and registered manifest [.addin]
    ) else (
        echo   -^> Revit %%Y : [SUCCESS] Registered manifest [.addin]
    )
)

rem 2. Optional: Deploy to All Users ProgramData if available
if exist "%PROGRAM_ADDINS%" (
    for %%Y in (2024 2025 2026 2027) do (
        if exist "%PROGRAM_ADDINS%\%%Y" (
            mkdir "%PROGRAM_ADDINS%\%%Y\RhinoToRevitTransfer" >nul 2>&1
            copy /y "%MANIFEST_SRC%" "%PROGRAM_ADDINS%\%%Y\RhinoToRevitTransfer.addin" >nul 2>&1
            if defined DLL_SRC (
                copy /y "!DLL_SRC!" "%PROGRAM_ADDINS%\%%Y\RhinoToRevitTransfer\RhinoToRevitTransfer.dll" >nul 2>&1
            )
        )
    )
)

echo.
echo Unblocking deployed add-in files (preventing 0x80131515 security block)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%USER_ADDINS%' -Filter '*RhinoToRevitTransfer*' -Recurse -ErrorAction SilentlyContinue | Unblock-File; Get-ChildItem -Path '%PROGRAM_ADDINS%' -Filter '*RhinoToRevitTransfer*' -Recurse -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

echo.
echo ==========================================================
echo                INSTALLATION COMPLETE!
echo ==========================================================
echo.
echo Successfully configured for Revit 2024, 2025, 2026, and 2027!
echo.
echo How to use:
echo   1. Launch Autodesk Revit (2024, 2025, 2026, or 2027).
echo   2. Launch Rhino.Inside from the 'Rhino.Inside' ribbon tab.
echo   3. You will see the 'R2R Transfer' ribbon tab!
echo   4. Click 'One-Click Transfer' to start.
echo.
echo Press any key to exit...
pause >nul
