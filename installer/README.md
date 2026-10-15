# Rhino-to-Revit Transfer (R2R) - Add-in Installer & Setup

This directory contains automated setup and uninstall utilities for registering the **Rhino-to-Revit Transfer (R2R)** add-in into Autodesk Revit.

---

## 🚀 Quick Setup (1-Click)

1. **Close Autodesk Revit** (if currently running).
2. Simply double-click **`install.bat`**.
   > **Note:** `install.bat` runs pure native Windows commands and does **NOT** require PowerShell, administrative privileges, or any digital code-signing certificate.
3. The installer will:
   - Configure Autodesk Revit versions (`2024`, `2025`, `2026`, and `2027`).
   - Automatically deploy matching binaries: .NET Framework 4.8 for Revit 2024 and .NET 8.0 for Revit 2025–2027.
   - Register `RhinoToRevitTransfer.addin` into `%APPDATA%\Autodesk\Revit\Addins\<Year>\`.
   - Automatically unblock all files so they run without 0x80131515 errors.
4. Launch **Autodesk Revit**.
5. Start **Rhino.Inside** from the `Rhino.Inside` ribbon tab.
6. Switch to the newly created **R2R Transfer** ribbon tab and click **One-Click Transfer**.

---

## ⚠️ PowerShell Digital Signature Workarounds (For `install.ps1`)

If you run `install.ps1` directly in PowerShell and receive the error:
`"File install.ps1 cannot be loaded because running scripts is disabled on this system"` or `"File is not digitally signed"`, choose any of these workarounds:

* **Workaround 1 (Recommended): Run `install.bat` instead**
  Double-click `install.bat`. It bypasses PowerShell completely.

* **Workaround 2: Run with `-ExecutionPolicy Bypass` in terminal**
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  ```

* **Workaround 3: Unblock the downloaded script (Remove Mark-of-the-Web)**
  Files downloaded from GitHub are marked as remote by Windows. Unblock it via PowerShell:
  ```powershell
  Unblock-File .\install.ps1
  ```
  *(Or right-click `install.ps1` -> **Properties** -> check **Unblock** -> click **OK**)*.

* **Workaround 4: Temporarily bypass execution policy in your terminal session**
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\install.ps1
  ```

---

## 🛡️ Revit Error: Operation is not supported (HRESULT: 0x80131515)

If Revit displays an error dialog stating:
> *"Could not load file or assembly ... RhinoToRevitTransfer.dll or one of its dependencies. Operation is not supported. (Exception from HRESULT: 0x80131515)"*

This occurs because Windows security attaches a **Mark-of-the-Web (ZoneId=3)** attribute to any `.dll` downloaded from the internet/GitHub.

### How to Fix in 1 Click:
1. Double-click **`installer\unblock.bat`**.
2. Or in PowerShell:
   ```powershell
   Get-ChildItem -Path "$env:APPDATA\Autodesk\Revit\Addins" -Filter "*RhinoToRevitTransfer*" -Recurse | Unblock-File
   ```
3. Or manually in Windows Explorer:
   - Navigate to `%APPDATA%\Autodesk\Revit\Addins\<Revit_Year>\RhinoToRevitTransfer\RhinoToRevitTransfer.dll`
   - Right-click `RhinoToRevitTransfer.dll` ➔ **Properties** ➔ check **Unblock** ➔ click **OK**.
4. Restart Revit.

---

## 🛠️ Files in this Directory

| File | Description |
| :--- | :--- |
| **`install.bat`** | **Recommended:** Pure native batch installer. Zero dependencies, no PowerShell required, no digital signature issues. |
| **`install.ps1`** | PowerShell installer script for automated version detection and compilation. |
| **`RhinoToRevitTransfer.addin`** | Clean XML application manifest template deployed to Revit's Addins folder. |
| **`uninstall.bat`** | Pure native batch uninstaller to cleanly remove the add-in from all Revit versions. |
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
