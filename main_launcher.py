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

# Environment and CLR Assembly Resolution
REVIT_DOC = None
REVIT_UIDOC = None
IS_RIR = False

try:
    import clr
    clr.AddReference("System")

    # Try RhinoCommon
    try:
        clr.AddReference("RhinoCommon")
        import Rhino
        import Rhino.UI as rui
    except Exception:
        Rhino = None

    # Try Rhino.Inside.Revit
    try:
        clr.AddReference("RhinoInside.Revit")
        import RhinoInside.Revit as RIR
        from RhinoInside.Revit import Revit
        REVIT_DOC = Revit.ActiveDBDocument
        REVIT_UIDOC = Revit.ActiveUIDocument
        IS_RIR = True
    except Exception:
        pass

    # Try pyRevit / Standard Revit UI environment fallback
    if not REVIT_DOC:
        revit_app = None
        if "__revit__" in globals():
            revit_app = globals()["__revit__"]
        elif sys.modules.get('__main__') and hasattr(sys.modules['__main__'], '__revit__'):
            revit_app = getattr(sys.modules['__main__'], '__revit__')
        
        if revit_app:
            try:
                REVIT_UIDOC = revit_app.ActiveUIDocument
                REVIT_DOC = REVIT_UIDOC.Document if REVIT_UIDOC else None
                IS_RIR = True
            except Exception:
                pass

except Exception as ex:
    print("[R2R Transfer] CLR Initialization Notice: {}".format(ex))

# Import Transfer Dialog from local package
try:
    from ui_dialog import TransferDashboardDialog
except (ImportError, ValueError):
    try:
        from .ui_dialog import TransferDashboardDialog
    except Exception:
        TransferDashboardDialog = None


def launch_transfer(revit_doc=None, revit_uidoc=None):
    """Launches the interactive One-Click Rhino-to-Revit Transfer Dashboard."""
    doc = revit_doc or REVIT_DOC
    uidoc = revit_uidoc or REVIT_UIDOC

    if not doc:
        print("[R2R Transfer] WARNING: Running outside active Revit document.")
        print("[R2R Transfer] Launching in offline geometry inspection mode.")

    if TransferDashboardDialog is None:
        err_msg = "[R2R Transfer] Could not load TransferDashboardDialog. Please check python environment."
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

    # Show modal anchored to Rhino Main Window if available
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
            try:
                # Fallback to modeless show
                dialog.Show()
            except Exception:
                import traceback
                print("[R2R Transfer] Dialog presentation notice: {}".format(traceback.format_exc()))

    return dialog


if __name__ == "__main__":
    launch_transfer()
