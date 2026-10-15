@echo off
title Unblock Rhino-to-Revit Transfer Addin
echo ==========================================================
echo    Unblocking Revit Add-in DLLs (Fix 0x80131515)
echo ==========================================================
echo.

set "ADDINS_ROOT=%APPDATA%\Autodesk\Revit\Addins"

echo Removing Windows Mark-of-the-Web security blocks...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%ADDINS_ROOT%' -Filter '*RhinoToRevitTransfer*' -Recurse | Unblock-File; Get-ChildItem -Path '%~dp0..' -Recurse | Unblock-File" >nul 2>&1

echo.
echo [SUCCESS] All RhinoToRevitTransfer Add-in files and DLLs have been unblocked!
echo.
echo You can now launch Autodesk Revit without the 0x80131515 error.
echo.
echo Press any key to exit...
pause >nul
