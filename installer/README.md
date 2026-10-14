# Rhino-to-Revit Transfer (R2R) - Add-in Installer & Setup

This directory contains automated setup and uninstall utilities for registering the **Rhino-to-Revit Transfer (R2R)** add-in into Autodesk Revit.

---

## 🚀 Quick Setup (1-Click)

1. **Close Autodesk Revit** (if currently running).
2. Right-click **`install.bat`** and select **Run as Administrator** (or double-click `install.bat`).
3. The installer will:
   - Scan your system for all installed Autodesk Revit versions (`2020` through `2026`).
   - Register `RhinoToRevitTransfer.addin` into `%APPDATA%\Autodesk\Revit\Addins\<Year>\`.
   - Prepare the add-in subfolder and deploy necessary files.
4. Launch **Autodesk Revit**.
5. Start **Rhino.Inside** from the `Rhino.Inside` ribbon tab.
6. Switch to the newly created **R2R Transfer** ribbon tab and click **One-Click Transfer**.

---

## 🛠️ Files in this Directory

| File | Description |
| :--- | :--- |
| **`install.bat`** | Double-click Windows batch file that launches the PowerShell installer with bypassed execution policy. |
| **`install.ps1`** | Core PowerShell script that detects Revit versions, compiles (if .NET SDK is present), and deploys the `.addin` manifest. |
| **`uninstall.bat`** | Double-click batch file to cleanly remove the add-in from all Revit versions. |
| **`uninstall.ps1`** | PowerShell uninstaller that cleans up all `.addin` manifests and plugin directories. |

---

## ⚙️ Advanced: Building the C# Assembly

If you want to compile custom modifications to the C# source code in `csharp/`:
1. Ensure you have the **.NET SDK** (.NET 8.0 or .NET Framework 4.8 targeting developer pack) or **Visual Studio 2022+**.
2. Open terminal in the `csharp/` folder:
   ```shell
   dotnet build RhinoToRevitTransfer.csproj -c Release
   ```
3. Run `install.bat` to deploy the updated binary to your Revit add-ins directory.

---

## 🐍 Alternative: Python / ScriptEditor Execution (No Compilation Required)

If you prefer to run directly without compiling C#, you can run the Python engine inside Rhino.Inside.Revit:
```python
# In Rhino 8 ScriptEditor or Rhino Python Editor:
import main_launcher
main_launcher.launch_transfer()
```
Or hook `main_launcher.py` into a pyRevit pushbutton!
