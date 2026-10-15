@echo off
setlocal enabledelayedexpansion
title Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller
echo ==========================================================
echo    Rhino-to-Revit Transfer (R2R) - Full Add-in Cleaner
echo ==========================================================
echo.

set "USER_ADDINS=%APPDATA%\Autodesk\Revit\Addins"
set "PROGRAM_ADDINS=%ProgramData%\Autodesk\Revit\Addins"

echo Removing Rhino-to-Revit Transfer add-in from User AppData...
if exist "%USER_ADDINS%" (
    for /d %%D in ("%USER_ADDINS%\20*") do (
        set "YEAR=%%~nxD"
        if exist "%%D\RhinoToRevitTransfer.addin" (
            del /f /q "%%D\RhinoToRevitTransfer.addin" >nul 2>&1
            echo   -^> Removed AppData manifest from Revit !YEAR!
        )
        if exist "%%D\RhinoToRevitTransfer" (
            rd /s /q "%%D\RhinoToRevitTransfer" >nul 2>&1
            echo   -^> Removed AppData plugin folder from Revit !YEAR!
        )
    )
)

echo.
echo Removing Rhino-to-Revit Transfer add-in from ProgramData (All Users)...
if exist "%PROGRAM_ADDINS%" (
    for /d %%D in ("%PROGRAM_ADDINS%\20*") do (
        set "YEAR=%%~nxD"
        if exist "%%D\RhinoToRevitTransfer.addin" (
            del /f /q "%%D\RhinoToRevitTransfer.addin" >nul 2>&1
            echo   -^> Removed ProgramData manifest from Revit !YEAR!
        )
        if exist "%%D\RhinoToRevitTransfer" (
            rd /s /q "%%D\RhinoToRevitTransfer" >nul 2>&1
            echo   -^> Removed ProgramData plugin folder from Revit !YEAR!
        )
    )
)

echo.
echo ==========================================================
echo              UNINSTALLATION COMPLETE!
echo ==========================================================
echo RhinoToRevitTransfer has been cleanly removed from all
echo AppData and ProgramData Revit add-in directories.
echo.
echo When you launch Revit, no startup error will appear.
echo.
echo Press any key to exit...
pause >nul
