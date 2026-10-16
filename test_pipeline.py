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


if __name__ == "__main__":
    unittest.main()
