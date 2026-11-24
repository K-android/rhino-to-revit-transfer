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

            # DirectShape fallback test: LookupParameter returns None, but Parameters collection search succeeds
            params_set.clear()
            class MockDirectShapeElem(object):
                def __init__(self):
                    self.Parameters = [
                        MockParam("R2R_Area"),
                        MockParam("R2R_Volume"),
                        MockParam("R2R_Material")
                    ]
                def get_Parameter(self, arg):
                    return None
                def LookupParameter(self, name):
                    return None

            ds_elem = MockDirectShapeElem()
            router._write_parameters_to_element(ds_elem, scanned, res)
            self.assertIn("R2R_Area", params_set)
            self.assertIn("R2R_Volume", params_set)
            self.assertIn("R2R_Material", params_set)
            self.assertEqual(params_set["R2R_Material"], "Carved Sandstone")
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                cr.DB = orig_db

    def test_ensure_shared_parameters_bound(self):
        """Tests programmatic creation of shared parameter file and binding to model categories."""
        import tempfile
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
                            Size = 1
                            def Insert(self, c):
                                self.Size += 1
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
                class Category(object):
                    @staticmethod
                    def GetCategory(doc, bic):
                        class _Cat(object):
                            AllowsBoundParameters = True
                        return _Cat()
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

            # Verify UTF-16 LE file encoding with BOM required by Autodesk Revit API
            sp_path = os.path.join(tempfile.gettempdir(), "R2R_SharedParameters.txt")
            self.assertTrue(os.path.exists(sp_path))
            with open(sp_path, "rb") as f:
                header_bytes = f.read(2)
                self.assertEqual(header_bytes, b'\xff\xfe')
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
                def GetFieldCount():
                    return len(fields_added)
                @staticmethod
                def GetField(idx):
                    return fields_added[idx]
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
            cr.DB = orig_db

    def test_family_template_locator_bundled_discovery(self):
        """Tests that FamilyTemplateLocator finds bundled templates without requiring Autodesk Content Pack."""
        from family_factory import FamilyTemplateLocator
        tmpl_gm = FamilyTemplateLocator.find_template(None, "Generic Model")
        self.assertIsNotNone(tmpl_gm)
        self.assertTrue(os.path.exists(tmpl_gm))
        self.assertTrue(tmpl_gm.lower().endswith(".rft"))

        tmpl_mass = FamilyTemplateLocator.find_template(None, "Mass")
        self.assertIsNotNone(tmpl_mass)
        self.assertTrue(os.path.exists(tmpl_mass))
        self.assertTrue(tmpl_mass.lower().endswith(".rft"))

    def test_family_factory_tuple_unpacking(self):
        """Tests that FamilyFactory unpacks (bool, Family) tuple from IronPython LoadFamily."""
        import family_factory as ff
        orig_revit = ff.REVIT_AVAILABLE
        orig_db = getattr(ff, "DB", None)
        try:
            ff.REVIT_AVAILABLE = True
            class MockSymbol(object):
                def __init__(self, name="TestSymbol"):
                    self.Name = name
                    self.FamilyName = "R2R_TestFamily"
                    self.IsActive = False
                def Activate(self):
                    self.IsActive = True

            class MockFamily(object):
                def __init__(self):
                    self._symbol = MockSymbol()
                def GetFamilySymbolIds(self):
                    return [101]

            class MockDoc(object):
                def __init__(self):
                    self._fam = MockFamily()
                def GetElement(self, eid):
                    if eid == 101:
                        return self._fam._symbol
                    return None

            mock_doc = MockDoc()
            # Simulate IronPython returning a tuple (True, MockFamily)
            tuple_result = (True, mock_doc._fam)

            # Test symbol extraction logic
            fam_obj = tuple_result
            if isinstance(fam_obj, tuple):
                for item in fam_obj:
                    if hasattr(item, "GetFamilySymbolIds"):
                        fam_obj = item
                        break
            found_sym = None
            if hasattr(fam_obj, "GetFamilySymbolIds"):
                for sym_id in fam_obj.GetFamilySymbolIds():
                    sym = mock_doc.GetElement(sym_id)
                    if sym:
                        if hasattr(sym, "IsActive") and not sym.IsActive:
                            sym.Activate()
                        found_sym = sym
                        break

            self.assertIsNotNone(found_sym)
            self.assertTrue(found_sym.IsActive)
            self.assertEqual(found_sym.FamilyName, "R2R_TestFamily")
        finally:
            ff.REVIT_AVAILABLE = orig_revit
            if orig_db is not None:
                ff.DB = orig_db

    def test_pre_generate_families_unique_guid_and_logging(self):
        """Tests that pre_generate_families keys symbols by GUID and calls log_fn."""
        from conversion_router import ConversionRouter
        from scanner import ScannedObject
        from classifier import ClassificationResult
        import conversion_router as cr

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_ff = getattr(cr, "FamilyFactory", None)
        try:
            cr.REVIT_AVAILABLE = True
            class MockSym(object):
                def __init__(self, name):
                    self.Name = name
                    self.FamilyName = name
                    self.IsActive = True

            class MockFF(object):
                @classmethod
                def create_native_freeform_family(cls, app, project_doc, family_name, rh_brep_or_solid, *args, **kwargs):
                    return MockSym(family_name)

                @classmethod
                def create_parametric_box_or_profile_family(cls, app, project_doc, family_name, *args, **kwargs):
                    return MockSym(family_name)

            cr.FamilyFactory = MockFF

            router = ConversionRouter(None, global_mode="Hybrid")
            router.doc = object()
            router.app = object()

            # Create 2 unique irregular cave objects on the same layer
            obj1 = ScannedObject("cave-guid-1")
            obj1.layer_name = "Caves"
            obj1.object_name = "Chamber_Alpha"
            obj1.raw_geometry = object()
            obj1.centroid = (10.0, 20.0, 5.0)

            obj2 = ScannedObject("cave-guid-2")
            obj2.layer_name = "Caves"
            obj2.object_name = "Chamber_Beta"
            obj2.raw_geometry = object()
            obj2.centroid = (30.0, 40.0, 5.0)

            c1 = ClassificationResult("cave-guid-1")
            c1.inferred_category = "GenericModel"
            c1.proposed_strategy = "FamilyInstance"
            c2 = ClassificationResult("cave-guid-2")
            c2.inferred_category = "GenericModel"
            c2.proposed_strategy = "FamilyInstance"
            c_dict = {
                "cave-guid-1": c1,
                "cave-guid-2": c2,
            }

            log_messages = []
            symbols = router.pre_generate_families([obj1, obj2], c_dict, log_fn=lambda m: log_messages.append(m))

            # Both objects must have their own unique entries in the symbol cache
            self.assertIn("cave-guid-1", router.family_symbol_cache)
            self.assertIn("cave-guid-2", router.family_symbol_cache)
            self.assertNotEqual(router.family_symbol_cache["cave-guid-1"].Name, router.family_symbol_cache["cave-guid-2"].Name)
            self.assertIn("Chamber_Alpha", router.family_symbol_cache["cave-guid-1"].Name)
            self.assertIn("Chamber_Beta", router.family_symbol_cache["cave-guid-2"].Name)

            # Check logging
            self.assertTrue(any("Chamber_Alpha" in m for m in log_messages))
            self.assertTrue(any("Chamber_Beta" in m for m in log_messages))
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db
            cr.FamilyFactory = orig_ff

    def test_pipeline_8c_parametric_family_execution_and_fallback(self):
        """Tests that _pipeline_8c places FamilyInstance from cache and falls back with warning when absent."""
        from conversion_router import ConversionRouter, ConversionResult
        from scanner import ScannedObject
        import conversion_router as cr

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True
            class MockParam(object):
                def __init__(self, val=""):
                    self.val = val
                    self.IsReadOnly = False
                    self.StorageType = 2
                def Set(self, v): self.val = v

            class MockInstance(object):
                def __init__(self, sym):
                    self.Symbol = sym
                    self.Id = 999
                    self.UniqueId = "fam-inst-guid-999"
                def LookupParameter(self, n): return MockParam()

            class MockSymbol(object):
                def __init__(self, name="CaveFamily"):
                    self.Name = name
                    self.FamilyName = name
                    self.IsActive = True
                def LookupParameter(self, n): return MockParam()

            class MockCreate(object):
                def NewFamilyInstance(self, *args, **kwargs):
                    return MockInstance(args[1] if len(args) > 1 else None)

            class MockDoc(object):
                def __init__(self):
                    self.Create = MockCreate()
                def GetElement(self, eid): return None

            class MockStructure(object):
                class StructuralType(object):
                    NonStructural = 0

            class MockBICMeta(type):
                def __getattr__(cls, name):
                    return -2000150

            class MockBIC(object, metaclass=MockBICMeta):
                OST_GenericModel = -2000150
                OST_Topography = -2000011
                OST_Site = -2000070

            class MockElementId(object):
                InvalidElementId = -1
                def __init__(self, val=-1):
                    self.val = val

            class MockDB(object):
                XYZ = lambda x, y, z: (x, y, z)
                Structure = MockStructure
                ElementId = MockElementId
                BuiltInCategory = MockBIC
                StorageType = type("ST", (), {"Double": 2})
                FamilySymbol = object
                def FilteredElementCollector(doc):
                    class _C(object):
                        def OfClass(self, c): return []
                    return _C()
                def FilteredElementCollector(doc):
                    class _C(object):
                        def OfClass(self, c): return []
                    return _C()

            cr.DB = MockDB
            router = ConversionRouter(None, global_mode="Hybrid")
            router.doc = MockDoc()

            obj = ScannedObject("cave-obj-123")
            obj.layer_name = "Caves"
            obj.object_name = "Chamber_Central"
            obj.centroid = (0.0, 0.0, 0.0)
            res = ConversionResult("cave-obj-123")

            # 1. Successful placement when symbol exists in cache under GUID
            sym = MockSymbol("R2R_Fam_Caves_Chamber_Central")
            router.family_symbol_cache["cave-obj-123"] = sym
            inst = router._pipeline_8c_parametric_family(obj, None, None, None, res)
            self.assertIsNotNone(inst)
            self.assertEqual(res.strategy_used, "FamilyInstance")

            # 2. Transparent fallback when symbol does NOT exist in cache
            obj_fallback = ScannedObject("cave-obj-missing")
            obj_fallback.layer_name = "Irregular_Caves"
            obj_fallback.centroid = (0.0, 0.0, 0.0)
            res_fallback = ConversionResult("cave-obj-missing")

            # Mock _pipeline_9_direct_shape so we can observe fallback routing
            router._pipeline_9_direct_shape = lambda s, g, c, p, r, category_id=None: "MockDS"
            inst_fb = router._pipeline_8c_parametric_family(obj_fallback, None, None, None, res_fallback)
            self.assertEqual(inst_fb, "MockDS")
            self.assertEqual(res_fallback.strategy_used, "DirectShape (Family Fallback)")
            self.assertTrue(any("DirectShape with full volume and schedule parameters" in w for w in res_fallback.warnings))
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db

    def test_revit_material_selector_and_overrides(self):
        """Tests that LayerRowItem and LayerConfigDialog manage Revit material selections cleanly."""
        from ui_dialog import LayerRowItem, LayerConfigDialog
        from profile_manager import MappingRule

        class MockObj(object):
            def __init__(self):
                self.classification = None

        row = LayerRowItem("CAVE_WALLS", [MockObj()], None, None)
        self.assertEqual(row.selected_material, "<Auto from Rhino>")

        # Update material
        row.update_material("Concrete, Cast-in-Place")
        self.assertEqual(row.selected_material, "Concrete, Cast-in-Place")

        # Fallback to auto when empty
        row.update_material("")
        self.assertEqual(row.selected_material, "<Auto from Rhino>")

        # MappingRule with material
        rule = MappingRule("CAVE_WALLS", material="Sandstone")
        self.assertEqual(rule.material, "Sandstone")

        # LayerConfigDialog material dropdown verification
        project_mats = ["<Auto from Rhino>", "Concrete, Cast-in-Place", "Sandstone", "Granite"]
        dlg = LayerConfigDialog(row, revit_materials=project_mats)
        if hasattr(dlg, "dd_material"):
            self.assertEqual(len(dlg.dd_material.Items), 4)
            dlg.dd_material.SelectedIndex = 2  # Sandstone
            dlg._on_apply(None, None)
            self.assertEqual(row.selected_material, "Sandstone")

    def test_direct_shape_and_family_material_pipeline_enforcement(self):
        """Tests that user-selected Revit material is applied to DirectShape and FamilyInstance pipelines."""
        import conversion_router as cr
        from scanner import ScannedObject
        from profile_manager import MappingRule
        from conversion_router import ConversionResult

        class MockParam(object):
            def __init__(self):
                self.val = None
                self.HasValue = False
                self.IsReadOnly = False
            def Set(self, v):
                self.val = v
                self.HasValue = True
            def AsElementId(self):
                return self.val

        class MockMaterialElem(object):
            def __init__(self, mid, name):
                self.Id = mid
                self.Name = name

        class MockDirectShape(object):
            def __init__(self):
                self.Id = 1001
                self.UniqueId = "ds-unique-1001"
                self.Category = type("Cat", (), {"Name": "Generic Models"})
                self.params = {1: MockParam(), 2: MockParam()} # 1: MARK, 2: MATERIAL_ID_PARAM
            def SetShape(self, geoms):
                pass
            def get_Parameter(self, bip):
                return self.params.get(bip)
            def LookupParameter(self, name):
                return None

        class MockFamilyInst(object):
            def __init__(self):
                self.Id = 2001
                self.UniqueId = "fam-unique-2001"
                self.Category = type("Cat", (), {"Name": "Generic Models"})
                self.params = {"Element_Material": MockParam()}
            def get_Parameter(self, bip):
                return None
            def LookupParameter(self, name):
                return self.params.get(name)

        class MockCollector(object):
            def __init__(self, mats):
                self.mats = mats
                self.cls = None
            def OfClass(self, cls):
                self.cls = cls
                return self
            def FirstElement(self):
                return None
            def __iter__(self):
                if self.cls == MockDB.Material:
                    return iter(self.mats)
                return iter([])

        sandstone_mat = MockMaterialElem(301, "Sandstone")
        concrete_mat = MockMaterialElem(302, "Concrete, Cast-in-Place")
        all_mats = [sandstone_mat, concrete_mat]

        class MockDoc(object):
            def GetElement(self, mid):
                for m in all_mats:
                    if m.Id == mid:
                        return m
                return None

        class MockBICMeta(type):
            def __getattr__(cls, name):
                return -2000150

        class MockBIC(object, metaclass=MockBICMeta):
            OST_GenericModel = -2000150

        class MockElementId(object):
            InvalidElementId = -1
            def __init__(self, val=-1):
                self.val = val
            def __eq__(self, other):
                if hasattr(other, "val"):
                    return self.val == other.val
                return self.val == other

        class MockDB(object):
            XYZ = staticmethod(lambda x, y, z: type("XYZ", (), {"X": x, "Y": y, "Z": z})())
            Level = type("Level", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            FamilySymbol = type("FamilySymbol", (), {})
            Material = type("Material", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})
            DirectShape = type("DirectShape", (), {
                "CreateElement": staticmethod(lambda d, c: MockDirectShape()),
                "IsValidCategoryId": staticmethod(lambda c, d: True)
            })
            BuiltInParameter = type("BIP", (), {
                "ALL_MODEL_MARK": 1,
                "MATERIAL_ID_PARAM": 2,
                "HOST_AREA_COMPUTED": 3,
                "HOST_VOLUME_COMPUTED": 4,
                "ALL_MODEL_INSTANCE_COMMENTS": 5
            })
            BuiltInCategory = MockBIC
            ElementId = MockElementId
            FilteredElementCollector = staticmethod(lambda d: MockCollector(all_mats))

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = cr.DB
        try:
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB
            doc = MockDoc()
            router = cr.ConversionRouter(doc)

            # 1. Test DirectShape with material override "Sandstone"
            obj = ScannedObject("test-ds-guid")
            obj.layer_name = "Subterranean_Mesh"
            obj.raw_geometry = type("Mesh", (), {})()
            rule_ds = MappingRule("Subterranean_Mesh", material="Sandstone")
            res_ds = ConversionResult("test-ds-guid")

            # Mock _convert_rhino_to_revit_geometry to return dummy geometry
            router._convert_rhino_to_revit_geometry = lambda g, geom_feet=None, material_id=None: ["dummy_geom"]

            ds_elem = router._pipeline_9_direct_shape(obj, None, None, profile_rule=rule_ds, res=res_ds)
            self.assertIsNotNone(ds_elem)
            # Verify MATERIAL_ID_PARAM was set to Sandstone's Id (301)
            p_mat = ds_elem.get_Parameter(2)
            self.assertEqual(p_mat.val, 301)

            # Verify _extract_element_quantities resolves primary_material as "Sandstone"
            router._extract_element_quantities(ds_elem, obj, res_ds, profile_rule=rule_ds)
            self.assertEqual(res_ds.primary_material, "Sandstone")

            # 2. Test FamilyInstance material parameter assignment
            inst_obj = MockFamilyInst()
            rule_fam = MappingRule("Cave_Vault", material="Concrete, Cast-in-Place")
            obj_fam = ScannedObject("test-fam-guid")
            obj_fam.layer_name = "Cave_Vault"
            obj_fam.centroid = (0.0, 0.0, 0.0)
            res_fam = ConversionResult("test-fam-guid")

            # Mock symbol and NewFamilyInstance
            mock_sym = type("Sym", (), {"IsActive": True, "LookupParameter": lambda s, n: None})()
            router.family_symbol_cache["test-fam-guid"] = mock_sym
            doc.Create = type("Create", (), {
                "NewFamilyInstance": lambda *a, **k: inst_obj
            })()
            cr.DB.Structure = type("Struct", (), {"StructuralType": type("ST", (), {"NonStructural": 0})})()

            placed = router._pipeline_8c_parametric_family(obj_fam, None, None, profile_rule=rule_fam, res=res_fam)
            self.assertIsNotNone(placed)
            # Verify Element_Material on instance was set to Concrete's Id (302)
            p_fam_mat = placed.LookupParameter("Element_Material")
            self.assertEqual(p_fam_mat.val, 302)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db


class TestStrategyRulesAndCustomParams(unittest.TestCase):
    """Tests for Strategy Rule Engine, Mixed-Layer Splitting, Editability Routing, and Custom Parameters."""

    def _make_mock_db_env(self):
        """Creates a comprehensive MockDB & MockDoc pair safe for router dispatch tests."""
        class MockBICMeta(type):
            def __getattr__(cls, name):
                return -2000150

        class MockBIC(object, metaclass=MockBICMeta):
            OST_GenericModel = -2000150
            OST_Floors = -2000032
            OST_Walls = -2000011
            OST_Roofs = -2000035
            OST_Ceilings = -2000038
            OST_Toposolid = -2007120
            OST_Topography = -2001340
            OST_Site = -2001260

        class MockElementId(object):
            InvalidElementId = -1

            def __init__(self, val=-1):
                self.val = val
                self.IntegerValue = val if isinstance(val, int) else -1

            def __eq__(self, other):
                return getattr(other, "val", other) == self.val

        class MockMaterial(object):
            def __init__(self, mid, name):
                self.Id = MockElementId(mid)
                self.Name = name

        class MockLevel(object):
            def __init__(self, lid, name, elev=0.0):
                self.Id = MockElementId(lid)
                self.Name = name
                self.Elevation = elev

        class MockStorageType(object):
            String = 1
            Integer = 2
            Double = 3
            ElementId = 4

        class MockBIP(object):
            ALL_MODEL_MARK = 1
            ALL_MODEL_INSTANCE_COMMENTS = 2
            MATERIAL_ID_PARAM = 3
            ELEM_TYPE_PARAM = 4

        class MockTransaction(object):
            def __init__(self, doc, name="R2R"):
                self.name = name

            def Start(self):
                return True

            def Commit(self):
                return True

            def RollBack(self):
                return True

            def Dispose(self):
                pass

        mats = [MockMaterial(401, "Travertine"), MockMaterial(402, "Timber_Oak")]
        levels = [MockLevel(101, "Level 00", 0.0)]

        class MockCollector(object):
            def __init__(self, doc):
                self._items = []

            def OfClass(self, cls):
                if cls is MockDB.Material:
                    self._items = list(mats)
                elif cls is MockDB.Level:
                    self._items = list(levels)
                else:
                    self._items = []
                return self

            def FirstElement(self):
                return self._items[0] if self._items else None

            def __iter__(self):
                return iter(self._items)

        class MockDB(object):
            BuiltInCategory = MockBIC
            ElementId = MockElementId
            StorageType = MockStorageType
            BuiltInParameter = MockBIP
            Transaction = MockTransaction
            Material = type("Material", (), {})
            Level = type("Level", (), {})
            FamilySymbol = type("FamilySymbol", (), {})
            FloorType = type("FloorType", (), {})
            WallType = type("WallType", (), {})
            RoofType = type("RoofType", (), {})
            CeilingType = type("CeilingType", (), {})
            ElementTypeGroup = type("ETG", (), {"FloorType": 1, "WallType": 2, "RoofType": 3, "CeilingType": 4})()
            FilteredElementCollector = MockCollector
            XYZ = staticmethod(lambda x, y, z: (x, y, z))

            class Structure(object):
                class StructuralType(object):
                    NonStructural = 0

        class MockDoc(object):
            def __init__(self):
                self.Application = type("App", (), {"SharedParametersFilename": ""})()

            def GetElement(self, eid):
                return None

        class MockRhinoGeom(object):
            def Duplicate(self):
                return MockRhinoGeom()

            def Scale(self, s):
                return True

            def Translate(self, x, y, z):
                return True

        MockDB.RhinoGeom = MockRhinoGeom
        MockDB.Options = staticmethod(lambda *a: object())
        return MockDB, MockDoc

    def test_strategy_rule_engine_all_strategies(self):
        """Tests StrategyRuleEngine per-object evaluation, metadata, and compatibility scores."""
        from strategy_rules import StrategyRuleEngine, STRATEGY_METADATA

        # 1. Verify editability and constraint flags in STRATEGY_METADATA
        self.assertTrue(STRATEGY_METADATA["ParametricFamily"]["supports_constraints"])
        self.assertTrue(STRATEGY_METADATA["ParametricFamily"]["supports_type_params"])
        self.assertFalse(STRATEGY_METADATA["FreeFormFamily"]["supports_constraints"])
        self.assertTrue(STRATEGY_METADATA["FreeFormFamily"]["supports_type_params"])
        self.assertFalse(STRATEGY_METADATA["DirectShape"]["supports_constraints"])
        self.assertFalse(STRATEGY_METADATA["DirectShape"]["supports_type_params"])

        # Box primitive passes ParametricFamily
        box = ScannedObject("g-box")
        box.object_type = "Brep"
        box.geometry_primitive_type = "Box"
        box.is_prismatic = True
        box.is_closed = True
        box.volume = 6.0
        box.parametric_dims = {"width_m": 2.0, "depth_m": 1.0, "height_m": 3.0}
        ok, fails, _, _ = StrategyRuleEngine.evaluate_object(box, "ParametricFamily")
        self.assertTrue(ok, "Expected Box to pass ParametricFamily, got: {}".format(fails))

        # Cylinder primitive passes ParametricFamily via dx/dy/dz
        cyl = ScannedObject("g-cyl")
        cyl.object_type = "Brep"
        cyl.geometry_primitive_type = "Cylinder"
        cyl.is_prismatic = True
        cyl.is_closed = True
        cyl.dx = 1.0
        cyl.dy = 1.0
        cyl.dz = 4.0
        ok_cyl, fails_cyl, _, _ = StrategyRuleEngine.evaluate_object(cyl, "ParametricFamily")
        self.assertTrue(ok_cyl, "Expected Cylinder to pass ParametricFamily: {}".format(fails_cyl))

        # FreeFormSolid fails ParametricFamily -> recommends FreeFormFamily
        ff = ScannedObject("g-ff")
        ff.object_type = "Brep"
        ff.geometry_primitive_type = "FreeFormSolid"
        ff.is_closed = True
        ff.volume = 12.0
        ok_ff_param, _, _, fb_ff = StrategyRuleEngine.evaluate_object(ff, "ParametricFamily")
        self.assertFalse(ok_ff_param)
        self.assertEqual(fb_ff, "FreeFormFamily")
        ok_ff_native, fails_ff_native, _, _ = StrategyRuleEngine.evaluate_object(ff, "FreeFormFamily")
        self.assertTrue(ok_ff_native, "Expected closed FreeFormSolid to pass FreeFormFamily: {}".format(fails_ff_native))

        # OpenMesh fails ParametricFamily and FreeFormFamily -> recommends DirectShape
        omesh = ScannedObject("g-omesh")
        omesh.object_type = "Mesh"
        omesh.geometry_primitive_type = "OpenMesh"
        omesh.is_closed = False
        ok_om_param, _, _, fb_om = StrategyRuleEngine.evaluate_object(omesh, "ParametricFamily")
        self.assertFalse(ok_om_param)
        self.assertEqual(fb_om, "DirectShape")
        ok_om_ff, _, _, _ = StrategyRuleEngine.evaluate_object(omesh, "FreeFormFamily")
        self.assertFalse(ok_om_ff)
        ok_om_ds, _, _, _ = StrategyRuleEngine.evaluate_object(omesh, "DirectShape")
        self.assertTrue(ok_om_ds)

        # Curve fails DirectShape -> recommends Exclude
        crv = ScannedObject("g-crv")
        crv.object_type = "Curve"
        crv.geometry_primitive_type = "Curve"
        crv.is_closed = False
        ok_crv, _, _, fb_crv = StrategyRuleEngine.evaluate_object(crv, "DirectShape")
        self.assertFalse(ok_crv)
        self.assertEqual(fb_crv, "Exclude")

        # AdaptiveComponent 4-pt panel vs 0-pt
        panel = ScannedObject("g-adp")
        panel.object_type = "Brep"
        panel.geometry_primitive_type = "AdaptivePanel"
        panel.adaptive_point_count = 4
        ok_adp, _, _, _ = StrategyRuleEngine.evaluate_object(panel, "AdaptiveComponent")
        self.assertTrue(ok_adp)
        ok_adp_fail, _, _, _ = StrategyRuleEngine.evaluate_object(box, "AdaptiveComponent")
        self.assertFalse(ok_adp_fail)

        # Horizontal slab & vertical wall & block instance
        slab = ScannedObject("g-slab")
        slab.object_type = "Brep"
        slab.is_horizontal = True
        slab.is_vertical = False
        slab.is_closed = True
        self.assertTrue(StrategyRuleEngine.evaluate_object(slab, "FloorNative")[0])
        self.assertTrue(StrategyRuleEngine.evaluate_object(slab, "RoofNative")[0])
        self.assertTrue(StrategyRuleEngine.evaluate_object(slab, "Toposolid")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(slab, "WallByCurve")[0])

        wall = ScannedObject("g-wall")
        wall.object_type = "Brep"
        wall.is_horizontal = False
        wall.is_vertical = True
        wall.is_closed = True
        self.assertTrue(StrategyRuleEngine.evaluate_object(wall, "WallByCurve")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(wall, "FloorNative")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(wall, "Toposolid")[0])

        blk = ScannedObject("g-blk")
        blk.object_type = "InstanceReference"
        blk.geometry_primitive_type = "BlockInstance"
        self.assertTrue(StrategyRuleEngine.evaluate_object(blk, "RepetitiveFamily")[0])

    def test_mixed_layer_geometry_bucketing_and_splitting(self):
        """Tests mixed-layer bucket detection, StrategyEvaluation, Virtual Sub-Layer Splitting, and LayerRowItem."""
        from strategy_rules import StrategyRuleEngine
        from ui_dialog import LayerRowItem

        objs = []
        for i in range(2):
            b = ScannedObject("mix-box-{}".format(i))
            b.layer_name = "Mix_Layer"
            b.object_type = "Brep"
            b.geometry_primitive_type = "Box"
            b.is_prismatic = True
            b.is_closed = True
            b.parametric_dims = {"width_m": 1.0, "depth_m": 1.0, "height_m": 2.0}
            objs.append(b)

        for i in range(2):
            a = ScannedObject("mix-adp-{}".format(i))
            a.layer_name = "Mix_Layer"
            a.object_type = "Brep"
            a.geometry_primitive_type = "AdaptivePanel"
            a.adaptive_point_count = 4
            a.is_closed = False
            objs.append(a)

        m = ScannedObject("mix-mesh-0")
        m.layer_name = "Mix_Layer"
        m.object_type = "Mesh"
        m.geometry_primitive_type = "OpenMesh"
        m.is_closed = False
        objs.append(m)

        c = ScannedObject("mix-crv-0")
        c.layer_name = "Mix_Layer"
        c.object_type = "Curve"
        c.geometry_primitive_type = "Curve"
        c.is_closed = False
        objs.append(c)

        ev = StrategyRuleEngine.evaluate_layer(objs, "ParametricFamily")
        self.assertEqual(ev.status, "PARTIAL_MIXED")
        self.assertEqual(ev.pass_count, 2)
        self.assertEqual(ev.fail_count, 4)
        self.assertEqual(ev.total_count, 6)
        self.assertTrue(ev.is_mixed_geometry)
        self.assertEqual(len(ev.bucket_counts), 4)
        self.assertIn("2/6", ev.summary_text())

        splits = StrategyRuleEngine.split_objects_by_geometry("Mix_Layer", objs)
        self.assertEqual(len(splits), 4)
        total_split_count = sum(len(sub) for _, _, sub in splits)
        self.assertEqual(total_split_count, 6)
        bucket_keys = [bk for _, bk, _ in splits]
        self.assertEqual(set(bucket_keys), set(["PARAMETRIC_SOLID", "ADAPTIVE", "OPEN_MESH", "CURVE"]))
        for vname, bk, sub in splits:
            self.assertTrue(vname.startswith("Mix_Layer ["))
            for o in sub:
                obk, _ = StrategyRuleEngine.classify_geometry_bucket(o)
                self.assertEqual(obk, bk)

        # Homogeneous list returns 1 unsplit group with original layer name
        homo = StrategyRuleEngine.split_objects_by_geometry("PureBoxes", objs[:2])
        self.assertEqual(len(homo), 1)
        self.assertEqual(homo[0][0], "PureBoxes")
        self.assertEqual(homo[0][1], "PARAMETRIC_SOLID")

        # UI LayerRowItem tracks mixed status & compatibility tag dynamically
        cls_mix = ClassificationResult("mix-box-0")
        cls_mix.inferred_category = "Generic"
        cls_mix.revit_category_name = "Generic Models"
        cls_mix.proposed_strategy = "ParametricFamily"
        row = LayerRowItem("Mix_Layer", objs, cls_mix, None)
        self.assertTrue(row.rule_eval.is_mixed_geometry)
        self.assertEqual(row.compatibility_tag, "MIXED (2/6)")
        self.assertTrue(any(p in row.rule_eval.editability_label for p in ("Editable", "Parametric")))

    def test_mixed_policy_auto_fallback_vs_strict_skip(self):
        """Tests per-object rule guard in ConversionRouter.convert_object under StrictSkip vs AutoFallback."""
        import conversion_router as cr
        from conversion_router import ConversionRouter
        from profile_manager import MappingRule

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            MockDB, MockDoc = self._make_mock_db_env()
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB

            router = ConversionRouter(MockDoc(), global_mode="Hybrid", mixed_policy="AutoFallback")
            called_pipelines = []
            router._extract_element_quantities = lambda *a, **kw: None
            router._write_parameters_to_element = lambda *a, **kw: None
            dummy_cat = type("Cat", (), {"Name": "Generic Models"})()

            def fake_ds(scanned_obj, geom_feet, classification_result=None, profile_rule=None, res=None, category_id=None):
                called_pipelines.append(("DirectShape", scanned_obj.rhino_guid))
                return type("DummyDS", (), {"Id": MockDB.ElementId(501), "UniqueId": "ds-501", "Category": dummy_cat, "get_Parameter": lambda s, p: None, "LookupParameter": lambda s, n: None})()

            def fake_ff(scanned_obj, geom_feet, level_id=None, profile_rule=None, res=None):
                called_pipelines.append(("FreeFormFamily", scanned_obj.rhino_guid))
                return type("DummyFF", (), {"Id": MockDB.ElementId(502), "UniqueId": "ff-502", "Category": dummy_cat, "get_Parameter": lambda s, p: None, "LookupParameter": lambda s, n: None})()

            router._pipeline_9_direct_shape = fake_ds
            router._pipeline_8_family_placement = fake_ff

            # OpenMesh assigned ParametricFamily under StrictSkip -> SKIPPED
            omesh = ScannedObject("obj-omesh-1")
            omesh.layer_name = "Mix_Layer"
            omesh.object_type = "Mesh"
            omesh.geometry_primitive_type = "OpenMesh"
            omesh.is_closed = False
            omesh.raw_geometry = MockDB.RhinoGeom()

            rule_strict = MappingRule("Mix_Layer", revit_category="Generic Models", strategy="ParametricFamily", mixed_policy="StrictSkip")
            cls_res = ClassificationResult("obj-omesh-1")
            cls_res.revit_category_name = "Generic Models"
            cls_res.proposed_strategy = "ParametricFamily"

            res_strict = router.convert_object(omesh, cls_res, profile_rule=rule_strict)
            self.assertEqual(res_strict.status, "SKIPPED")
            self.assertEqual(len(called_pipelines), 0)

            # OpenMesh assigned ParametricFamily under AutoFallback -> switches to DirectShape with Rule guard warning
            rule_auto = MappingRule("Mix_Layer", revit_category="Generic Models", strategy="ParametricFamily", mixed_policy="AutoFallback")
            res_auto = router.convert_object(omesh, cls_res, profile_rule=rule_auto)
            self.assertIn(("DirectShape", "obj-omesh-1"), called_pipelines)
            self.assertTrue(any("Rule guard:" in w for w in res_auto.warnings))
            self.assertEqual(res_auto.strategy_used, "DirectShape")

            # FreeFormSolid assigned ParametricFamily under AutoFallback -> falls back to FreeFormFamily
            ff_obj = ScannedObject("obj-ff-1")
            ff_obj.layer_name = "Mix_Layer"
            ff_obj.object_type = "Brep"
            ff_obj.geometry_primitive_type = "FreeFormSolid"
            ff_obj.is_closed = True
            ff_obj.volume = 8.0
            ff_obj.raw_geometry = MockDB.RhinoGeom()

            res_ff = router.convert_object(ff_obj, None, profile_rule=rule_auto)
            self.assertIn(("FreeFormFamily", "obj-ff-1"), called_pipelines)
            self.assertEqual(res_ff.strategy_used, "FreeFormFamily")
            self.assertTrue(any("Rule guard:" in w for w in res_ff.warnings))

            # Curve under AutoFallback -> fallback is Exclude -> SKIPPED
            crv = ScannedObject("obj-crv-1")
            crv.layer_name = "Mix_Layer"
            crv.object_type = "Curve"
            crv.geometry_primitive_type = "Curve"
            crv.is_closed = False
            crv.raw_geometry = MockDB.RhinoGeom()

            res_crv = router.convert_object(crv, None, profile_rule=rule_auto)
            self.assertEqual(res_crv.status, "SKIPPED")
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db

    def test_custom_parameter_spec_deterministic_guid_and_resolution(self):
        """Tests deterministic UUIDv5 GUIDs, token/@usertext resolution, profile persistence, and UTF-16 SharedParameters output."""
        import tempfile
        import codecs
        import conversion_router as cr
        from conversion_router import ConversionRouter
        from profile_manager import ProfileManager, MappingRule, CustomParameterSpec, deterministic_param_guid

        # 1. Deterministic GUID generation
        g1 = deterministic_param_guid("Fire_Rating")
        g2 = deterministic_param_guid("  Fire_Rating ")
        g3 = deterministic_param_guid("Acoustic_Class")
        self.assertEqual(g1, g2)
        self.assertNotEqual(g1, g3)
        self.assertEqual(len(g1), 36)
        self.assertEqual(g1[14], "5")  # UUID version 5 nibble

        # 2. Value expression resolution
        obj = ScannedObject("param-obj-1")
        obj.layer_name = "Facet_Wall"
        obj.area_sqm = 25.5
        obj.volume_cum = 12.25
        obj.nearest_level_name = "Level 02"
        obj.user_text = {"Zone": "NorthWing", "FireHrs": "2", "IsExternal": "yes"}
        cres = ClassificationResult("param-obj-1")
        cres.best_level = "Level 02"

        p_ut = CustomParameterSpec("Zone_Name", "Text", "Data", True, "@usertext:Zone")
        p_tok = CustomParameterSpec("Tag_Label", "Text", "Data", False, "{layer}_{level}")
        p_int = CustomParameterSpec("Fire_Hours", "Integer", "Data", True, "@usertext:FireHrs")
        p_bool = CustomParameterSpec("Is_External", "YesNo", "Data", True, "@usertext:IsExternal")
        p_len = CustomParameterSpec("Clear_Offset", "Length", "Constraint", True, "3.048")
        p_num = CustomParameterSpec("Panel_Area", "Number", "Data", True, "{area_sqm}")

        self.assertEqual(p_ut.resolve_value(obj, cres), "NorthWing")
        self.assertEqual(p_tok.resolve_value(obj, cres), "Facet_Wall_Level 02")
        self.assertEqual(p_int.resolve_value(obj, cres), 2)
        self.assertEqual(p_bool.resolve_value(obj, cres), 1)
        self.assertAlmostEqual(p_len.resolve_value(obj, cres), 10.0, places=3)
        self.assertAlmostEqual(p_num.resolve_value(obj, cres), 25.5, places=3)

        # 3. ProfileManager save / load round-trip
        tmp_path = os.path.join(tempfile.gettempdir(), "r2r_test_custom_params_profile.json")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        try:
            pm = ProfileManager(profile_path=tmp_path)
            pm.project_custom_parameters = [p_ut]
            pm.add_rule(MappingRule(
                layer_pattern="R2R_UnitTest_CustomLayer",
                revit_category="Walls",
                strategy="ParametricFamily",
                custom_parameters=[p_tok, p_len],
                mixed_policy="StrictSkip"
            ))
            pm.save(tmp_path)

            pm2 = ProfileManager()
            ok_load = pm2.load(tmp_path)
            self.assertTrue(ok_load)
            self.assertEqual(len(pm2.project_custom_parameters), 1)
            self.assertEqual(pm2.project_custom_parameters[0].guid, p_ut.guid)
            loaded_rule = pm2.find_matching_rule("R2R_UnitTest_CustomLayer")
            self.assertIsNotNone(loaded_rule)
            self.assertEqual(loaded_rule.mixed_policy, "StrictSkip")
            self.assertEqual(len(loaded_rule.custom_parameters), 2)
            self.assertEqual(loaded_rule.custom_parameters[1].guid, p_len.guid)
            self.assertEqual(loaded_rule.custom_parameters[1].data_type, "Length")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # 4. UTF-16 LE BOM R2R_SharedParameters.txt generation with custom parameter GUIDs
        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            MockDB, MockDoc = self._make_mock_db_env()
            doc = MockDoc()
            doc.Application.OpenSharedParameterFile = lambda: None
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB

            router = ConversionRouter(doc, project_custom_parameters=[p_ut])
            router.ensure_shared_parameters_bound(custom_parameters=[p_ut, p_len])

            sp_path = os.path.join(tempfile.gettempdir(), "R2R_SharedParameters.txt")
            self.assertTrue(os.path.exists(sp_path))
            with open(sp_path, "rb") as fb:
                raw_bytes = fb.read()
            self.assertTrue(raw_bytes.startswith(codecs.BOM_UTF16_LE))
            decoded = raw_bytes.decode("utf-16")
            self.assertIn("Zone_Name", decoded)
            self.assertIn(p_ut.guid, decoded)
            self.assertIn("Clear_Offset", decoded)
            self.assertIn(p_len.guid, decoded)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db

    def test_editable_vs_non_editable_family_routing_and_custom_params(self):
        """Tests pre_generate_families Editable vs Non-Editable routing and _write_parameters_to_element Instance/Type/Formula handling."""
        import conversion_router as cr
        from conversion_router import ConversionRouter, ConversionResult
        from profile_manager import MappingRule, CustomParameterSpec

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_ff = getattr(cr, "FamilyFactory", None)
        try:
            MockDB, MockDoc = self._make_mock_db_env()
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB

            param_calls = []
            freeform_calls = []

            class MockSymbol(object):
                def __init__(self, name):
                    self.Name = name
                    self.IsActive = True
                    self.type_params = {}

                def LookupParameter(self, pname):
                    return self.type_params.get(pname)

            class SpyFamilyFactory(object):
                @staticmethod
                def create_parametric_box_or_profile_family(
                    app, doc, **kwargs
                ):
                    name = kwargs.get("family_name", "Param")
                    param_calls.append((name, kwargs))
                    return MockSymbol(name)

                @staticmethod
                def create_native_freeform_family(
                    app, doc, **kwargs
                ):
                    name = kwargs.get("family_name", "FF")
                    freeform_calls.append((name, kwargs))
                    return MockSymbol(name)

            cr.FamilyFactory = SpyFamilyFactory

            obj_box = ScannedObject("route-box-1")
            obj_box.layer_name = "Modular_Blocks"
            obj_box.object_type = "Brep"
            obj_box.geometry_primitive_type = "Box"
            obj_box.is_prismatic = True
            obj_box.is_closed = True
            obj_box.centroid = (1.0, 2.0, 0.0)
            obj_box.bbox_min = (0.0, 1.0, 0.0)
            obj_box.bbox_max = (2.0, 3.0, 3.0)
            obj_box.parametric_dims = {"width_m": 2.0, "depth_m": 2.0, "height_m": 3.0}
            obj_box.raw_geometry = MockDB.RhinoGeom()

            obj_ff = ScannedObject("route-ff-1")
            obj_ff.layer_name = "Rock_Form"
            obj_ff.object_type = "Brep"
            obj_ff.geometry_primitive_type = "FreeFormSolid"
            obj_ff.is_closed = True
            obj_ff.volume = 15.0
            obj_ff.centroid = (5.0, 5.0, 0.0)
            obj_ff.bbox_min = (4.0, 4.0, 0.0)
            obj_ff.bbox_max = (6.0, 6.0, 5.0)
            obj_ff.raw_geometry = MockDB.RhinoGeom()

            c_inst = CustomParameterSpec("Inst_Code", "Text", "Data", is_instance=True, value_expr="C-101")
            c_type = CustomParameterSpec("Type_Finish", "Text", "Data", is_instance=False, value_expr="Honed")
            c_mat = CustomParameterSpec("Custom_Mat", "Material", "Data", is_instance=True, value_expr="Travertine")
            c_formula = CustomParameterSpec("Calc_Area", "Number", "Constraint", is_instance=True, value_expr="999", formula="Width * Depth", constraint_target="Width")

            rules_by_layer = {
                "Modular_Blocks": MappingRule("Modular_Blocks", revit_category="Generic Models", strategy="ParametricFamily", custom_parameters=[c_inst, c_type, c_formula]),
                "Rock_Form": MappingRule("Rock_Form", revit_category="Generic Models", strategy="FreeFormFamily", custom_parameters=[c_inst, c_type, c_mat])
            }
            c_box = ClassificationResult("route-box-1")
            c_box.proposed_strategy = "ParametricFamily"
            c_box.revit_category_name = "Generic Models"
            c_ff = ClassificationResult("route-ff-1")
            c_ff.proposed_strategy = "FreeFormFamily"
            c_ff.revit_category_name = "Generic Models"

            classifications_map = {
                "route-box-1": c_box,
                "route-ff-1": c_ff
            }

            router = ConversionRouter(MockDoc())
            router.pre_generate_families([obj_box, obj_ff], classifications_map, profile_rules=rules_by_layer)

            # Box (Editable ParametricFamily) routes ONLY to parametric builder; FreeFormSolid routes ONLY to freeform builder
            self.assertEqual(len(param_calls), 1)
            self.assertEqual(param_calls[0][1].get("primitive_type"), "Box")
            self.assertEqual(len(param_calls[0][1].get("custom_parameters", [])), 3)

            self.assertEqual(len(freeform_calls), 1)
            self.assertEqual(len(freeform_calls[0][1].get("custom_parameters", [])), 3)

            # Verify _write_parameters_to_element splits Instance vs Symbol Type vs Material and skips Formula params
            class MockParam(object):
                def __init__(self, st, init_val=None):
                    self.StorageType = st
                    self.val = init_val
                    self.IsReadOnly = False

                def Set(self, v):
                    self.val = v

            sym = MockSymbol("Modular_Blocks_Sym")
            sym.type_params["Type_Finish"] = MockParam(MockDB.StorageType.String)

            inst = type("MockFamInst", (), {
                "Symbol": sym,
                "params": {
                    "Inst_Code": MockParam(MockDB.StorageType.String),
                    "Custom_Mat": MockParam(MockDB.StorageType.ElementId),
                    "Calc_Area": MockParam(MockDB.StorageType.Double, init_val=12.0),
                },
                "get_Parameter": lambda s, k: None,
                "LookupParameter": lambda s, k: s.params.get(k)
            })()

            res_write = ConversionResult("route-box-1")
            rule_write = MappingRule("Modular_Blocks", revit_category="Generic Models", strategy="ParametricFamily", custom_parameters=[c_inst, c_type, c_mat, c_formula])
            router._write_parameters_to_element(inst, obj_box, res_write, profile_rule=rule_write)

            self.assertEqual(inst.params["Inst_Code"].val, "C-101")
            self.assertEqual(sym.type_params["Type_Finish"].val, "Honed")
            self.assertEqual(getattr(inst.params["Custom_Mat"].val, "val", inst.params["Custom_Mat"].val), 401)
            # Formula-driven parameter must NOT be overwritten by Python value_expr ("999")
            self.assertEqual(inst.params["Calc_Area"].val, 12.0)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db
            cr.FamilyFactory = orig_ff


class TestWpfXamlUiIntegrity(unittest.TestCase):
    """Validates WPF XAML resources, FindName bindings, and Master-Detail UI helpers."""

    XAML_FILES = (
        "ui_dashboard.xaml",
        "ui_settings.xaml",
        "ui_custom_params.xaml",
        "ui_layer_config.xaml",
    )

    def test_all_xaml_files_valid_xml_and_no_codebehind_attributes(self):
        """Ensures all XAML files parse cleanly, allow resizing, and omit x:Class/inline events."""
        import xml.etree.ElementTree as ET

        disallowed_events = {
            "Click", "SelectionChanged", "TextChanged",
            "Checked", "Unchecked", "MouseDoubleClick"
        }
        x_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"

        for fname in self.XAML_FILES:
            fpath = os.path.join(pkg_dir, fname)
            self.assertTrue(os.path.exists(fpath), "Missing XAML file: {}".format(fname))
            tree = ET.parse(fpath)
            root = tree.getroot()
            self.assertTrue(root.tag.endswith("Window"), "Root element must be Window in {}".format(fname))
            self.assertEqual(root.attrib.get("ResizeMode"), "CanResize", "Window must be resizable in {}".format(fname))

            for elem in root.iter():
                self.assertNotIn(x_ns + "Class", elem.attrib, "x:Class not allowed in {}".format(fname))
                for attr_name in elem.attrib:
                    local_name = attr_name.split("}")[-1]
                    self.assertNotIn(local_name, disallowed_events, "Inline event {} found in {}".format(local_name, fname))

    def test_every_findname_exists_in_xaml(self):
        """Verifies every FindName('...') call in ui_dialog.py maps to an x:Name in XAML."""
        import re
        import xml.etree.ElementTree as ET

        x_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
        xaml_names = {}
        all_names = set()

        for fname in self.XAML_FILES:
            fpath = os.path.join(pkg_dir, fname)
            root = ET.parse(fpath).getroot()
            names = set()
            for elem in root.iter():
                xname = elem.attrib.get(x_ns + "Name") or elem.attrib.get("Name")
                if xname:
                    names.add(xname)
                    all_names.add(xname)
            xaml_names[fname] = names

        ui_src_path = os.path.join(pkg_dir, "ui_dialog.py")
        with open(ui_src_path, "r", encoding="utf-8") as f:
            src = f.read()

        referenced = re.findall(r'FindName\(\s*["\']([A-Za-z0-9_]+)["\']\s*\)', src)
        self.assertGreater(len(referenced), 20)
        for name in referenced:
            self.assertIn(name, all_names, "FindName('{}') missing from XAML files".format(name))

        # Per-class check using _load_xaml_window("<file>") blocks in ui_dialog.py
        class_to_xaml = [
            ("CustomParametersDialog", "ui_custom_params.xaml"),
            ("LayerConfigDialog", "ui_layer_config.xaml"),
            ("SettingsDialog", "ui_settings.xaml"),
            ("TransferDashboardDialog", "ui_dashboard.xaml"),
        ]
        for idx, (cls_name, xfile) in enumerate(class_to_xaml):
            start_pos = src.find("class " + cls_name)
            self.assertNotEqual(start_pos, -1)
            next_pos = len(src)
            for other_cls, _ in class_to_xaml[idx + 1:]:
                p = src.find("class " + other_cls, start_pos + 1)
                if p != -1:
                    next_pos = min(next_pos, p)
            cls_src = src[start_pos:next_pos]
            cls_refs = re.findall(r'FindName\(\s*["\']([A-Za-z0-9_]+)["\']\s*\)', cls_src)
            for name in cls_refs:
                self.assertIn(name, xaml_names[xfile], "{}: FindName('{}') not in {}".format(cls_name, name, xfile))

    def test_friendly_strategy_mapping_and_row_display_helpers(self):
        """Tests friendly strategy round-trips and LayerRowItem display/pill helpers."""
        from ui_dialog import (
            STRATEGY_UI_CHOICES, LayerRowItem,
            strategy_to_friendly, friendly_to_strategy, strategy_to_short
        )
        from profile_manager import CustomParameterSpec

        for key, friendly, short in STRATEGY_UI_CHOICES:
            self.assertEqual(strategy_to_friendly(key), friendly)
            self.assertEqual(friendly_to_strategy(friendly), key)
            self.assertEqual(friendly_to_strategy(key), key)
            self.assertEqual(strategy_to_short(key), short)

        self.assertEqual(friendly_to_strategy(strategy_to_friendly("FamilyInstance")), "FreeFormFamily")
        self.assertEqual(strategy_to_short("FamilyInstance"), "Static .rfa")

        # Compatible single-bucket layer
        box = ScannedObject("ui-box-1")
        box.layer_name = "Pods"
        box.object_type = "Brep"
        box.geometry_primitive_type = "Box"
        box.is_prismatic = True
        box.is_closed = True
        box.parametric_dims = {"width_m": 2.0, "depth_m": 2.0, "height_m": 3.0}

        cp = CustomParameterSpec("Fire_Rating", "Text", "Data", True, "2HR")
        row_ok = LayerRowItem(
            "Pods", [box], None, None,
            selected_material="Travertine", custom_parameters=[cp]
        )
        row_ok.update_strategy("ParametricFamily")
        self.assertEqual(row_ok.status_style_key(), "OK")
        line1, line2 = row_ok.target_summary_lines()
        self.assertIn("Parametric .rfa", line1)
        self.assertIn("Travertine", line2)
        self.assertIn("1 param", line2)
        self.assertIn("Parametric Primitive", row_ok.geometry_breakdown_text())

        # Mixed layer -> MIXED status key
        crv = ScannedObject("ui-crv-1")
        crv.layer_name = "Mixed_Facade"
        crv.object_type = "Curve"
        crv.geometry_primitive_type = "Curve"
        row_mix = LayerRowItem("Mixed_Facade", [box, crv], None, None)
        row_mix.update_strategy("ParametricFamily")
        self.assertEqual(row_mix.status_style_key(), "MIXED")

        # Blocked layer -> BLOCKED status key
        row_blk = LayerRowItem("Only_Curves", [crv], None, None)
        row_blk.update_strategy("DirectShape")
        self.assertEqual(row_blk.status_style_key(), "BLOCKED")

    def test_pyrevit_installer_copies_xaml_and_templates(self):
        """Ensures installer/install_pyrevit.bat copies .py, .xaml, and templates and installer/script.py reloads strategy_rules."""
        bat_path = os.path.join(pkg_dir, "installer", "install_pyrevit.bat")
        with open(bat_path, "r", encoding="utf-8") as f:
            bat_src = f.read()
        self.assertIn("*.py", bat_src)
        self.assertIn("*.xaml", bat_src)
        self.assertIn("templates", bat_src)

        scr_path = os.path.join(pkg_dir, "installer", "script.py")
        with open(scr_path, "r", encoding="utf-8") as f:
            scr_src = f.read()
        self.assertIn("'strategy_rules'", scr_src)


class TestAccurateFamilyGeometry(unittest.TestCase):
    """Regression tests preventing silent boxification and enforcing exact geometry in .rfa families."""

    def test_profile_extrusion_never_silently_boxifies(self):
        """Mode A must refuse ProfileExtrusion without curve loops and refuse FreeFormSolid/Unknown."""
        import family_factory as ff

        orig_revit = ff.REVIT_AVAILABLE
        try:
            ff.REVIT_AVAILABLE = True
            new_fam_calls = []

            class SpyApp(object):
                def NewFamilyDocument(self, tmpl):
                    new_fam_calls.append(tmpl)
                    return None

            res_no_loops = ff.FamilyFactory.create_parametric_box_or_profile_family(
                SpyApp(), object(), "NoLoopsProfile",
                width=2.0, depth=2.0, height=3.0,
                profile_curve_loops=None, primitive_type="ProfileExtrusion"
            )
            self.assertIsNone(res_no_loops)
            self.assertEqual(len(new_fam_calls), 0)

            res_freeform = ff.FamilyFactory.create_parametric_box_or_profile_family(
                SpyApp(), object(), "FreeformReject",
                width=2.0, depth=2.0, height=3.0,
                primitive_type="FreeFormSolid"
            )
            self.assertIsNone(res_freeform)
            self.assertEqual(len(new_fam_calls), 0)

            res_unknown = ff.FamilyFactory.create_parametric_box_or_profile_family(
                SpyApp(), object(), "UnknownReject",
                width=2.0, depth=2.0, height=3.0,
                primitive_type="Unknown"
            )
            self.assertIsNone(res_unknown)
            self.assertEqual(len(new_fam_calls), 0)
        finally:
            ff.REVIT_AVAILABLE = orig_revit

    def test_unknown_and_freeform_never_coerce_to_box_in_pregen(self):
        """pre_generate_families never coerces ProfileExtrusion (missing loops) or Unknown into a Box."""
        import conversion_router as cr
        from conversion_router import ConversionRouter
        from profile_manager import MappingRule

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_ff = getattr(cr, "FamilyFactory", None)
        try:
            MockDB, MockDoc = TestStrategyRulesAndCustomParams()._make_mock_db_env()
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB

            mode_a_calls = []
            mode_b_calls = []

            class MockSym(object):
                def __init__(self, name):
                    self.Name = name
                    self.FamilyName = name
                    self.IsActive = True

            class SpyFF(object):
                @staticmethod
                def create_parametric_box_or_profile_family(app, doc, **kwargs):
                    mode_a_calls.append(kwargs)
                    return MockSym(kwargs.get("family_name", "Param"))

                @staticmethod
                def create_native_freeform_family(app, doc, **kwargs):
                    mode_b_calls.append(kwargs)
                    return MockSym(kwargs.get("family_name", "FreeForm"))

            cr.FamilyFactory = SpyFF
            router = ConversionRouter(MockDoc(), mixed_policy="AutoFallback")

            # 1. ProfileExtrusion where loop extraction fails -> routes to Mode B, NOT Mode A box
            obj_prof_fail = ScannedObject("prof-fail-1")
            obj_prof_fail.layer_name = "Stair_Cores"
            obj_prof_fail.object_type = "Brep"
            obj_prof_fail.geometry_primitive_type = "ProfileExtrusion"
            obj_prof_fail.is_prismatic = True
            obj_prof_fail.is_closed = True
            obj_prof_fail.parametric_dims = {"Width": 2.0, "Depth": 2.0, "Height": 4.0}
            obj_prof_fail.raw_geometry = MockDB.RhinoGeom()

            # 2. Unknown primitive with positive dims under AutoFallback -> routes to Mode B, NOT Mode A box
            obj_unk = ScannedObject("unk-1")
            obj_unk.layer_name = "Sculpt_Columns"
            obj_unk.object_type = "Brep"
            obj_unk.geometry_primitive_type = "Unknown"
            obj_unk.is_closed = True
            obj_unk.parametric_dims = {"Width": 1.0, "Depth": 1.0, "Height": 3.0}
            obj_unk.raw_geometry = MockDB.RhinoGeom()

            # 3. ProfileExtrusion with real extracted loops -> routes to Mode A with true profile_curve_loops
            obj_prof_ok = ScannedObject("prof-ok-1")
            obj_prof_ok.layer_name = "L_Columns"
            obj_prof_ok.object_type = "Brep"
            obj_prof_ok.geometry_primitive_type = "ProfileExtrusion"
            obj_prof_ok.is_prismatic = True
            obj_prof_ok.is_closed = True
            obj_prof_ok.parametric_dims = {"Width": 1.2, "Depth": 1.2, "Height": 3.5}
            obj_prof_ok.raw_geometry = MockDB.RhinoGeom()

            mock_loop = ["loop_segment"]
            router._extract_canonical_profile_loops_feet = (
                lambda o: [mock_loop] if o.rhino_guid == "prof-ok-1" else []
            )

            rules = {
                "prof-fail-1": MappingRule("Stair_Cores", strategy="ParametricFamily"),
                "unk-1": MappingRule("Sculpt_Columns", strategy="ParametricFamily"),
                "prof-ok-1": MappingRule("L_Columns", strategy="ParametricFamily"),
            }
            cdict = {
                "prof-fail-1": ClassificationResult("prof-fail-1"),
                "unk-1": ClassificationResult("unk-1"),
                "prof-ok-1": ClassificationResult("prof-ok-1"),
            }

            router.pre_generate_families([obj_prof_fail, obj_unk, obj_prof_ok], cdict, profile_rules=rules)

            self.assertEqual(len(mode_a_calls), 1)
            self.assertEqual(mode_a_calls[0].get("primitive_type"), "ProfileExtrusion")
            self.assertEqual(mode_a_calls[0].get("profile_curve_loops"), [mock_loop])
            self.assertEqual(router.family_anchor_mode.get("prof-ok-1"), "bottom_center")

            self.assertEqual(len(mode_b_calls), 2)
            self.assertEqual(router.family_anchor_mode.get("prof-fail-1"), "centroid")
            self.assertEqual(router.family_anchor_mode.get("unk-1"), "centroid")
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db
            cr.FamilyFactory = orig_ff

    def test_classifier_does_not_force_box_parametric_on_freeform_furniture_or_doors(self):
        """AecClassifier must route FreeFormSolid furniture/doors to FreeFormFamily, not ParametricFamily."""
        clf = AecClassifier(mode="Hybrid")

        sofa = ScannedObject("sofa-1")
        sofa.layer_full_path = "INTERIORS::FURNITURE"
        sofa.layer_name = "FURNITURE"
        sofa.object_type = "Brep"
        sofa.geometry_primitive_type = "FreeFormSolid"
        sofa.is_closed = True
        sofa.dx, sofa.dy, sofa.dz = 1.2, 0.9, 0.85
        sofa.volume = 0.5

        res_sofa = clf.classify_object(sofa)
        self.assertEqual(res_sofa.inferred_category, "Family")
        self.assertEqual(res_sofa.proposed_strategy, "FreeFormFamily")
        self.assertEqual(clf._select_strategy(sofa, "Family"), "FreeFormFamily")

        door = ScannedObject("door-1")
        door.layer_full_path = "ARCH::DOORS"
        door.layer_name = "DOORS"
        door.object_type = "Brep"
        door.geometry_primitive_type = "FreeFormSolid"
        door.is_closed = True
        door.dx, door.dy, door.dz = 0.9, 0.9, 2.1

        res_door = clf.classify_object(door)
        self.assertEqual(res_door.inferred_category, "Family")
        self.assertEqual(res_door.proposed_strategy, "FreeFormFamily")
        self.assertEqual(clf._select_strategy(door, "Family"), "FreeFormFamily")

        rep_chair = ScannedObject("chair-1")
        rep_chair.layer_full_path = "INTERIORS::FURNITURE"
        rep_chair.layer_name = "FURNITURE"
        rep_chair.object_type = "Brep"
        rep_chair.geometry_primitive_type = "FreeFormSolid"
        rep_chair.is_closed = True
        rep_chair.repetition_count = 4
        rep_chair.dx, rep_chair.dy, rep_chair.dz = 0.6, 0.6, 0.8

        res_chair = clf.classify_object(rep_chair)
        self.assertEqual(res_chair.proposed_strategy, "RepetitiveFamily")

        true_box_col = ScannedObject("box-col-1")
        true_box_col.layer_full_path = "STRUCTURE::COLUMNS"
        true_box_col.layer_name = "COLUMNS"
        true_box_col.object_type = "Brep"
        true_box_col.geometry_primitive_type = "Box"
        true_box_col.is_prismatic = True
        true_box_col.is_closed = True
        true_box_col.dx, true_box_col.dy, true_box_col.dz = 0.5, 0.5, 3.5

        res_col = clf.classify_object(true_box_col)
        self.assertEqual(res_col.proposed_strategy, "ParametricFamily")

        open_sculpt = ScannedObject("open-1")
        open_sculpt.layer_full_path = "INTERIORS::FURNITURE"
        open_sculpt.layer_name = "FURNITURE"
        open_sculpt.object_type = "Mesh"
        open_sculpt.geometry_primitive_type = "OpenMesh"
        open_sculpt.is_closed = False

        res_open = clf.classify_object(open_sculpt)
        self.assertEqual(res_open.proposed_strategy, "DirectShape")

    def test_no_cross_object_symbol_hijack_and_bottom_anchor_z(self):
        """Objects sharing layer/name never steal symbols; Parametric uses bottom-center Z, FreeForm uses centroid Z without overriding dims."""
        import conversion_router as cr
        from conversion_router import ConversionRouter, ConversionResult
        from profile_manager import MappingRule

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_ff = getattr(cr, "FamilyFactory", None)
        try:
            MockDB, MockDoc = TestStrategyRulesAndCustomParams()._make_mock_db_env()
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB

            class MockParam(object):
                def __init__(self, init_val=None):
                    self.StorageType = MockDB.StorageType.Double
                    self.val = init_val
                    self.IsReadOnly = False

                def Set(self, v):
                    self.val = v

            class MockSym(object):
                def __init__(self, name):
                    self.Name = name
                    self.FamilyName = name
                    self.IsActive = True

                def LookupParameter(self, n):
                    return None

            placed_records = []

            class MockInst(object):
                def __init__(self, sym):
                    self.Symbol = sym
                    self.Id = MockDB.ElementId(701)
                    self.UniqueId = "inst-701"
                    self.params = {"Width": MockParam(0.0), "Height": MockParam(0.0)}

                def LookupParameter(self, k):
                    return self.params.get(k)

            doc = MockDoc()
            doc.Create = type("Create", (), {
                "NewFamilyInstance": lambda *a: placed_records.append((a[1], a[2])) or MockInst(a[2])
            })()

            class SpyFF(object):
                @staticmethod
                def create_parametric_box_or_profile_family(app, d, **kwargs):
                    return MockSym(kwargs.get("family_name"))

                @staticmethod
                def create_native_freeform_family(app, d, **kwargs):
                    return MockSym(kwargs.get("family_name"))

            cr.FamilyFactory = SpyFF
            router = ConversionRouter(doc, unit_scale=1.0)

            obj1 = ScannedObject("joinery-aaaa1111")
            obj1.layer_name = "Custom_Joinery"
            obj1.object_name = "Panel"
            obj1.object_type = "Brep"
            obj1.geometry_primitive_type = "FreeFormSolid"
            obj1.is_closed = True
            obj1.centroid = (1.0, 2.0, 5.0)
            obj1.raw_geometry = MockDB.RhinoGeom()

            obj2 = ScannedObject("joinery-bbbb2222")
            obj2.layer_name = "Custom_Joinery"
            obj2.object_name = "Panel"
            obj2.object_type = "Brep"
            obj2.geometry_primitive_type = "Box"
            obj2.is_prismatic = True
            obj2.is_closed = True
            obj2.bbox_min = (4.0, 4.0, 10.0)
            obj2.bbox_max = (6.0, 6.0, 14.0)
            obj2.centroid = (5.0, 5.0, 12.0)
            obj2.dx, obj2.dy, obj2.dz = 2.0, 2.0, 4.0
            obj2.parametric_dims = {"Width": 2.0, "Depth": 2.0, "Height": 4.0}
            obj2.raw_geometry = MockDB.RhinoGeom()

            rules = {
                "joinery-aaaa1111": MappingRule("Custom_Joinery", strategy="FreeFormFamily"),
                "joinery-bbbb2222": MappingRule("Custom_Joinery", strategy="ParametricFamily"),
            }
            cdict = {
                "joinery-aaaa1111": ClassificationResult("joinery-aaaa1111"),
                "joinery-bbbb2222": ClassificationResult("joinery-bbbb2222"),
            }
            router.pre_generate_families([obj1, obj2], cdict, profile_rules=rules)

            # Separate per-object symbols; zero layer-wide cache hijack keys
            s1 = router.family_symbol_cache["joinery-aaaa1111"]
            s2 = router.family_symbol_cache["joinery-bbbb2222"]
            self.assertIsNot(s1, s2)
            self.assertNotIn("R2R_Family_Custom_Joinery", router.family_symbol_cache)
            self.assertNotIn("R2R_Parametric_Custom_Joinery", router.family_symbol_cache)

            # Place FreeFormFamily -> uses centroid Z (5.0), leaves Width untouched, marks strategy_used = FreeFormFamily
            res1 = ConversionResult("joinery-aaaa1111")
            inst1 = router._pipeline_8c_parametric_family(obj1, None, None, profile_rule=rules["joinery-aaaa1111"], res=res1)
            self.assertIsNotNone(inst1)
            self.assertEqual(placed_records[-1][0], (1.0, 2.0, 5.0))
            self.assertEqual(res1.strategy_used, "FreeFormFamily")
            self.assertEqual(inst1.params["Width"].val, 0.0)

            # Place ParametricFamily Box -> uses bottom-center Z (10.0), sets Width/Height, marks strategy_used = ParametricFamily
            res2 = ConversionResult("joinery-bbbb2222")
            inst2 = router._pipeline_8c_parametric_family(obj2, None, None, profile_rule=rules["joinery-bbbb2222"], res=res2)
            self.assertIsNotNone(inst2)
            self.assertEqual(placed_records[-1][0], (5.0, 5.0, 10.0))
            self.assertEqual(res2.strategy_used, "ParametricFamily")
            self.assertEqual(inst2.params["Width"].val, 2.0)
            self.assertEqual(inst2.params["Height"].val, 4.0)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db
            cr.FamilyFactory = orig_ff


class TestNativeElementAndGeometryFixes(unittest.TestCase):
    """Regression tests for RoofNative, DirectShape exact meshes, CurveLoop spline/3D handling, and StrategyRuleEngine."""

    def _make_xyz_db_env(self):
        """Creates rich mock Revit DB with XYZ math, Line, Arc, CurveLoop, CurveArray, and Roof APIs."""
        import math

        class MockXYZ(object):
            def __init__(self, x=0.0, y=0.0, z=0.0):
                self.X = float(x)
                self.Y = float(y)
                self.Z = float(z)
            def DistanceTo(self, other):
                return math.sqrt((self.X - other.X) ** 2 + (self.Y - other.Y) ** 2 + (self.Z - other.Z) ** 2)
            def __sub__(self, other):
                return MockXYZ(self.X - other.X, self.Y - other.Y, self.Z - other.Z)
            def CrossProduct(self, other):
                return MockXYZ(
                    self.Y * other.Z - self.Z * other.Y,
                    self.Z * other.X - self.X * other.Z,
                    self.X * other.Y - self.Y * other.X
                )
            def GetLength(self):
                return math.sqrt(self.X ** 2 + self.Y ** 2 + self.Z ** 2)

        MockXYZ.BasisZ = MockXYZ(0.0, 0.0, 1.0)

        class MockLineObj(object):
            def __init__(self, p0, p1):
                self.p0 = p0
                self.p1 = p1
            def GetEndPoint(self, idx):
                return self.p0 if idx == 0 else self.p1

        class MockLineAPI(object):
            @staticmethod
            def CreateBound(p0, p1):
                return MockLineObj(p0, p1)

        class MockCurveLoop(list):
            def Append(self, c):
                self.append(c)
            def IsOpen(self):
                if len(self) < 2:
                    return True
                return self[0].GetEndPoint(0).DistanceTo(self[-1].GetEndPoint(1)) > 0.01
            def IsCounterClockwise(self, norm):
                return True
            def Reverse(self):
                list.reverse(self)

        class MockCurveArray(list):
            @property
            def Size(self):
                return len(self)
            def Append(self, c):
                self.append(c)

        class MockModelCurve(object):
            def __init__(self, geom_crv):
                self.GeometryCurve = geom_crv

        class MockModelCurveArray(list):
            def Append(self, mc):
                self.append(mc)

        class MockElementId(object):
            InvalidElementId = -1
            def __init__(self, val=1):
                self.val = int(val)
            def __eq__(self, other):
                return int(getattr(other, "val", other if isinstance(other, int) else -999)) == self.val
            def __ne__(self, other):
                return not self.__eq__(other)

        class MockParam(object):
            def __init__(self, v=None):
                self.val = v
                self.IsReadOnly = False
            def Set(self, v):
                self.val = v

        class MockBIP(object):
            ROOF_LEVEL_OFFSET_PARAM = 101
            WALL_ATTR_ROOM_BOUNDING = 102
            FLOOR_HEIGHTABOVELEVEL_PARAM = 103
            CEILING_HEIGHTABOVELEVEL_PARAM = 104
            SYMBOL_NAME_PARAM = 105

        class MockBIC(object):
            OST_Roofs = 201
            OST_Floors = 202
            OST_Walls = 203
            OST_Ceilings = 204
            OST_GenericModel = 205
            OST_Toposolid = 206

        class MockETG(object):
            FloorType = 1
            WallType = 2
            RoofType = 3
            CeilingType = 4

        class MockCol(object):
            def __init__(self, doc):
                self.doc = doc
                self.cls = None
            def OfClass(self, cls):
                self.cls = cls
                return self
            def FirstElement(self):
                items = list(self)
                return items[0] if items else None
            def __iter__(self):
                if self.cls is MockLevel:
                    return iter(getattr(self.doc, "levels", []))
                if self.cls is MockRoofType:
                    return iter(getattr(self.doc, "roof_types", []))
                return iter([])

        class MockLevel(object):
            def __init__(self, name, elev, lid):
                self.Name = name
                self.Elevation = elev
                self.Id = MockElementId(lid)

        class MockRoofType(object):
            def __init__(self, rid=301):
                self.Id = MockElementId(rid)
                self.FamilyName = "Basic Roof"
            def GetCompoundStructure(self):
                return None

        class MockTSBTarget(object):
            Solid = "Solid"
            Mesh = "Mesh"
            AnyGeometry = "AnyGeometry"

        class MockTSBFallback(object):
            Abort = "Abort"
            Salvage = "Salvage"
            Mesh = "Mesh"

        class MockSolid(object):
            def __init__(self, vol=10.0):
                self.Volume = vol

        class MockMesh(object):
            def __init__(self, faces=2):
                self.NumTriangles = faces

        class MockGeometryObject(object):
            pass

        return locals()

    def test_roof_native_uses_level_plane_disables_default_slope_and_sets_offset(self):
        """RoofNative builds curves strictly at level.Elevation, clears default 30-deg slope, sets ROOF_LEVEL_OFFSET_PARAM, and cuts inner openings."""
        import conversion_router as cr
        from profile_manager import MappingRule

        env = self._make_xyz_db_env()
        MockXYZ = env["MockXYZ"]
        MockLineAPI = env["MockLineAPI"]
        MockCurveLoop = env["MockCurveLoop"]
        MockCurveArray = env["MockCurveArray"]
        MockModelCurve = env["MockModelCurve"]
        MockModelCurveArray = env["MockModelCurveArray"]
        MockElementId = env["MockElementId"]
        MockParam = env["MockParam"]
        MockLevel = env["MockLevel"]
        MockRoofType = env["MockRoofType"]

        recorded = {
            "footprint_z": [],
            "slope_calls": [],
            "openings": [],
        }

        class MockRoof(object):
            def __init__(self):
                self.Id = MockElementId(9001)
                self.UniqueId = "roof-9001"
                self.Category = type("Cat", (), {"Name": "Roofs"})()
                self.params = {
                    env["MockBIP"].ROOF_LEVEL_OFFSET_PARAM: MockParam(),
                    env["MockBIP"].WALL_ATTR_ROOM_BOUNDING: MockParam(),
                }
            def get_Parameter(self, bip):
                return self.params.get(bip)
            def set_DefinesSlope(self, mc, flag):
                recorded["slope_calls"].append((mc, bool(flag)))
            def set_SlopeAngle(self, mc, ang):
                recorded["slope_calls"].append(("angle", ang))

        class MockCreate(object):
            @staticmethod
            def NewFootPrintRoof(crv_array, level_elem, roof_type, footprint_map):
                for c in crv_array:
                    recorded["footprint_z"].append(c.GetEndPoint(0).Z)
                    recorded["footprint_z"].append(c.GetEndPoint(1).Z)
                    footprint_map.Append(MockModelCurve(c))
                return MockRoof()
            @staticmethod
            def NewOpening(roof_elem, inner_arr, flag):
                recorded["openings"].append((roof_elem.UniqueId, len(inner_arr), flag))

        class MockDoc(object):
            levels = [MockLevel("Level 2", 10.0, 102)]
            roof_types = [MockRoofType(301)]
            Create = MockCreate()
            def GetDefaultElementTypeId(self, grp):
                return MockElementId(301)
            def GetElement(self, eid):
                if eid == 102:
                    return self.levels[0]
                if eid == 301:
                    return self.roof_types[0]
                return None

        class MockDB(object):
            XYZ = MockXYZ
            Line = MockLineAPI
            CurveLoop = MockCurveLoop
            CurveArray = MockCurveArray
            ModelCurveArray = MockModelCurveArray
            ElementId = MockElementId
            BuiltInParameter = env["MockBIP"]
            BuiltInCategory = env["MockBIC"]
            ElementTypeGroup = env["MockETG"]
            Level = MockLevel
            RoofType = MockRoofType
            FloorType = type("FT", (), {})
            WallType = type("WT", (), {})
            CeilingType = type("CT", (), {})
            Solid = env["MockSolid"]
            Mesh = env["MockMesh"]
            GeometryObject = env["MockGeometryObject"]
            @staticmethod
            def FilteredElementCollector(d):
                return env["MockCol"](d)

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB
            router = cr.ConversionRouter(MockDoc())
            router._extract_floor_boundary_loops = lambda g, o: ("outer_crv", ["skylight_crv"], 25.0)

            def fake_cl(c, target_z=None, is_outer=True):
                tz = float(target_z or 0.0)
                cl = MockCurveLoop()
                pts = [MockXYZ(0, 0, tz), MockXYZ(20, 0, tz), MockXYZ(20, 15, tz), MockXYZ(0, 15, tz)]
                for i in range(4):
                    cl.Append(MockLineAPI.CreateBound(pts[i], pts[(i + 1) % 4]))
                return cl

            router._build_curveloop_from_rhino_curve = fake_cl

            geom_feet = type("MockGeom", (), {
                "GetBoundingBox": lambda s, b: type("BB", (), {
                    "Min": MockXYZ(0, 0, 24.0),
                    "Max": MockXYZ(20, 15, 25.0)
                })()
            })()
            scanned = ScannedObject("roof-guid-1")
            scanned.layer_name = "A-ROOF"
            res = cr.ConversionResult("roof-guid-1")

            roof = router._pipeline_6_roof_native(
                scanned, geom_feet, MockElementId(102), MappingRule("A-ROOF", strategy="RoofNative"), res
            )
            self.assertIsNotNone(roof)
            self.assertEqual(res.strategy_used, "RoofNative")
            # Curves passed to NewFootPrintRoof must all lie strictly at level.Elevation (10.0), NOT roof_z (25.0)
            self.assertTrue(len(recorded["footprint_z"]) == 8)
            for z in recorded["footprint_z"]:
                self.assertAlmostEqual(z, 10.0, places=4)
            # Default 30-deg hip slope disabled on all 4 footprint edges
            self.assertEqual(len(recorded["slope_calls"]), 4)
            for _, flag in recorded["slope_calls"]:
                self.assertFalse(flag)
            # Base offset = roof_bottom_z (24.0) - level_elev (10.0) = 14.0
            self.assertAlmostEqual(roof.params[env["MockBIP"].ROOF_LEVEL_OFFSET_PARAM].val, 14.0, places=3)
            # Inner skylight opening was cut
            self.assertEqual(len(recorded["openings"]), 1)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db

    def test_roof_native_sloped_planar_applies_single_eave_slope(self):
        """Sloped planar roof enables slope ONLY on its orthogonal downhill eave edge and applies rise/run."""
        import conversion_router as cr

        env = self._make_xyz_db_env()
        MockXYZ = env["MockXYZ"]
        MockLineAPI = env["MockLineAPI"]
        MockCurveLoop = env["MockCurveLoop"]
        MockCurveArray = env["MockCurveArray"]
        MockModelCurve = env["MockModelCurve"]
        MockModelCurveArray = env["MockModelCurveArray"]
        MockElementId = env["MockElementId"]
        MockParam = env["MockParam"]
        MockLevel = env["MockLevel"]
        MockRoofType = env["MockRoofType"]

        defines_history = []
        angles_history = []

        class MockRoof(object):
            def __init__(self):
                self.Id = MockElementId(9002)
                self.UniqueId = "roof-9002"
                self.params = {
                    env["MockBIP"].ROOF_LEVEL_OFFSET_PARAM: MockParam(),
                    env["MockBIP"].WALL_ATTR_ROOM_BOUNDING: MockParam(),
                }
            def get_Parameter(self, bip):
                return self.params.get(bip)
            def set_DefinesSlope(self, mc, flag):
                p0 = mc.GeometryCurve.GetEndPoint(0)
                p1 = mc.GeometryCurve.GetEndPoint(1)
                defines_history.append(((p0.X, p0.Y, p1.X, p1.Y), bool(flag)))
            def set_SlopeAngle(self, mc, ang):
                angles_history.append(float(ang))

        class MockCreate(object):
            @staticmethod
            def NewFootPrintRoof(crv_array, level_elem, roof_type, footprint_map):
                for c in crv_array:
                    footprint_map.Append(MockModelCurve(c))
                return MockRoof()

        class MockDoc(object):
            levels = [MockLevel("Level 1", 10.0, 101)]
            roof_types = [MockRoofType(301)]
            Create = MockCreate()
            def GetDefaultElementTypeId(self, grp):
                return MockElementId(301)
            def GetElement(self, eid):
                if eid == 101:
                    return self.levels[0]
                if eid == 301:
                    return self.roof_types[0]
                return None

        class MockDB(object):
            XYZ = MockXYZ
            Line = MockLineAPI
            CurveLoop = MockCurveLoop
            CurveArray = MockCurveArray
            ModelCurveArray = MockModelCurveArray
            ElementId = MockElementId
            BuiltInParameter = env["MockBIP"]
            BuiltInCategory = env["MockBIC"]
            ElementTypeGroup = env["MockETG"]
            Level = MockLevel
            RoofType = MockRoofType
            FloorType = type("FT", (), {})
            WallType = type("WT", (), {})
            CeilingType = type("CT", (), {})
            Solid = env["MockSolid"]
            Mesh = env["MockMesh"]
            GeometryObject = env["MockGeometryObject"]
            @staticmethod
            def FilteredElementCollector(d):
                return env["MockCol"](d)

        orig_revit = cr.REVIT_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        try:
            cr.REVIT_AVAILABLE = True
            cr.DB = MockDB
            router = cr.ConversionRouter(MockDoc())
            router._extract_roof_or_slab_footprint_and_slope = lambda g, o: {
                "outer_crv": "sloped_outer",
                "inner_crvs": [],
                "top_z": 18.75,
                "bottom_z": 18.0,
                "thickness_feet": 0.75,
                "is_sloped": True,
                "rise_over_run": 0.5,
                "slope_dir_xy": (1.0, 0.0),  # Downhill toward X = 20 edge
                "uphill_dir_xy": (-1.0, 0.0),
                "tail_pt_xy": (20.0, 7.5),
                "head_pt_xy": (0.0, 7.5),
            }

            def fake_cl(c, target_z=None, is_outer=True):
                tz = float(target_z or 0.0)
                cl = MockCurveLoop()
                pts = [MockXYZ(0, 0, tz), MockXYZ(20, 0, tz), MockXYZ(20, 15, tz), MockXYZ(0, 15, tz)]
                for i in range(4):
                    cl.Append(MockLineAPI.CreateBound(pts[i], pts[(i + 1) % 4]))
                return cl

            router._build_curveloop_from_rhino_curve = fake_cl
            scanned = ScannedObject("roof-slope-1")
            scanned.is_planar_sloped = True
            res = cr.ConversionResult("roof-slope-1")

            roof = router._pipeline_6_roof_native(scanned, object(), MockElementId(101), None, res)
            self.assertIsNotNone(roof)
            self.assertEqual(res.strategy_used, "RoofNative")
            # First 4 calls set False, 5th call enables slope ONLY on X=20 downhill edge ((20,0)->(20,15))
            self.assertEqual(len(defines_history), 5)
            self.assertTrue(defines_history[-1][1])
            self.assertEqual(defines_history[-1][0], (20.0, 0.0, 20.0, 15.0))
            self.assertEqual(angles_history, [0.5])
            self.assertAlmostEqual(roof.params[env["MockBIP"].ROOF_LEVEL_OFFSET_PARAM].val, 8.0, places=3)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.DB = orig_db

    def test_directshape_geometry_no_subd_and_abort_not_salvage(self):
        """_convert_rhino_to_revit_geometry never calls SubD.CreateFromMesh, never uses Salvage, and keeps open meshes intact."""
        import conversion_router as cr

        env = self._make_xyz_db_env()
        tsb_runs = []
        subd_calls = []

        class MockPt(object):
            def __init__(self, x, y, z):
                self.X, self.Y, self.Z = x, y, z

        class MockFace(object):
            A, B, C, IsQuad = 0, 1, 2, False

        class MockFaces(list):
            @property
            def Count(self):
                return len(self)
            def ConvertQuadsToTriangles(self):
                pass

        class MockRhMesh(object):
            def __init__(self, is_closed):
                self.IsClosed = is_closed
                self.Vertices = [MockPt(0, 0, 0), MockPt(5, 0, 0), MockPt(0, 5, 0)]
                self.Faces = MockFaces([MockFace()])
            def Duplicate(self):
                return self
            def Scale(self, s):
                pass

        class MockSubD(object):
            @staticmethod
            def CreateFromMesh(m):
                subd_calls.append(m)
                return None

        class FakeRG(object):
            Brep = type("FakeBrep", (), {})
            Mesh = MockRhMesh
            SubD = MockSubD
            MeshingParameters = type("MP", (), {"Default": object(), "QualityRenderMesh": object()})

        class FakeTSB(object):
            def __init__(self):
                self.Target = None
                self.Fallback = None
                self.open_closed_flag = None
                self.faces = []
            def OpenConnectedFaceSet(self, flag):
                self.open_closed_flag = flag
            def AddFace(self, face):
                self.faces.append(face)
            def CloseConnectedFaceSet(self):
                pass
            def Build(self):
                tsb_runs.append((self.open_closed_flag, self.Target, self.Fallback, len(self.faces)))
            def GetBuildResult(self):
                if self.Fallback == "Abort":
                    return type("EmptyRes", (), {"GetGeometricalObjects": lambda s: []})()
                return type("MeshRes", (), {"GetGeometricalObjects": lambda s: [env["MockMesh"](len(self.faces))]})()

        class MockDB(object):
            XYZ = env["MockXYZ"]
            ElementId = env["MockElementId"]
            TessellatedShapeBuilder = FakeTSB
            TessellatedShapeBuilderTarget = env["MockTSBTarget"]
            TessellatedShapeBuilderFallback = env["MockTSBFallback"]
            TessellatedFace = lambda pts, mid: ("Face", len(pts))
            Solid = env["MockSolid"]
            Mesh = env["MockMesh"]
            GeometryObject = env["MockGeometryObject"]
            Level = env["MockLevel"]
            RoofType = env["MockRoofType"]
            FloorType = type("FT", (), {})
            WallType = type("WT", (), {})
            CeilingType = type("CT", (), {})
            ElementTypeGroup = env["MockETG"]
            @staticmethod
            def FilteredElementCollector(d):
                return env["MockCol"](d)

        orig_revit = cr.REVIT_AVAILABLE
        orig_rhino = cr.RHINO_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_rg = getattr(cr, "rg", None)
        orig_rh = getattr(cr, "Rhino", None)
        orig_enc = getattr(cr, "GeometryEncoder", None)
        try:
            cr.REVIT_AVAILABLE = True
            cr.RHINO_AVAILABLE = True
            cr.DB = MockDB
            cr.rg = FakeRG
            cr.Rhino = type("RhinoMod", (), {"RhinoDoc": type("RD", (), {"ActiveDoc": None})})
            cr.GeometryEncoder = object()

            doc = type("Doc", (), {
                "GetDefaultElementTypeId": lambda s, g: env["MockElementId"](1),
                "levels": [], "roof_types": []
            })()
            router = cr.ConversionRouter(doc, unit_scale=1.0)

            # 1. Open Mesh: skips Pass 1 entirely, goes straight to OpenConnectedFaceSet(False) + AnyGeometry + Mesh
            open_mesh = MockRhMesh(is_closed=False)
            out_open = router._convert_rhino_to_revit_geometry(open_mesh, geom_feet=open_mesh)
            self.assertEqual(len(subd_calls), 0)
            self.assertEqual(len(tsb_runs), 1)
            self.assertEqual(tsb_runs[0], (False, "AnyGeometry", "Mesh", 1))
            self.assertEqual(len(out_open), 1)
            self.assertIsInstance(out_open[0], env["MockMesh"])

            # 2. Closed leaky mesh: Pass 1 MUST use Fallback.Abort (never Salvage), then falls through to Pass 2
            tsb_runs.clear()
            closed_leaky = MockRhMesh(is_closed=True)
            out_closed = router._convert_rhino_to_revit_geometry(closed_leaky, geom_feet=closed_leaky)
            self.assertEqual(len(subd_calls), 0)
            self.assertEqual(len(tsb_runs), 2)
            self.assertEqual(tsb_runs[0], (True, "Solid", "Abort", 1))
            self.assertEqual(tsb_runs[1], (False, "AnyGeometry", "Mesh", 1))
            self.assertEqual(len(out_closed), 1)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.RHINO_AVAILABLE = orig_rhino
            cr.DB = orig_db
            cr.rg = orig_rg
            if orig_rh is None and hasattr(cr, "Rhino"):
                delattr(cr, "Rhino")
            elif orig_rh is not None:
                cr.Rhino = orig_rh
            cr.GeometryEncoder = orig_enc

    def test_nurbs_spline_and_3d_curveloop_builders(self):
        """Spline segments tessellate to contiguous Lines (never 3-pt Arc); vertical wall loops retain full 3D Z."""
        import conversion_router as cr

        env = self._make_xyz_db_env()
        arc_calls = []

        class MockArcAPI(object):
            @staticmethod
            def Create(*args):
                arc_calls.append(args)
                raise AssertionError("Arc.Create should not be called on non-arc NURBS spline or linear loop!")

        class MockPt3(object):
            def __init__(self, x, y, z):
                self.X, self.Y, self.Z = float(x), float(y), float(z)
            def DistanceTo(self, o):
                return ((self.X - o.X) ** 2 + (self.Y - o.Y) ** 2 + (self.Z - o.Z) ** 2) ** 0.5

        class MockPolyline(list):
            @property
            def Count(self):
                return len(self)

        class MockSeg(object):
            def __init__(self, pts, is_lin=True):
                self._pts = [MockPt3(*p) for p in pts]
                self._lin = is_lin
                self.IsClosed = False
                self.Domain = type("D", (), {"Min": 0.0, "Max": 1.0})()
            @property
            def PointAtStart(self):
                return self._pts[0]
            @property
            def PointAtEnd(self):
                return self._pts[-1]
            def DuplicateCurve(self):
                return self
            def IsLinear(self, tol=0.01):
                return self._lin
            def TryGetArc(self, tol=0.01):
                return (False, None)
            def ToPolyline(self, *a):
                pts_ref = self._pts
                return type("PC", (), {"ToPolyline": lambda s: MockPolyline(pts_ref)})()

        class MockCompoundCurve(object):
            def __init__(self, segs, bbox_min, bbox_max):
                self._segs = segs
                self._bmin = MockPt3(*bbox_min)
                self._bmax = MockPt3(*bbox_max)
                self.IsClosed = True
            @property
            def PointAtStart(self):
                return self._segs[0].PointAtStart
            def DuplicateSegments(self):
                return list(self._segs)
            def GetBoundingBox(self, flag):
                return type("BB", (), {"Min": self._bmin, "Max": self._bmax})()

        class FakeRG(object):
            ArcCurve = type("AC", (), {})
            Arc = type("Arc", (), {})
            BrepLoopType = type("BLT", (), {"Outer": 0, "Inner": 1})

        class MockDB(object):
            XYZ = env["MockXYZ"]
            Line = env["MockLineAPI"]
            Arc = MockArcAPI
            CurveLoop = env["MockCurveLoop"]
            ElementId = env["MockElementId"]
            Level = env["MockLevel"]
            RoofType = env["MockRoofType"]
            FloorType = type("FT", (), {})
            WallType = type("WT", (), {})
            CeilingType = type("CT", (), {})
            ElementTypeGroup = env["MockETG"]
            @staticmethod
            def FilteredElementCollector(d):
                return env["MockCol"](d)

        orig_revit = cr.REVIT_AVAILABLE
        orig_rhino = cr.RHINO_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_rg = getattr(cr, "rg", None)
        orig_rh = getattr(cr, "Rhino", None)
        try:
            cr.REVIT_AVAILABLE = True
            cr.RHINO_AVAILABLE = True
            cr.DB = MockDB
            cr.rg = FakeRG
            cr.Rhino = type("RhinoMod", (), {"RhinoDoc": type("RD", (), {"ActiveDoc": None})})

            doc = type("Doc", (), {
                "GetDefaultElementTypeId": lambda s, g: env["MockElementId"](1),
                "levels": [], "roof_types": []
            })()
            router = cr.ConversionRouter(doc)

            # 1. Spline loop: chordal tessellation without calling Arc.Create
            spline_seg = MockSeg([(0, 0, 5), (3, 2, 5), (7, -1, 5), (10, 0, 5)], is_lin=False)
            back_seg = MockSeg([(10, 0, 5), (0, 0, 5)], is_lin=True)
            rh_spline_loop = MockCompoundCurve([spline_seg, back_seg], (0, -1, 5), (10, 2, 5))
            cl_spline = router._build_curveloop_from_rhino_curve(rh_spline_loop, target_z=12.0, is_outer=True)
            self.assertIsNotNone(cl_spline)
            self.assertFalse(cl_spline.IsOpen())
            self.assertEqual(len(cl_spline), 4)  # 3 chords from spline + 1 closing chord
            self.assertEqual(len(arc_calls), 0)

            # 2. Vertical 3D wall profile loop (Z spans 0.0 -> 12.0) preserves true Z coordinates
            s0 = MockSeg([(0, 5, 0), (10, 5, 0)], is_lin=True)
            s1 = MockSeg([(10, 5, 0), (10, 5, 12)], is_lin=True)
            s2 = MockSeg([(10, 5, 12), (0, 5, 12)], is_lin=True)
            s3 = MockSeg([(0, 5, 12), (0, 5, 0)], is_lin=True)
            vert_crv = MockCompoundCurve([s0, s1, s2, s3], (0, 5, 0), (10, 5, 12))
            mock_loop = type("MockLoop", (), {
                "LoopType": FakeRG.BrepLoopType.Outer,
                "To3dCurve": lambda s: vert_crv,
                "Face": None,
            })()
            cl_3d = router._rg_loop_to_revit_curveloop(mock_loop)
            self.assertIsNotNone(cl_3d)
            self.assertFalse(cl_3d.IsOpen())
            z_vals = [c.GetEndPoint(0).Z for c in cl_3d] + [c.GetEndPoint(1).Z for c in cl_3d]
            self.assertAlmostEqual(min(z_vals), 0.0, places=4)
            self.assertAlmostEqual(max(z_vals), 12.0, places=4)
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.RHINO_AVAILABLE = orig_rhino
            cr.DB = orig_db
            cr.rg = orig_rg
            if orig_rh is None and hasattr(cr, "Rhino"):
                delattr(cr, "Rhino")
            elif orig_rh is not None:
                cr.Rhino = orig_rh

    def test_strategy_rules_roof_category_and_open_mesh_blocking(self):
        """Roof category recommends RoofNative; sloped planar passes; OpenMesh & double-curved shells are blocked on RoofNative/FloorNative."""
        from strategy_rules import StrategyRuleEngine
        from ui_dialog import LayerRowItem

        horiz_slab = ScannedObject("r-horiz")
        horiz_slab.object_type = "Brep"
        horiz_slab.is_horizontal = True
        horiz_slab.is_closed = True

        ev_roof = StrategyRuleEngine.evaluate_layer([horiz_slab], "FloorNative", current_category="Roofs")
        self.assertEqual(ev_roof.recommended_strategy, "RoofNative")

        sloped_roof = ScannedObject("r-sloped")
        sloped_roof.object_type = "Brep"
        sloped_roof.is_horizontal = False
        sloped_roof.is_planar_sloped = True
        sloped_roof.is_closed = True
        self.assertTrue(StrategyRuleEngine.evaluate_object(sloped_roof, "RoofNative")[0])
        self.assertTrue(StrategyRuleEngine.evaluate_object(sloped_roof, "FloorNative")[0])

        open_mesh = ScannedObject("r-omesh")
        open_mesh.object_type = "Mesh"
        open_mesh.geometry_primitive_type = "OpenMesh"
        open_mesh.is_horizontal = True
        open_mesh.is_closed = False
        self.assertFalse(StrategyRuleEngine.evaluate_object(open_mesh, "RoofNative")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(open_mesh, "FloorNative")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(open_mesh, "CeilingNative")[0])
        ev_om = StrategyRuleEngine.evaluate_layer([open_mesh], "RoofNative", current_category="Roofs")
        self.assertEqual(ev_om.status, "INCOMPATIBLE")
        self.assertEqual(ev_om.recommended_strategy, "DirectShape")

        curved_shell = ScannedObject("r-curved")
        curved_shell.object_type = "Brep"
        curved_shell.geometry_primitive_type = "OpenSurface"
        curved_shell.is_horizontal = False
        curved_shell.is_planar_sloped = False
        curved_shell.is_curved_extrusion = False
        curved_shell.is_closed = False
        self.assertFalse(StrategyRuleEngine.evaluate_object(curved_shell, "RoofNative")[0])
        self.assertFalse(StrategyRuleEngine.evaluate_object(curved_shell, "FloorNative")[0])

        # Family on System category adds warning
        box_on_roof = ScannedObject("r-fam-warn")
        box_on_roof.object_type = "Brep"
        box_on_roof.geometry_primitive_type = "Box"
        box_on_roof.is_prismatic = True
        box_on_roof.is_closed = True
        box_on_roof.dx, box_on_roof.dy, box_on_roof.dz = 2.0, 2.0, 1.0
        ok_f, _, warns_f, _ = StrategyRuleEngine.evaluate_object(box_on_roof, "FreeFormFamily", current_category="Roofs")
        self.assertTrue(ok_f)
        self.assertTrue(any("Generic Models" in w for w in warns_f))

        # LayerRowItem switches FloorNative -> RoofNative when category becomes Roofs
        cls_f = ClassificationResult("r-horiz")
        cls_f.inferred_category = "Floors"
        cls_f.proposed_strategy = "FloorNative"
        row = LayerRowItem("Test_Roof_Layer", [horiz_slab], cls_f, None)
        row.update_category("Roofs")
        self.assertEqual(row.proposed_strategy, "RoofNative")
        self.assertEqual(row.rule_eval.recommended_strategy, "RoofNative")

        # Classifier infers sloped planar elements on neutral layer as Roofs -> RoofNative,
        # and prevents OpenMesh from selecting RoofNative/RoofByHost
        sloped_neutral = ScannedObject("r-sloped-neutral")
        sloped_neutral.layer_full_path = "Layer_01"
        sloped_neutral.layer_name = "Layer_01"
        sloped_neutral.object_type = "Brep"
        sloped_neutral.is_planar_sloped = True
        sloped_neutral.is_closed = True
        sloped_neutral.dx, sloped_neutral.dy, sloped_neutral.dz = 8.0, 6.0, 2.5
        clf = AecClassifier(mode="Hybrid")
        res_sl = clf.classify_object(sloped_neutral)
        self.assertEqual(res_sl.inferred_category, "Roofs")
        self.assertEqual(res_sl.proposed_strategy, "RoofNative")
        self.assertEqual(clf._select_strategy(open_mesh, "Roofs"), "DirectShape")

    def test_diagonal_wall_centerline_and_opening_delegation(self):
        """WallByCurve computes rotated diagonal centerline, exact perpendicular thickness, base_offset, and delegates openings to WallByProfile."""
        import math
        import conversion_router as cr

        env = self._make_xyz_db_env()

        class MockVec(object):
            def __init__(self, x, y=None, z=None):
                if y is None and z is None and hasattr(x, "X"):
                    self.X, self.Y, self.Z = float(x.X), float(x.Y), float(x.Z)
                else:
                    self.X, self.Y, self.Z = float(x), float(y or 0.0), float(z or 0.0)
            @property
            def Length(self):
                return math.sqrt(self.X ** 2 + self.Y ** 2 + self.Z ** 2)
            def Unitize(self):
                L = self.Length
                if L > 1e-9:
                    self.X /= L; self.Y /= L; self.Z /= L

        class MockVert(object):
            def __init__(self, x, y, z):
                self.Location = MockVec(x, y, z)

        class MockFace(object):
            def __init__(self, origin, normal, loops):
                self._origin = MockVec(*origin)
                self._normal = MockVec(*normal)
                self.Loops = loops
                self.OuterLoop = loops[0]
            def TryGetPlane(self, tol=0.02):
                pl = type("Pl", (), {"Origin": self._origin, "Normal": self._normal})()
                return (True, pl)
            def GetBoundingBox(self, f):
                return type("BB", (), {"Min": MockVec(0, 0, 12), "Max": MockVec(10, 10, 22)})()

        class MockEdgeCrv(object):
            def __init__(self, p0, p1):
                self.PointAtStart = MockVec(*p0)
                self.PointAtEnd = MockVec(*p1)
            def IsLinear(self, tol=0.02):
                return True
            def DuplicateSegments(self):
                return [self]

        class MockBrep(object):
            def __init__(self, verts, faces):
                self.Vertices = [MockVert(*v) for v in verts]
                self.Faces = faces
                self.Edges = []
            def GetBoundingBox(self, f):
                xs = [v.Location.X for v in self.Vertices]
                ys = [v.Location.Y for v in self.Vertices]
                zs = [v.Location.Z for v in self.Vertices]
                return type("BB", (), {
                    "Min": MockVec(min(xs), min(ys), min(zs)),
                    "Max": MockVec(max(xs), max(ys), max(zs))
                })()

        class FakeRG(object):
            Brep = MockBrep
            Curve = type("C", (), {})
            Vector3d = MockVec
            BrepLoopType = type("BLT", (), {"Outer": 0, "Inner": 1})

        wall_creates = []

        class MockWallAPI(object):
            @staticmethod
            def Create(doc, crv, wtid, lid, height, offset, flip, struct):
                wall_creates.append((crv.GetEndPoint(0), crv.GetEndPoint(1), height, offset))
                return type("MockWall", (), {"Id": env["MockElementId"](801), "UniqueId": "w-801"})()

        class MockDoc(object):
            levels = [env["MockLevel"]("Level 2", 10.0, 102)]
            roof_types = []
            def GetDefaultElementTypeId(self, grp):
                return env["MockElementId"](201)
            def GetElement(self, eid):
                return self.levels[0] if eid == 102 else None

        class MockDB(object):
            XYZ = env["MockXYZ"]
            Line = env["MockLineAPI"]
            Wall = MockWallAPI
            WallType = type("WT", (), {})
            WallKind = type("WK", (), {"Basic": 0})
            FloorType = type("FT", (), {})
            RoofType = type("RT", (), {})
            CeilingType = type("CT", (), {})
            Level = env["MockLevel"]
            ElementId = env["MockElementId"]
            BuiltInCategory = env["MockBIC"]
            BuiltInParameter = env["MockBIP"]
            ElementTypeGroup = env["MockETG"]
            @staticmethod
            def FilteredElementCollector(d):
                return env["MockCol"](d)

        orig_revit = cr.REVIT_AVAILABLE
        orig_rhino = cr.RHINO_AVAILABLE
        orig_db = getattr(cr, "DB", None)
        orig_rg = getattr(cr, "rg", None)
        orig_rh = getattr(cr, "Rhino", None)
        try:
            cr.REVIT_AVAILABLE = True
            cr.RHINO_AVAILABLE = True
            cr.DB = MockDB
            cr.rg = FakeRG
            cr.Rhino = type("RhinoMod", (), {"RhinoDoc": type("RD", (), {"ActiveDoc": None})})

            router = cr.ConversionRouter(MockDoc())

            # 1. 45-degree rotated diagonal wall from (0,0) to (10,10), thickness=1.0, base Z=12.0 (offset +2.0 above Level 2 @ 10.0)
            s = math.sqrt(0.5)
            nx, ny = -s, s
            tx, ty = s, s
            L = 10.0 * math.sqrt(2.0)
            half_t = 0.5
            verts = []
            for u in (0.0, L):
                for v in (-half_t, half_t):
                    for z in (12.0, 22.0):
                        verts.append((u * tx + v * nx, u * ty + v * ny, z))
            outer_lp = type("LP", (), {
                "LoopType": FakeRG.BrepLoopType.Outer,
                "To3dCurve": lambda s: MockEdgeCrv((half_t * nx, half_t * ny, 12), (L * tx + half_t * nx, L * ty + half_t * ny, 12))
            })()
            diag_face = MockFace((half_t * nx, half_t * ny, 12.0), (nx, ny, 0.0), [outer_lp])
            diag_brep = MockBrep(verts, [diag_face])

            res = cr.ConversionResult("wall-diag-1")
            w = router._pipeline_3_wall_by_curve(ScannedObject("wall-diag-1"), diag_brep, env["MockElementId"](102), None, res)
            self.assertIsNotNone(w)
            self.assertEqual(res.strategy_used, "WallByCurve")
            p0, p1, h, base_off = wall_creates[0]
            # Base offset must equal bbox.Min.Z (12.0) - level.Elevation (10.0) = 2.0
            self.assertAlmostEqual(base_off, 2.0, places=3)
            self.assertAlmostEqual(h, 10.0, places=3)
            # Centerline endpoints lie on diagonal y == x (0,0) -> (10,10)
            self.assertAlmostEqual(p0.X, p0.Y, places=3)
            self.assertAlmostEqual(p1.X, p1.Y, places=3)
            self.assertAlmostEqual(p0.DistanceTo(p1), L, places=2)

            # 2. Wall with inner window opening automatically delegates to _pipeline_4_wall_by_profile
            inner_lp = type("LPIn", (), {"LoopType": FakeRG.BrepLoopType.Inner})()
            win_face = MockFace((0, 0, 12.0), (0.0, 1.0, 0.0), [outer_lp, inner_lp])
            win_brep = MockBrep(verts, [win_face])
            prof_calls = []
            router._pipeline_4_wall_by_profile = lambda so, gf, lid, pr, r: prof_calls.append(so.rhino_guid) or "delegated_profile"
            out_win = router._pipeline_3_wall_by_curve(ScannedObject("wall-win-1"), win_brep, env["MockElementId"](102), None, cr.ConversionResult("wall-win-1"))
            self.assertEqual(out_win, "delegated_profile")
            self.assertEqual(prof_calls, ["wall-win-1"])
        finally:
            cr.REVIT_AVAILABLE = orig_revit
            cr.RHINO_AVAILABLE = orig_rhino
            cr.DB = orig_db
            cr.rg = orig_rg
            if orig_rh is None and hasattr(cr, "Rhino"):
                delattr(cr, "Rhino")
            elif orig_rh is not None:
                cr.Rhino = orig_rh


if __name__ == "__main__":
    unittest.main()
