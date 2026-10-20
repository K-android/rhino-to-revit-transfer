# -*- coding: utf-8 -*-
"""Rhino-to-Revit One-Click Transfer Launcher for pyRevit."""

import sys
import os

# 1. FAST ZERO-OVERHEAD PRE-CHECK (Runs in < 0.5ms)
# Checks only in-memory loaded assemblies in the AppDomain.
# CRITICAL: Does NOT call clr.AddReference("RhinoCommon") or touch Rhino DLLs
# if Rhino has not been started, completely preventing openNURBS AppDomain locks.
try:
    import clr
    clr.AddReference("System")
    import System
    loaded_assemblies = [a.GetName().Name for a in System.AppDomain.CurrentDomain.GetAssemblies()]
except Exception:
    loaded_assemblies = []

if "RhinoCommon" not in loaded_assemblies:
    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogIcon
        td = TaskDialog("R2R Transfer - Rhino.Inside Required")
        td.MainInstruction = "Rhino.Inside.Revit is Not Started"
        td.MainContent = (
            "Rhino.Inside is not currently active in Revit.\n\n"
            "Please go to the 'Rhino.Inside' ribbon tab and click 'Start' (or 'Rhino') first.\n\n"
            "Once Rhino is open, click 'One-Click Transfer' again."
        )
        td.MainIcon = TaskDialogIcon.TaskDialogIconWarning
        td.CommonButtons = TaskDialogCommonButtons.Ok
        td.Show()
    except Exception:
        try:
            import Autodesk.Revit.UI as UI
            UI.TaskDialog.Show(
                "R2R Transfer - Rhino.Inside Required",
                "Rhino.Inside is not currently active in Revit.\n\n"
                "Please go to the 'Rhino.Inside' ribbon tab and click 'Start' first."
            )
        except Exception:
            pass
    # Exit immediately without loading any R2R modules or touching RhinoCommon
    sys.exit(0)

# 2. Setup module search path for self-contained extension lib/
_this_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.normpath(os.path.join(_this_dir, '..', '..', '..', 'lib'))
if os.path.exists(lib_dir) and lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

# 3. Reload R2R modules for live iterative updates
for _mod in [
    'scanner', 'classifier', 'ai_assistant', 'profile_manager',
    'conversion_router', 'family_factory', 'graphics_validator', 'sync_engine',
    'reporting', 'ui_dialog', 'main_launcher'
]:
    if _mod in sys.modules:
        del sys.modules[_mod]

import main_launcher

# 4. Resolve Revit context and launch
doc = None
uidoc = None
try:
    if '__revit__' in globals():
        uidoc = __revit__.ActiveUIDocument
        doc = uidoc.Document if uidoc else None
except Exception:
    pass

main_launcher.launch_transfer(revit_doc=doc, revit_uidoc=uidoc)
