"""
One-Click Rhino-to-Revit Transfer (RIR): Module 9
ui_dialog.py — Eto.Forms Multi-Step Wizard & Dashboard UI

Provides a 5-stage interactive workflow dashboard:
  1. SCAN — Rhino model scanning & preflight metrics
  2. REVIEW & CONFIGURE — Filtering, classification overrides, profile rules, AI assist
  3. PREVIEW — Transaction count KPIs & pre-execution validation
  4. EXECUTE — Controlled Revit API TransactionGroup execution with real-time logging
  5. VALIDATE & QA REPORT — Dual-Status BIM & Graphics grid, Isolate in Revit, HTML/CSV export
"""

import sys
import os
import traceback

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("Eto")
    clr.AddReference("RhinoCommon")
    import Eto.Forms as ef
    import Eto.Drawing as ed
    import Rhino
    ETO_AVAILABLE = True
except Exception:
    ETO_AVAILABLE = False

try:
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    import Autodesk.Revit.DB as DB
    import Autodesk.Revit.UI as UI
    REVIT_AVAILABLE = True
except Exception:
    REVIT_AVAILABLE = False

# Import sibling modules
from .scanner import RhinoScanner
from .classifier import AecClassifier
from .ai_assistant import AISemanticInterpreter
from .profile_manager import ProfileManager, MappingRule
from .conversion_router import ConversionRouter
from .graphics_validator import GraphicsValidator
from .sync_engine import SyncEngine
from .reporting import TransferQAReport, ReportExporter


class LayerRowItem(object):
    """Data item model for the Review & Configure GridView."""

    def __init__(self, layer_name, objects, classification, sync_eval, checked=True):
        self.checked = checked
        self.layer_name = layer_name
        self.objects = objects  # list of ScannedObject
        self.object_count = len(objects)
        self.classification = classification  # ClassificationResult
        self.inferred_category = classification.inferred_category
        self.proposed_strategy = classification.proposed_strategy
        self.revit_type = classification.revit_category_name
        self.confidence_score = classification.confidence_score
        self.confidence_tier = classification.confidence_tier
        self.sync_state = sync_eval.sync_state if sync_eval else "CREATE"
        self.sync_eval = sync_eval


class ValidationRowItem(object):
    """Data item model for the Validate & QA Report GridView."""

    def __init__(self, val_report, layer_name, strategy):
        self.revit_id = str(val_report.revit_element_id)
        self.rhino_guid = val_report.rhino_guid
        self.layer_name = layer_name
        self.category = val_report.category_name
        self.strategy = strategy
        self.bim_status = val_report.bim_status
        self.graphics_status = val_report.graphics_status
        self.overall_status = val_report.overall_status
        self.plan = val_report.view_matrix.get("Plan", "PASS")
        self.section = val_report.view_matrix.get("Section", "PASS")
        self.issues = "; ".join(val_report.issues) if val_report.issues else "None"
        self.raw_report = val_report


class TransferDashboardDialog(ef.Dialog[bool] if ETO_AVAILABLE else object):
    """Complete 5-Stage Eto.Forms Dashboard Dialog for Rhino-to-Revit Transfer."""

    def __init__(self, revit_doc=None, revit_uidoc=None):
        if not ETO_AVAILABLE:
            return

        super(TransferDashboardDialog, self).__init__()
        self.Title = "One-Click Rhino-to-Revit Transfer (RIR)"
        self.ClientSize = ed.Size(1080, 720)
        self.MinimumSize = ed.Size(950, 600)
        self.Padding = ed.Padding(12)

        # Context
        self.revit_doc = revit_doc
        self.revit_uidoc = revit_uidoc
        self.scanner = RhinoScanner()
        self.classifier = AecClassifier()
        self.ai_interpreter = AISemanticInterpreter()
        self.profile_mgr = ProfileManager()
        self.sync_engine = SyncEngine(self.revit_doc)
        self.validator = GraphicsValidator(self.revit_doc)

        # Pipeline Session Data
        self.scanned_objects = []
        self.classifications = {}  # guid -> ClassificationResult
        self.sync_evaluations = {}  # guid -> SyncEvaluation
        self.missing_in_rhino = []
        self.layer_rows = []       # list of LayerRowItem
        self.filtered_layer_rows = []
        self.validation_rows = []  # list of ValidationRowItem
        self.qa_report = TransferQAReport()

        # Build UI Structure
        self._init_ui()

    def _init_ui(self):
        """Constructs layout, header tabs, and stage views."""
        layout = ef.DynamicLayout()
        layout.Spacing = ed.Size(8, 8)

        # Top Header Bar
        header_label = ef.Label()
        header_label.Text = "ONE-CLICK RHINO-TO-REVIT TRANSFER"
        header_label.Font = ed.Font(ed.SystemFont.Bold, 15)
        header_label.TextColor = ed.Color.FromArgb(20, 110, 190)

        subtitle = ef.Label()
        subtitle.Text = "Production-grade geometry fingerprinting, architectural semantic classification, dual-status BIM validation & revision sync"
        subtitle.TextColor = ed.Colors.Gray

        layout.Add(header_label)
        layout.Add(subtitle)
        layout.Add(ef.Separator())

        # Main 5-Stage Tab Control
        self.tabs = ef.TabControl()
        self.tab_scan = ef.TabPage()
        self.tab_scan.Text = "1. SCAN"
        self.tab_scan.Content = self._build_scan_tab()

        self.tab_review = ef.TabPage()
        self.tab_review.Text = "2. REVIEW & CONFIGURE"
        self.tab_review.Content = self._build_review_tab()

        self.tab_preview = ef.TabPage()
        self.tab_preview.Text = "3. PREVIEW"
        self.tab_preview.Content = self._build_preview_tab()

        self.tab_execute = ef.TabPage()
        self.tab_execute.Text = "4. EXECUTE"
        self.tab_execute.Content = self._build_execute_tab()

        self.tab_validate = ef.TabPage()
        self.tab_validate.Text = "5. VALIDATE & QA"
        self.tab_validate.Content = self._build_validate_tab()

        self.tabs.Pages.Add(self.tab_scan)
        self.tabs.Pages.Add(self.tab_review)
        self.tabs.Pages.Add(self.tab_preview)
        self.tabs.Pages.Add(self.tab_execute)
        self.tabs.Pages.Add(self.tab_validate)

        layout.Add(self.tabs, yscale=True)

        # Bottom Action Bar
        bottom_layout = ef.DynamicLayout()
        bottom_layout.Spacing = ed.Size(10, 5)

        self.lbl_global_status = ef.Label()
        self.lbl_global_status.Text = "Ready to scan active Rhino document."
        self.lbl_global_status.VerticalAlignment = ef.VerticalAlignment.Center

        btn_close = ef.Button()
        btn_close.Text = "Close"
        btn_close.Click += lambda sender, e: self.Close(True)

        bottom_layout.BeginHorizontal()
        bottom_layout.Add(self.lbl_global_status, xscale=True)
        bottom_layout.Add(btn_close, xscale=False)
        bottom_layout.EndHorizontal()

        layout.Add(bottom_layout)
        self.Content = layout

    # =========================================================================
    # TAB 1: SCAN
    # =========================================================================
    def _build_scan_tab(self):
        layout = ef.DynamicLayout()
        layout.Padding = ed.Padding(15)
        layout.Spacing = ed.Size(10, 10)

        desc = ef.Label()
        desc.Text = "Scan all active Rhino document objects, inspect topological features, and compute deterministic SHA-256 geometry hashes."

        # Scan controls
        self.chk_include_hidden = ef.CheckBox()
        self.chk_include_hidden.Text = "Include Hidden Layers & Objects"
        self.chk_include_hidden.Checked = False

        self.chk_include_locked = ef.CheckBox()
        self.chk_include_locked.Text = "Include Locked Layers"
        self.chk_include_locked.Checked = False

        btn_scan = ef.Button()
        btn_scan.Text = "Scan Active Rhino Document"
        btn_scan.Font = ed.Font(ed.SystemFont.Bold, 11)
        btn_scan.Size = ed.Size(260, 36)
        btn_scan.Click += self._on_scan_clicked

        layout.Add(desc)
        layout.BeginHorizontal()
        layout.Add(self.chk_include_hidden)
        layout.Add(self.chk_include_locked)
        layout.EndHorizontal()
        layout.Add(btn_scan)
        layout.Add(ef.Separator())

        # Preflight KPI Cards Box
        cards_title = ef.Label()
        cards_title.Text = "PREFLIGHT & GEOMETRIC HEALTH SUMMARY"
        cards_title.Font = ed.Font(ed.SystemFont.Bold, 11)
        layout.Add(cards_title)

        self.lbl_kpi_units = ef.Label()
        self.lbl_kpi_units.Text = "Rhino Units: Not Scanned"

        self.lbl_kpi_origin = ef.Label()
        self.lbl_kpi_origin.Text = "Distance to Origin: Safe"

        self.lbl_kpi_duplicates = ef.Label()
        self.lbl_kpi_duplicates.Text = "Duplicates Detected: 0"

        self.lbl_kpi_loops = ef.Label()
        self.lbl_kpi_loops.Text = "Non-Closed Boundary Loops: 0"

        self.lbl_kpi_total = ef.Label()
        self.lbl_kpi_total.Text = "Total Scanned Objects: 0"

        layout.Add(self.lbl_kpi_total)
        layout.Add(self.lbl_kpi_units)
        layout.Add(self.lbl_kpi_origin)
        layout.Add(self.lbl_kpi_duplicates)
        layout.Add(self.lbl_kpi_loops)

        # Warnings TextArea
        self.txt_scan_warnings = ef.TextArea()
        self.txt_scan_warnings.ReadOnly = True
        self.txt_scan_warnings.Size = ed.Size(-1, 140)
        self.txt_scan_warnings.Text = "No preflight warnings."
        layout.Add(self.txt_scan_warnings, yscale=True)

        return layout

    # =========================================================================
    # TAB 2: REVIEW & CONFIGURE
    # =========================================================================
    def _build_review_tab(self):
        layout = ef.DynamicLayout()
        layout.Padding = ed.Padding(10)
        layout.Spacing = ed.Size(8, 8)

        # Filter Bar
        filter_layout = ef.DynamicLayout()
        filter_layout.Spacing = ed.Size(8, 5)

        lbl_search = ef.Label()
        lbl_search.Text = "Search Layer:"
        self.txt_search = ef.TextBox()
        self.txt_search.Size = ed.Size(180, 24)
        self.txt_search.TextChanged += self._apply_review_filters

        lbl_cat_filter = ef.Label()
        lbl_cat_filter.Text = "Category:"
        self.dd_cat_filter = ef.DropDown()
        self.dd_cat_filter.Items.Add("All Categories")
        for cat in ["Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape"]:
            self.dd_cat_filter.Items.Add(cat)
        self.dd_cat_filter.SelectedIndex = 0
        self.dd_cat_filter.SelectedIndexChanged += self._apply_review_filters

        lbl_conf_filter = ef.Label()
        lbl_conf_filter.Text = "Confidence:"
        self.dd_conf_filter = ef.DropDown()
        self.dd_conf_filter.Items.Add("All Tiers")
        self.dd_conf_filter.Items.Add("Auto Ready (>=85%)")
        self.dd_conf_filter.Items.Add("Needs Approval (60-84%)")
        self.dd_conf_filter.Items.Add("Manual / Low (<60%)")
        self.dd_conf_filter.SelectedIndex = 0
        self.dd_conf_filter.SelectedIndexChanged += self._apply_review_filters

        lbl_sync_filter = ef.Label()
        lbl_sync_filter.Text = "Sync State:"
        self.dd_sync_filter = ef.DropDown()
        self.dd_sync_filter.Items.Add("All States")
        self.dd_sync_filter.Items.Add("CREATE")
        self.dd_sync_filter.Items.Add("UPDATE_MODIFIED")
        self.dd_sync_filter.Items.Add("SKIP_UNCHANGED")
        self.dd_sync_filter.SelectedIndex = 0
        self.dd_sync_filter.SelectedIndexChanged += self._apply_review_filters

        filter_layout.BeginHorizontal()
        filter_layout.Add(lbl_search)
        filter_layout.Add(self.txt_search)
        filter_layout.Add(lbl_cat_filter)
        filter_layout.Add(self.dd_cat_filter)
        filter_layout.Add(lbl_conf_filter)
        filter_layout.Add(self.dd_conf_filter)
        filter_layout.Add(lbl_sync_filter)
        filter_layout.Add(self.dd_sync_filter)
        filter_layout.EndHorizontal()

        layout.Add(filter_layout)

        # Global Mode & Options Bar
        opts_layout = ef.DynamicLayout()
        opts_layout.Spacing = ed.Size(10, 5)

        lbl_mode = ef.Label()
        lbl_mode.Text = "Execution Mode:"
        lbl_mode.VerticalAlignment = ef.VerticalAlignment.Center

        self.dd_global_mode = ef.DropDown()
        self.dd_global_mode.Items.Add("Hybrid")
        self.dd_global_mode.Items.Add("Native BIM")
        self.dd_global_mode.Items.Add("Geometry-First")
        self.dd_global_mode.SelectedIndex = 0
        self.dd_global_mode.SelectedIndexChanged += self._on_mode_changed

        self.chk_gen_bua = ef.CheckBox()
        self.chk_gen_bua.Text = "Auto-Generate BUA / GFA Area Boundary Lines"
        self.chk_gen_bua.Checked = True

        btn_ai_assist = ef.Button()
        btn_ai_assist.Text = "Run AI Semantic Assist"
        btn_ai_assist.ToolTip = "Interpret Spanish/Catalan architectural layer naming using semantic AI heuristics"
        btn_ai_assist.Click += self._on_run_ai_assist

        btn_load_profile = ef.Button()
        btn_load_profile.Text = "Load Profile"
        btn_load_profile.Click += self._on_load_profile

        btn_save_profile = ef.Button()
        btn_save_profile.Text = "Save Profile"
        btn_save_profile.Click += self._on_save_profile

        opts_layout.BeginHorizontal()
        opts_layout.Add(lbl_mode)
        opts_layout.Add(self.dd_global_mode)
        opts_layout.Add(self.chk_gen_bua)
        opts_layout.Add(btn_ai_assist)
        opts_layout.Add(btn_load_profile)
        opts_layout.Add(btn_save_profile)
        opts_layout.EndHorizontal()

        layout.Add(opts_layout)

        # DataGridView
        self.grid_review = ef.GridView()
        self.grid_review.ShowHeader = True
        self.grid_review.AllowMultipleSelection = True

        # Checkbox column
        col_check = ef.GridColumn()
        col_check.HeaderText = "Select"
        col_check.DataCell = ef.CheckBoxCell(0)
        col_check.Editable = True
        self.grid_review.Columns.Add(col_check)

        # Layer Name
        col_layer = ef.GridColumn()
        col_layer.HeaderText = "Rhino Layer"
        col_layer.DataCell = ef.TextBoxCell(1)
        col_layer.Width = 240
        self.grid_review.Columns.Add(col_layer)

        # Count
        col_count = ef.GridColumn()
        col_count.HeaderText = "Count"
        col_count.DataCell = ef.TextBoxCell(2)
        col_count.Width = 60
        self.grid_review.Columns.Add(col_count)

        # Inferred Category
        col_cat = ef.GridColumn()
        col_cat.HeaderText = "Inferred Category"
        col_cat.DataCell = ef.TextBoxCell(3)
        col_cat.Width = 140
        self.grid_review.Columns.Add(col_cat)

        # Proposed Strategy
        col_strat = ef.GridColumn()
        col_strat.HeaderText = "Proposed Strategy"
        col_strat.DataCell = ef.TextBoxCell(4)
        col_strat.Width = 140
        self.grid_review.Columns.Add(col_strat)

        # Target Type
        col_type = ef.GridColumn()
        col_type.HeaderText = "Target Revit Type"
        col_type.DataCell = ef.TextBoxCell(5)
        col_type.Width = 150
        self.grid_review.Columns.Add(col_type)

        # Confidence
        col_conf = ef.GridColumn()
        col_conf.HeaderText = "Confidence"
        col_conf.DataCell = ef.TextBoxCell(6)
        col_conf.Width = 90
        self.grid_review.Columns.Add(col_conf)

        # Sync State
        col_sync = ef.GridColumn()
        col_sync.HeaderText = "Sync State"
        col_sync.DataCell = ef.TextBoxCell(7)
        col_sync.Width = 110
        self.grid_review.Columns.Add(col_sync)

        layout.Add(self.grid_review, yscale=True)
        return layout

    # =========================================================================
    # TAB 3: PREVIEW
    # =========================================================================
    def _build_preview_tab(self):
        layout = ef.DynamicLayout()
        layout.Padding = ed.Padding(20)
        layout.Spacing = ed.Size(12, 12)

        title = ef.Label()
        title.Text = "PRE-EXECUTION TRANSACTION DASHBOARD"
        title.Font = ed.Font(ed.SystemFont.Bold, 12)
        layout.Add(title)

        self.lbl_prev_create = ef.Label()
        self.lbl_prev_create.Text = "Elements to CREATE: 0"
        self.lbl_prev_create.Font = ed.Font(ed.SystemFont.Bold, 11)

        self.lbl_prev_update = ef.Label()
        self.lbl_prev_update.Text = "Elements to UPDATE (Modified Geometry): 0"

        self.lbl_prev_skip = ef.Label()
        self.lbl_prev_skip.Text = "Elements to SKIP (Unchanged Geometry): 0"

        self.lbl_prev_review = ef.Label()
        self.lbl_prev_review.Text = "Elements in REVIEW QUEUE (<60% Confidence): 0"

        self.lbl_prev_missing = ef.Label()
        self.lbl_prev_missing.Text = "Revit Elements MISSING in Rhino (Preserved): 0"

        layout.Add(self.lbl_prev_create)
        layout.Add(self.lbl_prev_update)
        layout.Add(self.lbl_prev_skip)
        layout.Add(self.lbl_prev_review)
        layout.Add(self.lbl_prev_missing)
        layout.Add(ef.Separator())

        notice = ef.Label()
        notice.Text = "Notice: Clicking 'Proceed to Transfer' will switch to the Execute tab and initiate Revit API Transactions with automated warning suppression."
        notice.TextColor = ed.Colors.Gray
        layout.Add(notice)

        btn_go_execute = ef.Button()
        btn_go_execute.Text = "Proceed to Transfer ->"
        btn_go_execute.Font = ed.Font(ed.SystemFont.Bold, 11)
        btn_go_execute.Size = ed.Size(220, 36)

        def _on_nav_to_execute(s, e):
            self.tabs.SelectedIndex = 3

        btn_go_execute.Click += _on_nav_to_execute
        layout.Add(btn_go_execute)

        return layout

    # =========================================================================
    # TAB 4: EXECUTE
    # =========================================================================
    def _build_execute_tab(self):
        layout = ef.DynamicLayout()
        layout.Padding = ed.Padding(20)
        layout.Spacing = ed.Size(12, 12)

        title = ef.Label()
        title.Text = "REVIT TRANSACTION EXECUTION PIPELINE"
        title.Font = ed.Font(ed.SystemFont.Bold, 12)
        layout.Add(title)

        self.btn_execute = ef.Button()
        self.btn_execute.Text = "Execute R2R Transfer"
        self.btn_execute.Font = ed.Font(ed.SystemFont.Bold, 12)
        self.btn_execute.Size = ed.Size(260, 40)
        self.btn_execute.Click += self._on_execute_transfer
        layout.Add(self.btn_execute)

        self.progress_bar = ef.ProgressBar()
        self.progress_bar.MinValue = 0
        self.progress_bar.MaxValue = 100
        self.progress_bar.Value = 0
        layout.Add(self.progress_bar)

        self.lbl_exec_status = ef.Label()
        self.lbl_exec_status.Text = "Status: Idle. Click 'Execute R2R Transfer' to begin."
        layout.Add(self.lbl_exec_status)

        lbl_log_header = ef.Label()
        lbl_log_header.Text = "Live Execution Log:"
        layout.Add(lbl_log_header)

        self.txt_exec_log = ef.TextArea()
        self.txt_exec_log.ReadOnly = True
        self.txt_exec_log.Size = ed.Size(-1, 260)
        layout.Add(self.txt_exec_log, yscale=True)

        return layout

    # =========================================================================
    # TAB 5: VALIDATE & QA REPORT
    # =========================================================================
    def _build_validate_tab(self):
        layout = ef.DynamicLayout()
        layout.Padding = ed.Padding(10)
        layout.Spacing = ed.Size(8, 8)

        # Title & Summary
        top_layout = ef.DynamicLayout()
        top_layout.Spacing = ed.Size(10, 5)

        lbl_val_title = ef.Label()
        lbl_val_title.Text = "DUAL-STATUS BIM & GRAPHICS VALIDATION AUDIT"
        lbl_val_title.Font = ed.Font(ed.SystemFont.Bold, 12)

        btn_isolate = ef.Button()
        btn_isolate.Text = "Select / Isolate in Revit"
        btn_isolate.ToolTip = "Highlight and isolate selected element in the active Revit view"
        btn_isolate.Click += self._on_isolate_element

        btn_export_html = ef.Button()
        btn_export_html.Text = "Export QA Report (HTML)"
        btn_export_html.Click += self._on_export_html

        btn_export_csv = ef.Button()
        btn_export_csv.Text = "Export QA Report (CSV)"
        btn_export_csv.Click += self._on_export_csv

        top_layout.BeginHorizontal()
        top_layout.Add(lbl_val_title, xscale=True)
        top_layout.Add(btn_isolate)
        top_layout.Add(btn_export_html)
        top_layout.Add(btn_export_csv)
        top_layout.EndHorizontal()

        layout.Add(top_layout)

        # Validation GridView
        self.grid_validate = ef.GridView()
        self.grid_validate.ShowHeader = True

        col_defs = [
            ("Revit ID", 75),
            ("Rhino GUID", 120),
            ("Layer", 150),
            ("Category", 120),
            ("Strategy", 110),
            ("BIM Status", 85),
            ("Graphics Status", 105),
            ("Overall Status", 120),
            ("Plan", 70),
            ("Section", 70),
            ("Issues / Recommendations", 220)
        ]

        for idx, (header, width) in enumerate(col_defs):
            col = ef.GridColumn()
            col.HeaderText = header
            col.DataCell = ef.TextBoxCell(idx)
            col.Width = width
            self.grid_validate.Columns.Add(col)

        layout.Add(self.grid_validate, yscale=True)
        return layout

    # =========================================================================
    # EVENT HANDLERS & LOGIC
    # =========================================================================
    def _on_scan_clicked(self, sender, e):
        """Executes full scan of active Rhino document and updates UI."""
        self.lbl_global_status.Text = "Scanning Rhino document..."

        # Query Revit Levels if doc is available
        revit_levels = []
        if REVIT_AVAILABLE and self.revit_doc:
            collector = DB.FilteredElementCollector(self.revit_doc).OfClass(DB.Level)
            # Unit scale from feet to Rhino units
            scale_from_ft = 1.0 / self.scanner.get_unit_scale_to_meters() * 0.3048
            for lvl in collector:
                revit_levels.append({
                    "name": lvl.Name,
                    "elevation": lvl.Elevation * scale_from_ft
                })

        include_hidden = self.chk_include_hidden.Checked or False
        include_locked = self.chk_include_locked.Checked or False

        self.scanned_objects = self.scanner.scan_all(
            include_hidden=include_hidden,
            include_locked=include_locked,
            revit_levels=revit_levels
        )

        pf = self.scanner.preflight
        self.lbl_kpi_total.Text = "Total Scanned Objects: {}".format(pf.total_scanned)
        self.lbl_kpi_units.Text = "Rhino Units: {} (Scale to m: {:.4f})".format(pf.rhino_unit_system, pf.unit_scale_to_meters)
        self.lbl_kpi_origin.Text = "Far From Origin (>16km): {}".format(pf.total_far_from_origin)
        self.lbl_kpi_duplicates.Text = "Duplicates Detected: {}".format(pf.total_duplicates)
        self.lbl_kpi_loops.Text = "Non-Closed Boundary Loops: {}".format(pf.total_non_closed_loops)

        # Warnings display
        warn_text = []
        if pf.total_duplicates > 0:
            warn_text.append("[WARNING] Found {} duplicate objects with identical geometry hashes.".format(pf.total_duplicates))
        if pf.total_far_from_origin > 0:
            warn_text.append("[CAUTION] Found {} objects beyond Revit's 16km origin boundary limit.".format(pf.total_far_from_origin))
        if pf.total_non_closed_loops > 0:
            warn_text.append("[NOTICE] Found {} objects with non-closed boundary loops.".format(pf.total_non_closed_loops))

        self.txt_scan_warnings.Text = "\n".join(warn_text) if warn_text else "Preflight clean. No geometric anomalies detected."

        # Run Classification & Sync Evaluation
        self._run_classification_and_sync()

        # Populate Review Grid
        self._populate_review_grid()

        # Update Preview Tab KPIs
        self._update_preview_kpis()

        self.lbl_global_status.Text = "Scan complete. {} objects ready for review.".format(len(self.scanned_objects))
        self.tabs.SelectedIndex = 1  # Move to Review tab

    def _run_classification_and_sync(self):
        """Runs classifier and sync engine on scanned objects."""
        self.classifier.mode = str(self.dd_global_mode.SelectedValue)
        self.classifier.prepare_repetition_cache(self.scanned_objects)

        self.classifications = {}
        for obj in self.scanned_objects:
            res = self.classifier.classify_object(obj, self.scanned_objects)
            self.classifications[obj.rhino_guid] = res

        # Apply Persistent Profile
        self.profile_mgr.apply_profile(self.scanned_objects, self.classifications)

        # Sync Engine Evaluation
        self.sync_evaluations, self.missing_in_rhino = self.sync_engine.evaluate_sync_states(self.scanned_objects)

    def _populate_review_grid(self):
        """Groups scanned objects by layer and populates DataGridView."""
        layer_groups = {}
        for obj in self.scanned_objects:
            lp = obj.layer_full_path
            if lp not in layer_groups:
                layer_groups[lp] = []
            layer_groups[lp].append(obj)

        self.layer_rows = []
        for lp, objs in layer_groups.items():
            rep_obj = objs[0]
            c_res = self.classifications.get(rep_obj.rhino_guid)
            s_ev = self.sync_evaluations.get(rep_obj.rhino_guid)

            checked = c_res.is_prechecked if c_res else False
            row = LayerRowItem(lp, objs, c_res, s_ev, checked=checked)
            self.layer_rows.append(row)

        self._apply_review_filters(None, None)

    def _apply_review_filters(self, sender, e):
        """Filters the review DataGridView based on search text, category, confidence and sync state."""
        search_txt = (self.txt_search.Text or "").strip().lower()
        cat_filter = str(self.dd_cat_filter.SelectedValue or "All Categories")
        conf_filter = str(self.dd_conf_filter.SelectedValue or "All Tiers")
        sync_filter = str(self.dd_sync_filter.SelectedValue or "All States")

        self.filtered_layer_rows = []
        for r in self.layer_rows:
            if search_txt and search_txt not in r.layer_name.lower():
                continue
            if cat_filter != "All Categories" and r.inferred_category != cat_filter:
                continue
            if conf_filter.startswith("Auto") and r.confidence_tier != "AUTO_READY":
                continue
            if conf_filter.startswith("Needs") and r.confidence_tier != "SUGGESTED_NEEDS_APPROVAL":
                continue
            if conf_filter.startswith("Manual") and r.confidence_tier != "MANUAL_OR_EXCLUDED":
                continue
            if sync_filter != "All States" and r.sync_state != sync_filter:
                continue

            self.filtered_layer_rows.append(r)

        # Bind to Eto GridView data store
        grid_data = []
        for r in self.filtered_layer_rows:
            grid_data.append([
                r.checked,
                r.layer_name,
                str(r.object_count),
                r.inferred_category,
                r.proposed_strategy,
                r.revit_type,
                "{:.0f}%".format(r.confidence_score),
                r.sync_state
            ])

        self.grid_review.DataStore = grid_data

    def _update_preview_kpis(self):
        """Calculates exact execution counts for the Preview tab."""
        cnt_create = 0
        cnt_update = 0
        cnt_skip = 0
        cnt_review = 0

        for r in self.layer_rows:
            if not r.checked:
                continue
            if r.sync_state == "CREATE":
                cnt_create += r.object_count
            elif r.sync_state == "UPDATE_MODIFIED":
                cnt_update += r.object_count
            elif r.sync_state == "SKIP_UNCHANGED":
                cnt_skip += r.object_count

            if r.confidence_score < 60.0:
                cnt_review += r.object_count

        self.lbl_prev_create.Text = "Elements to CREATE: {}".format(cnt_create)
        self.lbl_prev_update.Text = "Elements to UPDATE (Modified Geometry): {}".format(cnt_update)
        self.lbl_prev_skip.Text = "Elements to SKIP (Unchanged Geometry): {}".format(cnt_skip)
        self.lbl_prev_review.Text = "Elements in REVIEW QUEUE (<60% Confidence): {}".format(cnt_review)
        self.lbl_prev_missing.Text = "Revit Elements MISSING in Rhino (Preserved): {}".format(len(self.missing_in_rhino))

    def _on_mode_changed(self, sender, e):
        """Re-evaluates proposed strategies when global mode changes."""
        self._run_classification_and_sync()
        self._populate_review_grid()
        self._update_preview_kpis()

    def _on_run_ai_assist(self, sender, e):
        """Uses AI semantic interpreter to resolve ambiguous Catalan/Spanish layers."""
        self.lbl_global_status.Text = "Running semantic interpretation on layers..."
        boosted = 0
        for r in self.layer_rows:
            interp = self.ai_interpreter.interpret(r.layer_name)
            if interp and interp.confidence_boost > 0:
                r.inferred_category = interp.inferred_category
                r.revit_type = interp.suggested_revit_type
                r.confidence_score = min(100.0, r.confidence_score + (interp.confidence_boost * 20.0))
                r.checked = True
                boosted += 1

        self._apply_review_filters(None, None)
        self.lbl_global_status.Text = "AI Semantic Assist refined {} layer classifications.".format(boosted)

    def _on_load_profile(self, sender, e):
        """Loads transfer profile from disk."""
        if self.profile_mgr.load():
            self._run_classification_and_sync()
            self._populate_review_grid()
            ef.MessageBox.Show("Transfer Profile loaded successfully.", "Profile Manager")

    def _on_save_profile(self, sender, e):
        """Saves current layer table configurations as persistent profile rules."""
        for r in self.layer_rows:
            if r.checked:
                rule = MappingRule(
                    layer_pattern=r.layer_name,
                    revit_category=r.classification.revit_category_name if r.classification else "OST_GenericModel",
                    revit_type=r.revit_type,
                    strategy=r.proposed_strategy,
                    room_bounding=True
                )
                self.profile_mgr.add_rule(rule)

        self.profile_mgr.save()
        ef.MessageBox.Show("Active mappings saved to Transfer_Profile.json.", "Profile Manager")

    def _on_execute_transfer(self, sender, e):
        """Executes full transfer with Revit TransactionGroup and Failure handling."""
        if not REVIT_AVAILABLE or not self.revit_doc:
            ef.MessageBox.Show("Revit document is not available. Please run inside Rhino.Inside.Revit.", "Transfer")
            return

        self.btn_execute.Enabled = False
        self.lbl_exec_status.Text = "Starting transfer transaction..."
        self.txt_exec_log.Text = "Beginning R2R Transfer TransactionGroup...\n"

        router = ConversionRouter(
            self.revit_doc,
            global_mode=str(self.dd_global_mode.SelectedValue),
            generate_bua=self.chk_gen_bua.Checked or False
        )

        # Collect objects to transfer
        approved_objs = []
        for r in self.layer_rows:
            if r.checked:
                approved_objs.extend(r.objects)

        total = len(approved_objs)
        if total == 0:
            self.txt_exec_log.Text += "No objects approved for transfer.\n"
            self.btn_execute.Enabled = True
            return

        created_cnt = 0
        updated_cnt = 0
        skipped_cnt = 0
        failed_cnt = 0

        self.validation_rows = []
        self.qa_report = TransferQAReport()
        self.qa_report.execution_mode = str(self.dd_global_mode.SelectedValue)

        tg = DB.TransactionGroup(self.revit_doc, "R2R One-Click Transfer")
        tg.Start()

        try:
            for idx, obj in enumerate(approved_objs):
                guid = obj.rhino_guid
                c_res = self.classifications.get(guid)
                s_ev = self.sync_evaluations.get(guid)

                # Skip unchanged geometry
                if s_ev and s_ev.sync_state == "SKIP_UNCHANGED":
                    skipped_cnt += 1
                    self.txt_exec_log.Text += "[SKIP] Object {} (Hash match: {})\n".format(guid[:8], obj.geo_hash[:8])
                    continue

                # Run transaction per element for robust isolation
                with DB.Transaction(self.revit_doc, "Transfer R2R Object") as trans:
                    if router.failures_preprocessor:
                        opts = trans.GetFailureHandlingOptions()
                        opts.SetFailuresPreprocessor(router.failures_preprocessor)
                        trans.SetFailureHandlingOptions(opts)

                    trans.Start()

                    conv_res = router.convert_object(obj, c_res)

                    if conv_res.status == "SUCCESS" and conv_res.revit_element_id != -1:
                        # Write ExtensibleStorage sync tracking
                        created_elem = self.revit_doc.GetElement(DB.ElementId(conv_res.revit_element_id))
                        self.sync_engine.write_sync_record(created_elem, obj, conv_res.strategy_used)

                        trans.Commit()

                        if s_ev and s_ev.sync_state == "UPDATE_MODIFIED":
                            updated_cnt += 1
                        else:
                            created_cnt += 1

                        self.txt_exec_log.Text += "[SUCCESS] {} -> Revit ID {} ({})\n".format(
                            obj.layer_name, conv_res.revit_element_id, conv_res.strategy_used
                        )

                        # Dual-Status Validation
                        val_rep = self.validator.validate_element(conv_res.revit_element_id, guid)
                        val_row = ValidationRowItem(val_rep, obj.layer_name, conv_res.strategy_used)
                        self.validation_rows.append(val_row)

                        # Log to QA report
                        self.qa_report.element_records.append({
                            "rhino_guid": guid,
                            "revit_element_id": conv_res.revit_element_id,
                            "layer_name": obj.layer_name,
                            "category": c_res.inferred_category if c_res else "Generic",
                            "strategy": conv_res.strategy_used,
                            "sync_state": s_ev.sync_state if s_ev else "CREATE",
                            "confidence_pct": c_res.confidence_score if c_res else 0,
                            "bim_status": val_rep.bim_status,
                            "graphics_status": val_rep.graphics_status,
                            "overall_status": val_rep.overall_status,
                            "view_matrix": val_rep.view_matrix,
                            "rhino_area": obj.area,
                            "bua_area": conv_res.bua_area,
                            "issues": val_rep.issues
                        })

                    else:
                        trans.RollBack()
                        failed_cnt += 1
                        self.txt_exec_log.Text += "[ERROR] {} -> Failed: {}\n".format(obj.layer_name, conv_res.error_message)

                # Update progress
                pct = int(((idx + 1) / float(total)) * 100)
                self.progress_bar.Value = pct

            tg.Assimilate()
            self.lbl_exec_status.Text = "Transfer complete! Created: {}, Updated: {}, Skipped: {}, Errors: {}".format(
                created_cnt, updated_cnt, skipped_cnt, failed_cnt
            )

        except Exception as ex:
            tg.RollBack()
            self.lbl_exec_status.Text = "Transfer failed with error: {}".format(ex)
            self.txt_exec_log.Text += "\nFATAL EXCEPTION: {}\n{}".format(ex, traceback.format_exc())

        finally:
            self.btn_execute.Enabled = True

        # Populate Validation Tab
        self._populate_validation_grid()

        # Update report stats
        self.qa_report.transfer_stats["created"] = created_cnt
        self.qa_report.transfer_stats["updated"] = updated_cnt
        self.qa_report.transfer_stats["skipped"] = skipped_cnt
        self.qa_report.transfer_stats["failed"] = failed_cnt
        self.qa_report.transfer_stats["missing_in_rhino"] = len(self.missing_in_rhino)

        self.tabs.SelectedIndex = 4  # Move to Validation tab

    def _populate_validation_grid(self):
        """Populates the Validate & QA Report GridView."""
        grid_data = []
        for r in self.validation_rows:
            grid_data.append([
                r.revit_id,
                r.rhino_guid[:8] + "...",
                r.layer_name,
                r.category,
                r.strategy,
                r.bim_status,
                r.graphics_status,
                r.overall_status,
                r.plan,
                r.section,
                r.issues
            ])
        self.grid_validate.DataStore = grid_data

    def _on_isolate_element(self, sender, e):
        """Isolates highlighted row's element in active Revit view."""
        sel_idx = self.grid_validate.SelectedRow
        if sel_idx < 0 or sel_idx >= len(self.validation_rows):
            ef.MessageBox.Show("Please select an element row in the table first.", "Isolate Element")
            return

        row = self.validation_rows[sel_idx]
        elem_id = int(row.revit_id)
        if elem_id > 0 and self.revit_uidoc:
            GraphicsValidator.isolate_element_in_view(self.revit_uidoc, elem_id)
            self.lbl_global_status.Text = "Element ID {} isolated in Revit view.".format(elem_id)

    def _on_export_html(self, sender, e):
        """Exports QA report as interactive HTML."""
        dlg = ef.SaveFileDialog()
        dlg.Title = "Save QA Report (HTML)"
        dlg.Filters.Add(ef.FileFilter("HTML File (*.html)", "*.html"))
        dlg.FileName = "R2R_QA_Report.html"

        if dlg.ShowDialog(self) == ef.DialogResult.Ok:
            dest = dlg.FileName
            ReportExporter.export_html(self.qa_report, dest)
            ef.MessageBox.Show("HTML QA Report generated at:\n{}".format(dest), "QA Export")

    def _on_export_csv(self, sender, e):
        """Exports QA report as CSV."""
        dlg = ef.SaveFileDialog()
        dlg.Title = "Save QA Report (CSV)"
        dlg.Filters.Add(ef.FileFilter("CSV File (*.csv)", "*.csv"))
        dlg.FileName = "R2R_QA_Report.csv"

        if dlg.ShowDialog(self) == ef.DialogResult.Ok:
            dest = dlg.FileName
            ReportExporter.export_csv(self.qa_report, dest)
            ef.MessageBox.Show("CSV QA Report generated at:\n{}".format(dest), "QA Export")
