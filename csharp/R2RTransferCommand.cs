using System;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Rhino;

namespace RhinoToRevitTransfer
{
    /// <summary>
    /// Revit External Command triggered from the Ribbon Button.
    /// Handles Rhino.Inside.Revit context binding and launches the interactive transfer dashboard.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    [Regeneration(RegenerationOption.Manual)]
    public class R2RTransferCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            var uiapp = commandData.Application;
            var uidoc = uiapp.ActiveUIDocument;
            var doc = uidoc?.Document;

            if (doc == null)
            {
                TaskDialog.Show("R2R Transfer", "Please open a Revit project document before launching transfer.");
                return Result.Cancelled;
            }

            // 1. Verify Rhino.Inside.Revit context
            RhinoDoc activeRhinoDoc = null;
            try
            {
                activeRhinoDoc = RhinoDoc.ActiveDoc;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("R2R Transfer Error", 
                    "Rhino.Inside.Revit is not currently active.\n" +
                    "Please launch Rhino from the 'Rhino.Inside' Revit ribbon panel first.\n\n" +
                    "Details: " + ex.Message);
                return Result.Failed;
            }

            if (activeRhinoDoc == null)
            {
                TaskDialog.Show("R2R Transfer", "No active Rhino document found. Please open your Rhino model.");
                return Result.Cancelled;
            }

            // 2. Launch R2R Transfer Dialog / Dashboard
            try
            {
                var dialog = new TransferDashboardWindow(doc, uidoc, activeRhinoDoc);
                dialog.ShowDialog();
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("Execution Error", "Error executing R2R Transfer: " + ex.Message);
                return Result.Failed;
            }
        }
    }
}
