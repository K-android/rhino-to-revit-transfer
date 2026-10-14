using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Rhino;

namespace RhinoToRevitTransfer
{
    /// <summary>
    /// Native WPF window implementation of the One-Click Rhino-to-Revit Transfer Dashboard.
    /// Runs directly inside Revit as a compiled modal or modeless dialog.
    /// </summary>
    public class TransferDashboardWindow : Window
    {
        private readonly Document _revitDoc;
        private readonly UIDocument _revitUiDoc;
        private readonly RhinoDoc _rhinoDoc;

        private readonly RhinoScanner _scanner;
        private readonly AecClassifier _classifier;
        private readonly SyncEngine _syncEngine;
        private readonly ConversionRouter _router;

        private List<ScannedObject> _scannedObjects = new();
        private Dictionary<Guid, ClassificationResult> _classifications = new();

        private TextBlock _lblStatus;
        private ProgressBar _progressBar;
        private TextBox _txtLog;
        private Button _btnExecute;

        public TransferDashboardWindow(Document revitDoc, UIDocument revitUiDoc, RhinoDoc rhinoDoc)
        {
            _revitDoc = revitDoc;
            _revitUiDoc = revitUiDoc;
            _rhinoDoc = rhinoDoc;

            _scanner = new RhinoScanner(_rhinoDoc);
            _classifier = new AecClassifier();
            _syncEngine = new SyncEngine(_revitDoc);
            _router = new ConversionRouter(_revitDoc);

            Title = "One-Click Rhino-to-Revit Transfer (Native C# .NET)";
            Width = 950;
            Height = 650;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;

            InitLayout();
        }

        private void InitLayout()
        {
            var root = new Grid { Margin = new Thickness(15) };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Header
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Action bar
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); // Main area
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Progress
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Footer

            // Header
            var headerStack = new StackPanel { Margin = new Thickness(0, 0, 0, 15) };
            var titleBlock = new TextBlock
            {
                Text = "ONE-CLICK RHINO-TO-REVIT TRANSFER",
                FontSize = 18,
                FontWeight = FontWeights.Bold,
                Foreground = System.Windows.Media.Brushes.Navy
            };
            var subBlock = new TextBlock
            {
                Text = "Compiled C# Revit Add-in | RIR Geometry Fingerprinting & Revision Synchronization",
                FontSize = 12,
                Foreground = System.Windows.Media.Brushes.DimGray
            };
            headerStack.Children.Add(titleBlock);
            headerStack.Children.Add(subBlock);
            Grid.SetRow(headerStack, 0);
            root.Children.Add(headerStack);

            // Action Bar
            var actionStack = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 10) };
            var btnScan = new Button
            {
                Content = "Scan Active Rhino Model",
                Padding = new Thickness(15, 8, 15, 8),
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 0, 10, 0)
            };
            btnScan.Click += OnScanClicked;

            _btnExecute = new Button
            {
                Content = "Execute R2R Transfer",
                Padding = new Thickness(15, 8, 15, 8),
                FontWeight = FontWeights.Bold,
                IsEnabled = false
            };
            _btnExecute.Click += OnExecuteClicked;

            actionStack.Children.Add(btnScan);
            actionStack.Children.Add(_btnExecute);
            Grid.SetRow(actionStack, 1);
            root.Children.Add(actionStack);

            // Main Log / Details
            _txtLog = new TextBox
            {
                IsReadOnly = true,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                FontFamily = new System.Windows.Media.FontFamily("Consolas"),
                FontSize = 12,
                Text = "Ready. Click 'Scan Active Rhino Model' to initiate preflight fingerprinting...\n"
            };
            Grid.SetRow(_txtLog, 2);
            root.Children.Add(_txtLog);

            // Progress Bar
            _progressBar = new ProgressBar
            {
                Height = 16,
                Margin = new Thickness(0, 10, 0, 5),
                Minimum = 0,
                Maximum = 100
            };
            Grid.SetRow(_progressBar, 3);
            root.Children.Add(_progressBar);

            // Status Bar
            _lblStatus = new TextBlock
            {
                Text = "Status: Idle",
                FontSize = 12,
                Foreground = System.Windows.Media.Brushes.DarkSlateGray
            };
            Grid.SetRow(_lblStatus, 4);
            root.Children.Add(_lblStatus);

            Content = root;
        }

        private void OnScanClicked(object sender, RoutedEventArgs e)
        {
            _txtLog.AppendText("Scanning Rhino Document: " + _rhinoDoc.Name + "...\n");
            _scannedObjects = _scanner.ScanAll();

            _txtLog.AppendText($"Scanned {_scannedObjects.Count} objects across {_rhinoDoc.Layers.Count} layers.\n");

            // Classification
            int autoCount = 0;
            _classifications.Clear();
            foreach (var obj in _scannedObjects)
            {
                var res = _classifier.Classify(obj);
                _classifications[obj.RhinoGuid] = res;
                if (res.ConfidenceTier == "AUTO_READY") autoCount++;
            }

            _txtLog.AppendText($"Classification complete: {autoCount} elements Auto-Ready (>=85% confidence).\n");

            // Sync evaluation
            var (states, missing) = _syncEngine.Evaluate(_scannedObjects);
            int createCount = 0, skipCount = 0, updateCount = 0;
            foreach (var kvp in states)
            {
                if (kvp.Value == "CREATE") createCount++;
                else if (kvp.Value == "SKIP_UNCHANGED") skipCount++;
                else if (kvp.Value == "UPDATE_MODIFIED") updateCount++;
            }

            _txtLog.AppendText($"Sync State: Create={createCount}, Skip={skipCount}, Update={updateCount}, MissingInRhino={missing.Count}\n");

            _btnExecute.IsEnabled = true;
            _lblStatus.Text = $"Scan ready: {createCount} to create, {updateCount} to update.";
        }

        private void OnExecuteClicked(object sender, RoutedEventArgs e)
        {
            _btnExecute.IsEnabled = false;
            _txtLog.AppendText("\nExecuting Revit API Transactions...\n");

            var collector = new FilteredElementCollector(_revitDoc).OfClass(typeof(Level));
            var firstLevel = collector.FirstElement();
            var levelId = firstLevel?.Id ?? ElementId.InvalidElementId;

            int successCount = 0;
            int skipCount = 0;

            var preprocessor = new TransferFailuresPreprocessor();

            using (var tg = new TransactionGroup(_revitDoc, "One-Click Rhino-to-Revit Transfer"))
            {
                tg.Start();

                for (int i = 0; i < _scannedObjects.Count; i++)
                {
                    var obj = _scannedObjects[i];
                    var cls = _classifications[obj.RhinoGuid];

                    using (var trans = new Transaction(_revitDoc, "Transfer Object"))
                    {
                        var opts = trans.GetFailureHandlingOptions();
                        opts.SetFailuresPreprocessor(preprocessor);
                        trans.SetFailureHandlingOptions(opts);

                        trans.Start();

                        var elem = _router.Convert(obj, cls, levelId);
                        if (elem != null)
                        {
                            _syncEngine.WriteRecord(elem, obj);
                            trans.Commit();
                            successCount++;
                            _txtLog.AppendText($"[SUCCESS] {obj.LayerName} -> Revit Element ID: {elem.Id}\n");
                        }
                        else
                        {
                            trans.RollBack();
                            skipCount++;
                        }
                    }

                    _progressBar.Value = (int)(((i + 1) / (double)_scannedObjects.Count) * 100);
                }

                tg.Assimilate();
            }

            _lblStatus.Text = $"Transfer Completed! Successfully transferred: {successCount} elements.";
            _txtLog.AppendText($"\nTransfer Finished: {successCount} created/updated, {skipCount} skipped/failed.\n");
            _btnExecute.IsEnabled = true;
        }
    }
}
