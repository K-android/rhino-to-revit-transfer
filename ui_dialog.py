# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 9
ui_dialog.py - Pure-Python WPF XAML Master-Detail 3-Step Dashboard UI

Provides a responsive, non-clipping 3-stage Revit-native WPF workflow:
  1. SCAN - Rhino model scanning, unit/origin check, and geometric health KPIs
  2. CONFIGURE LAYERS - Master-detail split view with live Inspector, bulk-edit,
     searchable Revit categories/materials, rule compatibility, and custom parameters
  3. TRANSFER & QA - Progress + collapsible log, Dual-Status audit grid, Isolate & Export
"""

import sys
import os
import re
import codecs
import traceback

ETO_AVAILABLE = False
WPF_AVAILABLE = False
WPF_IMPORT_ERROR = ""
swcp = None
swi = None

try:
    import clr
    for _asm in (
        "System",
        "System.Runtime",
        "System.Diagnostics.Process",
        "PresentationFramework",
        "PresentationCore",
        "WindowsBase",
        "System.Xaml",
    ):
        try:
            clr.AddReference(_asm)
        except Exception:
            pass

    import System
    import System.Windows as sw
    import System.Windows.Media as swm
    import System.Windows.Markup as swma
    import System.Windows.Controls as swc
    from System.Collections.Generic import Dictionary, List

    try:
        import System.Windows.Controls.Primitives as swcp
    except Exception:
        swcp = None

    try:
        import System.Windows.Interop as swi
    except Exception:
        swi = None

    WPF_AVAILABLE = True
except Exception:
    WPF_AVAILABLE = False
    WPF_IMPORT_ERROR = traceback.format_exc()

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
    from profile_manager import ProfileManager, MappingRule, CustomParameterSpec
    from strategy_rules import StrategyRuleEngine, STRATEGY_METADATA
    from conversion_router import ConversionRouter
    from graphics_validator import GraphicsValidator
    from sync_engine import SyncEngine
    from reporting import TransferQAReport, ReportExporter
except (ImportError, ValueError):
    from .scanner import RhinoScanner
    from .classifier import AecClassifier
    from .ai_assistant import AISemanticInterpreter, PROVIDER_PRESETS
    from .profile_manager import ProfileManager, MappingRule, CustomParameterSpec
    from .strategy_rules import StrategyRuleEngine, STRATEGY_METADATA
    from .conversion_router import ConversionRouter
    from .graphics_validator import GraphicsValidator
    from .sync_engine import SyncEngine
    from .reporting import TransferQAReport, ReportExporter


STANDARD_REVIT_CATEGORIES = [
    # Architecture - Core Systems
    "Floors", "Walls", "Roofs", "Ceilings", "Columns", "Structural Columns",
    "Doors", "Windows", "Curtain Panels", "Curtain Wall Mullions",
    "Stairs", "Railings", "Ramps",
    # FF&E, Casework & Specialties
    "Furniture", "Furniture Systems", "Casework", "Specialty Equipment",
    # MEP (Services)
    "Plumbing Fixtures", "Mechanical Equipment", "Electrical Equipment", "Electrical Fixtures", "Lighting Fixtures",
    # Structure
    "Structural Framing", "Structural Foundation",
    # Site, Topo & Civil
    "Toposolid", "Site", "Planting", "Parking", "Roads", "Hardscape",
    # Mass, Context & Generic
    "Mass", "Entourage", "Signage", "Generic Models"
]

STRATEGY_UI_CHOICES = [
    ("DirectShape", "Fast 3D Mesh / Solid (DirectShape) [Non-Editable]", "DirectShape"),
    ("FreeFormFamily", "Static Solid .rfa Family (Edit Type & Material)", "Static .rfa"),
    ("ParametricFamily", "Editable Parametric .rfa Family (Flex Width/Depth/Ht)", "Parametric .rfa"),
    ("RepetitiveFamily", "Instanced Block / Repeated .rfa Family", "Repetitive .rfa"),
    ("AdaptiveComponent", "Adaptive Component .rfa (2/3/4-Pt Panels)", "Adaptive .rfa"),
    ("FloorNative", "Revit Native System Floor", "Native Floor"),
    ("WallByCurve", "Revit Native System Wall (by Curve)", "Native Wall"),
    ("RoofNative", "Revit Native System Roof", "Native Roof"),
    ("CeilingNative", "Revit Native System Ceiling", "Native Ceiling"),
    ("Toposolid", "Revit Native Toposolid / Terrain", "Toposolid"),
]

_UNIT_SCALE_SHORT = [
    "Auto Units", "mm", "Meters", "cm", "Inches", "Feet"
]


def strategy_to_friendly(strategy_key):
    """Maps an internal strategy identifier to its plain-English dropdown label."""
    key = str(strategy_key or "DirectShape").strip()
    if key == "FamilyInstance":
        key = "FreeFormFamily"
    for k, friendly, _ in STRATEGY_UI_CHOICES:
        if k.lower() == key.lower():
            return friendly
    return key


def friendly_to_strategy(label_or_key):
    """Maps a plain-English dropdown label (or strategy key) back to the canonical strategy key."""
    txt = str(label_or_key or "").strip()
    if not txt:
        return "DirectShape"
    low = txt.lower()
    for k, friendly, short in STRATEGY_UI_CHOICES:
        if low == k.lower() or low == friendly.lower() or low == short.lower():
            return k
    if low == "familyinstance":
        return "FreeFormFamily"
    return txt


def strategy_to_short(strategy_key):
    """Maps an internal strategy identifier to a compact table display tag."""
    key = str(strategy_key or "DirectShape").strip()
    if key == "FamilyInstance":
        key = "FreeFormFamily"
    for k, _, short in STRATEGY_UI_CHOICES:
        if k.lower() == key.lower():
            return short
    return key


# =============================================================================
# WPF INFRASTRUCTURE HELPERS
# =============================================================================
def _ui_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _load_xaml_window(xaml_filename):
    """Loads a pure WPF XAML window cleanly on both .NET 4.8 and .NET 8 (Revit 2025/2026)."""
    if not WPF_AVAILABLE:
        return None
    xaml_path = os.path.join(_ui_dir(), xaml_filename)
    with codecs.open(xaml_path, "r", "utf-8-sig") as f:
        xaml_text = f.read()
    try:
        return swma.XamlReader.Parse(xaml_text)
    except Exception:
        import System.IO
        import System.Xml
        sr = System.IO.StringReader(xaml_text)
        xr = System.Xml.XmlReader.Create(sr)
        try:
            return swma.XamlReader.Load(xr)
        finally:
            xr.Close()
            sr.Dispose()


def _make_brush(hex_str):
    """Creates a frozen WPF SolidColorBrush from a #RRGGBB hex string."""
    h = str(hex_str or "#FFFFFF").lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    color = swm.Color.FromRgb(System.Byte(r), System.Byte(g), System.Byte(b))
    brush = swm.SolidColorBrush(color)
    try:
        brush.Freeze()
    except Exception:
        pass
    return brush


def _make_clr_dict(py_dict):
    """Builds a CLR Dictionary[String, Object] so WPF {Binding [Key]} works across IPy 2.7 & CPython 3."""
    d = Dictionary[System.String, System.Object]()
    for k, v in py_dict.items():
        d[str(k)] = v
    return d


def _make_clr_list(items):
    """Builds a CLR List[Object] suitable for WPF ItemsControl.ItemsSource."""
    lst = List[System.Object]()
    for item in items:
        lst.Add(item)
    return lst


def _clamp_window_to_screen(window, desired_w, desired_h):
    """Clamps initial window size against WPF SystemParameters.WorkArea for small/HiDPI displays."""
    if not WPF_AVAILABLE or window is None:
        return
    try:
        wa = sw.SystemParameters.WorkArea
        max_w = max(500.0, float(wa.Width) - 36.0)
        max_h = max(420.0, float(wa.Height) - 36.0)
        window.Width = min(float(desired_w), max_w)
        window.Height = min(float(desired_h), max_h)
        if float(window.MinWidth) > window.Width:
            window.MinWidth = max(420.0, window.Width - 20.0)
        if float(window.MinHeight) > window.Height:
            window.MinHeight = max(360.0, window.Height - 20.0)
    except Exception:
        pass


def _set_revit_owner(window, revit_uidoc=None, parent_window=None):
    """Anchors a WPF window to the calling WPF parent or Revit's main window handle."""
    if not WPF_AVAILABLE or window is None:
        return
    try:
        if parent_window is not None and isinstance(parent_window, sw.Window):
            window.Owner = parent_window
            window.WindowStartupLocation = sw.WindowStartupLocation.CenterOwner
            return

        hwnd = None
        if revit_uidoc is not None:
            try:
                app = revit_uidoc.Application
                if hasattr(app, "MainWindowHandle") and app.MainWindowHandle != System.IntPtr.Zero:
                    hwnd = app.MainWindowHandle
            except Exception:
                hwnd = None

        if hwnd is None:
            try:
                proc_hwnd = System.Diagnostics.Process.GetCurrentProcess().MainWindowHandle
                if proc_hwnd != System.IntPtr.Zero:
                    hwnd = proc_hwnd
            except Exception:
                hwnd = None

        if hwnd is not None and hwnd != System.IntPtr.Zero and swi is not None:
            helper = swi.WindowInteropHelper(window)
            helper.Owner = hwnd
            window.WindowStartupLocation = sw.WindowStartupLocation.CenterOwner
        else:
            window.WindowStartupLocation = sw.WindowStartupLocation.CenterScreen
    except Exception:
        try:
            window.WindowStartupLocation = sw.WindowStartupLocation.CenterScreen
        except Exception:
            pass


def _pump_wpf_events():
    """Flushes WPF Dispatcher render/layout queue gently during long transaction loops."""
    if not WPF_AVAILABLE:
        return
    try:
        import System.Windows.Threading as swt
        swt.Dispatcher.CurrentDispatcher.Invoke(
            swt.DispatcherPriority.Background,
            System.Action(lambda: None)
        )
    except Exception:
        pass


def _set_combo_value(cb, text_value):
    """Selects matching item in a WPF ComboBox or assigns editable text cleanly."""
    if cb is None:
        return
    target = str(text_value or "").strip()
    low = target.lower()
    for i, item in enumerate(cb.Items):
        if str(item).strip().lower() == low:
            cb.SelectedIndex = i
            return
    if getattr(cb, "IsEditable", False):
        cb.SelectedIndex = -1
        cb.Text = target
    elif cb.Items.Count > 0:
        cb.SelectedIndex = 0


def _get_combo_value(cb, default_val=""):
    """Reads selected item or typed text from a WPF ComboBox."""
    if cb is None:
        return default_val
    if cb.SelectedItem is not None:
        txt = str(cb.SelectedItem).strip()
        if txt:
            return txt
    if getattr(cb, "IsEditable", False):
        txt = str(cb.Text or "").strip()
        if txt:
            return txt
    return default_val


# =============================================================================
# PURE PYTHON DATA MODELS (TESTED HEADLESSLY IN CPYTHON & IPY2.7)
# =============================================================================
class LayerRowItem(object):
    """Data item model for the Configure Layers list & Inspector."""

    def __init__(self, layer_name, objects, classification, sync_eval, checked=True,
                 selected_material="<Auto from Rhino>", source_layer_path=None,
                 bucket_key="", bucket_label="", custom_parameters=None):
        self.checked = checked
        self.layer_name = layer_name
        self.source_layer_path = source_layer_path or layer_name
        self.bucket_key = bucket_key or ""
        self.bucket_label = bucket_label or ""
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
        self.selected_material = selected_material or "<Auto from Rhino>"
        self.custom_parameters = [
            CustomParameterSpec.from_dict(cp) for cp in (custom_parameters or [])
        ]
        self.rule_eval = None
        self.compatibility_tag = "OK"

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
        elif self.bucket_label:
            self.pattern_tag = self.bucket_label
        else:
            self.pattern_tag = "Unique Mesh/Brep"

        self._refresh_rule_eval()

    def _refresh_rule_eval(self):
        """Evaluates rule compatibility and flags mixed-geometry layers without mutating initial classification."""
        eval_res = StrategyRuleEngine.evaluate_layer(self.objects, self.proposed_strategy)
        self.rule_eval = eval_res
        if eval_res.status == "COMPATIBLE":
            self.compatibility_tag = "OK ({}/{})".format(eval_res.pass_count, eval_res.total_count)
        elif eval_res.status == "PARTIAL_MIXED":
            self.compatibility_tag = "MIXED ({}/{})".format(eval_res.pass_count, eval_res.total_count)
        else:
            self.compatibility_tag = "BLOCKED (0/{})".format(eval_res.total_count)

        if eval_res.is_mixed_geometry:
            mix_parts = [
                "{} ({}x)".format(lbl.split(" (")[0].split("/")[0].strip(), cnt)
                for lbl, cnt in sorted(eval_res.bucket_counts.items(), key=lambda x: -x[1])
            ]
            self.pattern_tag = "MIXED: " + ", ".join(mix_parts[:3])

    def status_style_key(self):
        """Returns canonical badge key ('OK', 'MIXED', 'BLOCKED') for UI pill rendering."""
        if self.rule_eval:
            if self.rule_eval.status == "COMPATIBLE":
                return "OK"
            if self.rule_eval.status == "PARTIAL_MIXED":
                return "MIXED"
            return "BLOCKED"
        if str(self.compatibility_tag or "").startswith("MIXED"):
            return "MIXED"
        if str(self.compatibility_tag or "").startswith("BLOCKED"):
            return "BLOCKED"
        return "OK"

    def geometry_breakdown_text(self):
        """Formats human-readable object geometry counts for the Layer Inspector."""
        if self.rule_eval and self.rule_eval.bucket_counts:
            parts = [
                "{} {}".format(cnt, lbl)
                for lbl, cnt in sorted(self.rule_eval.bucket_counts.items(), key=lambda x: -x[1])
            ]
            return ", ".join(parts)
        return getattr(self, "pattern_tag", "3D Geometry") or "3D Geometry"

    def target_summary_lines(self):
        """Returns compact two-line summary strings for the Layer List target column."""
        line1 = "{} · {}".format(
            self.inferred_category or "Generic",
            strategy_to_short(self.proposed_strategy)
        )
        mat = getattr(self, "selected_material", "<Auto from Rhino>") or "<Auto from Rhino>"
        mat_short = "Auto Mat" if mat == "<Auto from Rhino>" else (mat[:18] + ".." if len(mat) > 20 else mat)
        cp_cnt = len(getattr(self, "custom_parameters", []) or [])
        if cp_cnt > 0:
            line2 = "Mat: {} · {} param(s)".format(mat_short, cp_cnt)
        else:
            line2 = "Mat: {}".format(mat_short)
        return (line1, line2)

    def update_material(self, new_material):
        """Updates selected Revit material for the layer."""
        self.selected_material = str(new_material).strip() if new_material else "<Auto from Rhino>"

    def update_strategy(self, new_strategy):
        """Updates proposed strategy and evaluates compatibility + confidence deterministically."""
        self.proposed_strategy = friendly_to_strategy(new_strategy)
        eval_res = StrategyRuleEngine.evaluate_layer(self.objects, self.proposed_strategy)
        self.rule_eval = eval_res
        comp_score = eval_res.compatibility_score

        if eval_res.status == "COMPATIBLE":
            self.compatibility_tag = "OK ({}/{})".format(eval_res.pass_count, eval_res.total_count)
        elif eval_res.status == "PARTIAL_MIXED":
            self.compatibility_tag = "MIXED ({}/{})".format(eval_res.pass_count, eval_res.total_count)
        else:
            self.compatibility_tag = "BLOCKED (0/{})".format(eval_res.total_count)

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
        elif "wall" in cat_lower and "curtain" not in cat_lower:
            self.revit_type = "OST_Walls"
            if self.proposed_strategy in ("FloorNative", "RoofNative", "Toposolid"):
                self.proposed_strategy = "WallByCurve"
        elif "ceiling" in cat_lower:
            self.revit_type = "OST_Ceilings"
            if self.proposed_strategy in ("FloorNative", "RoofNative", "Toposolid"):
                self.proposed_strategy = "CeilingNative"
        elif "structural col" in cat_lower:
            self.revit_type = "OST_StructuralColumns"
        elif "column" in cat_lower or "pillar" in cat_lower or "pilar" in cat_lower:
            self.revit_type = "OST_Columns"
        elif "mullion" in cat_lower:
            self.revit_type = "OST_CurtainWallMullions"
        elif "curtain" in cat_lower or "panel" in cat_lower:
            self.revit_type = "OST_CurtainWallPanels"
        elif "door" in cat_lower or "puerta" in cat_lower or "porta" in cat_lower:
            self.revit_type = "OST_Doors"
        elif "window" in cat_lower or "ventana" in cat_lower or "finestra" in cat_lower:
            self.revit_type = "OST_Windows"
        elif "furniture system" in cat_lower:
            self.revit_type = "OST_FurnitureSystems"
        elif "furniture" in cat_lower or "mobiliari" in cat_lower or "sofa" in cat_lower or "chair" in cat_lower or "table" in cat_lower:
            self.revit_type = "OST_Furniture"
        elif "casework" in cat_lower or "cabinet" in cat_lower or "closet" in cat_lower or "armario" in cat_lower or "cocina" in cat_lower:
            self.revit_type = "OST_Casework"
        elif "plumb" in cat_lower or "sanit" in cat_lower or "lavabo" in cat_lower or "toilet" in cat_lower or "sink" in cat_lower:
            self.revit_type = "OST_PlumbingFixtures"
        elif "mechanic" in cat_lower or "hvac" in cat_lower or "clima" in cat_lower or "duct" in cat_lower:
            self.revit_type = "OST_MechanicalEquipment"
        elif "light" in cat_lower or "lamp" in cat_lower or "lum" in cat_lower or "llum" in cat_lower:
            self.revit_type = "OST_LightingFixtures"
        elif "electric" in cat_lower and "equip" in cat_lower:
            self.revit_type = "OST_ElectricalEquipment"
        elif "electric" in cat_lower:
            self.revit_type = "OST_ElectricalFixtures"
        elif "special" in cat_lower or "elevator" in cat_lower or "ascensor" in cat_lower or "equip" in cat_lower:
            self.revit_type = "OST_SpecialityEquipment"
        elif "railing" in cat_lower or "barandilla" in cat_lower or "barana" in cat_lower or "balustrade" in cat_lower:
            self.revit_type = "OST_StairsRailing"
        elif "ramp" in cat_lower or "rampa" in cat_lower:
            self.revit_type = "OST_Ramps"
        elif "park" in cat_lower or "aparcam" in cat_lower:
            self.revit_type = "OST_Parking"
        elif "road" in cat_lower or "street" in cat_lower or "vial" in cat_lower:
            self.revit_type = "OST_Roads"
        elif "hardscape" in cat_lower:
            self.revit_type = "OST_Hardscape"
        elif "plant" in cat_lower or "tree" in cat_lower or "vegetat" in cat_lower or "jardin" in cat_lower:
            self.revit_type = "OST_Planting"
        elif "site" in cat_lower:
            self.revit_type = "OST_Site"
        elif "foundat" in cat_lower or "footing" in cat_lower or "zapata" in cat_lower or "ciment" in cat_lower:
            self.revit_type = "OST_StructuralFoundation"
        elif "framing" in cat_lower or "beam" in cat_lower or "viga" in cat_lower or "truss" in cat_lower:
            self.revit_type = "OST_StructuralFraming"
        elif "stair" in cat_lower or "escala" in cat_lower:
            self.revit_type = "OST_Stairs"
        elif "entourage" in cat_lower or "person" in cat_lower or "people" in cat_lower or "coche" in cat_lower:
            self.revit_type = "OST_Entourage"
        elif "sign" in cat_lower:
            self.revit_type = "OST_Signage"
        elif "mass" in cat_lower:
            self.revit_type = "OST_Mass"
        elif "generic" in cat_lower:
            self.revit_type = "OST_GenericModel"
        elif self.inferred_category.startswith("OST_"):
            self.revit_type = self.inferred_category
        else:
            clean_tok = re.sub(r'[^a-zA-Z0-9_]', '', self.inferred_category)
            if clean_tok:
                self.revit_type = "OST_" + clean_tok if not clean_tok.startswith("OST_") else clean_tok
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
    """Data item model for the Validate & QA Audit table."""

    def __init__(self, val_report, layer_name, strategy, area_sqm=0.0, volume_cum=0.0, material_name="", material_vol=0.0):
        self.revit_id = str(val_report.revit_element_id)
        self.rhino_guid = val_report.rhino_guid
        self.layer_name = layer_name
        self.category = val_report.category_name
        self.strategy = strategy
        self.area = "{:.2f}".format(float(area_sqm)) if float(area_sqm or 0.0) > 0 else "-"
        self.volume = "{:.2f}".format(float(volume_cum)) if float(volume_cum or 0.0) > 0 else "-"
        self.material = "{}: {:.2f} m³".format(material_name, float(material_vol)) if (material_name and float(material_vol or 0.0) > 0) else (material_name or "-")
        self.bim_status = val_report.bim_status
        self.graphics_status = val_report.graphics_status
        self.overall_status = val_report.overall_status
        self.plan = val_report.view_matrix.get("Plan", "PASS")
        self.section = val_report.view_matrix.get("Section", "PASS")
        self.issues = "; ".join(val_report.issues) if val_report.issues else "None"
        self.raw_report = val_report


# =============================================================================
# WPF MODAL DIALOGS
# =============================================================================
class CustomParametersDialog(object):
    """WPF modal editor for deterministic Data parameters and Editable Family Constraints/Formulas."""

    def __init__(self, custom_parameters=None, strategy="DirectShape", scope_label="Layer", parent=None):
        self.strategy = friendly_to_strategy(strategy or "DirectShape")
        self.scope_label = scope_label
        self.parent = parent
        self.result_parameters = [
            CustomParameterSpec.from_dict(cp) for cp in (custom_parameters or [])
        ]
        self.window = None
        if not WPF_AVAILABLE:
            return

        win = _load_xaml_window("ui_custom_params.xaml")
        self.window = win
        _clamp_window_to_screen(win, 740, 560)

        meta = STRATEGY_METADATA.get(self.strategy, {})
        supports_type = bool(meta.get("supports_type_params", False))
        supports_constraints = bool(meta.get("supports_constraints", False))
        self.supports_type = supports_type
        self.supports_constraints = supports_constraints

        win.Title = "Custom Parameters - {}".format(scope_label)
        self.lbl_cp_header = win.FindName("lbl_cp_header")
        self.border_cp_scope = win.FindName("border_cp_scope")
        self.lbl_cp_scope = win.FindName("lbl_cp_scope")
        self.grid_params = win.FindName("grid_params")
        self.txt_name = win.FindName("txt_name")
        self.dd_type = win.FindName("dd_type")
        self.dd_scope = win.FindName("dd_scope")
        self.lbl_scope_hint = win.FindName("lbl_scope_hint")
        self.txt_value_expr = win.FindName("txt_value_expr")
        self.lbl_const_sec = win.FindName("lbl_const_sec")
        self.dd_constraint = win.FindName("dd_constraint")
        self.txt_formula = win.FindName("txt_formula")

        self.lbl_cp_header.Text = "Deterministic Custom Parameters: {}".format(scope_label)
        editability = meta.get("editability", self.strategy)
        self.lbl_cp_scope.Text = "{}  |  Mode: {}".format(strategy_to_short(self.strategy), editability)

        for dt in CustomParameterSpec.VALID_DATA_TYPES:
            self.dd_type.Items.Add(dt)
        self.dd_type.SelectedIndex = 0

        self.dd_scope.Items.Add("Instance")
        if supports_type:
            self.dd_scope.Items.Add("Type")
            self.lbl_scope_hint.Visibility = sw.Visibility.Collapsed
        else:
            self.lbl_scope_hint.Visibility = sw.Visibility.Visible
        self.dd_scope.SelectedIndex = 0
        self.dd_scope.IsEnabled = supports_type

        self.dd_constraint.Items.Add("None (Data Only)")
        for ct in ("Width", "Depth", "Height", "Base_Offset", "Radius", "Visibility", "Material"):
            self.dd_constraint.Items.Add(ct)
        self.dd_constraint.SelectedIndex = 0
        self.dd_constraint.IsEnabled = supports_constraints
        self.txt_formula.IsEnabled = supports_constraints

        if supports_constraints:
            self.lbl_const_sec.Text = "Editable Family Constraints & Formulas (Model-Driving):"
            self.lbl_const_sec.Foreground = _make_brush("#166534")
        else:
            self.lbl_const_sec.Text = "Model constraints & formulas require Editable Parametric / Adaptive Families:"
            self.lbl_const_sec.Foreground = _make_brush("#64748B")

        win.FindName("btn_tok_usertext").Click += self._on_tok_usertext
        win.FindName("btn_tok_layer").Click += self._on_tok_layer
        win.FindName("btn_tok_level").Click += self._on_tok_level
        win.FindName("btn_tok_material").Click += self._on_tok_material
        win.FindName("btn_tok_area").Click += self._on_tok_area

        win.FindName("btn_add_param").Click += self._on_add_or_update
        win.FindName("btn_rem_param").Click += self._on_remove_selected
        win.FindName("btn_cp_ok").Click += self._on_ok
        win.FindName("btn_cp_cancel").Click += self._on_cancel
        self.grid_params.SelectionChanged += self._on_grid_selected

        self._refresh_param_grid()

    def ShowModal(self, parent=None):
        if not WPF_AVAILABLE or self.window is None:
            return False
        _set_revit_owner(self.window, parent_window=parent or self.parent)
        return bool(self.window.ShowDialog() == True)

    def _append_token(self, tok):
        curr = str(self.txt_value_expr.Text or "")
        if tok == "@usertext:Key" and not curr:
            self.txt_value_expr.Text = "@usertext:Key"
        elif tok == "@usertext:Key":
            self.txt_value_expr.Text = tok
        else:
            self.txt_value_expr.Text = curr + tok

    def _on_tok_usertext(self, s, e):
        self._append_token("@usertext:Key")

    def _on_tok_layer(self, s, e):
        self._append_token("{layer}")

    def _on_tok_level(self, s, e):
        self._append_token("{level}")

    def _on_tok_material(self, s, e):
        self._append_token("{material}")

    def _on_tok_area(self, s, e):
        self._append_token("{area_sqm}")

    def _refresh_param_grid(self):
        rows = []
        for cp in self.result_parameters:
            val_display = ("= " + cp.formula) if cp.formula else (cp.value_expr or "-")
            rows.append(_make_clr_dict({
                "Name": cp.name,
                "Kind": cp.param_kind,
                "Type": cp.data_type,
                "Scope": "Instance" if cp.is_instance else "Type",
                "ValueRule": val_display,
                "ConstraintTarget": cp.constraint_target or "-"
            }))
        self.grid_params.ItemsSource = _make_clr_list(rows)

    def _on_grid_selected(self, s, e):
        idx = int(self.grid_params.SelectedIndex)
        if 0 <= idx < len(self.result_parameters):
            cp = self.result_parameters[idx]
            self.txt_name.Text = cp.name
            _set_combo_value(self.dd_type, cp.data_type)
            _set_combo_value(self.dd_scope, "Instance" if cp.is_instance else "Type")
            self.txt_value_expr.Text = cp.value_expr or ""
            _set_combo_value(self.dd_constraint, cp.constraint_target or "None (Data Only)")
            self.txt_formula.Text = cp.formula or ""

    def _on_add_or_update(self, s, e):
        raw_name = str(self.txt_name.Text or "").strip()
        if not raw_name:
            return
        dtype = _get_combo_value(self.dd_type, "Text")
        is_inst = (_get_combo_value(self.dd_scope, "Instance") == "Instance")
        expr = str(self.txt_value_expr.Text or "")
        ct_sel = _get_combo_value(self.dd_constraint, "None (Data Only)")
        ct_val = "" if ct_sel.startswith("None") else ct_sel
        formula_val = str(self.txt_formula.Text or "").strip() if self.txt_formula.IsEnabled else ""
        kind = "Constraint" if (ct_val or formula_val) else "Data"

        spec = CustomParameterSpec(
            name=raw_name,
            data_type=dtype,
            param_kind=kind,
            is_instance=is_inst,
            value_expr=expr,
            formula=formula_val,
            constraint_target=ct_val
        )
        replaced = False
        for i, existing in enumerate(self.result_parameters):
            if existing.name.lower() == spec.name.lower():
                self.result_parameters[i] = spec
                replaced = True
                break
        if not replaced:
            self.result_parameters.append(spec)
        self._refresh_param_grid()

    def _on_remove_selected(self, s, e):
        idx = int(self.grid_params.SelectedIndex)
        if 0 <= idx < len(self.result_parameters):
            self.result_parameters.pop(idx)
            self._refresh_param_grid()

    def _on_ok(self, s, e):
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, s, e):
        self.window.DialogResult = False
        self.window.Close()


class LayerConfigDialog(object):
    """Modal WPF dialog to inspect/edit one LayerRowItem (invoked on double-click or standalone)."""

    def __init__(self, layer_row, parent=None, revit_materials=None):
        self.layer_row = layer_row
        self.parent = parent
        self.working_custom_params = [
            CustomParameterSpec.from_dict(cp) for cp in (getattr(layer_row, "custom_parameters", []) or [])
        ]
        self.window = None
        self._last_recommended = "DirectShape"
        if not WPF_AVAILABLE:
            return

        win = _load_xaml_window("ui_layer_config.xaml")
        self.window = win
        _clamp_window_to_screen(win, 560, 530)

        win.Title = "Edit Layer Mapping: {}".format(layer_row.layer_name)
        win.FindName("lbl_lc_header").Text = "Configure Layer: {}".format(layer_row.layer_name)
        win.FindName("lbl_lc_info").Text = "Objects: {}  |  Geometry: {}".format(
            layer_row.object_count, layer_row.geometry_breakdown_text()
        )

        self.dd_cat = win.FindName("dd_cat")
        for c in STANDARD_REVIT_CATEGORIES:
            self.dd_cat.Items.Add(c)
        _set_combo_value(self.dd_cat, layer_row.inferred_category or "Generic Models")

        self.dd_strat = win.FindName("dd_strat")
        for _, friendly, _ in STRATEGY_UI_CHOICES:
            self.dd_strat.Items.Add(friendly)
        _set_combo_value(self.dd_strat, strategy_to_friendly(layer_row.proposed_strategy))
        self.dd_strat.SelectionChanged += self._on_strategy_selection_changed

        self.lbl_editability = win.FindName("lbl_editability")
        self.border_compat = win.FindName("border_compat")
        self.lbl_compat_banner = win.FindName("lbl_compat_banner")
        self.btn_use_rec = win.FindName("btn_use_rec")
        self.btn_use_rec.Click += self._on_use_recommended

        self.dd_material = win.FindName("dd_material")
        mats_list = list(revit_materials) if revit_materials else (
            list(parent.revit_materials) if parent and hasattr(parent, "revit_materials") and parent.revit_materials else ["<Auto from Rhino>"]
        )
        if "<Auto from Rhino>" not in mats_list:
            mats_list.insert(0, "<Auto from Rhino>")
        for m in mats_list:
            self.dd_material.Items.Add(m)
        _set_combo_value(self.dd_material, getattr(layer_row, "selected_material", "<Auto from Rhino>"))

        self.btn_custom_params = win.FindName("btn_custom_params")
        self.btn_custom_params.Content = "Custom Parameters ({})...".format(len(self.working_custom_params))
        self.btn_custom_params.Click += self._on_edit_custom_params

        self.chk_include = win.FindName("chk_include")
        self.chk_include.IsChecked = bool(layer_row.checked)

        win.FindName("btn_apply").Click += self._on_apply
        win.FindName("btn_cancel").Click += self._on_cancel

        self._on_strategy_selection_changed(None, None)

    def ShowModal(self, parent=None):
        if not WPF_AVAILABLE or self.window is None:
            return False
        _set_revit_owner(self.window, parent_window=parent or self.parent)
        return bool(self.window.ShowDialog() == True)

    def _on_strategy_selection_changed(self, sender, e):
        sel_strat = friendly_to_strategy(_get_combo_value(self.dd_strat, "DirectShape"))
        meta = STRATEGY_METADATA.get(sel_strat, {})
        self.lbl_editability.Text = "Revit Mode: {}".format(meta.get("editability", sel_strat))
        eval_res = StrategyRuleEngine.evaluate_layer(self.layer_row.objects, sel_strat)
        self._last_recommended = eval_res.recommended_strategy or "DirectShape"
        self.lbl_compat_banner.Text = eval_res.summary_text()
        if eval_res.status == "COMPATIBLE":
            self.border_compat.Background = _make_brush("#F0FDF4")
            self.border_compat.BorderBrush = _make_brush("#BBF7D0")
            self.lbl_compat_banner.Foreground = _make_brush("#166534")
            self.btn_use_rec.Visibility = sw.Visibility.Collapsed
        elif eval_res.status == "PARTIAL_MIXED":
            self.border_compat.Background = _make_brush("#FFFBEB")
            self.border_compat.BorderBrush = _make_brush("#FDE68A")
            self.lbl_compat_banner.Foreground = _make_brush("#92400E")
            self.btn_use_rec.Visibility = sw.Visibility.Visible
        else:
            self.border_compat.Background = _make_brush("#FEF2F2")
            self.border_compat.BorderBrush = _make_brush("#FECACA")
            self.lbl_compat_banner.Foreground = _make_brush("#991B1B")
            self.btn_use_rec.Visibility = sw.Visibility.Visible

    def _on_use_recommended(self, sender, e):
        _set_combo_value(self.dd_strat, strategy_to_friendly(self._last_recommended))
        self._on_strategy_selection_changed(None, None)

    def _on_edit_custom_params(self, sender, e):
        sel_strat = friendly_to_strategy(_get_combo_value(self.dd_strat, "DirectShape"))
        dlg = CustomParametersDialog(
            custom_parameters=self.working_custom_params,
            strategy=sel_strat,
            scope_label=self.layer_row.layer_name,
            parent=self.window
        )
        if dlg.ShowModal(self.window):
            self.working_custom_params = list(dlg.result_parameters)
            self.btn_custom_params.Content = "Custom Parameters ({})...".format(len(self.working_custom_params))

    def _on_apply(self, sender, e):
        sel_cat = _get_combo_value(self.dd_cat, "Generic Models")
        sel_strat = friendly_to_strategy(_get_combo_value(self.dd_strat, "DirectShape"))
        sel_mat = _get_combo_value(self.dd_material, "<Auto from Rhino>")

        self.layer_row.checked = bool(self.chk_include.IsChecked)
        self.layer_row.custom_parameters = list(self.working_custom_params)
        self.layer_row.update_category(sel_cat)
        self.layer_row.update_strategy(sel_strat)
        self.layer_row.update_material(sel_mat)

        for obj in self.layer_row.objects:
            c = getattr(obj, "classification", None)
            if c:
                c.inferred_category = self.layer_row.inferred_category
                c.revit_category_name = self.layer_row.revit_type
                c.proposed_strategy = self.layer_row.proposed_strategy
                c.confidence_score = self.layer_row.confidence_score
                c.confidence_tier = self.layer_row.confidence_tier

        if self.window is not None:
            self.window.DialogResult = True
            self.window.Close()

    def _on_cancel(self, sender, e):
        if self.window is not None:
            self.window.DialogResult = False
            self.window.Close()


class SettingsDialog(object):
    """Unified WPF modal for Transfer Rules, Units, Origin/BUA, and AI Classifier settings."""

    def __init__(self, ai_interpreter, dashboard_state=None, parent=None):
        self.ai_interpreter = ai_interpreter
        self.parent = parent
        self.state = dict(dashboard_state or {})
        self.window = None
        if not WPF_AVAILABLE:
            return

        win = _load_xaml_window("ui_settings.xaml")
        self.window = win
        _clamp_window_to_screen(win, 620, 590)

        self.dd_global_mode = win.FindName("dd_global_mode")
        for m in ("Hybrid", "Native BIM", "Geometry-First"):
            self.dd_global_mode.Items.Add(m)
        _set_combo_value(self.dd_global_mode, self.state.get("global_mode", "Hybrid"))

        self.dd_unit_scale = win.FindName("dd_unit_scale")
        for u in (
            "Auto-Detect", "mm -> Feet (x0.00328)", "Meters -> Feet (x3.28084)",
            "cm -> Feet (x0.0328)", "Inches -> Feet (x0.0833)", "Feet -> Feet (1.0)"
        ):
            self.dd_unit_scale.Items.Add(u)
        self.dd_unit_scale.SelectedIndex = int(self.state.get("unit_scale_idx", 0))

        self.dd_mixed_policy = win.FindName("dd_mixed_policy")
        self.dd_mixed_policy.Items.Add("Auto-Fallback")
        self.dd_mixed_policy.Items.Add("Strict Skip")
        self.dd_mixed_policy.SelectedIndex = 1 if self.state.get("mixed_policy", "AutoFallback") == "StrictSkip" else 0

        self.dd_rep_threshold = win.FindName("dd_rep_threshold")
        for r_opt in (">= 2 identical", ">= 3 identical", ">= 5 identical", ">= 10 identical"):
            self.dd_rep_threshold.Items.Add(r_opt)
        self.dd_rep_threshold.SelectedIndex = int(self.state.get("rep_threshold_idx", 1))

        self.chk_move_to_origin = win.FindName("chk_move_to_origin")
        self.chk_move_to_origin.IsChecked = bool(self.state.get("move_to_origin", True))

        self.chk_gen_bua = win.FindName("chk_gen_bua")
        self.chk_gen_bua.IsChecked = bool(self.state.get("generate_bua", True))

        # AI Settings
        self.dd_ai_provider = win.FindName("dd_ai_provider")
        providers = ["OpenAI", "Google Gemini", "DeepSeek", "OpenRouter", "Local Ollama / LM Studio", "Custom / Other"]
        for p in providers:
            self.dd_ai_provider.Items.Add(p)
        curr_prov = getattr(self.ai_interpreter, "provider", "OpenAI")
        _set_combo_value(self.dd_ai_provider, curr_prov if curr_prov in providers else "OpenAI")
        self.dd_ai_provider.SelectionChanged += self._on_provider_changed

        self.dd_ai_model = win.FindName("dd_ai_model")
        self.txt_ai_custom_model = win.FindName("txt_ai_custom_model")
        self.txt_ai_custom_model.Text = getattr(self.ai_interpreter, "model", "gpt-4o-mini") or ""
        self._populate_model_options()

        self.txt_ai_api_key = win.FindName("txt_ai_api_key")
        self.txt_ai_api_key.Text = getattr(self.ai_interpreter, "api_key", "") or ""

        self.txt_ai_endpoint = win.FindName("txt_ai_endpoint")
        self.txt_ai_endpoint.Text = getattr(self.ai_interpreter, "api_endpoint", "https://api.openai.com/v1/chat/completions") or ""

        self.lbl_ai_status = win.FindName("lbl_ai_status")
        win.FindName("btn_ai_test").Click += self._on_test_connection
        win.FindName("btn_settings_save").Click += self._on_save
        win.FindName("btn_settings_cancel").Click += self._on_cancel

    def ShowModal(self, parent=None):
        if not WPF_AVAILABLE or self.window is None:
            return False
        _set_revit_owner(self.window, parent_window=parent or self.parent)
        return bool(self.window.ShowDialog() == True)

    def _populate_model_options(self):
        prov = _get_combo_value(self.dd_ai_provider, "OpenAI")
        preset = PROVIDER_PRESETS.get(prov, {})
        models = preset.get("models", ["gpt-4o-mini", "gpt-4o"])
        try:
            self.dd_ai_model.SelectionChanged -= self._on_model_dd_changed
        except Exception:
            pass
        self.dd_ai_model.Items.Clear()
        for m in models:
            self.dd_ai_model.Items.Add(m)
        curr_model = getattr(self.ai_interpreter, "model", "")
        _set_combo_value(self.dd_ai_model, curr_model if curr_model in models else models[0])
        self.dd_ai_model.SelectionChanged += self._on_model_dd_changed

    def _on_model_dd_changed(self, s, e):
        val = _get_combo_value(self.dd_ai_model, "")
        if val:
            self.txt_ai_custom_model.Text = val

    def _on_provider_changed(self, s, e):
        prov = _get_combo_value(self.dd_ai_provider, "OpenAI")
        preset = PROVIDER_PRESETS.get(prov, {})
        if preset:
            self.txt_ai_endpoint.Text = preset.get("endpoint", "")
            self._populate_model_options()
            self.txt_ai_custom_model.Text = preset.get("default_model", "")

    def _on_test_connection(self, s, e):
        self.lbl_ai_status.Text = "Testing live connection..."
        self.lbl_ai_status.Foreground = _make_brush("#475569")
        _pump_wpf_events()
        prov = _get_combo_value(self.dd_ai_provider, "")
        model = str(self.txt_ai_custom_model.Text or _get_combo_value(self.dd_ai_model, "")).strip()
        key = str(self.txt_ai_api_key.Text or "").strip()
        endpoint = str(self.txt_ai_endpoint.Text or "").strip()

        success, msg = self.ai_interpreter.test_connection(
            api_key=key,
            api_endpoint=endpoint,
            model=model,
            provider=prov
        )
        self.lbl_ai_status.Text = msg
        self.lbl_ai_status.Foreground = _make_brush("#166534" if success else "#991B1B")

    def _on_save(self, s, e):
        self.state["global_mode"] = _get_combo_value(self.dd_global_mode, "Hybrid")
        self.state["unit_scale_idx"] = max(0, int(self.dd_unit_scale.SelectedIndex))
        self.state["mixed_policy"] = "StrictSkip" if self.dd_mixed_policy.SelectedIndex == 1 else "AutoFallback"
        self.state["rep_threshold_idx"] = max(0, int(self.dd_rep_threshold.SelectedIndex))
        self.state["move_to_origin"] = bool(self.chk_move_to_origin.IsChecked)
        self.state["generate_bua"] = bool(self.chk_gen_bua.IsChecked)

        prov = _get_combo_value(self.dd_ai_provider, "OpenAI")
        model = str(self.txt_ai_custom_model.Text or _get_combo_value(self.dd_ai_model, "gpt-4o-mini")).strip()
        key = str(self.txt_ai_api_key.Text or "").strip()
        endpoint = str(self.txt_ai_endpoint.Text or "").strip()

        self.ai_interpreter.provider = prov
        self.ai_interpreter.model = model
        self.ai_interpreter.api_key = key
        self.ai_interpreter.api_endpoint = endpoint
        self.ai_interpreter.save_config()

        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, s, e):
        self.window.DialogResult = False
        self.window.Close()


class AISettingsDialog(SettingsDialog):
    """Backwards-compatible alias launching the unified Settings & AI modal."""

    def __init__(self, ai_interpreter, parent=None):
        super(AISettingsDialog, self).__init__(ai_interpreter, dashboard_state=None, parent=parent)


# =============================================================================
# MAIN 3-STEP MASTER-DETAIL TRANSFER DASHBOARD
# =============================================================================
class TransferDashboardDialog(object):
    """Complete 3-Step WPF XAML Master-Detail Dashboard Dialog for Rhino-to-Revit Transfer."""

    def __init__(self, revit_doc=None, revit_uidoc=None):
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
        self.classifications = {}   # guid -> ClassificationResult
        self.sync_evaluations = {}  # guid -> SyncEvaluation
        self.missing_in_rhino = []
        self.layer_rows = []        # list of LayerRowItem
        self.filtered_layer_rows = []
        self.validation_rows = []   # list of ValidationRowItem
        self.qa_report = TransferQAReport()

        # Global Transfer Settings State
        self.global_mode = "Hybrid"
        self.unit_scale_idx = 0
        self.mixed_policy = "AutoFallback"
        self.rep_threshold_idx = 1  # >= 3 identical
        self.move_to_origin = True
        self.generate_bua = True

        self.revit_materials = self._get_project_materials()
        self.project_custom_parameters = list(getattr(self.profile_mgr, "project_custom_parameters", []) or [])

        # UI Guard Flags & Step State
        self.active_step = 0
        self._updating_inspector = False
        self._restoring_selection = False
        self._refreshing_grid = False
        self._last_recommended = "DirectShape"
        self.window = None

        if not WPF_AVAILABLE:
            return

        self._init_ui()

    def _get_project_materials(self):
        """Queries the active Revit document for all available project materials."""
        mats = ["<Auto from Rhino>"]
        if REVIT_AVAILABLE and self.revit_doc:
            try:
                col = DB.FilteredElementCollector(self.revit_doc).OfClass(DB.Material)
                names = []
                for m in col:
                    if hasattr(m, "Name") and m.Name:
                        names.append(m.Name)
                mats.extend(sorted(names, key=lambda s: s.lower()))
            except Exception:
                pass
        return mats

    @staticmethod
    def _make_elem_id(val):
        try:
            return DB.ElementId(System.Int64(val))
        except Exception:
            return DB.ElementId(int(val))

    def ShowModal(self, parent=None):
        """Displays the WPF dashboard modally anchored to Revit's main window."""
        if not WPF_AVAILABLE or self.window is None:
            return False
        _set_revit_owner(self.window, revit_uidoc=self.revit_uidoc, parent_window=parent)
        return bool(self.window.ShowDialog())

    def Show(self, parent=None):
        """Alias for ShowModal."""
        return self.ShowModal(parent=parent)

    def show(self, parent=None):
        """Lowercase alias for Show / ShowModal compatibility."""
        return self.ShowModal(parent=parent)

    def Close(self, result=True):
        if WPF_AVAILABLE and self.window is not None:
            try:
                self.window.Close()
            except Exception:
                pass

    # =========================================================================
    # UI INITIALIZATION & WIRING
    # =========================================================================
    def _init_ui(self):
        win = _load_xaml_window("ui_dashboard.xaml")
        self.window = win
        _clamp_window_to_screen(win, 1180, 760)

        # Header & Step Navigation
        self.lbl_header_settings = win.FindName("lbl_header_settings")
        self.btn_step_scan = win.FindName("btn_step_scan")
        self.btn_step_configure = win.FindName("btn_step_configure")
        self.btn_step_run = win.FindName("btn_step_run")
        self.btn_load_profile = win.FindName("btn_load_profile")
        self.btn_save_profile = win.FindName("btn_save_profile")
        self.btn_open_settings = win.FindName("btn_open_settings")

        self.panel_step_scan = win.FindName("panel_step_scan")
        self.panel_step_configure = win.FindName("panel_step_configure")
        self.panel_step_run = win.FindName("panel_step_run")

        # Step 1 Controls
        self.chk_include_hidden = win.FindName("chk_include_hidden")
        self.chk_include_locked = win.FindName("chk_include_locked")
        self.chk_auto_split_mixed = win.FindName("chk_auto_split_mixed")
        self.btn_scan = win.FindName("btn_scan")
        self.lbl_kpi_total = win.FindName("lbl_kpi_total")
        self.lbl_kpi_units = win.FindName("lbl_kpi_units")
        self.lbl_kpi_origin = win.FindName("lbl_kpi_origin")
        self.lbl_kpi_duplicates = win.FindName("lbl_kpi_duplicates")
        self.lbl_kpi_loops = win.FindName("lbl_kpi_loops")
        self.txt_scan_warnings = win.FindName("txt_scan_warnings")

        # Step 2 Toolbar & Master Grid
        self.txt_search = win.FindName("txt_search")
        self.dd_status_filter = win.FindName("dd_status_filter")
        self.dd_cat_filter = win.FindName("dd_cat_filter")
        self.btn_select_all = win.FindName("btn_select_all")
        self.btn_deselect_all = win.FindName("btn_deselect_all")
        self.btn_split_mixed = win.FindName("btn_split_mixed")
        self.btn_ai_assist = win.FindName("btn_ai_assist")
        self.btn_project_params = win.FindName("btn_project_params")
        self.grid_review = win.FindName("grid_review")

        for s_opt in ("All Layers", "Compatible (OK)", "Mixed Geometry", "Blocked / Low Conf", "Modified / New"):
            self.dd_status_filter.Items.Add(s_opt)
        self.dd_status_filter.SelectedIndex = 0

        self.dd_cat_filter.Items.Add("All Categories")
        for c_opt in ("Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape"):
            self.dd_cat_filter.Items.Add(c_opt)
        self.dd_cat_filter.SelectedIndex = 0

        # Step 2 Inspector
        self.panel_inspector_empty = win.FindName("panel_inspector_empty")
        self.panel_inspector_body = win.FindName("panel_inspector_body")
        self.lbl_insp_title = win.FindName("lbl_insp_title")
        self.lbl_insp_subtitle = win.FindName("lbl_insp_subtitle")
        self.border_insp_geom = win.FindName("border_insp_geom")
        self.lbl_insp_geom = win.FindName("lbl_insp_geom")
        self.btn_insp_split = win.FindName("btn_insp_split")
        self.chk_insp_include = win.FindName("chk_insp_include")
        self.cb_insp_category = win.FindName("cb_insp_category")
        self.cb_insp_strategy = win.FindName("cb_insp_strategy")
        self.border_insp_editability = win.FindName("border_insp_editability")
        self.lbl_insp_editability = win.FindName("lbl_insp_editability")
        self.border_insp_compat = win.FindName("border_insp_compat")
        self.lbl_insp_compat = win.FindName("lbl_insp_compat")
        self.btn_insp_use_rec = win.FindName("btn_insp_use_rec")
        self.cb_insp_material = win.FindName("cb_insp_material")
        self.lbl_insp_params_summary = win.FindName("lbl_insp_params_summary")
        self.btn_insp_params = win.FindName("btn_insp_params")
        self.panel_insp_bulk = win.FindName("panel_insp_bulk")
        self.btn_insp_bulk_apply = win.FindName("btn_insp_bulk_apply")

        for c in STANDARD_REVIT_CATEGORIES:
            self.cb_insp_category.Items.Add(c)
        for _, friendly, _ in STRATEGY_UI_CHOICES:
            self.cb_insp_strategy.Items.Add(friendly)
        self._populate_inspector_materials()

        # Step 3 Controls
        self.lbl_run_summary = win.FindName("lbl_run_summary")
        self.progress_bar = win.FindName("progress_bar")
        self.lbl_exec_status = win.FindName("lbl_exec_status")
        self.btn_execute = win.FindName("btn_execute")
        self.exp_exec_log = win.FindName("exp_exec_log")
        self.txt_exec_log = win.FindName("txt_exec_log")
        self.btn_isolate = win.FindName("btn_isolate")
        self.btn_export_html = win.FindName("btn_export_html")
        self.btn_export_csv = win.FindName("btn_export_csv")
        self.grid_validate = win.FindName("grid_validate")

        # Footer
        self.lbl_global_status = win.FindName("lbl_global_status")
        self.lbl_footer_summary = win.FindName("lbl_footer_summary")
        self.btn_back = win.FindName("btn_back")
        self.btn_next = win.FindName("btn_next")

        # Wire Events
        self.btn_step_scan.Click += lambda s, e: self._set_active_step(0)
        self.btn_step_configure.Click += lambda s, e: self._set_active_step(1)
        self.btn_step_run.Click += lambda s, e: self._set_active_step(2)
        self.btn_load_profile.Click += self._on_load_profile
        self.btn_save_profile.Click += self._on_save_profile
        self.btn_open_settings.Click += self._on_open_settings

        self.btn_scan.Click += self._on_scan_clicked

        self.txt_search.TextChanged += self._apply_review_filters
        self.dd_status_filter.SelectionChanged += self._apply_review_filters
        self.dd_cat_filter.SelectionChanged += self._apply_review_filters
        self.btn_select_all.Click += self._on_select_all_layers
        self.btn_deselect_all.Click += self._on_deselect_all_layers
        self.btn_split_mixed.Click += self._on_split_mixed_layers
        self.btn_ai_assist.Click += self._on_run_ai_assist
        self.btn_project_params.Click += self._on_edit_project_params

        self.grid_review.SelectionChanged += self._on_review_selection_changed
        self.grid_review.MouseDoubleClick += self._on_grid_cell_double_click
        if swcp is not None:
            try:
                chk_handler = sw.RoutedEventHandler(self._on_grid_checkbox_toggled)
                self.grid_review.AddHandler(swcp.ToggleButton.CheckedEvent, chk_handler)
                self.grid_review.AddHandler(swcp.ToggleButton.UncheckedEvent, chk_handler)
            except Exception:
                pass

        # Inspector Events
        self.btn_insp_split.Click += self._on_insp_split_clicked
        self.chk_insp_include.Checked += self._on_insp_single_changed
        self.chk_insp_include.Unchecked += self._on_insp_single_changed
        self.cb_insp_category.SelectionChanged += self._on_insp_single_changed
        self.cb_insp_category.LostFocus += self._on_insp_single_changed
        self.cb_insp_strategy.SelectionChanged += self._on_insp_strategy_changed
        self.btn_insp_use_rec.Click += self._on_insp_use_recommended
        self.cb_insp_material.SelectionChanged += self._on_insp_single_changed
        self.cb_insp_material.LostFocus += self._on_insp_single_changed
        self.btn_insp_params.Click += self._on_insp_edit_params
        self.btn_insp_bulk_apply.Click += self._on_insp_bulk_apply

        # Step 3 Events
        self.btn_execute.Click += self._on_execute_transfer
        self.btn_isolate.Click += self._on_isolate_element
        self.btn_export_html.Click += self._on_export_html
        self.btn_export_csv.Click += self._on_export_csv

        # Footer Events
        self.btn_back.Click += self._on_wizard_back
        self.btn_next.Click += self._on_wizard_next

        self._update_header_settings_chip()
        self._update_preview_kpis()
        self._set_active_step(0)

    def _populate_inspector_materials(self):
        if not WPF_AVAILABLE or getattr(self, "cb_insp_material", None) is None:
            return
        self.cb_insp_material.Items.Clear()
        for m in (self.revit_materials or ["<Auto from Rhino>"]):
            self.cb_insp_material.Items.Add(m)

    def _update_header_settings_chip(self):
        if not WPF_AVAILABLE or getattr(self, "lbl_header_settings", None) is None:
            return
        unit_short = _UNIT_SCALE_SHORT[self.unit_scale_idx] if 0 <= self.unit_scale_idx < len(_UNIT_SCALE_SHORT) else "Auto Units"
        policy_lbl = "Strict Skip" if self.mixed_policy == "StrictSkip" else "Auto-Fallback"
        self.lbl_header_settings.Text = "{} · {} · {}".format(self.global_mode, unit_short, policy_lbl)
        self.btn_project_params.Content = "Project Params ({})...".format(len(self.project_custom_parameters))

    def _set_active_step(self, step_idx):
        self.active_step = max(0, min(2, int(step_idx)))
        if not WPF_AVAILABLE:
            return

        panels = [self.panel_step_scan, self.panel_step_configure, self.panel_step_run]
        pills = [self.btn_step_scan, self.btn_step_configure, self.btn_step_run]
        active_bg = _make_brush("#0066CC")
        active_fg = _make_brush("#FFFFFF")
        active_bd = _make_brush("#0052A3")
        idle_bg = _make_brush("#F1F5F9")
        idle_fg = _make_brush("#475569")
        idle_bd = _make_brush("#DCE2EA")

        for i, p in enumerate(panels):
            p.Visibility = sw.Visibility.Visible if i == self.active_step else sw.Visibility.Collapsed
        for i, b in enumerate(pills):
            is_act = (i == self.active_step)
            b.Background = active_bg if is_act else idle_bg
            b.Foreground = active_fg if is_act else idle_fg
            b.BorderBrush = active_bd if is_act else idle_bd

        self.btn_back.Visibility = sw.Visibility.Collapsed if self.active_step == 0 else sw.Visibility.Visible
        if self.active_step == 0:
            self.btn_next.Content = "Next: Configure ->"
        elif self.active_step == 1:
            self.btn_next.Content = "Proceed to Transfer ->"
        else:
            self.btn_next.Content = "Close"

        self._update_preview_kpis()

    def _on_wizard_back(self, s, e):
        if self.active_step > 0:
            self._set_active_step(self.active_step - 1)

    def _on_wizard_next(self, s, e):
        if self.active_step < 2:
            self._set_active_step(self.active_step + 1)
        else:
            self.Close(True)

    # =========================================================================
    # STEP 1: SCAN HANDLERS
    # =========================================================================
    def _on_scan_clicked(self, sender, e):
        """Executes full scan of active Rhino document and moves cleanly to Step 2."""
        self.btn_scan.IsEnabled = False
        self.btn_scan.Content = "Scanning..."
        self.lbl_global_status.Text = "Step 1/4: Scanning Rhino document objects..."
        _pump_wpf_events()
        try:
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

                self.revit_materials = self._get_project_materials()
                self._populate_inspector_materials()

            include_hidden = bool(self.chk_include_hidden.IsChecked)
            include_locked = bool(self.chk_include_locked.IsChecked)

            self.scanned_objects = self.scanner.scan_all(
                include_hidden=include_hidden,
                include_locked=include_locked,
                revit_levels=revit_levels
            )

            pf = self.scanner.preflight
            self.lbl_kpi_total.Text = str(pf.total_scanned)
            self.lbl_kpi_units.Text = "{} (x{:.4f}m)".format(pf.rhino_unit_system, pf.unit_scale_to_meters)
            self.lbl_kpi_origin.Text = "Far (>16km): {}".format(pf.total_far_from_origin)
            self.lbl_kpi_duplicates.Text = "Duplicates: {}".format(pf.total_duplicates)
            self.lbl_kpi_loops.Text = str(pf.total_non_closed_loops)

            warn_text = []
            if pf.total_duplicates > 0:
                warn_text.append("[WARNING] Found {} duplicate objects with identical geometry hashes.".format(pf.total_duplicates))
            if pf.total_far_from_origin > 0:
                warn_text.append(
                    "[CAUTION] Found {} object(s) beyond Revit's 16km origin limit. "
                    "'Move Model to Revit Origin (0,0,0)' will center the model cleanly in Revit.".format(pf.total_far_from_origin)
                )
            if pf.total_non_closed_loops > 0:
                warn_text.append("[NOTICE] Found {} object(s) with non-closed boundary loops.".format(pf.total_non_closed_loops))
            if getattr(pf, "warnings", None):
                warn_text.extend(pf.warnings)

            self.txt_scan_warnings.Text = "\n".join(warn_text) if warn_text else "Preflight clean. No geometric anomalies detected."

            self.lbl_global_status.Text = "Step 2/4: Classifying elements and matching sync records..."
            self._run_classification_and_sync()

            self.lbl_global_status.Text = "Step 3/4: Populating layer list..."
            self._populate_review_grid()
            self._update_preview_kpis()

            self.lbl_global_status.Text = "Scan complete. {} objects across {} layer(s) ready to configure.".format(
                len(self.scanned_objects), len(self.layer_rows)
            )
            self._set_active_step(1)
        except Exception as ex:
            err_details = traceback.format_exc()
            print("[R2R Scan Error]:\n" + err_details)
            self.lbl_global_status.Text = "Scan error: {}".format(ex)
            self.txt_scan_warnings.Text = "Scan Exception:\n{}\n{}".format(ex, err_details)
        finally:
            self.btn_scan.IsEnabled = True
            self.btn_scan.Content = "Scan Active Rhino Document"

    def _run_classification_and_sync(self):
        """Runs classifier and sync engine on scanned objects."""
        self.classifier.mode = self.global_mode
        self.classifier.prepare_repetition_cache(self.scanned_objects)

        self.classifications = {}
        for obj in self.scanned_objects:
            res = self.classifier.classify_object(obj, self.scanned_objects)
            self.classifications[obj.rhino_guid] = res

        self.profile_mgr.apply_profile(self.scanned_objects, self.classifications)
        self.sync_evaluations, self.missing_in_rhino = self.sync_engine.evaluate_sync_states(self.scanned_objects)

    # =========================================================================
    # STEP 2: LAYER LIST + MASTER-DETAIL INSPECTOR
    # =========================================================================
    def _build_rows_for_layer(self, layer_name, objs, auto_split=False, inherit_material="<Auto from Rhino>", inherit_params=None):
        """Creates one LayerRowItem (if homogeneous or unsplit) or multiple virtual sub-rows if split."""
        matched_rule = self.profile_mgr.find_matching_rule(layer_name) if getattr(self, "profile_mgr", None) else None
        base_mat = inherit_material
        if (not base_mat or base_mat == "<Auto from Rhino>") and matched_rule and getattr(matched_rule, "material", "ByLayer") not in ("ByLayer", "<Auto from Rhino>", ""):
            base_mat = matched_rule.material
        base_params = list(inherit_params or (getattr(matched_rule, "custom_parameters", None) or []))

        if auto_split:
            splits = StrategyRuleEngine.split_objects_by_geometry(layer_name, objs)
        else:
            only_bk, _ = StrategyRuleEngine.classify_geometry_bucket(objs[0]) if objs else ("", "")
            splits = [(layer_name, only_bk, objs)]

        if len(splits) <= 1:
            rep_obj = objs[0]
            c_res = self.classifications.get(rep_obj.rhino_guid)
            s_ev = self.sync_evaluations.get(rep_obj.rhino_guid)
            checked = c_res.is_prechecked if c_res else True
            row = LayerRowItem(
                layer_name, objs, c_res, s_ev, checked=checked,
                selected_material=base_mat, source_layer_path=layer_name,
                bucket_key=splits[0][1] if splits else "", custom_parameters=base_params
            )
            return [row]

        created_rows = []
        for v_name, bk, sub_objs in splits:
            rep_obj = sub_objs[0]
            c_res = self.classifications.get(rep_obj.rhino_guid)
            s_ev = self.sync_evaluations.get(rep_obj.rhino_guid)
            _, b_label = StrategyRuleEngine.classify_geometry_bucket(rep_obj)
            rec_strat = StrategyRuleEngine.recommend_best_strategy(
                sub_objs, c_res.inferred_category if c_res else None
            )
            checked = (bk != "CURVE")
            row = LayerRowItem(
                v_name, sub_objs, c_res, s_ev, checked=checked,
                selected_material=base_mat, source_layer_path=layer_name,
                bucket_key=bk, bucket_label=b_label, custom_parameters=base_params
            )
            row.update_strategy(rec_strat)
            if bk == "CURVE":
                row.checked = False
            created_rows.append(row)

        return created_rows

    def _populate_review_grid(self):
        """Groups scanned objects by layer (auto-splitting mixed layers when enabled) and binds DataGrid."""
        layer_groups = {}
        layer_order = []
        for obj in self.scanned_objects:
            lp = obj.layer_full_path
            if lp not in layer_groups:
                layer_groups[lp] = []
                layer_order.append(lp)
            layer_groups[lp].append(obj)

        auto_split = bool(
            WPF_AVAILABLE and getattr(self, "chk_auto_split_mixed", None) is not None and self.chk_auto_split_mixed.IsChecked
        )

        self.layer_rows = []
        for lp in layer_order:
            self.layer_rows.extend(self._build_rows_for_layer(lp, layer_groups[lp], auto_split=auto_split))

        if self.layer_rows and not any(r.checked for r in self.layer_rows):
            for r in self.layer_rows:
                if getattr(r, "bucket_key", "") != "CURVE":
                    r.checked = True

        self._apply_review_filters(None, None)

    def _row_to_clr_dict(self, r):
        line1, line2 = r.target_summary_lines()
        s_key = r.status_style_key()
        if s_key == "OK":
            bg, fg = "#DCFCE7", "#166534"
        elif s_key == "MIXED":
            bg, fg = "#FEF3C7", "#92400E"
        else:
            bg, fg = "#FEE2E2", "#991B1B"

        return _make_clr_dict({
            "Checked": bool(r.checked),
            "LayerName": r.layer_name,
            "PatternSub": "{} · {}".format(getattr(r, "pattern_tag", "Standard"), r.sync_state),
            "CountText": str(r.object_count),
            "TargetLine1": line1,
            "TargetLine2": line2,
            "StatusText": getattr(r, "compatibility_tag", "OK"),
            "StatusBg": bg,
            "StatusFg": fg,
            "RowObj": r
        })

    def _get_selected_review_rows(self):
        """Returns highlighted LayerRowItem objects from the WPF DataGrid."""
        selected = []
        if not WPF_AVAILABLE or getattr(self, "grid_review", None) is None:
            return selected
        try:
            for item in self.grid_review.SelectedItems:
                row_obj = item["RowObj"] if (hasattr(item, "ContainsKey") and item.ContainsKey("RowObj")) else None
                if row_obj is not None:
                    selected.append(row_obj)
        except Exception:
            pass
        return selected

    def _refresh_review_grid_datastore(self):
        """Rebinds filtered_layer_rows to WPF grid_review while preserving row selection."""
        if not WPF_AVAILABLE or getattr(self, "grid_review", None) is None:
            return

        prev_selected = set(id(r) for r in self._get_selected_review_rows())
        self._refreshing_grid = True
        self._restoring_selection = True
        try:
            clr_rows = [self._row_to_clr_dict(r) for r in self.filtered_layer_rows]
            self.grid_review.ItemsSource = _make_clr_list(clr_rows)

            if prev_selected and self.grid_review.Items.Count > 0:
                self.grid_review.SelectedItems.Clear()
                for item in self.grid_review.Items:
                    ro = item["RowObj"] if (hasattr(item, "ContainsKey") and item.ContainsKey("RowObj")) else None
                    if ro is not None and id(ro) in prev_selected:
                        self.grid_review.SelectedItems.Add(item)
        finally:
            self._restoring_selection = False
            self._refreshing_grid = False

        self._refresh_inspector()

    def _on_grid_checkbox_toggled(self, sender, e):
        """Syncs 1-click row CheckBox changes back to LayerRowItem immediately."""
        if self._refreshing_grid:
            return
        try:
            src = e.OriginalSource
            if src is None:
                return
            dc = getattr(src, "DataContext", None)
            if dc is not None and hasattr(dc, "ContainsKey") and dc.ContainsKey("RowObj"):
                row_obj = dc["RowObj"]
                row_obj.checked = bool(src.IsChecked)
                self._update_preview_kpis()
                if not self._updating_inspector:
                    sel = self._get_selected_review_rows()
                    if len(sel) == 1 and sel[0] is row_obj:
                        self._updating_inspector = True
                        try:
                            self.chk_insp_include.IsChecked = bool(row_obj.checked)
                        finally:
                            self._updating_inspector = False
        except Exception:
            pass

    def _on_review_selection_changed(self, s, e):
        if self._restoring_selection:
            return
        self._refresh_inspector()

    def _apply_review_filters(self, sender, e):
        """Filters the left Layer List based on search text, status, and category."""
        search_txt = ""
        status_filter = "All Layers"
        cat_filter = "All Categories"
        if WPF_AVAILABLE and getattr(self, "txt_search", None) is not None:
            search_txt = str(self.txt_search.Text or "").strip().lower()
            status_filter = _get_combo_value(self.dd_status_filter, "All Layers")
            cat_filter = _get_combo_value(self.dd_cat_filter, "All Categories")

        self.filtered_layer_rows = []
        for r in self.layer_rows:
            if search_txt and search_txt not in r.layer_name.lower():
                continue
            if status_filter == "Compatible (OK)" and r.status_style_key() != "OK":
                continue
            if status_filter == "Mixed Geometry" and not (r.rule_eval and r.rule_eval.is_mixed_geometry):
                continue
            if status_filter == "Blocked / Low Conf" and (r.status_style_key() == "OK" and r.confidence_score >= 60.0):
                continue
            if status_filter == "Modified / New" and r.sync_state == "SKIP_UNCHANGED":
                continue
            if cat_filter != "All Categories":
                if cat_filter == "Family":
                    if r.proposed_strategy not in ("ParametricFamily", "FreeFormFamily", "FamilyInstance", "RepetitiveFamily", "AdaptiveComponent"):
                        continue
                elif cat_filter == "DirectShape":
                    if r.proposed_strategy != "DirectShape":
                        continue
                elif cat_filter.lower() not in (r.inferred_category or "").lower():
                    continue
            self.filtered_layer_rows.append(r)

        self._refresh_review_grid_datastore()

    def _on_select_all_layers(self, s, e):
        for r in self.filtered_layer_rows:
            r.checked = True
        self._refresh_review_grid_datastore()
        self._update_preview_kpis()

    def _on_deselect_all_layers(self, s, e):
        for r in self.filtered_layer_rows:
            r.checked = False
        self._refresh_review_grid_datastore()
        self._update_preview_kpis()

    # =========================================================================
    # RIGHT-SIDE LIVE INSPECTOR (SINGLE & BULK EDIT)
    # =========================================================================
    def _refresh_inspector(self):
        if not WPF_AVAILABLE or getattr(self, "panel_inspector_body", None) is None:
            return

        selected = self._get_selected_review_rows()
        if not selected:
            self.panel_inspector_empty.Visibility = sw.Visibility.Visible
            self.panel_inspector_body.Visibility = sw.Visibility.Collapsed
            return

        self.panel_inspector_empty.Visibility = sw.Visibility.Collapsed
        self.panel_inspector_body.Visibility = sw.Visibility.Visible

        self._updating_inspector = True
        try:
            if len(selected) == 1:
                row = selected[0]
                self.lbl_insp_title.Text = row.layer_name
                self.lbl_insp_subtitle.Text = "{} object(s) · Sync: {} · Conf: {:.0f}%".format(
                    row.object_count, row.sync_state, row.confidence_score
                )
                self.lbl_insp_geom.Text = row.geometry_breakdown_text()
                is_mixed_geom = bool(row.rule_eval and row.rule_eval.is_mixed_geometry)
                self.btn_insp_split.Visibility = sw.Visibility.Visible if is_mixed_geom else sw.Visibility.Collapsed

                self.chk_insp_include.IsChecked = bool(row.checked)
                _set_combo_value(self.cb_insp_category, row.inferred_category or "Generic Models")
                _set_combo_value(self.cb_insp_strategy, strategy_to_friendly(row.proposed_strategy))
                _set_combo_value(self.cb_insp_material, getattr(row, "selected_material", "<Auto from Rhino>"))

                layer_cp = len(getattr(row, "custom_parameters", []) or [])
                proj_cp = len(self.project_custom_parameters or [])
                self.lbl_insp_params_summary.Text = "{} layer-specific + {} project-wide parameter(s)".format(layer_cp, proj_cp)
                self.btn_insp_params.IsEnabled = True
                self.panel_insp_bulk.Visibility = sw.Visibility.Collapsed

                self._update_inspector_rule_banner(row.objects, row.proposed_strategy)
            else:
                total_objs = sum(r.object_count for r in selected)
                self.lbl_insp_title.Text = "Bulk Edit ({} layers selected)".format(len(selected))
                self.lbl_insp_subtitle.Text = "{} total objects · Edit fields below and click Apply".format(total_objs)
                all_objs = []
                for r in selected:
                    all_objs.extend(r.objects)
                ev_bulk = StrategyRuleEngine.evaluate_layer(all_objs, selected[0].proposed_strategy)
                self.lbl_insp_geom.Text = "{} buckets across selection".format(len(ev_bulk.bucket_counts))
                has_any_mixed = any(r.rule_eval and r.rule_eval.is_mixed_geometry for r in selected)
                self.btn_insp_split.Visibility = sw.Visibility.Visible if has_any_mixed else sw.Visibility.Collapsed

                self.chk_insp_include.IsChecked = all(r.checked for r in selected)
                _set_combo_value(self.cb_insp_category, selected[0].inferred_category or "Generic Models")
                _set_combo_value(self.cb_insp_strategy, strategy_to_friendly(selected[0].proposed_strategy))
                _set_combo_value(self.cb_insp_material, getattr(selected[0], "selected_material", "<Auto from Rhino>"))

                self.lbl_insp_params_summary.Text = "Configure per-layer parameters by selecting a single layer."
                self.btn_insp_params.IsEnabled = False
                self.panel_insp_bulk.Visibility = sw.Visibility.Visible

                self._update_inspector_rule_banner(all_objs, selected[0].proposed_strategy)
        finally:
            self._updating_inspector = False

    def _update_inspector_rule_banner(self, objects, strategy_key):
        sel_strat = friendly_to_strategy(strategy_key)
        meta = STRATEGY_METADATA.get(sel_strat, {})
        self.lbl_insp_editability.Text = "Revit Editability: {}".format(meta.get("editability", sel_strat))

        eval_res = StrategyRuleEngine.evaluate_layer(objects, sel_strat)
        self._last_recommended = eval_res.recommended_strategy or "DirectShape"
        self.lbl_insp_compat.Text = eval_res.summary_text()

        if eval_res.status == "COMPATIBLE":
            self.border_insp_compat.Background = _make_brush("#F0FDF4")
            self.border_insp_compat.BorderBrush = _make_brush("#BBF7D0")
            self.lbl_insp_compat.Foreground = _make_brush("#166534")
            self.btn_insp_use_rec.Visibility = sw.Visibility.Collapsed
        elif eval_res.status == "PARTIAL_MIXED":
            self.border_insp_compat.Background = _make_brush("#FFFBEB")
            self.border_insp_compat.BorderBrush = _make_brush("#FDE68A")
            self.lbl_insp_compat.Foreground = _make_brush("#92400E")
            self.btn_insp_use_rec.Content = "Switch to {}".format(strategy_to_short(self._last_recommended))
            self.btn_insp_use_rec.Visibility = sw.Visibility.Visible
        else:
            self.border_insp_compat.Background = _make_brush("#FEF2F2")
            self.border_insp_compat.BorderBrush = _make_brush("#FECACA")
            self.lbl_insp_compat.Foreground = _make_brush("#991B1B")
            self.btn_insp_use_rec.Content = "Switch to {}".format(strategy_to_short(self._last_recommended))
            self.btn_insp_use_rec.Visibility = sw.Visibility.Visible

    def _apply_row_changes(self, row, cat_name, strat_key, mat_name, is_checked):
        """Applies updated Category, Strategy, Material, and Checked state to a LayerRowItem & its objects."""
        row.checked = bool(is_checked)
        row.update_category(cat_name)
        row.update_strategy(strat_key)
        row.update_material(mat_name)
        for obj in row.objects:
            c = getattr(obj, "classification", None) or self.classifications.get(getattr(obj, "rhino_guid", ""))
            if c:
                c.inferred_category = row.inferred_category
                c.revit_category_name = row.revit_type
                c.proposed_strategy = row.proposed_strategy
                c.confidence_score = row.confidence_score
                c.confidence_tier = row.confidence_tier

    def _on_insp_single_changed(self, s, e):
        """Live-applies Category, Material, or Include changes when exactly 1 layer is selected."""
        if self._updating_inspector:
            return
        selected = self._get_selected_review_rows()
        if len(selected) != 1:
            return
        row = selected[0]
        cat_name = _get_combo_value(self.cb_insp_category, row.inferred_category)
        strat_key = friendly_to_strategy(_get_combo_value(self.cb_insp_strategy, row.proposed_strategy))
        mat_name = _get_combo_value(self.cb_insp_material, row.selected_material)
        is_checked = bool(self.chk_insp_include.IsChecked)

        if (cat_name == row.inferred_category and strat_key == row.proposed_strategy
                and mat_name == row.selected_material and is_checked == bool(row.checked)):
            return

        self._apply_row_changes(row, cat_name, strat_key, mat_name, is_checked)
        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        self.lbl_global_status.Text = "Updated '{}' -> {} ({}), Mat: {}".format(
            row.layer_name, row.inferred_category, strategy_to_short(row.proposed_strategy), row.selected_material
        )

    def _on_insp_strategy_changed(self, s, e):
        if self._updating_inspector:
            return
        selected = self._get_selected_review_rows()
        if len(selected) == 1:
            self._on_insp_single_changed(s, e)
        elif len(selected) > 1:
            all_objs = []
            for r in selected:
                all_objs.extend(r.objects)
            self._update_inspector_rule_banner(all_objs, _get_combo_value(self.cb_insp_strategy, "DirectShape"))

    def _on_insp_use_recommended(self, s, e):
        rec = self._last_recommended or "DirectShape"
        _set_combo_value(self.cb_insp_strategy, strategy_to_friendly(rec))
        selected = self._get_selected_review_rows()
        if len(selected) > 1:
            self._on_insp_bulk_apply(None, None)

    def _on_insp_bulk_apply(self, s, e):
        """Applies Inspector settings across all highlighted rows in Bulk Edit mode."""
        selected = self._get_selected_review_rows()
        if not selected:
            return
        cat_name = _get_combo_value(self.cb_insp_category, "Generic Models")
        strat_key = friendly_to_strategy(_get_combo_value(self.cb_insp_strategy, "DirectShape"))
        mat_name = _get_combo_value(self.cb_insp_material, "<Auto from Rhino>")
        is_checked = bool(self.chk_insp_include.IsChecked)

        for r in selected:
            self._apply_row_changes(r, cat_name, strat_key, mat_name, is_checked)

        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        self.lbl_global_status.Text = "Bulk updated {} layer(s) -> {} ({}), Mat: {}.".format(
            len(selected), cat_name, strategy_to_short(strat_key), mat_name
        )

    def _on_insp_edit_params(self, s, e):
        selected = self._get_selected_review_rows()
        if len(selected) != 1:
            return
        row = selected[0]
        dlg = CustomParametersDialog(
            custom_parameters=getattr(row, "custom_parameters", []),
            strategy=row.proposed_strategy,
            scope_label=row.layer_name,
            parent=self.window
        )
        if dlg.ShowModal(self.window):
            row.custom_parameters = list(dlg.result_parameters)
            self._refresh_review_grid_datastore()
            self.lbl_global_status.Text = "Saved {} custom parameter(s) on '{}'.".format(
                len(row.custom_parameters), row.layer_name
            )

    def _on_insp_split_clicked(self, s, e):
        """Splits only the currently selected mixed layer(s) into homogeneous sub-rows."""
        selected_ids = set(id(r) for r in self._get_selected_review_rows())
        if not selected_ids:
            self._on_split_mixed_layers(s, e)
            return

        new_rows = []
        split_cnt = 0
        for r in self.layer_rows:
            if id(r) in selected_ids and r.rule_eval and r.rule_eval.is_mixed_geometry:
                subs = self._build_rows_for_layer(
                    r.source_layer_path or r.layer_name,
                    r.objects,
                    auto_split=True,
                    inherit_material=getattr(r, "selected_material", "<Auto from Rhino>"),
                    inherit_params=getattr(r, "custom_parameters", [])
                )
                if len(subs) > 1:
                    split_cnt += 1
                new_rows.extend(subs)
            else:
                new_rows.append(r)

        if split_cnt > 0:
            self.layer_rows = new_rows
            self._apply_review_filters(None, None)
            self._update_preview_kpis()
            self.lbl_global_status.Text = "Split {} selected mixed layer(s) into homogeneous sub-rows.".format(split_cnt)
        else:
            self.lbl_global_status.Text = "Selected layer already contains homogeneous geometry."

    def _on_split_mixed_layers(self, sender, e):
        """Splits all mixed-geometry LayerRowItem rows into clean homogeneous virtual sub-rows."""
        new_rows = []
        split_count = 0
        for r in self.layer_rows:
            if r.rule_eval and r.rule_eval.is_mixed_geometry:
                sub_rows = self._build_rows_for_layer(
                    r.source_layer_path or r.layer_name,
                    r.objects,
                    auto_split=True,
                    inherit_material=getattr(r, "selected_material", "<Auto from Rhino>"),
                    inherit_params=getattr(r, "custom_parameters", [])
                )
                if len(sub_rows) > 1:
                    split_count += 1
                new_rows.extend(sub_rows)
            else:
                new_rows.append(r)

        if split_count > 0:
            self.layer_rows = new_rows
            self._apply_review_filters(None, None)
            self._update_preview_kpis()
            self.lbl_global_status.Text = "Split {} mixed-geometry layer(s) into {} homogeneous row(s).".format(
                split_count, len(self.layer_rows)
            )
        else:
            self.lbl_global_status.Text = "All visible layers already contain homogeneous geometry."

    def _on_grid_cell_double_click(self, sender, e):
        """Optional double-click shortcut opening LayerConfigDialog (ignores CheckBox clicks)."""
        try:
            orig = getattr(e, "OriginalSource", None)
            if orig is not None and swcp is not None and isinstance(orig, swcp.ToggleButton):
                return
            sel = self._get_selected_review_rows()
            if len(sel) == 1:
                row = sel[0]
                dlg = LayerConfigDialog(row, parent=self.window, revit_materials=self.revit_materials)
                if dlg.ShowModal(self.window):
                    self._refresh_review_grid_datastore()
                    self._update_preview_kpis()
                    self.lbl_global_status.Text = "Updated '{}' -> {}, {}, Mat: {}".format(
                        row.layer_name, row.inferred_category, strategy_to_short(row.proposed_strategy), row.selected_material
                    )
        except Exception as ex:
            self.lbl_global_status.Text = "Layer config notice: {}".format(ex)

    def _on_edit_project_params(self, sender, e):
        """Opens CustomParametersDialog for project-wide deterministic data parameters."""
        dlg = CustomParametersDialog(
            custom_parameters=self.project_custom_parameters,
            strategy="DirectShape",
            scope_label="All Layers (Project-Wide)",
            parent=self.window
        )
        if dlg.ShowModal(self.window):
            self.project_custom_parameters = list(dlg.result_parameters)
            self._update_header_settings_chip()
            self._refresh_inspector()
            self.lbl_global_status.Text = "Updated {} project-wide deterministic parameter(s).".format(
                len(self.project_custom_parameters)
            )

    def _on_open_settings(self, sender, e):
        """Opens the unified Transfer Settings & AI Config modal."""
        state = {
            "global_mode": self.global_mode,
            "unit_scale_idx": self.unit_scale_idx,
            "mixed_policy": self.mixed_policy,
            "rep_threshold_idx": self.rep_threshold_idx,
            "move_to_origin": self.move_to_origin,
            "generate_bua": self.generate_bua
        }
        dlg = SettingsDialog(self.ai_interpreter, dashboard_state=state, parent=self.window)
        if dlg.ShowModal(self.window):
            old_mode = self.global_mode
            old_rep = self.rep_threshold_idx

            self.global_mode = dlg.state["global_mode"]
            self.unit_scale_idx = dlg.state["unit_scale_idx"]
            self.mixed_policy = dlg.state["mixed_policy"]
            self.rep_threshold_idx = dlg.state["rep_threshold_idx"]
            self.move_to_origin = dlg.state["move_to_origin"]
            self.generate_bua = dlg.state["generate_bua"]

            thresh_map = [2, 3, 5, 10]
            self.classifier.repetition_threshold = thresh_map[self.rep_threshold_idx] if 0 <= self.rep_threshold_idx < len(thresh_map) else 3

            self._update_header_settings_chip()
            if self.scanned_objects and (old_mode != self.global_mode or old_rep != self.rep_threshold_idx):
                self._run_classification_and_sync()
                self._populate_review_grid()
                self._update_preview_kpis()
            self.lbl_global_status.Text = "Settings saved ({}).".format(self.lbl_header_settings.Text)

    def _on_run_ai_assist(self, sender, e):
        """Uses AI semantic interpreter to resolve ambiguous architectural layers."""
        prov = getattr(self.ai_interpreter, "provider", "OpenAI")
        model = getattr(self.ai_interpreter, "model", "gpt-4o-mini")
        has_key = bool(getattr(self.ai_interpreter, "api_key", ""))
        mode_str = "{} ({})".format(prov, model) if has_key else "Offline Heuristic Lexicon"
        self.lbl_global_status.Text = "Running AI semantic interpretation [{}]...".format(mode_str)
        _pump_wpf_events()

        boosted = 0
        llm_count = 0
        for r in self.layer_rows:
            interp = self.ai_interpreter.interpret(r.layer_name)
            if interp and interp.confidence_boost > 0:
                r.update_category(interp.inferred_category)
                r.revit_type = interp.suggested_revit_type
                r.confidence_score = min(100.0, r.confidence_score + (interp.confidence_boost * 20.0))
                r.checked = True
                boosted += 1
                if getattr(interp, "source", "") == "LLM":
                    llm_count += 1

        self._refresh_review_grid_datastore()
        self._update_preview_kpis()
        if llm_count > 0:
            self.lbl_global_status.Text = "AI Assist refined {} layer(s) ({} live LLM, {} lexicon).".format(
                boosted, llm_count, boosted - llm_count
            )
        else:
            self.lbl_global_status.Text = "AI Assist refined {} layer(s) (Offline Heuristic Lexicon).".format(boosted)

    def _on_load_profile(self, sender, e):
        """Loads transfer profile from disk."""
        try:
            if self.profile_mgr.load():
                self.project_custom_parameters = list(getattr(self.profile_mgr, "project_custom_parameters", []) or [])
                self._update_header_settings_chip()
                self._run_classification_and_sync()
                self._populate_review_grid()
                self._update_preview_kpis()
                self.lbl_global_status.Text = "Transfer Profile loaded successfully."
            else:
                self.lbl_global_status.Text = "No saved Transfer_Profile.json found on disk."
        except Exception as ex:
            self.lbl_global_status.Text = "Profile load notice: {}".format(ex)

    def _on_save_profile(self, sender, e):
        """Saves current layer table configurations as persistent profile rules."""
        try:
            saved_count = 0
            for r in self.layer_rows:
                if r.checked:
                    rule = MappingRule(
                        layer_pattern=r.layer_name,
                        revit_category=r.revit_type if (r.revit_type and r.revit_type.startswith("OST_")) else (r.classification.revit_category_name if r.classification else "OST_GenericModel"),
                        revit_type=r.revit_type,
                        strategy=r.proposed_strategy,
                        material=getattr(r, "selected_material", "<Auto from Rhino>") or "<Auto from Rhino>",
                        room_bounding=True,
                        custom_parameters=getattr(r, "custom_parameters", []),
                        mixed_policy=self.mixed_policy
                    )
                    self.profile_mgr.add_rule(rule)
                    saved_count += 1

            self.profile_mgr.project_custom_parameters = list(self.project_custom_parameters or [])
            self.profile_mgr.save()
            self.lbl_global_status.Text = "Saved {} layer mapping rule(s) to Transfer_Profile.json.".format(saved_count)
        except Exception as ex:
            self.lbl_global_status.Text = "Profile save notice: {}".format(ex)

    # =========================================================================
    # KPIs & UNIT SCALE
    # =========================================================================
    def _update_preview_kpis(self):
        """Updates footer summary chip & Step 3 readiness banner."""
        cnt_create = 0
        cnt_update = 0
        cnt_skip = 0
        cnt_mixed = 0
        cnt_blocked = 0
        checked_layers = 0
        checked_objs = 0

        for r in self.layer_rows:
            if not r.checked:
                continue
            checked_layers += 1
            checked_objs += r.object_count
            if r.sync_state == "CREATE":
                cnt_create += r.object_count
            elif r.sync_state == "UPDATE_MODIFIED":
                cnt_update += r.object_count
            elif r.sync_state == "SKIP_UNCHANGED":
                cnt_skip += r.object_count

            ev = getattr(r, "rule_eval", None)
            if ev:
                if ev.status == "PARTIAL_MIXED":
                    cnt_mixed += 1
                elif ev.status == "INCOMPATIBLE":
                    cnt_blocked += 1

        if not WPF_AVAILABLE:
            return
        if getattr(self, "lbl_footer_summary", None) is not None:
            self.lbl_footer_summary.Text = "{} layer(s) · {} obj(s) checked".format(checked_layers, checked_objs)
        if getattr(self, "lbl_run_summary", None) is not None:
            self.lbl_run_summary.Text = (
                "Ready: {} Create  |  {} Update  |  {} Unchanged Skip  |  {} Mixed Layer(s)  |  {} Blocked"
            ).format(cnt_create, cnt_update, cnt_skip, cnt_mixed, cnt_blocked)

    def _get_active_unit_scale(self):
        """Returns chosen unit scale factor for converting Rhino units to Revit feet."""
        idx = int(getattr(self, "unit_scale_idx", 0))
        if idx == 1:
            return 1.0 / 304.8
        elif idx == 2:
            return 3.280839895013123
        elif idx == 3:
            return 1.0 / 30.48
        elif idx == 4:
            return 1.0 / 12.0
        elif idx == 5:
            return 1.0
        return None

    # =========================================================================
    # STEP 3: EXECUTE TRANSFER & QA VALIDATION
    # =========================================================================
    def _on_execute_transfer(self, sender, e):
        """Executes full transfer with Revit TransactionGroup and Failure handling."""
        if not REVIT_AVAILABLE or not self.revit_doc:
            self.lbl_global_status.Text = "Transfer aborted: Revit document is not available. Run inside Rhino.Inside.Revit."
            if WPF_AVAILABLE and getattr(self, "lbl_exec_status", None) is not None:
                self.lbl_exec_status.Text = "Transfer aborted: Revit document is not available."
            return

        self.btn_execute.IsEnabled = False
        self.lbl_exec_status.Text = "Starting transfer transaction..."
        chosen_scale = self._get_active_unit_scale()
        scale_label = _UNIT_SCALE_SHORT[self.unit_scale_idx] if 0 <= self.unit_scale_idx < len(_UNIT_SCALE_SHORT) else "Auto-Detect"
        self.txt_exec_log.Text = "Beginning R2R Transfer TransactionGroup (Units: {}, Policy: {})...\n".format(
            scale_label, self.mixed_policy
        )
        _pump_wpf_events()

        router = ConversionRouter(
            self.revit_doc,
            global_mode=self.global_mode,
            generate_bua=self.generate_bua,
            unit_scale=chosen_scale,
            move_to_origin=self.move_to_origin,
            mixed_policy=self.mixed_policy,
            project_custom_parameters=self.project_custom_parameters
        )

        approved_objs = []
        for r in self.layer_rows:
            if r.checked:
                approved_objs.extend(r.objects)

        shift = router.calculate_and_set_origin_shift(approved_objs, force_center=self.move_to_origin)
        if router.has_origin_shift:
            self.txt_exec_log.Text += "Origin Alignment Active: Model moved to Revit (0,0,0).\n"
            self.txt_exec_log.Text += "  Rhino Shift: dX={:.2f}, dY={:.2f}, dZ={:.2f}\n".format(shift[0], shift[1], shift[2])
            self.txt_exec_log.Text += "  Revit Offset: dX={:.2f}ft, dY={:.2f}ft, dZ={:.2f}ft\n\n".format(
                router.origin_shift_feet[0], router.origin_shift_feet[1], router.origin_shift_feet[2]
            )

        guid_to_row = {}
        layer_to_row = {}
        for r in self.layer_rows:
            layer_to_row[r.layer_name] = r
            for o in getattr(r, "objects", []):
                if getattr(o, "rhino_guid", None):
                    guid_to_row[o.rhino_guid] = r
                layer_to_row[getattr(o, "layer_name", "")] = r
                layer_to_row[getattr(o, "layer_full_path", "")] = r

        all_custom_params = list(self.project_custom_parameters or [])
        seen_cp = set(cp.name.lower() for cp in all_custom_params if getattr(cp, "name", ""))
        for r in self.layer_rows:
            if r.checked:
                for cp in (getattr(r, "custom_parameters", []) or []):
                    cname = getattr(cp, "name", "")
                    if cname and cname.lower() not in seen_cp:
                        seen_cp.add(cname.lower())
                        all_custom_params.append(cp)

        try:
            router.prepare_shared_parameter_file(custom_parameters=all_custom_params)
        except Exception as ex_sp_prep:
            self.txt_exec_log.Text += "Shared Parameter pre-prep notice: {}\n".format(ex_sp_prep)

        total = len(approved_objs)
        if total == 0:
            self.txt_exec_log.Text += "No objects approved for transfer. Check layers in Step 2 first.\n"
            self.btn_execute.IsEnabled = True
            return

        created_cnt = 0
        updated_cnt = 0
        skipped_cnt = 0
        failed_cnt = 0

        self.validation_rows = []
        self.qa_report = TransferQAReport()
        self.qa_report.execution_mode = self.global_mode

        def _append_log(msg):
            self.txt_exec_log.Text += str(msg) + "\n"

        # Phase 1: Pre-Transaction Native .rfa Family Generation
        try:
            _append_log("Phase 1: Pre-generating and loading .rfa families...")
            profile_rules_map = {}
            for r in self.layer_rows:
                if r.checked:
                    cat_name = r.revit_type if (r.revit_type and r.revit_type.startswith("OST_")) else (r.classification.revit_category_name if r.classification else "OST_GenericModel")
                    rule_item = MappingRule(
                        layer_pattern=r.layer_name,
                        revit_category=cat_name,
                        revit_type=r.revit_type,
                        strategy=r.proposed_strategy,
                        material=getattr(r, "selected_material", "<Auto from Rhino>") or "<Auto from Rhino>",
                        room_bounding=True,
                        custom_parameters=getattr(r, "custom_parameters", []),
                        mixed_policy=self.mixed_policy
                    )
                    profile_rules_map[r.layer_name] = rule_item
                    for o in getattr(r, "objects", []):
                        if getattr(o, "rhino_guid", None):
                            profile_rules_map[o.rhino_guid] = rule_item
                        profile_rules_map[getattr(o, "layer_name", "")] = rule_item
                        profile_rules_map[getattr(o, "layer_full_path", "")] = rule_item

            pregen_symbols = router.pre_generate_families(
                approved_objs,
                self.classifications,
                profile_rules=profile_rules_map,
                log_fn=_append_log
            )
            pregen_cnt = len(pregen_symbols) if pregen_symbols else 0
            fam_req_objs = [
                o for o in approved_objs
                if (
                    (profile_rules_map.get(o.rhino_guid) or profile_rules_map.get(o.layer_name))
                    and (profile_rules_map.get(o.rhino_guid) or profile_rules_map.get(o.layer_name)).strategy in ("ParametricFamily", "FreeFormFamily", "FamilyInstance", "RepetitiveFamily")
                )
                or (self.classifications.get(o.rhino_guid) and self.classifications.get(o.rhino_guid).proposed_strategy in ("ParametricFamily", "FreeFormFamily", "FamilyInstance", "RepetitiveFamily"))
            ]
            if pregen_cnt > 0:
                _append_log("Phase 1 Complete: {} native .rfa family definition(s) loaded.".format(pregen_cnt))
            elif len(fam_req_objs) > 0:
                _append_log("Phase 1 Notice: {} object(s) could not form closed solids in .rfa; using fallback in Phase 2.".format(len(fam_req_objs)))
            else:
                _append_log("Phase 1 Complete: No family pre-generation required.")
        except Exception as ex:
            _append_log("Phase 1 Warning: {}. Continuing with fallback pipelines.".format(ex))

        tg = DB.TransactionGroup(self.revit_doc, "R2R One-Click Transfer")
        tg.Start()

        # Phase 2A: Bind Shared Parameters
        try:
            with DB.Transaction(self.revit_doc, "Bind R2R Shared Parameters") as trans_sp:
                trans_sp.Start()
                sp_bound = router.ensure_shared_parameters_bound(
                    custom_parameters=all_custom_params,
                    log_fn=_append_log
                )
                trans_sp.Commit()
                if sp_bound:
                    _append_log("Phase 2A: R2R Schedulable Shared Parameters bound to model categories.")
                else:
                    _append_log("Phase 2A Notice: Some categories use Built-In parameters.")
        except Exception as ex_sp:
            _append_log("Phase 2A Notice: {}. Continuing...".format(ex_sp))

        try:
            for idx, obj in enumerate(approved_objs):
                guid = obj.rhino_guid
                c_res = self.classifications.get(guid)
                s_ev = self.sync_evaluations.get(guid)

                if s_ev and s_ev.sync_state == "SKIP_UNCHANGED":
                    skipped_cnt += 1
                    _append_log("[SKIP] Object {} (Hash match: {})".format(guid[:8], obj.geo_hash[:8]))
                    continue

                with DB.Transaction(self.revit_doc, "Transfer R2R Object") as trans:
                    if router.failures_preprocessor:
                        opts = trans.GetFailureHandlingOptions()
                        opts.SetFailuresPreprocessor(router.failures_preprocessor)
                        trans.SetFailureHandlingOptions(opts)

                    trans.Start()

                    if s_ev and s_ev.sync_state == "UPDATE_MODIFIED" and s_ev.revit_element_id > 0:
                        try:
                            old_eid = self._make_elem_id(s_ev.revit_element_id)
                            if self.revit_doc.GetElement(old_eid) is not None:
                                self.revit_doc.Delete(old_eid)
                        except Exception:
                            pass

                    row = guid_to_row.get(guid) or layer_to_row.get(obj.layer_full_path) or layer_to_row.get(getattr(obj, "layer_name", ""))
                    layer_rule = None
                    if row:
                        cat_name = row.revit_type if (row.revit_type and row.revit_type.startswith("OST_")) else (c_res.revit_category_name if c_res else "OST_GenericModel")
                        layer_rule = MappingRule(
                            layer_pattern=row.layer_name,
                            revit_category=cat_name,
                            revit_type=row.revit_type,
                            strategy=row.proposed_strategy,
                            material=getattr(row, "selected_material", "<Auto from Rhino>") or "<Auto from Rhino>",
                            room_bounding=True,
                            custom_parameters=getattr(row, "custom_parameters", []),
                            mixed_policy=self.mixed_policy
                        )

                    conv_res = router.convert_object(obj, c_res, profile_rule=layer_rule)

                    if conv_res.status == "SUCCESS" and conv_res.revit_element_id != -1:
                        created_elem = self.revit_doc.GetElement(self._make_elem_id(conv_res.revit_element_id))
                        self.sync_engine.write_sync_record(created_elem, obj, conv_res.strategy_used)
                        trans.Commit()

                        if s_ev and s_ev.sync_state == "UPDATE_MODIFIED":
                            updated_cnt += 1
                        else:
                            created_cnt += 1

                        log_msg = "[SUCCESS] {} -> Revit ID {} ({})".format(
                            obj.layer_name, conv_res.revit_element_id, conv_res.strategy_used
                        )
                        if conv_res.area_sqm > 0 or conv_res.volume_cum > 0 or conv_res.material_volume_cum > 0:
                            log_msg += " | Area: {:.2f} m², Vol: {:.2f} m³, Mat: {} ({:.2f} m³)".format(
                                conv_res.area_sqm, conv_res.volume_cum, conv_res.primary_material, conv_res.material_volume_cum
                            )
                        _append_log(log_msg)
                        if conv_res.warnings:
                            for w in conv_res.warnings:
                                _append_log("  [NOTICE] {}".format(w))

                        val_rep = self.validator.validate_element(conv_res.revit_element_id, guid)
                        val_row = ValidationRowItem(
                            val_rep, obj.layer_name, conv_res.strategy_used,
                            area_sqm=conv_res.area_sqm,
                            volume_cum=conv_res.volume_cum,
                            material_name=conv_res.primary_material,
                            material_vol=conv_res.material_volume_cum
                        )
                        self.validation_rows.append(val_row)

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
                            "rhino_area": conv_res.rhino_area_sqm or obj.area,
                            "rhino_volume": conv_res.rhino_volume_cum or getattr(obj, "volume", 0.0),
                            "area_sqm": conv_res.area_sqm,
                            "volume_cum": conv_res.volume_cum,
                            "material_name": conv_res.primary_material,
                            "material_volume_cum": conv_res.material_volume_cum,
                            "material_takeoff": conv_res.material_takeoff,
                            "bua_area": conv_res.bua_area,
                            "issues": val_rep.issues
                        })

                    elif conv_res.status == "SKIPPED":
                        trans.RollBack()
                        skipped_cnt += 1
                        _append_log("[SKIPPED BY RULE] {} ({}) -> {}".format(
                            obj.layer_name, conv_res.strategy_used, conv_res.error_message
                        ))
                    else:
                        trans.RollBack()
                        failed_cnt += 1
                        _append_log("[ERROR] {} -> Failed: {}".format(obj.layer_name, conv_res.error_message))

                pct = int(((idx + 1) / float(total)) * 100)
                self.progress_bar.Value = pct
                self.lbl_exec_status.Text = "Processing {}/{} ({}%)...".format(idx + 1, total, pct)
                _pump_wpf_events()

            # Phase 2B: Generate / Update Takeoff Schedule
            try:
                with DB.Transaction(self.revit_doc, "Create R2R Takeoff Schedule") as trans_sc:
                    trans_sc.Start()
                    sched_view = router.create_r2r_takeoff_schedule()
                    trans_sc.Commit()
                    if sched_view:
                        _append_log("Phase 2B: Updated Revit Schedule '{}'.".format(getattr(sched_view, "Name", "Takeoff Schedule")))
            except Exception as ex_sc:
                _append_log("Phase 2B Notice: {}. Continuing...".format(ex_sc))

            tg.Assimilate()
            self.lbl_exec_status.Text = "Transfer complete! Created: {}, Updated: {}, Skipped: {}, Errors: {}".format(
                created_cnt, updated_cnt, skipped_cnt, failed_cnt
            )
            self.lbl_global_status.Text = self.lbl_exec_status.Text

        except Exception as ex:
            tg.RollBack()
            self.lbl_exec_status.Text = "Transfer failed with error: {}".format(ex)
            _append_log("\nFATAL EXCEPTION: {}\n{}".format(ex, traceback.format_exc()))
        finally:
            self.btn_execute.IsEnabled = True

        self._populate_validation_grid()
        self.qa_report.transfer_stats["created"] = created_cnt
        self.qa_report.transfer_stats["updated"] = updated_cnt
        self.qa_report.transfer_stats["skipped"] = skipped_cnt
        self.qa_report.transfer_stats["failed"] = failed_cnt
        self.qa_report.transfer_stats["missing_in_rhino"] = len(self.missing_in_rhino)

    def _populate_validation_grid(self):
        """Populates the Dual-Status Validation & QA Audit DataGrid."""
        if not WPF_AVAILABLE or getattr(self, "grid_validate", None) is None:
            return
        rows = []
        for r in self.validation_rows:
            st = str(r.overall_status or "")
            if st in ("COMPLETE", "PASS", "OK"):
                bg, fg = "#DCFCE7", "#166534"
            elif st in ("FAILED", "ERROR"):
                bg, fg = "#FEE2E2", "#991B1B"
            else:
                bg, fg = "#FEF3C7", "#92400E"

            rows.append(_make_clr_dict({
                "RevitId": r.revit_id,
                "Layer": r.layer_name,
                "CategoryMode": "{} · {}".format(r.category, strategy_to_short(r.strategy)),
                "Area": r.area,
                "Volume": r.volume,
                "Material": r.material,
                "Status": st,
                "StatusBg": bg,
                "StatusFg": fg,
                "ViewsIssues": "Plan:{} Sec:{} | {}".format(r.plan, r.section, r.issues)
            }))
        self.grid_validate.ItemsSource = _make_clr_list(rows)

    def _on_isolate_element(self, sender, e):
        """Isolates highlighted row's element in active Revit view."""
        sel_idx = int(self.grid_validate.SelectedIndex) if WPF_AVAILABLE else -1
        if sel_idx < 0 or sel_idx >= len(self.validation_rows):
            self.lbl_global_status.Text = "Select an element row in the Validation table first."
            return

        row = self.validation_rows[sel_idx]
        elem_id = int(row.revit_id)
        if elem_id > 0 and self.revit_uidoc:
            GraphicsValidator.isolate_element_in_view(self.revit_uidoc, elem_id)
            self.lbl_global_status.Text = "Element ID {} isolated in Revit view.".format(elem_id)

    @staticmethod
    def _create_save_file_dialog(title, filter_str, default_name):
        """Lazily instantiates Microsoft.Win32.SaveFileDialog (with WinForms fallback)."""
        try:
            import Microsoft.Win32 as mw32
            dlg = mw32.SaveFileDialog()
            dlg.Title = title
            dlg.Filter = filter_str
            dlg.FileName = default_name
            return (dlg, True)
        except Exception:
            clr.AddReference("System.Windows.Forms")
            import System.Windows.Forms as swf
            dlg = swf.SaveFileDialog()
            dlg.Title = title
            dlg.Filter = filter_str
            dlg.FileName = default_name
            return (dlg, False)

    def _on_export_html(self, sender, e):
        """Exports QA report as interactive HTML."""
        if not WPF_AVAILABLE:
            return
        dlg, is_wpf = self._create_save_file_dialog(
            "Save QA Report (HTML)",
            "HTML File (*.html)|*.html|All Files (*.*)|*.*",
            "R2R_QA_Report.html"
        )
        ok = (dlg.ShowDialog(self.window) == True) if is_wpf else (str(dlg.ShowDialog()) == "OK")
        if ok:
            dest = dlg.FileName
            ReportExporter.export_html(self.qa_report, dest)
            self.lbl_global_status.Text = "HTML QA Report saved: {}".format(dest)

    def _on_export_csv(self, sender, e):
        """Exports QA report as CSV."""
        if not WPF_AVAILABLE:
            return
        dlg, is_wpf = self._create_save_file_dialog(
            "Save QA Report (CSV)",
            "CSV File (*.csv)|*.csv|All Files (*.*)|*.*",
            "R2R_QA_Report.csv"
        )
        ok = (dlg.ShowDialog(self.window) == True) if is_wpf else (str(dlg.ShowDialog()) == "OK")
        if ok:
            dest = dlg.FileName
            ReportExporter.export_csv(self.qa_report, dest)
            self.lbl_global_status.Text = "CSV QA Report saved: {}".format(dest)
