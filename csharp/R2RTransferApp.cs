using System;
using System.IO;
using System.Reflection;
using Autodesk.Revit.UI;

namespace RhinoToRevitTransfer
{
    /// <summary>
    /// Revit External Application that registers the R2R Transfer Ribbon Tab and PushButton.
    /// Eliminates any dependency on pyRevit or manual script editors.
    /// </summary>
    public class R2RTransferApp : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication application)
        {
            string tabName = "R2R Transfer";
            string panelName = "Geometry Pipeline";

            // 1. Create Ribbon Tab
            try
            {
                application.CreateRibbonTab(tabName);
            }
            catch (Exception)
            {
                // Tab may already exist if other tools are installed
            }

            // 2. Create Ribbon Panel
            RibbonPanel? panel = null;
            var panels = application.GetRibbonPanels(tabName);
            foreach (var p in panels)
            {
                if (p.Name == panelName)
                {
                    panel = p;
                    break;
                }
            }
            if (panel == null)
            {
                panel = application.CreateRibbonPanel(tabName, panelName);
            }

            // 3. Create Push Button
            string thisAssemblyPath = Assembly.GetExecutingAssembly().Location;
            var buttonData = new PushButtonData(
                "btnR2ROneClickTransfer",
                "One-Click\nTransfer",
                thisAssemblyPath,
                "RhinoToRevitTransfer.R2RTransferCommand"
            )
            {
                ToolTip = "Execute controlled 6-stage Rhino-to-Revit BIM Transfer (SCAN -> CLASSIFY -> REVIEW -> TRANSFER -> VALIDATE -> SYNC)",
                LongDescription = "Scans active Rhino document, classifies geometry via architectural heuristics, creates native Revit elements with DirectShape fallback, validates 4 documentation views, and tracks revisions via ExtensibleStorage."
            };

            var pushButton = panel.AddItem(buttonData) as PushButton;

            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            return Result.Succeeded;
        }
    }
}
