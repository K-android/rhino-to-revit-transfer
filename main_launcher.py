# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR)
main_launcher.py - Main Entry Point Launcher Script

Executes inside Rhino.Inside.Revit (RIR) Python Script Editor, Grasshopper Python,
or as a pyRevit ribbon push-button command.

Initializes Revit and Rhino contexts, checks API dependencies, and launches
the TransferDashboardDialog Eto.Forms dashboard.
"""

import sys
import os

# Ensure current directory and package root are in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Environment and Context Defaults
REVIT_DOC = None
REVIT_UIDOC = None


def check_rhino_inside_status():
    """Checks whether Rhino.Inside.Revit is active in the Revit process.

    Inspects only ALREADY loaded assemblies in the AppDomain.
    CRITICAL: Does NOT call clr.AddReference or load DLLs from disk if Rhino
    is not running, preventing assembly conflict locks with openNURBS.
    """
    try:
        import clr
        clr.AddReference("System")
        import System

        loaded_assemblies = [a.GetName().Name for a in System.AppDomain.CurrentDomain.GetAssemblies()]
    except Exception:
        loaded_assemblies = []

    # If RhinoCommon is not already in the AppDomain, Rhino has not been started
    if "RhinoCommon" not in loaded_assemblies:
        return (False, "Rhino.Inside is not currently active in Revit.\n\n"
                       "Please go to the 'Rhino.Inside' ribbon tab and click 'Start' (or 'Rhino') first.\n\n"
                       "Once Rhino is open, click 'One-Click Transfer' again.")

    # RhinoCommon is loaded; verify active document
    try:
        import clr
        clr.AddReference("RhinoCommon")
        import Rhino
        if not hasattr(Rhino, "RhinoApp") or Rhino.RhinoApp.ExeVersion <= 0:
            return (False, "Rhino is initializing but not fully ready. Please wait a moment and try again.")
        if not Rhino.RhinoDoc.ActiveDoc:
            return (False, "Rhino is running, but no 3DM document is currently open.\n\n"
                           "Please open your model in Rhino before scanning.")
    except Exception as ex:
        return (False, "Rhino runtime check notice:\n{}".format(ex))

    return (True, "")


def _resolve_revit_context(revit_doc=None, revit_uidoc=None):
    """Resolves Revit Document and UIDocument from Rhino.Inside or pyRevit globals."""
    doc = revit_doc
    uidoc = revit_uidoc

    if not doc:
        # 1. Try RhinoInside.Revit only if already loaded in AppDomain
        try:
            import clr
            clr.AddReference("System")
            import System
            loaded = [a.GetName().Name for a in System.AppDomain.CurrentDomain.GetAssemblies()]
            if "RhinoInside.Revit" in loaded:
                clr.AddReference("RhinoInside.Revit")
                from RhinoInside.Revit import Revit
                doc = Revit.ActiveDBDocument
                uidoc = Revit.ActiveUIDocument
        except Exception:
            pass

    if not doc:
        # 2. Try pyRevit / Revit UI globals
        revit_app = None
        if "__revit__" in globals():
            revit_app = globals()["__revit__"]
        elif sys.modules.get('__main__') and hasattr(sys.modules['__main__'], '__revit__'):
            revit_app = getattr(sys.modules['__main__'], '__revit__')

        if revit_app:
            try:
                uidoc = revit_app.ActiveUIDocument
                doc = uidoc.Document if uidoc else None
            except Exception:
                pass

    return doc, uidoc


def launch_transfer(revit_doc=None, revit_uidoc=None):
    """Safely launches the interactive One-Click Rhino-to-Revit Transfer Dashboard."""
    # 1. Check Rhino.Inside readiness BEFORE loading modules to prevent locking Rhino.Inside
    ready, not_ready_msg = check_rhino_inside_status()
    if not ready:
        try:
            from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogIcon
            td = TaskDialog("R2R Transfer - Rhino.Inside Required")
            td.MainInstruction = "Rhino.Inside.Revit is Not Started"
            td.MainContent = not_ready_msg
            td.MainIcon = TaskDialogIcon.TaskDialogIconWarning
            td.CommonButtons = TaskDialogCommonButtons.Ok
            td.Show()
        except Exception:
            try:
                import Autodesk.Revit.UI as UI
                UI.TaskDialog.Show("R2R Transfer - Rhino.Inside Required", not_ready_msg)
            except Exception:
                print("[R2R Transfer NOTICE]:\n" + not_ready_msg)
        return None

    # 2. Resolve Revit context
    doc, uidoc = _resolve_revit_context(revit_doc, revit_uidoc)

    # 3. Load TransferDashboardDialog
    TransferDashboardDialog = None
    try:
        from ui_dialog import TransferDashboardDialog
    except (ImportError, ValueError):
        try:
            from .ui_dialog import TransferDashboardDialog
        except Exception as ex:
            import traceback
            err_msg = "[R2R Transfer] Could not load TransferDashboardDialog:\n" + traceback.format_exc()
            print(err_msg)
            try:
                import Autodesk.Revit.UI as UI
                UI.TaskDialog.Show("R2R Transfer", err_msg)
            except Exception:
                pass
            return None

    try:
        dialog = TransferDashboardDialog(revit_doc=doc, revit_uidoc=uidoc)
    except Exception:
        import traceback
        err_msg = "[R2R Transfer] Failed to initialize dialog:\n" + traceback.format_exc()
        print(err_msg)
        try:
            import Autodesk.Revit.UI as UI
            UI.TaskDialog.Show("R2R Transfer Error", err_msg)
        except Exception:
            pass
        return None

    if not dialog or not getattr(dialog, 'IsValid', True):
        return None

    # 4. Show modal anchored to Rhino Main Window if available
    try:
        import Rhino.UI as rui
        if hasattr(rui, 'RhinoEtoApp') and hasattr(rui.RhinoEtoApp, 'MainWindow') and rui.RhinoEtoApp.MainWindow:
            dialog.ShowModal(rui.RhinoEtoApp.MainWindow)
        else:
            dialog.ShowModal()
    except Exception:
        try:
            dialog.ShowModal()
        except Exception:
            import traceback
            print("[R2R Transfer] Dialog presentation notice:\n{}".format(traceback.format_exc()))

    return dialog


if __name__ == "__main__":
    launch_transfer()
