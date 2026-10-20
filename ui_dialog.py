# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 9
ui_dialog.py - Eto.Forms Multi-Step Wizard & Dashboard UI

Provides a 5-stage interactive workflow dashboard:
  1. SCAN - Rhino model scanning & preflight metrics
  2. REVIEW & CONFIGURE - Filtering, classification overrides, profile rules, AI assist
  3. PREVIEW - Transaction count KPIs & pre-execution validation
  4. EXECUTE - Controlled Revit API TransactionGroup execution with real-time logging
  5. VALIDATE & QA REPORT - Dual-Status BIM & Graphics grid, Isolate in Revit, HTML/CSV export
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

# Import sibling modules (supports both standalone module and package-relative modes)
try:
    from scanner import RhinoScanner
    from classifier import AecClassifier
    from ai_assistant import AISemanticInterpreter, PROVIDER_PRESETS
    from profile_manager import ProfileManager, MappingRule
    from conversion_router import ConversionRouter
    from graphics_validator import GraphicsValidator
    from sync_engine import SyncEngine
    from reporting import TransferQAReport, ReportExporter
except (ImportError, ValueError):
    from .scanner import RhinoScanner
    from .classifier import AecClassifier
    from .ai_assistant import AISemanticInterpreter, PROVIDER_PRESETS
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
        self.inferred_category = classification.inferred_category if classification else "Generic"
        self.proposed_strategy = classification.proposed_strategy if classification else "DirectShape"
        self.revit_type = classification.revit_category_name if classification else "OST_GenericModel"
        self.confidence_score = classification.confidence_score if classification else 0.0
        self.confidence_tier = classification.confidence_tier if classification else "MANUAL_OR_EXCLUDED"
        self.sync_state = sync_eval.sync_state if sync_eval else "CREATE"
        self.sync_eval = sync_eval

        # Geometric pattern & adaptive summary tag
        adaptive_cnt = sum(1 for o in objects if getattr(o, "adaptive_point_count", 0) in (2, 3, 4))
        rep_cnt = max([getattr(o, "repetition_count", 1) for o in objects] + [1])
        if adaptive_cnt > 0:
            sample_pts = next((o.adaptive_point_count for o in objects if getattr(o, "adaptive_point_count", 0) in (2, 3, 4)), 4)
            pt_desc = "4-Pt Quad" if sample_pts == 4 else ("3-Pt Tri" if sample_pts == 3 else "2-Pt Strut")
            self.pattern_tag = "{} ({}x)".format(pt_desc, adaptive_cnt)
        elif any(getattr(o, "object_type", "") == "InstanceReference" for o in objects):
            blk_name = next((o.block_name for o in objects if getattr(o, "block_name", "")), "Block")
            self.pattern_tag = "Block: {} ({}x)".format(blk_name[:12], len(objects))
        elif rep_cnt >= 2:
            self.pattern_tag = "Repetitive ({}x)".format(rep_cnt)
        else:
            self.pattern_tag = "Unique Mesh/Brep"

    def update_strategy(self, new_strategy):
        """Updates proposed strategy and recalculates confidence score based on geometric compatibility."""
        self.proposed_strategy = str(new_strategy).strip()
        comp_score = 100.0

        if self.proposed_strategy == "AdaptiveComponent":
            pts = sum(1 for o in self.objects if getattr(o, "adaptive_point_count", 0) in (2, 3, 4))
            if pts == len(self.objects):
                comp_score = 98.0
            elif pts > 0:
                comp_score = round((float(pts) / len(self.objects)) * 85.0, 1)
            else:
                comp_score = 0.0  # 0% confidence: physically impossible to place without 2, 3, or 4 boundary points

        elif self.proposed_strategy == "FloorNative":
            horiz_cnt = sum(1 for o in self.objects if getattr(o, "is_horizontal", False))
            comp_score = 96.0 if horiz_cnt == len(self.objects) else (round((float(horiz_cnt) / len(self.objects)) * 80.0, 1) if horiz_cnt > 0 else 0.0)

        elif self.proposed_strategy == "WallByCurve":
            vert_cnt = sum(1 for o in self.objects if getattr(o, "is_vertical", False))
            comp_score = 96.0 if vert_cnt == len(self.objects) else (round((float(vert_cnt) / len(self.objects)) * 80.0, 1) if vert_cnt > 0 else 0.0)

        elif self.proposed_strategy in ("RoofNative", "CeilingNative"):
            horiz_cnt = sum(1 for o in self.objects if getattr(o, "is_horizontal", False))
            comp_score = 95.0 if horiz_cnt > 0 else 0.0

        elif self.proposed_strategy == "Toposolid":
            horiz_cnt = sum(1 for o in self.objects if getattr(o, "is_horizontal", False))
            vert_cnt = sum(1 for o in self.objects if getattr(o, "is_vertical", False))
            if vert_cnt == len(self.objects):
                comp_score = 0.0  # Vertical geometry cannot form a Toposolid
            else:
                comp_score = 96.0 if horiz_cnt > 0 else 85.0

        elif self.proposed_strategy == "RepetitiveFamily":
            rep_cnt = max([getattr(o, "repetition_count", 1) for o in self.objects] + [1])
            is_blk = any(getattr(o, "object_type", "") == "InstanceReference" for o in self.objects)
            comp_score = 98.0 if (rep_cnt >= 2 or is_blk) else 75.0

        elif self.proposed_strategy == "ParametricFamily":
            has_dims = all(bool(getattr(o, "parametric_dims", {})) for o in self.objects)
            comp_score = 95.0 if has_dims else 0.0

        elif self.proposed_strategy == "DirectShape":
            comp_score = 100.0  # 100% universal geometric compatibility

        else:
            comp_score = 95.0

        self.confidence_score = comp_score
        if comp_score >= 85.0:
            self.confidence_tier = "AUTO_READY"
        elif comp_score >= 60.0:
            self.confidence_tier = "SUGGESTED_NEEDS_APPROVAL"
        else:
            self.confidence_tier = "MANUAL_OR_EXCLUDED"
            if comp_score == 0.0:
                self.checked = False  # Auto-uncheck physically incompatible strategy

        if self.classification:
            self.classification.proposed_strategy = self.proposed_strategy
            self.classification.confidence_score = comp_score
            self.classification.confidence_tier = self.confidence_tier

    def update_category(self, new_category):
        """Updates inferred Revit category, maps to proper BuiltInCategory name, and adjusts strategy."""
        self.inferred_category = str(new_category).strip()
        cat_lower = self.inferred_category.lower()

        # Map to standard OST Revit Category
        if "toposolid" in cat_lower or "topograph" in cat_lower or "terrain" in cat_lower:
            self.revit_type = "OST_Toposolid"
            if self.proposed_strategy not in ("Toposolid", "DirectShape"):
                self.proposed_strategy = "Toposolid"
        elif "floor" in cat_lower or "slab" in cat_lower:
            self.revit_type = "OST_Floors"
            if self.proposed_strategy in ("RoofNative", "CeilingNative", "Toposolid"):
                self.proposed_strategy = "FloorNative"
        elif "roof" in cat_lower:
            self.revit_type = "OST_Roofs"
            if self.proposed_strategy in ("FloorNative", "CeilingNative", "Toposolid"):
                self.proposed_strategy = "RoofNative"
        elif "wall" in cat_lower:
            self.revit_type = "OST_Walls"
            if self.proposed_strategy in ("FloorNative", "RoofNative", "Toposolid"):
                self.proposed_strategy = "WallByCurve"
        elif "ceiling" in cat_lower:
            self.revit_type = "OST_Ceilings"
            if self.proposed_strategy in ("FloorNative", "RoofNative", "Toposolid"):
                self.proposed_strategy = "CeilingNative"
        elif "column" in cat_lower or "pillar" in cat_lower:
            self.revit_type = "OST_Columns"
        elif "curtain" in cat_lower or "panel" in cat_lower:
            self.revit_type = "OST_CurtainWallPanels"
        elif "door" in cat_lower:
            self.revit_type = "OST_Doors"
        elif "window" in cat_lower:
            self.revit_type = "OST_Windows"
        elif "furniture" in cat_lower:
            self.revit_type = "OST_Furniture"
        elif "site" in cat_lower:
            self.revit_type = "OST_Site"
        elif "stair" in cat_lower:
            self.revit_type = "OST_Stairs"
        elif "mass" in cat_lower:
            self.revit_type = "OST_Mass"
        elif "generic" in cat_lower:
            self.revit_type = "OST_GenericModel"
        elif self.inferred_category.startswith("OST_"):
            self.revit_type = self.inferred_category
        else:
            self.revit_type = "OST_GenericModel"

        if self.classification:
            self.classification.inferred_category = self.inferred_category
            self.classification.revit_category_name = self.revit_type
            self.classification.proposed_strategy = self.proposed_strategy

        for obj in self.objects:
            if hasattr(obj, "classification") and obj.classification:
                obj.classification.inferred_category = self.inferred_category
                obj.classification.revit_category_name = self.revit_type
                obj.classification.proposed_strategy = self.proposed_strategy

        self.update_strategy(self.proposed_strategy)


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


class AISettingsDialog(ef.Dialog[bool] if ETO_AVAILABLE else object):
    """Configuration modal for LLM Provider, API Key, Model Engine, and Custom Endpoints."""

    def __init__(self, ai_interpreter, parent=None):
        if not ETO_AVAILABLE:
            return

        super(AISettingsDialog, self).__init__()
        self.Title = "R2R AI Model & API Key Configuration"
        self.ClientSize = ed.Size(560, 420)
        self.Resizable = False
        self.Padding = ed.Padding(16)
        self.ai_interpreter = ai_interpreter

        layout = ef.DynamicLayout()
        layout.Spacing = ed.Size(10, 10)

        # Header Title
        lbl_header = ef.Label()
        lbl_header.Text = "AI Semantic Classifier & Model Settings"
        try:
            lbl_header.Font = ed.Font(ed.SystemFont.Bold, 12)
        except Exception:
            pass
        layout.Add(lbl_header)

        lbl_desc = ef.Label()
        lbl_desc.Text = (
            "Configure an AI provider to automatically decode complex layer names, foreign\n"
            "architectural naming conventions, and ambiguous geometric categories."
        )
        try:
            lbl_desc.TextColor = ed.Colors.DimGray
        except Exception:
            pass
        layout.Add(lbl_desc)

        # Provider Selection
        lbl_provider = ef.Label()
        lbl_provider.Text = "LLM Provider:"
        self.dd_provider = ef.DropDown()
        providers = ["OpenAI", "Google Gemini", "DeepSeek", "OpenRouter", "Local Ollama / LM Studio", "Custom / Other"]
        for p in providers:
            self.dd_provider.Items.Add(p)

        curr_prov = getattr(self.ai_interpreter, "provider", "OpenAI")
        if curr_prov in providers:
            self.dd_provider.SelectedValue = curr_prov
        else:
            self.dd_provider.SelectedIndex = 0
        self.dd_provider.SelectedIndexChanged += self._on_provider_changed

        row_prov = ef.DynamicLayout()
        row_prov.Spacing = ed.Size(8, 5)
        row_prov.BeginHorizontal()
        row_prov.Add(lbl_provider)
        row_prov.Add(self.dd_provider, True)
        row_prov.EndHorizontal()
        layout.Add(row_prov)

        # Model Engine Selection
        lbl_model = ef.Label()
        lbl_model.Text = "Model Engine:"
        self.dd_model = ef.DropDown()

        self.txt_custom_model = ef.TextBox()
        self.txt_custom_model.PlaceholderText = "Or type custom model name (e.g. gpt-4o, gemini-2.0-flash)"
        self.txt_custom_model.Text = getattr(self.ai_interpreter, "model", "gpt-4o-mini")

        self._populate_model_options()

        row_model = ef.DynamicLayout()
        row_model.Spacing = ed.Size(8, 5)
        row_model.BeginHorizontal()
        row_model.Add(lbl_model)
        row_model.Add(self.dd_model, True)
        row_model.Add(self.txt_custom_model, True)
        row_model.EndHorizontal()
        layout.Add(row_model)

        # API Key
        lbl_key = ef.Label()
        lbl_key.Text = "API Key:"
        self.txt_api_key = ef.TextBox()
        self.txt_api_key.PlaceholderText = "Paste API Key (sk-... / AIza...)"
        self.txt_api_key.Text = getattr(self.ai_interpreter, "api_key", "") or ""

        row_key = ef.DynamicLayout()
        row_key.Spacing = ed.Size(8, 5)
        row_key.BeginHorizontal()
        row_key.Add(lbl_key)
        row_key.Add(self.txt_api_key, True)
        row_key.EndHorizontal()
        layout.Add(row_key)

        # API Endpoint URL
        lbl_endpoint = ef.Label()
        lbl_endpoint.Text = "API Endpoint:"
        self.txt_endpoint = ef.TextBox()
        self.txt_endpoint.Text = getattr(self.ai_interpreter, "api_endpoint", "https://api.openai.com/v1/chat/completions")

        row_ep = ef.DynamicLayout()
        row_ep.Spacing = ed.Size(8, 5)
        row_ep.BeginHorizontal()
        row_ep.Add(lbl_endpoint)
        row_ep.Add(self.txt_endpoint, True)
        row_ep.EndHorizontal()
        layout.Add(row_ep)

        # Status & Feedback
        self.lbl_status = ef.Label()
        self.lbl_status.Text = "Status: Configuration stored in AppData/pyRevit/R2R/ai_config.json"
        try:
            self.lbl_status.TextColor = ed.Colors.DimGray
        except Exception:
            pass
        layout.Add(self.lbl_status)

        # Buttons Bar
        btn_test = ef.Button()
        btn_test.Text = "Test Connection"
        btn_test.Click += self._on_test_connection

        btn_save = ef.Button()
        btn_save.Text = "Save & Apply"
        btn_save.Click += self._on_save_config

        btn_cancel = ef.Button()
        btn_cancel.Text = "Close"
        btn_cancel.Click += self._on_cancel

        btn_layout = ef.DynamicLayout()
        btn_layout.Spacing = ed.Size(10, 5)
        btn_layout.BeginHorizontal()
        btn_layout.Add(btn_test)
        btn_layout.Add(None, True)  # Spacer
        btn_layout.Add(btn_save)
        btn_layout.Add(btn_cancel)
        btn_layout.EndHorizontal()

        layout.Add(None, True)  # Vertical Spacer
        layout.Add(btn_layout)

        self.Content = layout

    def _populate_model_options(self):
        """Populates model dropdown based on selected provider presets."""
        prov = str(self.dd_provider.SelectedValue or "OpenAI")
        preset = PROVIDER_PRESETS.get(prov, {})
        models = preset.get("models", ["gpt-4o-mini", "gpt-4o"])
        self.dd_model.Items.Clear()
        for m in models:
            self.dd_model.Items.Add(m)
        curr_model = getattr(self.ai_interpreter, "model", "")
        if curr_model in models:
            self.dd_model.SelectedValue = curr_model
        else:
            self.dd_model.SelectedIndex = 0
        try:
            self.dd_model.SelectedIndexChanged -= self._on_model_dd_changed
        except Exception:
            pass
        self.dd_model.SelectedIndexChanged += self._on_model_dd_changed

    def _on_model_dd_changed(self, sender, e):
        val = str(self.dd_model.SelectedValue or "")
        if val:
            self.txt_custom_model.Text = val

    def _on_provider_changed(self, sender, e):
        prov = str(self.dd_provider.SelectedValue or "OpenAI")
        preset = PROVIDER_PRESETS.get(prov, {})
        if preset:
            self.txt_endpoint.Text = preset.get("endpoint", "")
            self._populate_model_options()
            self.txt_custom_model.Text = preset.get("default_model", "")

    def _on_test_connection(self, sender, e):
        self.lbl_status.Text = "Testing live connection..."
        prov = str(self.dd_provider.SelectedValue or "")
        model = (self.txt_custom_model.Text or str(self.dd_model.SelectedValue or "")).strip()
        key = (self.txt_api_key.Text or "").strip()
        endpoint = (self.txt_endpoint.Text or "").strip()

        success, msg = self.ai_interpreter.test_connection(
            api_key=key,
            api_endpoint=endpoint,
            model=model,
            provider=prov
        )
        self.lbl_status.Text = msg
        try:
            self.lbl_status.TextColor = ed.Colors.Green if success else ed.Colors.Red
        except Exception:
            pass

    def _on_save_config(self, sender, e):
        prov = str(self.dd_provider.SelectedValue or "OpenAI")
        model = (self.txt_custom_model.Text or str(self.dd_model.SelectedValue or "gpt-4o-mini")).strip()
        key = (self.txt_api_key.Text or "").strip()
        endpoint = (self.txt_endpoint.Text or "").strip()

        self.ai_interpreter.provider = prov
        self.ai_interpreter.model = model
        self.ai_interpreter.api_key = key
        self.ai_interpreter.api_endpoint = endpoint
        self.ai_interpreter.save_config()

        self.Close(True)

    def _on_cancel(self, sender, e):
        self.Close(False)


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

    def _create_separator(self):
        """Creates a 1px horizontal divider panel compatible with all Eto.Forms versions."""
        sep = ef.Panel()
        sep.Height = 1
        sep.Size = ed.Size(-1, 1)
        try:
            sep.BackgroundColor = ed.Colors.LightGrey
        except Exception:
            pass
        return sep

    def _set_bold_font(self, ctrl, size=11):
        """Safely assigns a bold font across IronPython 2.7 and CPython 3 / pythonnet."""
        try:
            ctrl.Font = ed.Font(ed.SystemFont.Bold, float(size))
        except Exception:
            try:
                ctrl.Font = ed.SystemFonts.Bold()
            except Exception:
                pass

    @staticmethod
    def _make_elem_id(val):
        try:
            import System
            return DB.ElementId(System.Int64(val))
        except Exception:
            return DB.ElementId(int(val))

    def _on_close_clicked(self, sender, e):
        try:
            self.Close(True)
        except Exception:
            self.Close()

    def Show(self, parent=None):
        """Presents dialog modally across all Rhino / Revit environments."""
        try:
            if parent is not None:
                return self.ShowModal(parent)
            import Rhino.UI as rui
            if hasattr(rui, 'RhinoEtoApp') and hasattr(rui.RhinoEtoApp, 'MainWindow') and rui.RhinoEtoApp.MainWindow:
                return self.ShowModal(rui.RhinoEtoApp.MainWindow)
        except Exception:
            pass
        return self.ShowModal()

    def show(self, parent=None):
        """Lowercase alias for Show / ShowModal compatibility."""
        return self.Show(parent)

    def _init_ui(self):
        """Constructs layout, header tabs, and stage views."""
        layout = ef.DynamicLayout()
        layout.Spacing = ed.Size(8, 8)

        # Top Header Bar
        header_label = ef.Label()
        header_label.Text = "ONE-CLICK RHINO-TO-REVIT TRANSFER"
        self._set_bold_font(header_label, 15)
        header_label.TextColor = ed.Color.FromArgb(20, 110, 190)

        subtitle = ef.Label()
        subtitle.Text = "Production-grade geometry fingerprinting, architectural semantic classification, dual-status BIM validation & revision sync"
        subtitle.TextColor = ed.Colors.Gray

        layout.Add(header_label)
        layout.Add(subtitle)
        layout.Add(self._create_separator())

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
        self.tabs.SelectedIndexChanged += self._on_tab_changed

        layout.Add(self.tabs, True, True)

        # Bottom Action Bar
        bottom_layout = ef.DynamicLayout()
        bottom_layout.Spacing = ed.Size(10, 5)

        self.lbl_global_status = ef.Label()
        self.lbl_global_status.Text = "Ready to scan active Rhino document."
        self.lbl_global_status.VerticalAlignment = ef.VerticalAlignment.Center

        btn_close = ef.Button()
        btn_close.Text = "Close"
        btn_close.Click += self._on_close_clicked

        bottom_layout.BeginHorizontal()
        bottom_layout.Add(self.lbl_global_status, True, False)
        bottom_layout.Add(btn_close, False, False)
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
        self._set_bold_font(btn_scan, 11)
        btn_scan.Size = ed.Size(260, 36)
        btn_scan.Click += self._on_scan_clicked

        layout.Add(desc)
        layout.BeginHorizontal()
        layout.Add(self.chk_include_hidden)
        layout.Add(self.chk_include_locked)
        layout.EndHorizontal()
        layout.Add(btn_scan)
        layout.Add(self._create_separator())

        # Preflight KPI Cards Box
        cards_title = ef.Label()
        cards_title.Text = "PREFLIGHT & GEOMETRIC HEALTH SUMMARY"
        self._set_bold_font(cards_title, 11)
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
        layout.Add(self.txt_scan_warnings, True, True)

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

        lbl_units = ef.Label()
        lbl_units.Text = "Unit Scale:"
        lbl_units.VerticalAlignment = ef.VerticalAlignment.Center

        self.dd_unit_scale = ef.DropDown()
        self.dd_unit_scale.Items.Add("Auto-Detect")
        self.dd_unit_scale.Items.Add("mm -> Feet (x0.00328)")
        self.dd_unit_scale.Items.Add("Meters -> Feet (x3.28084)")
        self.dd_unit_scale.Items.Add("cm -> Feet (x0.0328)")
        self.dd_unit_scale.Items.Add("Inches -> Feet (x0.0833)")
        self.dd_unit_scale.Items.Add("Feet -> Feet (1.0)")
        self.dd_unit_scale.SelectedIndex = 0

        self.chk_gen_bua = ef.CheckBox()
        self.chk_gen_bua.Text = "Auto-Generate BUA / GFA Area Boundary Lines"
        self.chk_gen_bua.Checked = True

        btn_select_all = ef.Button()
        btn_select_all.Text = "Select All"
        btn_select_all.Click += self._on_select_all_layers

        btn_deselect_all = ef.Button()
        btn_deselect_all.Text = "Deselect All"
        btn_deselect_all.Click += self._on_deselect_all_layers

        btn_ai_assist = ef.Button()
        btn_ai_assist.Text = "Run AI Semantic Assist"
        btn_ai_assist.ToolTip = "Interpret Spanish/Catalan architectural layer naming using semantic AI heuristics"
        btn_ai_assist.Click += self._on_run_ai_assist

        btn_ai_config = ef.Button()
        btn_ai_config.Text = "AI Config..."
        btn_ai_config.ToolTip = "Configure AI Provider (OpenAI, Gemini, DeepSeek, Ollama), API Key, and Model Engine"
        btn_ai_config.Click += self._on_open_ai_settings

        btn_load_profile = ef.Button()
        btn_load_profile.Text = "Load Profile"
        btn_load_profile.Click += self._on_load_profile

        btn_save_profile = ef.Button()
        btn_save_profile.Text = "Save Profile"
        btn_save_profile.Click += self._on_save_profile

        opts_layout.BeginHorizontal()
        opts_layout.Add(lbl_mode)
        opts_layout.Add(self.dd_global_mode)
        opts_layout.Add(lbl_units)
        opts_layout.Add(self.dd_unit_scale)
        opts_layout.Add(self.chk_gen_bua)
        opts_layout.Add(btn_select_all)
        opts_layout.Add(btn_deselect_all)
        opts_layout.Add(btn_ai_assist)
        opts_layout.Add(btn_ai_config)
        opts_layout.Add(btn_load_profile)
        opts_layout.Add(btn_save_profile)
        opts_layout.EndHorizontal()

        layout.Add(opts_layout)

        # Strategy Override & Repetition Toolbar
        strat_layout = ef.DynamicLayout()
        strat_layout.Spacing = ed.Size(8, 5)

        lbl_strat_override = ef.Label()
        lbl_strat_override.Text = "Override Strategy:"
        lbl_strat_override.VerticalAlignment = ef.VerticalAlignment.Center

        self.dd_strat_override = ef.DropDown()
        self.dd_strat_override.Items.Add("AdaptiveComponent")
        self.dd_strat_override.Items.Add("RepetitiveFamily")
        self.dd_strat_override.Items.Add("ParametricFamily")
        self.dd_strat_override.Items.Add("FloorNative")
        self.dd_strat_override.Items.Add("WallByCurve")
        self.dd_strat_override.Items.Add("RoofNative")
        self.dd_strat_override.Items.Add("Toposolid")
        self.dd_strat_override.Items.Add("FamilyInstance")
        self.dd_strat_override.Items.Add("DirectShape")
        self.dd_strat_override.SelectedIndex = 0

        btn_apply_strat = ef.Button()
        btn_apply_strat.Text = "Apply Strategy"
        btn_apply_strat.ToolTip = "Apply the selected strategy override to all currently checked layers"
        btn_apply_strat.Click += self._on_apply_strategy_override

        lbl_cat_override = ef.Label()
        lbl_cat_override.Text = "Override Category:"
        lbl_cat_override.VerticalAlignment = ef.VerticalAlignment.Center

        self.dd_cat_override = ef.DropDown()
        for cat_opt in ["Floors", "Walls", "Roofs", "Ceilings", "Columns", "Toposolid", "Curtain Panels", "Doors", "Windows", "Furniture", "Site", "Mass", "Generic Models"]:
            self.dd_cat_override.Items.Add(cat_opt)
        self.dd_cat_override.SelectedIndex = 0

        btn_apply_cat = ef.Button()
        btn_apply_cat.Text = "Apply Category"
        btn_apply_cat.ToolTip = "Override the Revit category for all currently checked layers"
        btn_apply_cat.Click += self._on_apply_category_override

        lbl_rep_thresh = ef.Label()
        lbl_rep_thresh.Text = "Repetition Grouping:"
        lbl_rep_thresh.VerticalAlignment = ef.VerticalAlignment.Center

        self.dd_rep_threshold = ef.DropDown()
        self.dd_rep_threshold.Items.Add(">= 2 identical")
        self.dd_rep_threshold.Items.Add(">= 3 identical")
        self.dd_rep_threshold.Items.Add(">= 5 identical")
        self.dd_rep_threshold.Items.Add(">= 10 identical")
        self.dd_rep_threshold.SelectedIndex = 1  # default >= 3 identical
        self.dd_rep_threshold.SelectedIndexChanged += self._on_repetition_threshold_changed

        strat_layout.BeginHorizontal()
        strat_layout.Add(lbl_strat_override)
        strat_layout.Add(self.dd_strat_override)
        strat_layout.Add(btn_apply_strat)
        strat_layout.Add(lbl_cat_override)
        strat_layout.Add(self.dd_cat_override)
        strat_layout.Add(btn_apply_cat)
        strat_layout.Add(lbl_rep_thresh)
        strat_layout.Add(self.dd_rep_threshold)
        strat_layout.EndHorizontal()

        layout.Add(strat_layout)

        # DataGridView
        self.grid_review = ef.GridView()
        self.grid_review.ShowHeader = True
        self.grid_review.AllowMultipleSelection = True
        try:
            self.grid_review.CellEdited += self._on_review_cell_edited
        except Exception:
            pass

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
        col_cat.Editable = True
        self.grid_review.Columns.Add(col_cat)

        # Proposed Strategy
        col_strat = ef.GridColumn()
        col_strat.HeaderText = "Proposed Strategy"
        col_strat.DataCell = ef.TextBoxCell(4)
        col_strat.Width = 140
        col_strat.Editable = True
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

        # Pattern / Signature
        col_pattern = ef.GridColumn()
        col_pattern.HeaderText = "Pattern / Points"
        col_pattern.DataCell = ef.TextBoxCell(8)
        col_pattern.Width = 140
        self.grid_review.Columns.Add(col_pattern)

        layout.Add(self.grid_review, True, True)
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
        self._set_bold_font(title, 12)
        layout.Add(title)

        self.lbl_prev_create = ef.Label()
        self.lbl_prev_create.Text = "Elements to CREATE: 0"
        self._set_bold_font(self.lbl_prev_create, 11)

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
        layout.Add(self._create_separator())

        notice = ef.Label()
        notice.Text = "Notice: Clicking 'Proceed to Transfer' will switch to the Execute tab and initiate Revit API Transactions with automated warning suppression."
        notice.TextColor = ed.Colors.Gray
        layout.Add(notice)

        btn_go_execute = ef.Button()
        btn_go_execute.Text = "Proceed to Transfer ->"
        self._set_bold_font(btn_go_execute, 11)
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
        self._set_bold_font(title, 12)
        layout.Add(title)

        self.btn_execute = ef.Button()
        self.btn_execute.Text = "Execute R2R Transfer"
        self._set_bold_font(self.btn_execute, 12)
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
        layout.Add(self.txt_exec_log, True, True)

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
        self._set_bold_font(lbl_val_title, 12)

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
        top_layout.Add(lbl_val_title, True, False)
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

        layout.Add(self.grid_validate, True, True)
        return layout

    # =========================================================================
    # EVENT HANDLERS & LOGIC
    # =========================================================================
    def _on_scan_clicked(self, sender, e):
        """Executes full scan of active Rhino document and updates UI."""
        try:
            self.lbl_global_status.Text = "Scanning Rhino document..."

            # Query Revit Levels if doc is available
            revit_levels = []
            if REVIT_AVAILABLE and self.revit_doc:
                try:
                    collector = DB.FilteredElementCollector(self.revit_doc).OfClass(DB.Level)
                    scale_to_m = self.scanner.get_unit_scale_to_meters()
                    scale_from_ft = (1.0 / scale_to_m * 0.3048) if scale_to_m > 0 else 1.0
                    for lvl in collector:
                        try:
                            revit_levels.append({
                                "name": str(lvl.Name),
                                "elevation": float(lvl.Elevation) * scale_from_ft
                            })
                        except Exception:
                            continue
                except Exception as ex_lvl:
                    print("[R2R Scan] Level query notice: {}".format(ex_lvl))

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
            if getattr(pf, 'warnings', None):
                warn_text.extend(pf.warnings)

            self.txt_scan_warnings.Text = "\n".join(warn_text) if warn_text else "Preflight clean. No geometric anomalies detected."

            # Run Classification & Sync Evaluation
            self._run_classification_and_sync()

            # Populate Review Grid
            self._populate_review_grid()

            # Update Preview Tab KPIs
            self._update_preview_kpis()

            self.lbl_global_status.Text = "Scan complete. {} objects ready for review.".format(len(self.scanned_objects))
            self.tabs.SelectedIndex = 1  # Move to Review tab

        except Exception as ex:
            import traceback
            err_details = traceback.format_exc()
            print("[R2R Scan Error]:\n" + err_details)
            self.lbl_global_status.Text = "Scan error: {}. See warnings tab for details.".format(ex)
            self.txt_scan_warnings.Text = "Scan Exception:\n{}\n{}".format(ex, err_details)

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

    def _sync_grid_check_states(self):
        """Syncs checkbox state and category/strategy changes made in Eto GridView back to LayerRowItem objects."""
        try:
            ds = self.grid_review.DataStore
            if ds is not None and self.filtered_layer_rows:
                changed = False
                for idx, row_data in enumerate(ds):
                    if idx < len(self.filtered_layer_rows) and len(row_data) > 0:
                        row = self.filtered_layer_rows[idx]
                        row.checked = bool(row_data[0])

                        # If category was modified in-place (column 3)
                        if len(row_data) > 3:
                            new_cat = str(row_data[3] or "").strip()
                            if new_cat and new_cat != row.inferred_category:
                                row.update_category(new_cat)
                                for obj in row.objects:
                                    c = self.classifications.get(obj.rhino_guid)
                                    if c:
                                        c.inferred_category = row.inferred_category
                                        c.revit_category_name = row.revit_type
                                        c.proposed_strategy = row.proposed_strategy
                                        c.confidence_score = row.confidence_score
                                        c.confidence_tier = row.confidence_tier
                                if len(row_data) > 4:
                                    row_data[4] = row.proposed_strategy
                                if len(row_data) > 5:
                                    row_data[5] = row.revit_type
                                if len(row_data) > 6:
                                    row_data[6] = "{:.0f}%".format(row.confidence_score)
                                changed = True

                        # If strategy was modified in-place (column 4)
                        if len(row_data) > 4:
                            new_strat = str(row_data[4] or "").strip()
                            if new_strat and new_strat != row.proposed_strategy:
                                row.update_strategy(new_strat)
                                for obj in row.objects:
                                    c = self.classifications.get(obj.rhino_guid)
                                    if c:
                                        c.proposed_strategy = row.proposed_strategy
                                        c.confidence_score = row.confidence_score
                                        c.confidence_tier = row.confidence_tier
                                if len(row_data) > 6:
                                    row_data[6] = "{:.0f}%".format(row.confidence_score)
                                changed = True
                if changed:
                    self._update_preview_kpis()
        except Exception:
            pass

    def _on_review_cell_edited(self, sender, e):
        """Updates LayerRowItem checked state and preview KPIs when user edits a cell."""
        self._sync_grid_check_states()
        self._update_preview_kpis()

    def _on_tab_changed(self, sender, e):
        """Syncs grid checkbox selections whenever user switches tabs."""
        self._sync_grid_check_states()
        self._update_preview_kpis()

    def _on_select_all_layers(self, sender, e):
        """Checks all currently visible layers in the review grid."""
        self._sync_grid_check_states()
        for r in self.filtered_layer_rows:
            r.checked = True
        self._refresh_review_grid_datastore()
        self._update_preview_kpis()

    def _on_deselect_all_layers(self, sender, e):
        """Unchecks all currently visible layers in the review grid."""
        self._sync_grid_check_states()
        for r in self.filtered_layer_rows:
            r.checked = False
        self._refresh_review_grid_datastore()
        self._update_preview_kpis()

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

            checked = c_res.is_prechecked if c_res else True
            row = LayerRowItem(lp, objs, c_res, s_ev, checked=checked)
            self.layer_rows.append(row)

        # If no layers met the high auto-ready threshold (e.g., generic Rhino layer names
        # like 'Default' or 'Layer 01'), pre-check all layers so user can transfer immediately
        if self.layer_rows and not any(r.checked for r in self.layer_rows):
            for r in self.layer_rows:
                r.checked = True

        self._apply_review_filters(None, None)

    def _refresh_review_grid_datastore(self):
        """Rebinds filtered_layer_rows to self.grid_review.DataStore."""
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
                r.sync_state,
                getattr(r, "pattern_tag", "-")
            ])

        self.grid_review.DataStore = grid_data

    def _apply_review_filters(self, sender, e):
        """Filters the review DataGridView based on search text, category, confidence and sync state."""
        if sender is not None:
            self._sync_grid_check_states()

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

        self._refresh_review_grid_datastore()

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

    def _on_apply_strategy_override(self, sender, e):
        """Overrides proposed strategy for all currently checked layers."""
        self._sync_grid_check_states()
        selected_strat = str(self.dd_strat_override.SelectedValue or "").strip()
        if not selected_strat:
            return

        updated_count = 0
        for r in self.layer_rows:
            if r.checked:
                r.update_strategy(selected_strat)
                for obj in r.objects:
                    c = self.classifications.get(obj.rhino_guid)
                    if c:
                        c.proposed_strategy = r.proposed_strategy
                        c.confidence_score = r.confidence_score
                        c.confidence_tier = r.confidence_tier
                updated_count += 1

        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        self.lbl_global_status.Text = "Applied strategy override '{}' to {} checked layer(s). Confidence updated dynamically.".format(
            selected_strat, updated_count
        )

    def _on_apply_category_override(self, sender, e):
        """Overrides Revit category for all currently checked layers."""
        self._sync_grid_check_states()
        selected_cat = str(self.dd_cat_override.SelectedValue or "").strip()
        if not selected_cat:
            return

        updated_count = 0
        for r in self.layer_rows:
            if r.checked:
                r.update_category(selected_cat)
                for obj in r.objects:
                    c = self.classifications.get(obj.rhino_guid)
                    if c:
                        c.inferred_category = r.inferred_category
                        c.revit_category_name = r.revit_type
                        c.proposed_strategy = r.proposed_strategy
                        c.confidence_score = r.confidence_score
                        c.confidence_tier = r.confidence_tier
                updated_count += 1

        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        self.lbl_global_status.Text = "Applied category override '{}' to {} checked layer(s). Updated target Revit types and confidence.".format(
            selected_cat, updated_count
        )

    def _on_repetition_threshold_changed(self, sender, e):
        """Updates classifier repetition threshold and re-evaluates review grid."""
        try:
            val_str = str(self.dd_rep_threshold.SelectedValue or "3")
            thresh = 3
            if "2" in val_str:
                thresh = 2
            elif "5" in val_str:
                thresh = 5
            elif "10" in val_str:
                thresh = 10
            self.classifier.repetition_threshold = thresh
            if self.scanned_objects:
                self._run_classification_and_sync()
                self._populate_review_grid()
                self._update_preview_kpis()
                self.lbl_global_status.Text = "Repetition grouping threshold set to >= {}.".format(thresh)
        except Exception as ex:
            self.lbl_global_status.Text = "Threshold update notice: {}".format(ex)

    def _on_mode_changed(self, sender, e):
        """Re-evaluates proposed strategies when global mode changes."""
        self._run_classification_and_sync()
        self._populate_review_grid()
        self._update_preview_kpis()

    def _on_open_ai_settings(self, sender, e):
        """Opens the AI Model & API Key Configuration dialog."""
        try:
            dlg = AISettingsDialog(self.ai_interpreter)
            if dlg.ShowModal(self):
                prov = getattr(self.ai_interpreter, "provider", "OpenAI")
                model = getattr(self.ai_interpreter, "model", "gpt-4o-mini")
                has_key = bool(getattr(self.ai_interpreter, "api_key", ""))
                key_tag = "Key Configured" if has_key else "Offline Heuristics"
                self.lbl_global_status.Text = "AI configured: {} / {} [{}]".format(prov, model, key_tag)
        except Exception as ex:
            self.lbl_global_status.Text = "AI config dialog notice: {}".format(ex)

    def _on_run_ai_assist(self, sender, e):
        """Uses AI semantic interpreter to resolve ambiguous architectural layers."""
        self._sync_grid_check_states()
        prov = getattr(self.ai_interpreter, "provider", "OpenAI")
        model = getattr(self.ai_interpreter, "model", "gpt-4o-mini")
        has_key = bool(getattr(self.ai_interpreter, "api_key", ""))
        mode_str = "{} ({})".format(prov, model) if has_key else "Offline Heuristic Lexicon"
        self.lbl_global_status.Text = "Running AI semantic interpretation [{} on layers]...".format(mode_str)
        boosted = 0
        llm_count = 0
        for r in self.layer_rows:
            interp = self.ai_interpreter.interpret(r.layer_name)
            if interp and interp.confidence_boost > 0:
                r.inferred_category = interp.inferred_category
                r.revit_type = interp.suggested_revit_type
                r.confidence_score = min(100.0, r.confidence_score + (interp.confidence_boost * 20.0))
                r.checked = True
                boosted += 1
                if getattr(interp, "source", "") == "LLM":
                    llm_count += 1

        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        if llm_count > 0:
            self.lbl_global_status.Text = "AI Semantic Assist refined {} layer(s) ({} via live LLM, {} via lexicon).".format(
                boosted, llm_count, boosted - llm_count
            )
        else:
            self.lbl_global_status.Text = "AI Semantic Assist refined {} layer classifications (Lexicon Heuristics).".format(boosted)

    def _on_load_profile(self, sender, e):
        """Loads transfer profile from disk."""
        try:
            if self.profile_mgr.load():
                self._run_classification_and_sync()
                self._populate_review_grid()
                self._update_preview_kpis()
                self.lbl_global_status.Text = "Transfer Profile loaded successfully from disk."
            else:
                self.lbl_global_status.Text = "No saved transfer profile found on disk."
        except Exception as ex:
            self.lbl_global_status.Text = "Profile load notice: {}".format(ex)

    def _on_save_profile(self, sender, e):
        """Saves current layer table configurations as persistent profile rules."""
        try:
            self._sync_grid_check_states()
            saved_count = 0
            for r in self.layer_rows:
                if r.checked:
                    rule = MappingRule(
                        layer_pattern=r.layer_name,
                        revit_category=r.revit_type if (r.revit_type and r.revit_type.startswith("OST_")) else (r.classification.revit_category_name if r.classification else "OST_GenericModel"),
                        revit_type=r.revit_type,
                        strategy=r.proposed_strategy,
                        room_bounding=True
                    )
                    self.profile_mgr.add_rule(rule)
                    saved_count += 1

            self.profile_mgr.save()
            self.lbl_global_status.Text = "Saved {} active layer mapping rule(s) to Transfer_Profile.json.".format(saved_count)
        except Exception as ex:
            self.lbl_global_status.Text = "Profile save notice: {}".format(ex)

    def _get_active_unit_scale(self):
        """Returns the chosen unit scale factor for converting Rhino units to Revit feet."""
        try:
            if hasattr(self, "dd_unit_scale") and self.dd_unit_scale is not None:
                idx = self.dd_unit_scale.SelectedIndex
                if idx == 1:
                    return 1.0 / 304.8  # Millimeters to feet (0.003280839895013123)
                elif idx == 2:
                    return 3.280839895013123  # Meters to feet
                elif idx == 3:
                    return 1.0 / 30.48  # Centimeters to feet
                elif idx == 4:
                    return 1.0 / 12.0  # Inches to feet
                elif idx == 5:
                    return 1.0  # Feet to feet
        except Exception:
            pass
        return None  # Auto-Detect from active Rhino document unit system

    def _on_execute_transfer(self, sender, e):
        """Executes full transfer with Revit TransactionGroup and Failure handling."""
        self._sync_grid_check_states()

        if not REVIT_AVAILABLE or not self.revit_doc:
            self.lbl_global_status.Text = "Transfer aborted: Revit document is not available. Please run inside Rhino.Inside.Revit."
            self.lbl_exec_status.Text = "Transfer aborted: Revit document is not available."
            return

        self.btn_execute.Enabled = False
        self.lbl_exec_status.Text = "Starting transfer transaction..."
        chosen_scale = self._get_active_unit_scale()
        scale_label = str(self.dd_unit_scale.SelectedValue) if hasattr(self, "dd_unit_scale") else "Auto-Detect"
        self.txt_exec_log.Text = "Beginning R2R Transfer TransactionGroup (Unit Scale: {})...\n".format(scale_label)

        router = ConversionRouter(
            self.revit_doc,
            global_mode=str(self.dd_global_mode.SelectedValue),
            generate_bua=self.chk_gen_bua.Checked or False,
            unit_scale=chosen_scale
        )

        # Collect objects to transfer
        approved_objs = []
        for r in self.layer_rows:
            if r.checked:
                approved_objs.extend(r.objects)

        layer_to_row = {}
        for r in self.layer_rows:
            layer_to_row[r.layer_name] = r
            for o in getattr(r, "objects", []):
                layer_to_row[getattr(o, "layer_name", "")] = r
                layer_to_row[getattr(o, "layer_full_path", "")] = r

        total = len(approved_objs)
        if total == 0:
            self.txt_exec_log.Text += "No objects approved for transfer. Please scan and check layers in Review tab.\n"
            self.btn_execute.Enabled = True
            return

        created_cnt = 0
        updated_cnt = 0
        skipped_cnt = 0
        failed_cnt = 0

        self.validation_rows = []
        self.qa_report = TransferQAReport()
        self.qa_report.execution_mode = str(self.dd_global_mode.SelectedValue)

        # =========================================================================
        # PHASE 1: Pre-Transaction Native .rfa Family Generation & Loading
        # Runs BEFORE the main project TransactionGroup / Transaction to avoid
        # Revit API transaction nesting deadlocks with LoadFamily()
        # =========================================================================
        try:
            self.txt_exec_log.Text += "Phase 1: Pre-generating and loading parametric/repetitive .rfa families...\n"
            profile_rules_map = {}
            for r in self.layer_rows:
                if r.checked:
                    cat_name = r.revit_type if (r.revit_type and r.revit_type.startswith("OST_")) else (r.classification.revit_category_name if r.classification else "OST_GenericModel")
                    rule_item = MappingRule(
                        layer_pattern=r.layer_name,
                        revit_category=cat_name,
                        revit_type=r.revit_type,
                        strategy=r.proposed_strategy,
                        room_bounding=True
                    )
                    profile_rules_map[r.layer_name] = rule_item
                    for o in getattr(r, "objects", []):
                        profile_rules_map[getattr(o, "layer_name", "")] = rule_item
                        profile_rules_map[getattr(o, "layer_full_path", "")] = rule_item

            pregen_symbols = router.pre_generate_families(approved_objs, self.classifications, profile_rules=profile_rules_map)
            pregen_cnt = len(pregen_symbols) if pregen_symbols else 0
            if pregen_cnt > 0:
                self.txt_exec_log.Text += "Phase 1 Complete: {} native .rfa family definition(s) loaded into project.\n".format(pregen_cnt)
            else:
                self.txt_exec_log.Text += "Phase 1 Complete: No family pre-generation required.\n"
        except Exception as ex:
            self.txt_exec_log.Text += "Phase 1 Warning: Family pre-generation encountered: {}. Continuing with fallback pipelines.\n".format(ex)

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

                    # If updating modified geometry, delete previous Revit element before recreating
                    if s_ev and s_ev.sync_state == "UPDATE_MODIFIED" and s_ev.revit_element_id > 0:
                        try:
                            old_eid = self._make_elem_id(s_ev.revit_element_id)
                            if self.revit_doc.GetElement(old_eid) is not None:
                                self.revit_doc.Delete(old_eid)
                        except Exception:
                            pass

                    # Resolve layer override rule if configured by user
                    row = layer_to_row.get(obj.layer_full_path) or layer_to_row.get(getattr(obj, "layer_name", ""))
                    layer_rule = None
                    if row:
                        cat_name = row.revit_type if (row.revit_type and row.revit_type.startswith("OST_")) else (c_res.revit_category_name if c_res else "OST_GenericModel")
                        layer_rule = MappingRule(
                            layer_pattern=obj.layer_full_path,
                            revit_category=cat_name,
                            revit_type=row.revit_type,
                            strategy=row.proposed_strategy,
                            room_bounding=True
                        )

                    conv_res = router.convert_object(obj, c_res, profile_rule=layer_rule)

                    if conv_res.status == "SUCCESS" and conv_res.revit_element_id != -1:
                        # Write ExtensibleStorage sync tracking
                        created_elem = self.revit_doc.GetElement(self._make_elem_id(conv_res.revit_element_id))
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
            self.lbl_global_status.Text = "Please select an element row in the table first."
            return

        row = self.validation_rows[sel_idx]
        elem_id = int(row.revit_id)
        if elem_id > 0 and self.revit_uidoc:
            GraphicsValidator.isolate_element_in_view(self.revit_uidoc, elem_id)
            self.lbl_global_status.Text = "Element ID {} isolated in Revit view.".format(elem_id)

    @staticmethod
    def _add_file_filter(dlg, name, ext):
        """Safely adds a FileFilter across IronPython 2.7 and CPython 3 (pythonnet)."""
        try:
            import System
            arr = System.Array[System.String]([ext])
            dlg.Filters.Add(ef.FileFilter(name, arr))
        except Exception:
            try:
                dlg.Filters.Add(ef.FileFilter(name, ext))
            except Exception:
                pass

    def _on_export_html(self, sender, e):
        """Exports QA report as interactive HTML."""
        dlg = ef.SaveFileDialog()
        dlg.Title = "Save QA Report (HTML)"
        self._add_file_filter(dlg, "HTML File (*.html)", ".html")
        dlg.FileName = "R2R_QA_Report.html"

        if dlg.ShowDialog(self) == ef.DialogResult.Ok:
            dest = dlg.FileName
            ReportExporter.export_html(self.qa_report, dest)
            self.lbl_global_status.Text = "HTML QA Report generated at: {}".format(dest)

    def _on_export_csv(self, sender, e):
        """Exports QA report as CSV."""
        dlg = ef.SaveFileDialog()
        dlg.Title = "Save QA Report (CSV)"
        self._add_file_filter(dlg, "CSV File (*.csv)", ".csv")
        dlg.FileName = "R2R_QA_Report.csv"

        if dlg.ShowDialog(self) == ef.DialogResult.Ok:
            dest = dlg.FileName
            ReportExporter.export_csv(self.qa_report, dest)
            self.lbl_global_status.Text = "CSV QA Report generated at: {}".format(dest)

