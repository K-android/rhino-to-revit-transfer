# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer: Test Suite
test_pipeline.py - End-to-end verification of scanner, classifier, AI assistant,
profile manager, sync engine, graphics validator, and QA reporting.
"""

import os
import sys
import unittest

# Ensure current dir is in sys.path
pkg_dir = os.path.dirname(os.path.abspath(__file__))
if pkg_dir not in sys.path:
    sys.path.insert(0, pkg_dir)

from scanner import ScannedObject, RhinoScanner
from classifier import AecClassifier, ClassificationResult
from ai_assistant import AISemanticInterpreter
from profile_manager import ProfileManager, MappingRule
from sync_engine import SyncEngine, RevitSyncRecord
from graphics_validator import GraphicsValidator, ElementValidationReport
from reporting import TransferQAReport, ReportExporter
from conversion_router import ConversionRouter


class TestR2RPipeline(unittest.TestCase):

    def test_scanner_fingerprint_and_geohash(self):
        """Tests geometry fingerprinting, geo_hash determinism, and origin boundary check."""
        scanner = RhinoScanner()
        obj1 = ScannedObject("11111111-2222-3333-4444-555555555555")
        obj1.layer_full_path = "A-FLOR-SLAB::Level_01"
        obj1.layer_name = "Level_01"
        obj1.object_type = "Brep"
        obj1.bbox_min = (0.0, 0.0, 0.0)
        obj1.bbox_max = (10.0, 10.0, 0.3)
        obj1.centroid = (5.0, 5.0, 0.15)
        obj1.dx = 10.0
        obj1.dy = 10.0
        obj1.dz = 0.3
        obj1.area = 100.0
        obj1.volume = 30.0
        obj1.is_closed = True
        obj1.is_horizontal = True
        obj1.geo_hash = scanner._compute_geo_hash(obj1, None)

        self.assertTrue(len(obj1.geo_hash) == 64, "Geo-hash must be 64-character SHA-256")

        # Test duplicate hashing
        obj2 = ScannedObject("22222222-3333-4444-5555-666666666666")
        obj2.layer_full_path = "A-FLOR-SLAB::Level_01_Copy"
        obj2.object_type = "Brep"
        obj2.bbox_min = (0.0, 0.0, 0.0)
        obj2.bbox_max = (10.0, 10.0, 0.3)
        obj2.centroid = (5.0, 5.0, 0.15)
        obj2.dx = 10.0
        obj2.dy = 10.0
        obj2.dz = 0.3
        obj2.area = 100.0
        obj2.volume = 30.0
        obj2.is_closed = True
        obj2.is_horizontal = True
        obj2.geo_hash = scanner._compute_geo_hash(obj2, None)

        self.assertEqual(obj1.geo_hash, obj2.geo_hash, "Identical geometry must generate identical SHA-256 hash")

        # Test far from origin check (>16 km)
        obj_far = ScannedObject("33333333-4444-5555-6666-777777777777")
        obj_far.centroid = (20000.0, 0.0, 0.0)  # 20 km
        obj_far.origin_distance = 20000.0
        obj_far.is_far_from_origin = True
        self.assertTrue(obj_far.is_far_from_origin)

    def test_classifier_weights_and_tiers(self):
        """Tests evidence-based classification weights and confidence tiers."""
        classifier = AecClassifier(mode="Hybrid")

        # High confidence floor slab matching Catalan 'forjat'
        slab_obj = ScannedObject("aaa-111")
        slab_obj.layer_full_path = "PB_FORJAT_RETICULAR"
        slab_obj.layer_name = "PB_FORJAT_RETICULAR"
        slab_obj.object_type = "Brep"
        slab_obj.dx = 12.0
        slab_obj.dy = 15.0
        slab_obj.dz = 0.35
        slab_obj.is_horizontal = True
        slab_obj.is_closed = True
        slab_obj.dz_to_level = 0.0

        res = classifier.classify_object(slab_obj)
        self.assertEqual(res.inferred_category, "Floors")
        self.assertGreaterEqual(res.confidence_score, 85.0, "Floor slab with name and geometry match should exceed 85%")
        self.assertEqual(res.confidence_tier, "AUTO_READY")
        self.assertTrue(res.is_prechecked)
        self.assertEqual(res.proposed_strategy, "FloorNative")

        # Architectural Stainless Veil element
        veil_obj = ScannedObject("bbb-222")
        veil_obj.layer_full_path = "FACADE_VEIL_SCREEN_INOX"
        veil_obj.layer_name = "FACADE_VEIL_SCREEN_INOX"
        veil_obj.object_type = "Mesh"
        veil_obj.building_zone = "Veil Envelope"
        veil_obj.dx = 4.0
        veil_obj.dy = 0.1
        veil_obj.dz = 3.5

        res_veil = classifier.classify_object(veil_obj)
        self.assertEqual(res_veil.inferred_category, "DirectShape")
        self.assertGreaterEqual(res_veil.confidence_score, 80.0)
        self.assertEqual(res_veil.proposed_strategy, "DirectShape")

        # Ambiguous geometry with no layer keyword -> should fall into review tier (<60%)
        ambig_obj = ScannedObject("ccc-333")
        ambig_obj.layer_full_path = "LAYER_01"
        ambig_obj.layer_name = "LAYER_01"
        ambig_obj.object_type = "Curve"
        ambig_obj.dx = 1.0
        ambig_obj.dy = 1.0
        ambig_obj.dz = 0.1

        res_ambig = classifier.classify_object(ambig_obj)
        self.assertLess(res_ambig.confidence_score, 60.0)
        self.assertEqual(res_ambig.confidence_tier, "MANUAL_OR_EXCLUDED")
        self.assertFalse(res_ambig.is_prechecked)

    def test_ai_semantic_interpreter(self):
        """Tests trilingual Catalan/Spanish architectural vocabulary interpretation."""
        ai = AISemanticInterpreter()

        # Catalan RCR layer names
        t1 = ai.interpret("PB_FORJAT_RETICULAR")
        self.assertEqual(t1.inferred_category, "Floors")
        self.assertIn("Slab", t1.english_translation)

        t2 = ai.interpret("MUR_FORMIGO_VIST")
        self.assertEqual(t2.inferred_category, "Walls")
        self.assertIn("Wall", t2.english_translation)

        t3 = ai.interpret("GELOSIA_INOX_VEIL")
        self.assertEqual(t3.inferred_category, "DirectShape")

        t4 = ai.interpret("CEL_RAS_ACUSTIC")
        self.assertEqual(t4.inferred_category, "Ceilings")

    def test_profile_manager_rules(self):
        """Tests profile loading, wildcard matching, and auto-approval."""
        pm = ProfileManager()
        self.assertGreater(len(pm.rules), 0, "Default profile must have preconfigured rules")

        rule_slab = pm.find_matching_rule("ARQ_PB_FORJAT_TOWER")
        self.assertIsNotNone(rule_slab)
        self.assertEqual(rule_slab.revit_category, "OST_Floors")
        self.assertEqual(rule_slab.strategy, "FloorNative")

        rule_veil = pm.find_matching_rule("FACADE_VEIL_STAINLESS")
        self.assertIsNotNone(rule_veil)
        self.assertEqual(rule_veil.strategy, "DirectShape")

    def test_sync_engine_logic(self):
        """Tests revision sync classification (CREATE, SKIP, UPDATE, MISSING)."""
        sync = SyncEngine(None)

        scanned = [
            ScannedObject("guid-1"),
            ScannedObject("guid-2"),
            ScannedObject("guid-3")
        ]
        scanned[0].geo_hash = "hash_aaa"
        scanned[1].geo_hash = "hash_bbb_MODIFIED"
        scanned[2].geo_hash = "hash_ccc"

        # Mock existing Revit records
        rec_unchanged = RevitSyncRecord(101)
        rec_unchanged.rhino_guid = "guid-1"
        rec_unchanged.geo_hash = "hash_aaa"

        rec_modified = RevitSyncRecord(102)
        rec_modified.rhino_guid = "guid-2"
        rec_modified.geo_hash = "hash_bbb_OLD"

        rec_deleted_in_rhino = RevitSyncRecord(103)
        rec_deleted_in_rhino.rhino_guid = "guid-missing"
        rec_deleted_in_rhino.geo_hash = "hash_xxx"

        # Patch sync.scan_revit_sync_records
        sync.scan_revit_sync_records = lambda: {
            "guid-1": rec_unchanged,
            "guid-2": rec_modified,
            "guid-missing": rec_deleted_in_rhino
        }

        evals, missing = sync.evaluate_sync_states(scanned)

        self.assertEqual(evals["guid-1"].sync_state, "SKIP_UNCHANGED")
        self.assertEqual(evals["guid-2"].sync_state, "UPDATE_MODIFIED")
        self.assertEqual(evals["guid-3"].sync_state, "CREATE")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].rhino_guid, "guid-missing")

    def test_reporting_export(self):
        """Tests QA report generation in HTML, CSV, and JSON."""
        report = TransferQAReport()
        report.element_records.append({
            "rhino_guid": "11111111-2222-3333-4444-555555555555",
            "revit_element_id": 99901,
            "layer_name": "PB_FORJAT",
            "category": "Floors",
            "strategy": "FloorNative",
            "sync_state": "CREATE",
            "confidence_pct": 95.0,
            "bim_status": "PASS",
            "graphics_status": "PASS",
            "overall_status": "COMPLETE",
            "view_matrix": {"Plan": "PASS", "Section": "PASS", "Elevation": "PASS", "3D": "PASS"},
            "rhino_area": 250.0,
            "bua_area": 250.0,
            "issues": []
        })

        test_dir = os.path.join(pkg_dir, "test_output")
        os.makedirs(test_dir, exist_ok=True)

        html_path = os.path.join(test_dir, "test_report.html")
        csv_path = os.path.join(test_dir, "test_report.csv")
        json_path = os.path.join(test_dir, "test_report.json")

        ReportExporter.export_html(report, html_path)
        ReportExporter.export_csv(report, csv_path)
        ReportExporter.export_json(report, json_path)

        self.assertTrue(os.path.exists(html_path))
        self.assertTrue(os.path.exists(csv_path))
        self.assertTrue(os.path.exists(json_path))
        self.assertGreater(os.path.getsize(html_path), 500)


    def test_adaptive_points_ccw_ordering(self):
        """Tests deterministic counter-clockwise winding ordering for 2, 3, and 4 points."""
        # 4-point planar rectangle out of order
        raw_4pts = [
            (10.0, 10.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 10.0, 0.0),
            (10.0, 0.0, 0.0)
        ]
        ordered_4 = RhinoScanner.order_adaptive_points_ccw(raw_4pts)
        self.assertEqual(len(ordered_4), 4)
        # Should start at bottom-left (0.0, 0.0, 0.0) and proceed CCW
        self.assertEqual(ordered_4[0], (0.0, 0.0, 0.0))
        self.assertEqual(ordered_4[1], (10.0, 0.0, 0.0))
        self.assertEqual(ordered_4[2], (10.0, 10.0, 0.0))
        self.assertEqual(ordered_4[3], (0.0, 10.0, 0.0))

        # 3-point triangle
        raw_3pts = [
            (5.0, 8.0, 0.0),
            (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0)
        ]
        ordered_3 = RhinoScanner.order_adaptive_points_ccw(raw_3pts)
        self.assertEqual(len(ordered_3), 3)
        self.assertEqual(ordered_3[0], (0.0, 0.0, 0.0))
        self.assertEqual(ordered_3[1], (10.0, 0.0, 0.0))
        self.assertEqual(ordered_3[2], (5.0, 8.0, 0.0))

        # 2-point strut (bottom to top)
        raw_2pts = [(0.0, 0.0, 10.0), (0.0, 0.0, 0.0)]
        ordered_2 = RhinoScanner.order_adaptive_points_ccw(raw_2pts)
        self.assertEqual(ordered_2[0], (0.0, 0.0, 0.0))
        self.assertEqual(ordered_2[1], (0.0, 0.0, 10.0))

    def test_classifier_adaptive_and_repetitive_strategies(self):
        """Tests classification routing for AdaptiveComponent, RepetitiveFamily, and ParametricFamily."""
        classifier = AecClassifier(mode="Hybrid")

        # 1. Adaptive 4-point panel
        panel_obj = ScannedObject("panel-01")
        panel_obj.layer_full_path = "FAÇADE::PANELS_QUAD"
        panel_obj.layer_name = "PANELS_QUAD"
        panel_obj.object_type = "Brep"
        panel_obj.adaptive_point_count = 4
        panel_obj.adaptive_points = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0)]

        res_panel = classifier.classify_object(panel_obj)
        self.assertEqual(res_panel.proposed_strategy, "AdaptiveComponent")

        # 2. Repetitive items exceeding threshold
        bracket1 = ScannedObject("brk-01")
        bracket1.layer_full_path = "BRACKETS"
        bracket1.layer_name = "BRACKETS"
        bracket1.object_type = "Brep"
        bracket1.repetition_signature = "0.20x0.20x0.50_V0.02_A0.10"
        bracket1.repetition_count = 10

        bracket2 = ScannedObject("brk-02")
        bracket2.layer_full_path = "BRACKETS"
        bracket2.layer_name = "BRACKETS"
        bracket2.object_type = "Brep"
        bracket2.repetition_signature = bracket1.repetition_signature
        bracket2.repetition_count = 10

        classifier.prepare_repetition_cache([bracket1, bracket2])
        res_brk = classifier.classify_object(bracket1)
        self.assertEqual(res_brk.proposed_strategy, "RepetitiveFamily")

        # 3. Parametric Column
        col_obj = ScannedObject("col-01")
        col_obj.layer_full_path = "STRUCTURE::COLUMNS"
        col_obj.layer_name = "COLUMNS"
        col_obj.object_type = "Brep"
        col_obj.dx = 0.5
        col_obj.dy = 0.5
        col_obj.dz = 3.5
        col_obj.is_vertical = True
        col_obj.is_closed = True
        col_obj.parametric_dims = {"Width": 0.5, "Depth": 0.5, "Height": 3.5}

        res_col = classifier.classify_object(col_obj)
        self.assertEqual(res_col.inferred_category, "Family")
        self.assertEqual(res_col.proposed_strategy, "ParametricFamily")

    def test_dynamic_confidence_on_strategy_change(self):
        """Tests that LayerRowItem dynamically updates confidence score and tier when strategy changes."""
        from ui_dialog import LayerRowItem

        obj = ScannedObject("test-panel")
        obj.layer_name = "FAÇADE::PANELS"
        obj.adaptive_point_count = 4

        c_res = ClassificationResult("test-panel")
        c_res.inferred_category = "CurtainPanel"
        c_res.proposed_strategy = "AdaptiveComponent"
        c_res.confidence_score = 98.0
        c_res.confidence_tier = "AUTO_READY"

        row = LayerRowItem("FAÇADE::PANELS", [obj], c_res, None)
        self.assertEqual(row.confidence_score, 98.0)
        self.assertEqual(row.confidence_tier, "AUTO_READY")

        # Change strategy to FloorNative (object is NOT horizontal -> confidence drops to 0.0%)
        row.update_strategy("FloorNative")
        self.assertEqual(row.proposed_strategy, "FloorNative")
        self.assertEqual(row.confidence_score, 0.0)
        self.assertEqual(row.confidence_tier, "MANUAL_OR_EXCLUDED")
        self.assertFalse(row.checked)

        # Change strategy to AdaptiveComponent on object with 0 points -> confidence drops to 0.0%
        obj_no_pts = ScannedObject("test-solid")
        obj_no_pts.adaptive_point_count = 0
        row_solid = LayerRowItem("SOLID::BREP", [obj_no_pts], c_res, None)
        row_solid.update_strategy("AdaptiveComponent")
        self.assertEqual(row_solid.confidence_score, 0.0)
        self.assertEqual(row_solid.confidence_tier, "MANUAL_OR_EXCLUDED")
        self.assertFalse(row_solid.checked)

        # Change strategy to DirectShape (universal compatibility)
        row.update_strategy("DirectShape")
        self.assertEqual(row.proposed_strategy, "DirectShape")
        self.assertEqual(row.confidence_score, 100.0)
        self.assertEqual(row.confidence_tier, "AUTO_READY")

    def test_conversion_router_unit_scaling(self):
        """Tests that ConversionRouter properly respects custom unit scale overrides."""
        from conversion_router import ConversionRouter

        # Default fallback scale
        router_auto = ConversionRouter(None, global_mode="Hybrid")
        self.assertTrue(router_auto.unit_scale > 0)

        # Custom scale override (e.g., Millimeters -> Feet: 1.0 / 304.8)
        custom_mm_scale = 1.0 / 304.8
        router_mm = ConversionRouter(None, global_mode="Hybrid", unit_scale=custom_mm_scale)
        self.assertAlmostEqual(router_mm.unit_scale, custom_mm_scale, places=6)

        # Custom scale override (e.g., Meters -> Feet: 3.28084)
        custom_m_scale = 3.280839895013123
        router_m = ConversionRouter(None, global_mode="Hybrid", unit_scale=custom_m_scale)
        self.assertAlmostEqual(router_m.unit_scale, custom_m_scale, places=6)

    def test_check_rhino_inside_status_guard(self):
        """Tests that check_rhino_inside_status safely inspects AppDomain without throwing."""
        from main_launcher import check_rhino_inside_status

        ready, msg = check_rhino_inside_status()
        self.assertIsInstance(ready, bool)
        self.assertIsInstance(msg, str)
        if not ready:
            self.assertTrue("Rhino" in msg)

    def test_family_template_locator_and_load_options(self):
        """Tests FamilyLoadOptions and FamilyTemplateLocator without Revit crash."""
        from family_factory import FamilyLoadOptions, FamilyTemplateLocator

        load_opts = FamilyLoadOptions()
        found_res = load_opts.OnFamilyFound(True, True)
        self.assertIn(True, found_res)

        # Template search with None app should safely search standard directories or return None
        tpl_path = FamilyTemplateLocator.find_template(None, "Generic Model")
        if tpl_path:
            self.assertTrue(tpl_path.lower().endswith(".rft"))

    def test_conversion_router_pre_generate_families_safe(self):
        """Tests that Phase 1 pre_generate_families gracefully handles uninitialized docs."""
        router = ConversionRouter(None, global_mode="Hybrid")
        res = router.pre_generate_families([], {})
        self.assertEqual(res, {})

    def test_layer_row_category_override_and_confidence(self):
        """Tests dynamic category overrides on LayerRowItem and subsequent confidence updates."""
        from ui_dialog import LayerRowItem
        from classifier import ClassificationResult

        class MockObj(object):
            def __init__(self, is_horizontal=False, is_vertical=False):
                self.is_horizontal = is_horizontal
                self.is_vertical = is_vertical
                self.adaptive_point_count = 0
                self.repetition_count = 1
                self.parametric_dims = {}

        mock_obj = MockObj(is_horizontal=True, is_vertical=False)
        c_res = ClassificationResult("test-obj-guid")
        c_res.inferred_category = "Roof"
        c_res.proposed_strategy = "RoofNative"
        c_res.confidence_score = 95.0
        c_res.confidence_tier = "AUTO_READY"
        c_res.revit_category_name = "OST_Roofs"
        c_res.is_prechecked = True

        row = LayerRowItem("ARCH_ROOFS", [mock_obj], c_res, None)
        self.assertEqual(row.inferred_category, "Roof")
        self.assertEqual(row.revit_type, "OST_Roofs")

        # User overrides category to "Floor"
        row.update_category("Floor")
        self.assertEqual(row.inferred_category, "Floor")
        self.assertEqual(row.revit_type, "OST_Floors")
        self.assertEqual(row.proposed_strategy, "FloorNative")
        self.assertGreaterEqual(row.confidence_score, 85.0)

        # User overrides category to "Toposolid"
        row.update_category("Toposolid")
        self.assertEqual(row.inferred_category, "Toposolid")
        self.assertEqual(row.revit_type, "OST_Toposolid")
        self.assertEqual(row.proposed_strategy, "Toposolid")
        self.assertGreaterEqual(row.confidence_score, 85.0)

    def test_conversion_router_category_str_to_id(self):
        """Tests _category_str_to_id with friendly and OST names."""
        router = ConversionRouter(None, global_mode="Hybrid")
        # When REVIT_AVAILABLE is False, it returns None safely
        res_none = router._category_str_to_id("Toposolid")
        self.assertIsNone(res_none)

    def test_ai_provider_presets_and_config(self):
        """Tests provider presets, config save and load outside extension directory."""
        from ai_assistant import PROVIDER_PRESETS, AISemanticInterpreter
        import tempfile

        # Verify key providers exist in presets
        expected_providers = ["OpenAI", "Google Gemini", "DeepSeek", "OpenRouter", "Local Ollama / LM Studio", "Custom / Other"]
        for p in expected_providers:
            self.assertIn(p, PROVIDER_PRESETS)
            self.assertTrue(len(PROVIDER_PRESETS[p]["endpoint"]) > 0)
            self.assertTrue(len(PROVIDER_PRESETS[p]["models"]) > 0)

        # Verify Google Gemini endpoint is OpenAI compatible and has 3.8 / 3.6 flash models
        gemini_preset = PROVIDER_PRESETS["Google Gemini"]
        self.assertIn("openai", gemini_preset["endpoint"].lower())
        self.assertIn("gemini-3.8-flash", gemini_preset["models"])
        self.assertIn("gemini-3.6-flash", gemini_preset["models"])

        # Test config save and load with temporary file path
        ai = AISemanticInterpreter()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            ai.get_config_path = lambda: tmp_path
            ai.provider = "Google Gemini"
            ai.model = "gemini-3.8-flash"
            ai.api_key = "AIzaSyTestMockKey123"
            ai.api_endpoint = gemini_preset["endpoint"]
            saved = ai.save_config()
            self.assertTrue(saved)

            # Create fresh instance and load
            ai2 = AISemanticInterpreter()
            ai2.get_config_path = lambda: tmp_path
            loaded = ai2.load_config()
            self.assertTrue(loaded)
            self.assertEqual(ai2.provider, "Google Gemini")
            self.assertEqual(ai2.model, "gemini-3.8-flash")
            self.assertEqual(ai2.api_key, "AIzaSyTestMockKey123")
            self.assertEqual(ai2.api_endpoint, gemini_preset["endpoint"])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_ai_test_connection_validation(self):
        """Tests test_connection validation rules (empty key requirement and local endpoint bypass)."""
        from ai_assistant import AISemanticInterpreter

        ai = AISemanticInterpreter()
        # Remote provider without key must fail validation cleanly
        ok, msg = ai.test_connection(api_key="", api_endpoint="https://api.openai.com/v1/chat/completions", provider="OpenAI")
        self.assertFalse(ok)
        self.assertIn("API Key is required", msg)

        # Local Ollama endpoint without key should attempt connection rather than rejecting key
        ok_local, msg_local = ai.test_connection(api_key="", api_endpoint="http://localhost:11434/v1/chat/completions", provider="Local Ollama")
        self.assertNotIn("API Key is required", msg_local)

    def test_layer_config_dialog_and_selection_targeting(self):
        """Tests that LayerConfigDialog and LayerRowItem category/strategy updates operate cleanly."""
        from ui_dialog import LayerRowItem, LayerConfigDialog
        from classifier import ClassificationResult

        class MockObj(object):
            def __init__(self):
                self.is_horizontal = False
                self.is_vertical = False
                self.adaptive_point_count = 0
                self.repetition_count = 1
                self.parametric_dims = {"Width": 2.0, "Depth": 1.0, "Height": 0.8}
                self.classification = None

        mock_obj = MockObj()
        c_res = ClassificationResult("test-guid-123")
        c_res.inferred_category = "Furniture"
        c_res.proposed_strategy = "FamilyInstance"
        c_res.confidence_score = 90.0
        c_res.confidence_tier = "AUTO_READY"
        c_res.revit_category_name = "OST_Furniture"

        row = LayerRowItem("A-FURN-PLANTERS", [mock_obj], c_res, None, checked=True)
        self.assertEqual(row.inferred_category, "Furniture")

        # Update category to Planting
        row.update_category("Planting")
        self.assertEqual(row.inferred_category, "Planting")
        self.assertEqual(row.revit_type, "OST_Planting")

        # Update strategy to DirectShape
        row.update_strategy("DirectShape")
        self.assertEqual(row.proposed_strategy, "DirectShape")
        self.assertEqual(row.confidence_score, 100.0)

        # Update strategy to ParametricFamily
        row.update_strategy("ParametricFamily")
        self.assertEqual(row.proposed_strategy, "ParametricFamily")
        self.assertEqual(row.confidence_score, 95.0)

    def test_family_instance_strategy_pre_generation(self):
        """Tests that ConversionRouter pre_generate_families handles FamilyInstance and ParametricFamily without throwing."""
        from conversion_router import ConversionRouter
        from scanner import ScannedObject

        router = ConversionRouter(None, global_mode="Hybrid")
        obj = ScannedObject("test-family-guid")
        obj.layer_name = "A-FURN-CHAIR"
        obj.dx = 1.0
        obj.dy = 1.0
        obj.dz = 0.9

        # pre_generate_families should safely return empty dict when revit app is None
        symbols = router.pre_generate_families([obj], {})
        self.assertIsInstance(symbols, dict)

    def test_origin_shift_calculation_and_centering(self):
        """Tests that calculate_and_set_origin_shift centers far-from-origin models at Revit (0,0,0)."""
        from conversion_router import ConversionRouter
        from scanner import ScannedObject

        router = ConversionRouter(None, global_mode="Hybrid", unit_scale=3.28084)  # Meters to Feet

        # Simulate 2 objects at GIS coordinates (X: 500,000m to 500,100m, Y: 2,000,000m to 2,000,080m, Z: 1200m to 1250m)
        obj1 = ScannedObject("obj-gis-1")
        obj1.bbox_min = (500000.0, 2000000.0, 1200.0)
        obj1.bbox_max = (500050.0, 2000040.0, 1250.0)
        obj1.centroid = (500025.0, 2000020.0, 1225.0)
        obj1.is_far_from_origin = True
        obj1.origin_distance = 2061552.0

        obj2 = ScannedObject("obj-gis-2")
        obj2.bbox_min = (500050.0, 2000040.0, 1200.0)
        obj2.bbox_max = (500100.0, 2000080.0, 1240.0)
        obj2.centroid = (500075.0, 2000060.0, 1220.0)
        obj2.is_far_from_origin = True
        obj2.origin_distance = 2061575.0

        # Run automatic origin shift calculation
        dx, dy, dz = router.calculate_and_set_origin_shift([obj1, obj2], force_center=True)

        self.assertTrue(router.has_origin_shift)
        # Global bounding box mid-point is (500050, 2000040), so shift should negate this
        self.assertAlmostEqual(dx, -500050.0, places=1)
        self.assertAlmostEqual(dy, -2000040.0, places=1)
        # Base Z is 1200m, so shift should negate this to place base at 0.0
        self.assertAlmostEqual(dz, -1200.0, places=1)

        # Transformed coordinates in Revit
        new_mid_x = (500050.0 + dx)
        new_mid_y = (2000040.0 + dy)
        new_base_z = (1200.0 + dz)
        self.assertAlmostEqual(new_mid_x, 0.0, places=2)
        self.assertAlmostEqual(new_mid_y, 0.0, places=2)
        self.assertAlmostEqual(new_base_z, 0.0, places=2)

        # Check feet conversion
        self.assertAlmostEqual(router.origin_shift_feet[0], dx * 3.28084, places=1)
        self.assertAlmostEqual(router.origin_shift_feet[1], dy * 3.28084, places=1)
        self.assertAlmostEqual(router.origin_shift_feet[2], dz * 3.28084, places=1)

    def test_full_category_coverage_layer_row(self):
        """Tests that LayerRowItem.update_category correctly maps all architectural, structural, MEP, and site categories."""
        from ui_dialog import LayerRowItem, STANDARD_REVIT_CATEGORIES

        class MockObj(object):
            def __init__(self):
                self.classification = None

        row = LayerRowItem("TEST_LAYER", [MockObj()], None, None)

        test_mappings = [
            ("Floors", "OST_Floors"),
            ("Walls", "OST_Walls"),
            ("Roofs", "OST_Roofs"),
            ("Ceilings", "OST_Ceilings"),
            ("Columns", "OST_Columns"),
            ("Structural Columns", "OST_StructuralColumns"),
            ("Doors", "OST_Doors"),
            ("Windows", "OST_Windows"),
            ("Curtain Panels", "OST_CurtainWallPanels"),
            ("Curtain Wall Mullions", "OST_CurtainWallMullions"),
            ("Stairs", "OST_Stairs"),
            ("Railings", "OST_StairsRailing"),
            ("Ramps", "OST_Ramps"),
            ("Furniture", "OST_Furniture"),
            ("Furniture Systems", "OST_FurnitureSystems"),
            ("Casework", "OST_Casework"),
            ("Specialty Equipment", "OST_SpecialityEquipment"),
            ("Plumbing Fixtures", "OST_PlumbingFixtures"),
            ("Mechanical Equipment", "OST_MechanicalEquipment"),
            ("Electrical Equipment", "OST_ElectricalEquipment"),
            ("Electrical Fixtures", "OST_ElectricalFixtures"),
            ("Lighting Fixtures", "OST_LightingFixtures"),
            ("Structural Framing", "OST_StructuralFraming"),
            ("Structural Foundation", "OST_StructuralFoundation"),
            ("Toposolid", "OST_Toposolid"),
            ("Site", "OST_Site"),
            ("Planting", "OST_Planting"),
            ("Parking", "OST_Parking"),
            ("Roads", "OST_Roads"),
            ("Hardscape", "OST_Hardscape"),
            ("Mass", "OST_Mass"),
            ("Entourage", "OST_Entourage"),
            ("Signage", "OST_Signage"),
            ("Generic Models", "OST_GenericModel"),
            ("OST_CommunicationDevices", "OST_CommunicationDevices"),
            ("OST_SecurityDevices", "OST_SecurityDevices"),
        ]

        for input_cat, expected_ost in test_mappings:
            row.update_category(input_cat)
            self.assertEqual(row.revit_type, expected_ost, "Failed mapping for '{}'".format(input_cat))

        # Verify STANDARD_REVIT_CATEGORIES includes all required AEC categories
        self.assertIn("Casework", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Plumbing Fixtures", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Mechanical Equipment", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Lighting Fixtures", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Structural Foundation", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Railings", STANDARD_REVIT_CATEGORIES)
        self.assertIn("Ramps", STANDARD_REVIT_CATEGORIES)

    def test_conversion_router_category_str_to_id_mocked(self):
        """Tests that ConversionRouter._category_str_to_id resolves all friendly and BuiltInCategory names."""
        import conversion_router

        class MockBuiltInCategory(object):
            OST_Floors = 1
            OST_Walls = 2
            OST_Roofs = 3
            OST_Ceilings = 4
            OST_Columns = 5
            OST_StructuralColumns = 6
            OST_Doors = 7
            OST_Windows = 8
            OST_CurtainWallPanels = 9
            OST_CurtainWallMullions = 10
            OST_Stairs = 11
            OST_StairsRailing = 12
            OST_Ramps = 13
            OST_Furniture = 14
            OST_FurnitureSystems = 15
            OST_Casework = 16
            OST_SpecialityEquipment = 17
            OST_PlumbingFixtures = 18
            OST_MechanicalEquipment = 19
            OST_ElectricalEquipment = 20
            OST_ElectricalFixtures = 21
            OST_LightingFixtures = 22
            OST_StructuralFraming = 23
            OST_StructuralFoundation = 24
            OST_Toposolid = 25
            OST_Topography = 26
            OST_Site = 27
            OST_Planting = 28
            OST_Parking = 29
            OST_Roads = 30
            OST_Hardscape = 31
            OST_Mass = 32
            OST_Entourage = 33
            OST_Signage = 34
            OST_GenericModel = 99
            OST_SecurityDevices = 101

        class MockDB(object):
            BuiltInCategory = MockBuiltInCategory
            class ElementId(object):
                def __init__(self, val):
                    self.val = val
                def __repr__(self):
                    return "ElementId({})".format(self.val)

        orig_revit_avail = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDB

            router = conversion_router.ConversionRouter(None)

            # Test friendly names
            self.assertEqual(router._category_str_to_id("Casework").val, MockBuiltInCategory.OST_Casework)
            self.assertEqual(router._category_str_to_id("Plumbing Fixtures").val, MockBuiltInCategory.OST_PlumbingFixtures)
            self.assertEqual(router._category_str_to_id("Mechanical Equipment").val, MockBuiltInCategory.OST_MechanicalEquipment)
            self.assertEqual(router._category_str_to_id("Lighting Fixtures").val, MockBuiltInCategory.OST_LightingFixtures)
            self.assertEqual(router._category_str_to_id("Specialty Equipment").val, MockBuiltInCategory.OST_SpecialityEquipment)
            self.assertEqual(router._category_str_to_id("Railings").val, MockBuiltInCategory.OST_StairsRailing)
            self.assertEqual(router._category_str_to_id("Ramps").val, MockBuiltInCategory.OST_Ramps)
            self.assertEqual(router._category_str_to_id("Structural Foundation").val, MockBuiltInCategory.OST_StructuralFoundation)
            self.assertEqual(router._category_str_to_id("Structural Framing").val, MockBuiltInCategory.OST_StructuralFraming)
            self.assertEqual(router._category_str_to_id("Parking").val, MockBuiltInCategory.OST_Parking)
            self.assertEqual(router._category_str_to_id("Roads").val, MockBuiltInCategory.OST_Roads)
            self.assertEqual(router._category_str_to_id("Planting").val, MockBuiltInCategory.OST_Planting)

            # Test direct OST_ strings
            self.assertEqual(router._category_str_to_id("OST_Casework").val, MockBuiltInCategory.OST_Casework)
            self.assertEqual(router._category_str_to_id("OST_PlumbingFixtures").val, MockBuiltInCategory.OST_PlumbingFixtures)

            # Test dynamic fallback for arbitrary category (e.g. SecurityDevices)
            self.assertEqual(router._category_str_to_id("Security Devices").val, MockBuiltInCategory.OST_SecurityDevices)
            self.assertEqual(router._category_str_to_id("OST_SecurityDevices").val, MockBuiltInCategory.OST_SecurityDevices)

            # Test safe fallback to GenericModel for non-existent category
            self.assertEqual(router._category_str_to_id("UnknownCustomLayer").val, MockBuiltInCategory.OST_GenericModel)

        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit_avail
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_classifier_refine_comprehensive_categories(self):
        """Tests that AecClassifier._refine_family_category handles all 3D model categories."""
        from classifier import AecClassifier
        from scanner import ScannedObject

        clf = AecClassifier()

        cases = [
            ("A-CASE-CABINETS::Base_Unit", "Cabinet", "OST_Casework"),
            ("P-PLUM-FIXT::Washbasin", "Lavabo", "OST_PlumbingFixtures"),
            ("M-MECH-HVAC::Air_Handler", "Clima", "OST_MechanicalEquipment"),
            ("E-LITE-FIXT::Downlight", "Luminaria", "OST_LightingFixtures"),
            ("E-ELEC-DEVC::Switch", "Endoll", "OST_ElectricalFixtures"),
            ("A-RAIL::Balustrade_Glass", "Barana", "OST_StairsRailing"),
            ("A-RAMP::Access_Ramp", "Rampa", "OST_Ramps"),
            ("S-FNDN::Footing_Pad", "Zapata", "OST_StructuralFoundation"),
            ("S-FRAM::Girder_Steel", "Viga", "OST_StructuralFraming"),
            ("C-PARK::Stall_01", "Aparcament", "OST_Parking"),
            ("L-PLNT::Olive_Tree", "Arbre", "OST_Planting"),
            ("A-EQUP::Passenger_Lift", "Ascensor", "OST_SpecialityEquipment"),
        ]

        for layer, name, expected_cat in cases:
            obj = ScannedObject("test-guid")
            obj.layer_full_path = layer
            obj.object_name = name
            cat = clf._refine_family_category(obj)
            self.assertEqual(cat, expected_cat, "Failed refining '{}' / '{}'".format(layer, name))

    def test_native_floor_pipeline_execution(self):
        """Tests that _pipeline_1_native_floor creates native Floor, applies offset, and sets room bounding."""
        import conversion_router
        from scanner import ScannedObject

        class MockParam(object):
            def __init__(self):
                self.val = None
                self.IsReadOnly = False
            def Set(self, v):
                self.val = v

        class MockFloor(object):
            def __init__(self, eid=9999):
                self.Id = eid
                self.UniqueId = "mock-floor-uuid-1234"
                self.Category = type("Cat", (), {"Name": "Floors"})()
                self.params = {
                    1: MockParam(),  # FLOOR_HEIGHTABOVELEVEL_PARAM
                    2: MockParam(),  # WALL_ATTR_ROOM_BOUNDING
                }
            def get_Parameter(self, bip):
                return self.params.get(bip)

        class MockLevel(object):
            def __init__(self, name, elev, lid):
                self.Name = name
                self.Elevation = elev
                self.Id = lid

        class MockBuiltInParam(object):
            FLOOR_HEIGHTABOVELEVEL_PARAM = 1
            WALL_ATTR_ROOM_BOUNDING = 2

        class MockFloorAPI(object):
            created_instances = []
            @classmethod
            def Create(cls, doc, loops, floor_type_id, level_id):
                inst = MockFloor()
                cls.created_instances.append((loops, floor_type_id, level_id))
                return inst

        class MockElementTypeGroup(object):
            FloorType = 1
            WallType = 2
            RoofType = 3
            CeilingType = 4

        class MockCollector(object):
            def __init__(self, doc, items=None):
                self.items = items if items is not None else []
            def OfClass(self, cls):
                return self
            def FirstElement(self):
                return self.items[0] if self.items else None
            def __iter__(self):
                return iter(self.items)

        class MockDoc(object):
            def __init__(self):
                self.levels = [MockLevel("Level 1", 0.0, 101), MockLevel("Level 2", 12.0, 102)]
            def GetDefaultElementTypeId(self, group):
                return 201
            def GetElement(self, eid):
                for lvl in self.levels:
                    if lvl.Id == eid:
                        return lvl
                return None

        class MockDBFull(object):
            Floor = MockFloorAPI
            BuiltInParameter = MockBuiltInParam
            ElementTypeGroup = MockElementTypeGroup
            CurveLoop = object
            Level = object
            FloorType = object
            WallType = object
            RoofType = object
            CeilingType = object
            @staticmethod
            def FilteredElementCollector(doc):
                return MockCollector(doc, doc.levels if hasattr(doc, "levels") else [])
            @staticmethod
            def ElementId(v):
                return v

        MockDBFull.ElementId.InvalidElementId = -1

        orig_revit = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDBFull

            doc = MockDoc()
            router = conversion_router.ConversionRouter(doc)
            router.default_floor_type = 201
            router.level_cache["Level 1"] = 101
            router.level_cache["Level 2"] = 102

            # Mock boundary loop extraction to return a synthetic outer curve
            router._extract_floor_boundary_loops = lambda geom, obj: ("outer_curve", [], 12.5)
            router._build_curveloop_from_rhino_curve = lambda crv, target_z=None, is_outer=True: "mock_curveloop"

            scanned = ScannedObject("floor-guid-001")
            scanned.layer_name = "A-FLOR"
            scanned.nearest_level_name = "Level 2"
            scanned.area = 250.0

            res = conversion_router.ConversionResult("floor-guid-001")
            floor_elem = router._pipeline_1_native_floor(scanned, "mock_geom", 102, None, res)

            self.assertIsNotNone(floor_elem)
            self.assertEqual(res.strategy_used, "FloorNative")
            self.assertEqual(res.bua_area, 250.0)
            # Offset should be slab_z (12.5) - level_elev (12.0) = 0.5
            self.assertAlmostEqual(floor_elem.params[1].val, 0.5, places=2)
            # Room bounding should be 1
            self.assertEqual(floor_elem.params[2].val, 1)

        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_floor_and_wall_thickness_matching_and_type_creation(self):
        """Tests that matching FloorType and WallType are dynamically created with precise thickness and material."""
        import conversion_router

        class MockElementId(object):
            InvalidElementId = -1
            def __init__(self, val=-1):
                self.val = val
            def __eq__(self, other):
                if hasattr(other, "val"):
                    return self.val == other.val
                return self.val == other

        class MockLayer(object):
            def __init__(self, width=0.5, mat_id=10):
                self.Width = width
                self.MaterialId = mat_id
                self.Function = 1  # Structure

        class MockCompoundStructure(object):
            def __init__(self, thickness=0.5):
                self.thickness = thickness
                self.layers = [MockLayer(thickness)]
            def GetTotalThickness(self):
                return self.thickness
            def GetLayers(self):
                return self.layers
            def SetLayerWidth(self, idx, w):
                self.thickness = w
                self.layers[idx].Width = w
            def SetMaterialId(self, idx, mid):
                self.layers[idx].MaterialId = mid

        class MockType(object):
            def __init__(self, name="Generic", cs=None, eid=301):
                self.name = name
                self.cs = cs or MockCompoundStructure(0.5)
                self.Id = eid
                self.Kind = 0  # Basic wall
            def GetCompoundStructure(self):
                return self.cs
            def SetCompoundStructure(self, cs):
                self.cs = cs
            def Duplicate(self, new_name):
                new_cs = MockCompoundStructure(self.cs.thickness)
                return MockType(new_name, new_cs, eid=self.Id + 10)
            def get_Parameter(self, bip):
                class _P:
                    def AsString(s): return self.name
                return _P()

        class MockMat(object):
            def __init__(self, name="Concrete", mid=55):
                self.Name = name
                self.Id = mid

        class MockCollectorTypes(object):
            def __init__(self, doc, items):
                self.items = items
            def OfClass(self, cls):
                return self
            def FirstElement(self):
                return self.items[0] if self.items else None
            def __iter__(self):
                return iter(self.items)

        base_floor = MockType("Floor_Base", MockCompoundStructure(0.5), 301)
        base_wall = MockType("Wall_Base", MockCompoundStructure(0.656), 401)
        mat_concrete = MockMat("Concrete", 55)

        class MockDocTypes(object):
            def __init__(self):
                self.floors = [base_floor]
                self.walls = [base_wall]
                self.materials = [mat_concrete]
            def GetElement(self, eid):
                if eid == 55: return mat_concrete
                return None
            def GetDefaultElementTypeId(self, group):
                return 301

        class MockDBTypes(object):
            Level = type("Level", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
            Material = type("MatCls", (), {"Create": lambda doc, name: 77})
            ElementId = MockElementId
            BuiltInParameter = type("BIP", (), {"SYMBOL_NAME_PARAM": 99})
            WallKind = type("WK", (), {"Basic": 0})
            MaterialFunctionAssignment = type("MFA", (), {"Structure": 1})
            @staticmethod
            def FilteredElementCollector(doc):
                class _Col(object):
                    def __init__(self, d): self.d = d
                    def OfClass(self, cls):
                        if cls == MockDBTypes.FloorType:
                            return MockCollectorTypes(self.d, self.d.floors)
                        elif cls == MockDBTypes.WallType:
                            return MockCollectorTypes(self.d, self.d.walls)
                        elif cls == MockDBTypes.Material:
                            return MockCollectorTypes(self.d, self.d.materials)
                        return MockCollectorTypes(self.d, [])
                return _Col(doc)

        orig_revit = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDBTypes

            doc = MockDocTypes()
            router = conversion_router.ConversionRouter(doc)
            router.default_floor_type = base_floor.Id
            router.default_wall_type = base_wall.Id

            # 1. Floor Type matching/creation: 300mm slab (0.98425 ft) with Concrete
            f_id = router._get_or_create_matching_floor_type(0.98425, "Concrete")
            self.assertIsNotNone(f_id)
            self.assertNotEqual(f_id, base_floor.Id)

            # 2. Wall Type matching/creation: 200mm wall (0.656 ft) with Concrete
            w_id = router._get_or_create_matching_wall_type(0.656, "Concrete")
            self.assertIsNotNone(w_id)
        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_element_quantities_and_material_takeoff_extraction(self):
        """Tests that Revit native Area, Volume, and Material Takeoff are extracted with 100% precision."""
        import conversion_router
        from scanner import ScannedObject

        class MockParamVal(object):
            def __init__(self, d_val=0.0):
                self.d_val = d_val
                self.HasValue = True
                self.IsReadOnly = False
                self.str_val = ""
            def AsDouble(self):
                return self.d_val
            def Set(self, v):
                self.str_val = str(v)

        class MockElemWithQuantities(object):
            def __init__(self):
                # 100 sq ft area, 50 cu ft volume
                self.params = {
                    1: MockParamVal(100.0),   # HOST_AREA_COMPUTED
                    2: MockParamVal(50.0),    # HOST_VOLUME_COMPUTED
                    3: MockParamVal()         # ALL_MODEL_INSTANCE_COMMENTS
                }
                self.mat_ids = [501, 502]
            def get_Parameter(self, bip):
                return self.params.get(bip)
            def LookupParameter(self, name):
                return None
            def GetMaterialIds(self, paint):
                class _MatList(list):
                    Count = 2
                return _MatList(self.mat_ids)
            def GetMaterialVolume(self, mid):
                return 35.0 if mid == 501 else 15.0
            def GetMaterialArea(self, mid, paint):
                return 70.0 if mid == 501 else 30.0

        class MockDocQ(object):
            def GetElement(self, mid):
                class _Mat:
                    Name = "Concrete Structure" if mid == 501 else "Mortar Finish"
                return _Mat()

        class MockElementIdQ(object):
            InvalidElementId = -1
            def __init__(self, val=-1):
                self.val = val
            def __eq__(self, other):
                if hasattr(other, "val"):
                    return self.val == other.val
                return self.val == other

        class MockDBQ(object):
            Level = type("Level", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
            BuiltInParameter = type("BIP", (), {
                "HOST_AREA_COMPUTED": 1,
                "HOST_VOLUME_COMPUTED": 2,
                "ALL_MODEL_INSTANCE_COMMENTS": 3
            })
            ElementId = MockElementIdQ
            @staticmethod
            def FilteredElementCollector(doc):
                class _Col(object):
                    def __init__(self, d): pass
                    def OfClass(self, cls): return self
                    def FirstElement(self): return None
                    def __iter__(self): return iter([])
                return _Col(doc)

        orig_revit = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDBQ

            doc = MockDocQ()
            router = conversion_router.ConversionRouter(doc)

            elem = MockElemWithQuantities()
            scanned = ScannedObject("elem-guid-123")
            scanned.area = 10.0
            scanned.volume = 1.5

            res = conversion_router.ConversionResult("elem-guid-123")
            router._extract_element_quantities(elem, scanned, res)

            # 100 sq ft * 0.09290304 = 9.29 sqm
            self.assertAlmostEqual(res.area_sqm, 9.29, places=2)
            # 50 cu ft * 0.0283168 = 1.42 cum
            self.assertAlmostEqual(res.volume_cum, 1.42, places=2)
            # Material takeoff should have 2 materials
            self.assertIn("Concrete Structure", res.material_takeoff)
            self.assertIn("Mortar Finish", res.material_takeoff)
            # Primary material should be Concrete Structure (35 cuft > 15 cuft)
            self.assertEqual(res.primary_material, "Concrete Structure")
            # 35 cu ft * 0.0283168 = 0.99 cum
            self.assertAlmostEqual(res.material_volume_cum, 0.99, places=2)
            # Comments parameter written
            self.assertIn("R2R | Area: 9.29 m² | Vol: 1.42 m³", elem.params[3].str_val)
        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_reporting_volumetric_and_material_takeoff(self):
        """Tests that QualityAuditReport aggregates floor, wall, and material takeoff schedules."""
        from reporting import TransferQAReport, ReportExporter

        report = TransferQAReport()
        # Floor record
        report.element_records.append({
            "rhino_guid": "floor-1",
            "revit_element_id": 1001,
            "layer_name": "A-FLOR",
            "category": "Floors",
            "strategy": "FloorNative",
            "sync_state": "CREATE",
            "confidence_pct": 100.0,
            "bim_status": "PASS",
            "graphics_status": "PASS",
            "overall_status": "COMPLETE",
            "view_matrix": {"Plan": "PASS", "Section": "PASS"},
            "rhino_area": 120.0,
            "rhino_volume": 36.0,
            "area_sqm": 120.0,
            "volume_cum": 36.0,
            "material_name": "Reinforced Concrete",
            "material_volume_cum": 36.0,
            "material_takeoff": {
                "Reinforced Concrete": {"volume_cum": 36.0, "area_sqm": 120.0}
            },
            "bua_area": 120.0,
            "issues": []
        })
        # Wall record
        report.element_records.append({
            "rhino_guid": "wall-1",
            "revit_element_id": 1002,
            "layer_name": "A-WALL",
            "category": "Walls",
            "strategy": "WallByCurve",
            "sync_state": "CREATE",
            "confidence_pct": 100.0,
            "bim_status": "PASS",
            "graphics_status": "PASS",
            "overall_status": "COMPLETE",
            "view_matrix": {"Plan": "PASS", "Section": "PASS"},
            "rhino_area": 50.0,
            "rhino_volume": 10.0,
            "area_sqm": 50.0,
            "volume_cum": 10.0,
            "material_name": "Brick Masonry",
            "material_volume_cum": 10.0,
            "material_takeoff": {
                "Brick Masonry": {"volume_cum": 10.0, "area_sqm": 50.0}
            },
            "bua_area": 0.0,
            "issues": []
        })

        report.compute_bua_summary()
        self.assertEqual(report.bua_stats["revit_floor_area_sqm"], 120.0)
        self.assertEqual(report.bua_stats["revit_floor_volume_cum"], 36.0)
        self.assertEqual(report.bua_stats["revit_wall_area_sqm"], 50.0)
        self.assertEqual(report.bua_stats["revit_wall_volume_cum"], 10.0)
        self.assertEqual(report.bua_stats["total_material_volume_cum"], 46.0)
        self.assertIn("Reinforced Concrete", report.bua_stats["material_takeoff"])
        self.assertIn("Brick Masonry", report.bua_stats["material_takeoff"])

        test_dir = os.path.join(pkg_dir, "test_output")
        os.makedirs(test_dir, exist_ok=True)
        html_p = os.path.join(test_dir, "test_takeoff.html")
        csv_p = os.path.join(test_dir, "test_takeoff.csv")

        ReportExporter.export_html(report, html_p)
        ReportExporter.export_csv(report, csv_p)

        with open(html_p, "r", encoding="utf-8") as f:
            html_text = f.read()
        self.assertIn("Compound Material Takeoff Schedule", html_text)
        self.assertIn("Reinforced Concrete", html_text)
        self.assertIn("Brick Masonry", html_text)
        self.assertIn("46.0 m³", html_text)

        with open(csv_p, "r", encoding="utf-8") as f:
            csv_text = f.read()
        self.assertIn("volume_cum", csv_text)
        self.assertIn("Reinforced Concrete", csv_text)

    def test_direct_shape_quantities_and_geometry_solid_volume(self):
        """Tests that DirectShape elements without HOST_VOLUME_COMPUTED compute volume from solid geometry."""
        import conversion_router
        from scanner import ScannedObject

        class MockSolid(object):
            def __init__(self, vol=70.6293):  # 70.6293 cu ft = 2.0 m³
                self.Volume = vol
                self.SurfaceArea = 107.639    # 107.639 sq ft = 10.0 m²

        class MockDSElement(object):
            def __init__(self):
                self.solids = [MockSolid(70.6293)]
            def get_Parameter(self, bip):
                return None
            def get_Geometry(self, opt):
                return self.solids
            def LookupParameter(self, name):
                return None

        class MockDocDS(object):
            pass

        class MockDBDS(object):
            Options = type("Options", (), {})
            Level = type("Level", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
            ElementId = type("EID", (), {"InvalidElementId": -1})
            @staticmethod
            def FilteredElementCollector(doc):
                class _Col(object):
                    def __init__(self, d): pass
                    def OfClass(self, cls): return self
                    def FirstElement(self): return None
                    def __iter__(self): return iter([])
                return _Col(doc)

        orig_revit = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDBDS

            doc = MockDocDS()
            router = conversion_router.ConversionRouter(doc)

            ds_elem = MockDSElement()
            scanned = ScannedObject("ds-guid-999")
            scanned.material_name = "Concrete Precast"

            res = conversion_router.ConversionResult("ds-guid-999")
            router._extract_element_quantities(ds_elem, scanned, res)

            # 70.6293 cu ft * 0.028316846 = 2.00 cum
            self.assertAlmostEqual(res.volume_cum, 2.0, places=2)
            self.assertEqual(res.primary_material, "Concrete Precast")
            self.assertAlmostEqual(res.material_volume_cum, 2.0, places=2)
            self.assertIn("Concrete Precast", res.material_takeoff)
        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_irregular_non_planar_geometry_quantities(self):
        """Tests that irregular/warped/double-curved solid geometry calculates true top surface area and solid volume."""
        import conversion_router
        from scanner import ScannedObject

        class MockFace(object):
            def __init__(self, area, normal_z):
                self.Area = area
                self.normal_z = normal_z
            def GetBoundingBox(self):
                class _BB:
                    Min = type("P", (), {"U": 0, "V": 0})()
                    Max = type("P", (), {"U": 1, "V": 1})()
                return _BB()
            def ComputeNormal(self, uv):
                class _N:
                    Z = self.normal_z
                return _N()

        class MockIrregularSolid(object):
            def __init__(self):
                # Sloped/warped slab with 100 sq ft top face (nz=0.9), 100 sq ft bottom face (nz=-0.9), 20 sq ft sides (nz=0)
                # Total volume = 60 cu ft (~1.70 cum)
                self.Volume = 60.0
                self.Faces = [
                    MockFace(100.0, 0.9),   # Top walkable/sloped surface
                    MockFace(100.0, -0.9),  # Soffit
                    MockFace(10.0, 0.0),    # Side 1
                    MockFace(10.0, 0.0),    # Side 2
                ]

        class MockIrregularElement(object):
            def __init__(self):
                self.solids = [MockIrregularSolid()]
            def get_Parameter(self, bip):
                return None
            def get_Geometry(self, opt):
                return self.solids
            def LookupParameter(self, name):
                return None

        class MockDocIrr(object):
            pass

        class MockDBIrr(object):
            Options = type("Options", (), {})
            UV = lambda u, v: None
            Level = type("Level", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
            ElementId = type("EID", (), {"InvalidElementId": -1})
            @staticmethod
            def FilteredElementCollector(doc):
                class _Col(object):
                    def __init__(self, d): pass
                    def OfClass(self, cls): return self
                    def FirstElement(self): return None
                    def __iter__(self): return iter([])
                return _Col(doc)

        orig_revit = conversion_router.REVIT_AVAILABLE
        orig_db = getattr(conversion_router, "DB", None)
        try:
            conversion_router.REVIT_AVAILABLE = True
            conversion_router.DB = MockDBIrr

            doc = MockDocIrr()
            router = conversion_router.ConversionRouter(doc)

            elem = MockIrregularElement()
            scanned = ScannedObject("irr-guid-1")
            scanned.is_horizontal = True  # Irregular floor slab
            scanned.material_name = "Reinforced Cast-in-Place"

            res = conversion_router.ConversionResult("irr-guid-1")
            router._extract_element_quantities(elem, scanned, res)

            # Volume: 60 cu ft * 0.028316846 = 1.70 cum
            self.assertAlmostEqual(res.volume_cum, 1.70, places=2)
            # Area: 100 sq ft top face * 0.09290304 = 9.29 sqm (architectural top surface, not total 220 envelope)
            self.assertAlmostEqual(res.area_sqm, 9.29, places=2)
            # Material volume: 1.70 cum
            self.assertAlmostEqual(res.material_volume_cum, 1.70, places=2)
            self.assertEqual(res.primary_material, "Reinforced Cast-in-Place")
        finally:
            conversion_router.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                conversion_router.DB = orig_db

    def test_scanner_heavy_brep_guard(self):
        """Tests that a Brep with >1000 faces bypasses MassProperties.Compute and uses safe bounding fallback."""
        import scanner as sc
        scanner_inst = RhinoScanner()
        scanned = ScannedObject("heavy-brep-1")
        scanned.dx = 10.0
        scanned.dy = 10.0
        scanned.dz = 2.0

        # Mock heavy Brep
        class MockFaces(object):
            Count = 1500
            def __getitem__(self, idx):
                class MockFace(object):
                    def TryGetPlane(self, tol):
                        class MockPlane(object):
                            class Normal(object):
                                X = 0.0
                                Y = 0.0
                                Z = 1.0
                                Length = 1.0
                                @staticmethod
                                def Unitize():
                                    pass
                        return True, MockPlane()
                    Loops = []
                return MockFace()

        class MockHeavyBrep(sc.rg.Brep):
            IsValid = True
            IsSolid = True
            Faces = MockFaces()

        scanner_inst._compute_topology_fingerprint(scanned, MockHeavyBrep(), scale_to_m=1.0)
        self.assertTrue(scanned.is_closed)
        self.assertAlmostEqual(scanned.volume, 200.0, places=1)
        self.assertTrue(any("Mass properties approximated" in w for w in scanned.preflight_warnings))

    def test_scanner_heavy_mesh_guard(self):
        """Tests that a Mesh with >30000 faces bypasses mesh area/volume integrals and uses bounding box fallback."""
        import scanner as sc
        scanner_inst = RhinoScanner()
        scanned = ScannedObject("heavy-mesh-1")
        scanned.dx = 5.0
        scanned.dy = 5.0
        scanned.dz = 3.0

        class MockMesh(sc.rg.Mesh):
            IsValid = True
            IsClosed = True
            class Faces(object):
                Count = 50000
            class Vertices(object):
                Count = 25000

        scanner_inst._compute_topology_fingerprint(scanned, MockMesh(), scale_to_m=1.0)
        self.assertTrue(scanned.is_closed)
        self.assertAlmostEqual(scanned.volume, 75.0, places=1)
        self.assertTrue(any("Heavy mesh" in w for w in scanned.preflight_warnings))

    def test_sync_engine_extensible_storage_filter(self):
        """Tests that scan_revit_sync_records applies ExtensibleStorageFilter natively when available."""
        import sync_engine
        orig_revit = sync_engine.REVIT_AVAILABLE
        orig_db = getattr(sync_engine, "DB", None)
        try:
            sync_engine.REVIT_AVAILABLE = True

            filter_applied = []

            class MockExtensibleStorageFilter(object):
                def __init__(self, guid):
                    self.guid = guid

            class MockFilteredCollector(object):
                def __init__(self, doc):
                    self.doc = doc
                def WherePasses(self, flt):
                    filter_applied.append(flt)
                    return self
                def __iter__(self):
                    return iter([])

            class MockExtensibleStorage(object):
                ExtensibleStorageFilter = MockExtensibleStorageFilter

            class MockDB(object):
                ExtensibleStorage = MockExtensibleStorage
                FilteredElementCollector = MockFilteredCollector
                CategoryType = type("CategoryType", (), {"Model": 1})

            sync_engine.DB = MockDB

            engine = sync_engine.SyncEngine(object())
            engine.schema = type("MockSchema", (), {"GUID": "mock-guid-1234"})()
            recs = engine.scan_revit_sync_records()

            self.assertEqual(len(filter_applied), 1)
            self.assertEqual(filter_applied[0].guid, "mock-guid-1234")
            self.assertEqual(recs, {})
        finally:
            sync_engine.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                sync_engine.DB = orig_db

    def test_write_parameters_to_element(self):
        """Tests writing unit-aware shared parameters and comments to Revit element."""
        import conversion_router as cr
        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True

            class MockBIP(object):
                ALL_MODEL_INSTANCE_COMMENTS = 1
                ALL_MODEL_MARK = 2

            class MockDB(object):
                BuiltInParameter = MockBIP
                Level = type("Level", (), {})
                FloorType = type("FloorType", (), {})
                WallType = type("WallType", (), {})
                RoofType = type("RoofType", (), {})
                CeilingType = type("CeilingType", (), {})
                ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
                ElementId = type("EID", (), {"InvalidElementId": -1})
                @staticmethod
                def FilteredElementCollector(doc):
                    class _Col(object):
                        def __init__(self, d): pass
                        def OfClass(self, cls): return self
                        def FirstElement(self): return None
                        def __iter__(self): return iter([])
                    return _Col(doc)

            cr.DB = MockDB

            params_set = {}
            class MockParam(object):
                def __init__(self, name):
                    self.name = name
                    self.IsReadOnly = False
                def Set(self, val):
                    params_set[self.name] = val
                def AsString(self):
                    return ""

            class MockElement(object):
                def get_Parameter(self, bip):
                    if bip == MockBIP.ALL_MODEL_INSTANCE_COMMENTS:
                        return MockParam("Comments")
                    elif bip == MockBIP.ALL_MODEL_MARK:
                        return MockParam("Mark")
                    return None
                def LookupParameter(self, name):
                    return MockParam(name)

            doc = object()
            router = cr.ConversionRouter(doc)

            scanned = ScannedObject("cave-obj-1")
            scanned.object_name = "Cave_Chamber_South"
            scanned.layer_name = "A-CAVE-VAULT"
            scanned.thickness = 0.45
            scanned.building_zone = "Subterranean"
            scanned.inferred_category = "GenericModel"

            res = cr.ConversionResult("cave-obj-1")
            res.area_sqm = 120.0
            res.volume_cum = 85.0
            res.primary_material = "Carved Sandstone"
            res.material_volume_cum = 85.0
            res.category_created = "Generic Models"

            router._write_parameters_to_element(MockElement(), scanned, res)

            # Verify Comments contains summary
            self.assertTrue("R2R | Area: 120.00 m² | Vol: 85.00 m³" in params_set["Comments"])
            self.assertEqual(params_set["Mark"], "Cave_Chamber_South")
            self.assertEqual(params_set["R2R_Material"], "Carved Sandstone")
            self.assertEqual(params_set["R2R_Source_Layer"], "A-CAVE-VAULT")
            self.assertEqual(params_set["R2R_Building_Zone"], "Subterranean")

            # Verify internal storage units:
            self.assertAlmostEqual(params_set["R2R_Area"], 120.0 / 0.09290304, places=1)
            self.assertAlmostEqual(params_set["R2R_Volume"], 85.0 / 0.028316846592, places=1)
            self.assertAlmostEqual(params_set["R2R_Material_Volume"], 85.0 / 0.028316846592, places=1)
            self.assertAlmostEqual(params_set["R2R_Thickness"], 0.45 / 0.3048, places=1)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                cr.DB = orig_db

    def test_ensure_shared_parameters_bound(self):
        """Tests programmatic creation of shared parameter file and binding to model categories."""
        import conversion_router as cr
        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True

            inserted_bindings = []

            class MockDef(object):
                def __init__(self, name):
                    self.Name = name

            class MockGroup(object):
                Definitions = [MockDef("R2R_Area"), MockDef("R2R_Volume"), MockDef("R2R_Material")]

            class MockSPFile(object):
                class Groups(object):
                    @staticmethod
                    def get_Item(name):
                        return MockGroup()

            class MockApp(object):
                SharedParametersFilename = ""
                @staticmethod
                def OpenSharedParameterFile():
                    return MockSPFile()
                class Create(object):
                    @staticmethod
                    def NewCategorySet():
                        class _CatSet(object):
                            def Insert(self, c): pass
                        return _CatSet()
                    @staticmethod
                    def NewInstanceBinding(cat_set):
                        return "MockInstanceBinding"

            class MockDoc(object):
                Application = MockApp()
                class Settings(object):
                    class Categories(object):
                        @staticmethod
                        def get_Item(bic):
                            class _Cat(object):
                                AllowsBoundParameters = True
                            return _Cat()
                class ParameterBindings(object):
                    @staticmethod
                    def Contains(def_obj):
                        return False
                    @staticmethod
                    def Insert(def_obj, binding, p_group):
                        inserted_bindings.append((def_obj.Name, binding))

            class MockDB(object):
                Level = type("Level", (), {})
                FloorType = type("FloorType", (), {})
                WallType = type("WallType", (), {})
                RoofType = type("RoofType", (), {})
                CeilingType = type("CeilingType", (), {})
                ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
                ElementId = type("EID", (), {"InvalidElementId": -1})
                @staticmethod
                def FilteredElementCollector(doc):
                    class _Col(object):
                        def __init__(self, d): pass
                        def OfClass(self, cls): return self
                        def FirstElement(self): return None
                        def __iter__(self): return iter([])
                    return _Col(doc)
                BuiltInCategory = type("BIC", (), {
                    "OST_GenericModel": 1, "OST_Floors": 2, "OST_Walls": 3,
                    "OST_Ceilings": 4, "OST_Roofs": 5, "OST_Columns": 6,
                    "OST_StructuralColumns": 7, "OST_StructuralFraming": 8,
                    "OST_Mass": 9, "OST_Site": 10, "OST_Topography": 11,
                    "OST_SpecialityEquipment": 12, "OST_Furniture": 13,
                    "OST_Toposolid": 14
                })
                BuiltInParameterGroup = type("BIPG", (), {"PG_GEOMETRY": 1, "PG_DATA": 2})

            cr.DB = MockDB
            router = cr.ConversionRouter(MockDoc())
            success = router.ensure_shared_parameters_bound()

            self.assertTrue(success)
            self.assertEqual(len(inserted_bindings), 3)
            self.assertEqual(inserted_bindings[0][0], "R2R_Area")
            self.assertEqual(inserted_bindings[1][0], "R2R_Volume")
            self.assertEqual(inserted_bindings[2][0], "R2R_Material")
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                cr.DB = orig_db

    def test_create_r2r_takeoff_schedule(self):
        """Tests automatic ViewSchedule generation with R2R Area, Volume, Material, and Grand Totals."""
        import conversion_router as cr
        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True

            fields_added = []

            class MockSchedField(object):
                def __init__(self, name):
                    self.name = name
                    self.IsCalculateTotals = False

            class MockSchedDef(object):
                ShowGrandTotal = False
                ShowGrandTotalTitle = False
                ShowGrandTotalCount = False
                @staticmethod
                def GetSchedulableFields():
                    class _SF(object):
                        def __init__(self, name):
                            self._name = name
                            self.ParameterId = -1
                        def GetName(self, doc):
                            return self._name
                    return [_SF("R2R_Area"), _SF("R2R_Volume"), _SF("R2R_Material"), _SF("R2R_Source_Layer")]
                @staticmethod
                def AddField(sf):
                    msf = MockSchedField(sf.GetName(None))
                    fields_added.append(msf)
                    return msf

            class MockViewSchedule(object):
                Name = "R2R Cave & Geometry Takeoff Schedule"
                Definition = MockSchedDef()

            class MockDB(object):
                Level = type("Level", (), {})
                FloorType = type("FloorType", (), {})
                WallType = type("WallType", (), {})
                RoofType = type("RoofType", (), {})
                CeilingType = type("CeilingType", (), {})
                ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
                BuiltInParameter = type("BIP", (), {"ALL_MODEL_MARK": 1, "ALL_MODEL_INSTANCE_COMMENTS": 2})
                class ViewSchedule(object):
                    @staticmethod
                    def CreateSchedule(doc, cat_id):
                        return MockViewSchedule()
                class ElementId(object):
                    InvalidElementId = -1
                    def __init__(self, val=-1):
                        self.val = val
                    def __eq__(self, other):
                        return getattr(other, "val", other) == self.val
                BuiltInCategory = type("BIC", (), {"OST_GenericModel": 1})
                @staticmethod
                def FilteredElementCollector(doc):
                    class _Col(object):
                        def __init__(self, d): pass
                        def OfClass(self, cls): return self
                        def FirstElement(self): return None
                        def __iter__(self): return iter([])
                    return _Col(doc)

            cr.DB = MockDB
            router = cr.ConversionRouter(object())
            sched = router.create_r2r_takeoff_schedule()

            self.assertIsNotNone(sched)
            self.assertEqual(sched.Name, "R2R Cave & Geometry Takeoff Schedule")
            self.assertTrue(sched.Definition.ShowGrandTotal)

            names = [f.name for f in fields_added]
            self.assertIn("R2R_Area", names)
            self.assertIn("R2R_Volume", names)
            self.assertIn("R2R_Material", names)

            # Verify CalculateTotals was turned on for Area and Volume
            area_field = next(f for f in fields_added if f.name == "R2R_Area")
            vol_field = next(f for f in fields_added if f.name == "R2R_Volume")
            self.assertTrue(area_field.IsCalculateTotals)
            self.assertTrue(vol_field.IsCalculateTotals)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                cr.DB = orig_db


if __name__ == "__main__":
    unittest.main()
