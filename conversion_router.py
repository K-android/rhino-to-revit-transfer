# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 5
conversion_router.py - RIR Element Creation Engines (10-Pipeline Dispatcher)

Executes native Revit element generation and DirectShape fallback using Rhino.Inside.Revit (RIR).
Supports 3 Global Execution Modes:
  - Native BIM Mode
  - Hybrid Mode
  - Geometry-First Mode

Implements 10 translation pipelines:
  1. Floors (Native Floor via CurveLoops & Room Bounding)
  2. Floors by Host (Sloped / Faceted slabs)
  3. Walls by Curve (Linear rectangular walls)
  4. Walls by Profile (Profiled walls with arched cutouts/vaults + DirectShape fallback)
  5. Ceilings (Native Horizontal Ceiling)
  6. Roofs (Native FootPrint Roof)
  7. Roofs by Host / Face
  8. Family Creation & Placement (Rhino Blocks -> Revit Family Instances / Types)
  9. DirectShape Fallback (Facade screen / stainless mesh, SubD, complex freeform Breps)
  10. Auto-BUA / GFA Area Plan Generator (Area Schemes & Boundary Lines)

Includes TransferFailuresPreprocessor to cleanly suppress and log non-fatal Revit warnings.
"""

import sys
import os
import re
import math
import traceback

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("System.Collections")
    import System
    from System.Collections.Generic import List

    # RhinoCommon
    import Rhino
    import Rhino.Geometry as rg
    RHINO_AVAILABLE = True
except ImportError:
    RHINO_AVAILABLE = False

try:
    # Revit API
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    import Autodesk.Revit.DB as DB
    import Autodesk.Revit.UI as UI

    # Rhino.Inside.Revit
    clr.AddReference("RhinoInside.Revit")
    import RhinoInside.Revit as RIR
    from RhinoInside.Revit import Revit, Convert
    from RhinoInside.Revit.Convert.Geometry import GeometryEncoder
    REVIT_AVAILABLE = True
except Exception:
    REVIT_AVAILABLE = False


class TransferFailuresPreprocessor(DB.IFailuresPreprocessor if REVIT_AVAILABLE else object):
    """Suppresses non-fatal Revit warning messages during automated transfer to prevent modal popups."""

    def __init__(self):
        self.logged_warnings = []

    def PreprocessFailures(self, failuresAccessor):
        if not REVIT_AVAILABLE:
            return DB.FailureProcessingResult.Continue

        fail_list = failuresAccessor.GetFailureMessages()
        for failure in fail_list:
            severity = failure.GetSeverity()
            desc = failure.GetDescriptionText()

            if severity == DB.FailureSeverity.Warning:
                self.logged_warnings.append(desc)
                failuresAccessor.DeleteWarning(failure)
            elif severity == DB.FailureSeverity.Error:
                if failure.HasResolutions():
                    failuresAccessor.ResolveFailure(failure)
                    return DB.FailureProcessingResult.ProceedWithCommit
                else:
                    return DB.FailureProcessingResult.ProceedWithRollBack

        return DB.FailureProcessingResult.Continue


class ConversionResult(object):
    """Holds output of a single object conversion."""

    def __init__(self, rhino_guid):
        self.rhino_guid = str(rhino_guid)
        self.revit_element_id = -1
        self.revit_unique_id = ""
        self.status = "PENDING"  # SUCCESS | FAILED | SKIPPED
        self.strategy_used = ""
        self.category_created = ""
        self.warnings = []
        self.error_message = ""
        self.bua_area = 0.0  # Area contribution for floors (sqm)

    def to_dict(self):
        return {
            "rhino_guid": self.rhino_guid,
            "revit_element_id": self.revit_element_id,
            "revit_unique_id": self.revit_unique_id,
            "status": self.status,
            "strategy_used": self.strategy_used,
            "category_created": self.category_created,
            "warnings": self.warnings,
            "error_message": self.error_message,
            "bua_area": self.bua_area
        }


class ConversionRouter(object):
    """10-pipeline dispatcher coordinating Rhino.Inside.Revit element generation."""

    def __init__(self, revit_doc, global_mode="Hybrid", generate_bua=True, unit_scale=None):
        """
        :param revit_doc: Autodesk.Revit.DB.Document
        :param global_mode: "Native BIM" | "Hybrid" | "Geometry-First"
        :param generate_bua: Boolean toggle for Auto-BUA Area Plan generation
        :param unit_scale: Optional float override for Rhino-to-Revit foot scale
        """
        self.doc = revit_doc
        self.global_mode = global_mode
        self.generate_bua = generate_bua
        self.failures_preprocessor = TransferFailuresPreprocessor() if REVIT_AVAILABLE else None
        self.natural_unit_scale = self._calc_rhino_to_revit_scale()
        self.unit_scale = float(unit_scale) if unit_scale is not None else self.natural_unit_scale

        # Cache Revit Level and Type IDs for performance
        self.level_cache = {}
        self.type_cache = {}
        if REVIT_AVAILABLE and self.doc:
            self._cache_revit_data()

    def _calc_rhino_to_revit_scale(self):
        """Calculates scale factor from Rhino document units to Revit internal feet."""
        if not RHINO_AVAILABLE or not Rhino.RhinoDoc.ActiveDoc:
            return 1.0
        active_unit = Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem
        # Convert from Rhino doc unit to Revit internal Feet
        return Rhino.RhinoMath.UnitScale(active_unit, Rhino.UnitSystem.Feet)

    def _cache_revit_data(self):
        """Pre-fetches Revit Levels and common element types."""
        # Levels
        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.Level)
        for lvl in collector:
            self.level_cache[lvl.Name] = lvl.Id
            # Also store elevation in feet
            self.level_cache[lvl.Name + "_elev"] = lvl.Elevation

        # Default floor, wall, roof, ceiling types
        self.default_floor_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.FloorType)
        self.default_wall_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
        self.default_roof_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.RoofType)
        self.default_ceiling_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.CeilingType)

        # Caches for repetitive definitions, orientation baselines, and adaptive families
        self.repetitive_type_cache = {}
        self.canonical_repetition_cache = {}
        self.adaptive_family_cache = {}

    def convert_object(self, scanned_obj, classification_result, profile_rule=None):
        """Dispatches an object through the appropriate conversion strategy."""
        res = ConversionResult(scanned_obj.rhino_guid)
        if not REVIT_AVAILABLE or not self.doc:
            res.status = "FAILED"
            res.error_message = "Revit API environment not available"
            return res

        # Strategy resolution: Profile Rule override > Classification result
        strategy = profile_rule.strategy if profile_rule else classification_result.proposed_strategy
        if self.global_mode == "Geometry-First" and strategy not in ("FloorNative", "AdaptiveComponent"):
            strategy = "DirectShape"

        res.strategy_used = strategy

        try:
            # Scale Rhino geometry clone into Revit internal feet
            rh_geom = scanned_obj.raw_geometry
            if not rh_geom:
                res.status = "FAILED"
                res.error_message = "Object geometry missing or invalid"
                return res

            geom_feet = rh_geom.Duplicate()
            geom_feet.Scale(self.unit_scale)

            # Match Level
            level_id = self._resolve_level_id(scanned_obj)

            # Route through Conversion Pipelines
            created_elem = None

            if strategy == "AdaptiveComponent":
                created_elem = self._pipeline_8a_adaptive_component(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "RepetitiveFamily":
                created_elem = self._pipeline_8b_repetitive_family(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "ParametricFamily":
                created_elem = self._pipeline_8c_parametric_family(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "FloorNative":
                created_elem = self._pipeline_1_native_floor(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "FloorByHost":
                created_elem = self._pipeline_2_floor_by_host(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "WallByCurve":
                created_elem = self._pipeline_3_wall_by_curve(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "WallByProfile":
                created_elem = self._pipeline_4_wall_by_profile(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "CeilingNative":
                created_elem = self._pipeline_5_ceiling_native(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "RoofNative":
                created_elem = self._pipeline_6_roof_native(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "RoofByHost":
                created_elem = self._pipeline_7_roof_by_host(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "FamilyInstance":
                created_elem = self._pipeline_8_family_placement(scanned_obj, geom_feet, level_id, profile_rule, res)
            elif strategy == "DirectShape":
                created_elem = self._pipeline_9_direct_shape(scanned_obj, geom_feet, classification_result, profile_rule, res)
            else:
                created_elem = self._pipeline_9_direct_shape(scanned_obj, geom_feet, classification_result, profile_rule, res)

            # Optional Auto-BUA generator trigger
            if self.generate_bua and classification_result.inferred_category == "Floors" and created_elem:
                self._pipeline_10_auto_bua(scanned_obj, geom_feet, level_id, res)

            if created_elem:
                res.revit_element_id = self._get_elem_id_val(created_elem.Id)
                res.revit_unique_id = created_elem.UniqueId
                res.status = "SUCCESS"
                res.category_created = created_elem.Category.Name if created_elem.Category else "Unknown"
            elif res.status != "FAILED":
                res.status = "FAILED"
                res.error_message = "Pipeline returned null element without raising exception"

        except Exception as ex:
            res.status = "FAILED"
            res.error_message = str(ex)
            res.warnings.append(traceback.format_exc())

        return res

    @staticmethod
    def _get_elem_id_val(elem_id):
        if hasattr(elem_id, "Value"):
            return int(elem_id.Value)
        return int(elem_id.IntegerValue)

    def _resolve_level_id(self, scanned_obj):
        """Finds closest Revit Level element ID for an object."""
        if scanned_obj.nearest_level_name in self.level_cache:
            return self.level_cache[scanned_obj.nearest_level_name]

        # Fallback to first available level
        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.Level)
        first_lvl = collector.FirstElement()
        return first_lvl.Id if first_lvl else DB.ElementId.InvalidElementId

    # =========================================================================
    # PIPELINE 1: Native Floor
    # =========================================================================
    def _pipeline_1_native_floor(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native Revit Floor using top horizontal boundary and void loops."""
        working_brep = geom_feet if isinstance(geom_feet, rg.Brep) else (geom_feet.ToBrep() if hasattr(geom_feet, "ToBrep") else None)
        if not working_brep:
            # Fallback to DirectShape if not convertible to Brep
            res.warnings.append("Floor geometry not a Brep. Falling back to DirectShape.")
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)

        # Find top horizontal planar face (Normal.Z > 0.95)
        top_face = None
        max_face_z = float("-inf")
        for face in working_brep.Faces:
            u_mid = 0.5 * (face.Domain(0).Min + face.Domain(0).Max)
            v_mid = 0.5 * (face.Domain(1).Min + face.Domain(1).Max)
            norm = face.NormalAt(u_mid, v_mid)
            norm.Unitize()
            if norm.Z > 0.90:
                face_center_z = face.GetBoundingBox(True).Center.Z
                if face_center_z > max_face_z:
                    max_face_z = face_center_z
                    top_face = face

        if not top_face:
            res.warnings.append("No upward planar top face found on slab Brep. Falling back to DirectShape.")
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)

        # Extract boundary loops
        curve_loops = List[DB.CurveLoop]()
        outer_loop = top_face.OuterLoop
        if not outer_loop:
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)

        outer_cl = self._rg_loop_to_revit_curveloop(outer_loop)
        if not outer_cl or outer_cl.IsOpen():
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)
        curve_loops.Add(outer_cl)

        # Inner void / shaft loops
        for loop in top_face.Loops:
            if loop.LoopType == rg.BrepLoopType.Inner:
                inner_cl = self._rg_loop_to_revit_curveloop(loop)
                if inner_cl and not inner_cl.IsOpen():
                    curve_loops.Add(inner_cl)

        floor_type_id = self.default_floor_type

        # Modern Revit Floor.Create API (Revit 2022+)
        floor = None
        try:
            floor = DB.Floor.Create(self.doc, curve_loops, floor_type_id, level_id)
        except Exception:
            # Fallback to classic doc.Create.NewFloor or DirectShape
            try:
                curve_array = self._curve_loops_to_curve_array(curve_loops)
                floor = self.doc.Create.NewFloor(curve_array, False)
            except Exception as ex2:
                res.warnings.append("Floor.Create failed: {}. Falling back to DirectShape.".format(ex2))
                return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)

        if floor:
            # Ensure Room Bounding is True for BUA / GFA calculations
            room_bound_param = floor.get_Parameter(DB.BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
            if room_bound_param and not room_bound_param.IsReadOnly:
                room_bound_param.Set(1)

            # Record BUA area contribution
            res.bua_area = scanned_obj.area

        return floor

    # =========================================================================
    # PIPELINE 2: Floor by Host
    # =========================================================================
    def _pipeline_2_floor_by_host(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates floor hosted on sloped/stepped geometry or as DirectShape with Floor category."""
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Floors))

    # =========================================================================
    # PIPELINE 3: Walls by Curve
    # =========================================================================
    def _pipeline_3_wall_by_curve(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native vertical rectangular wall from baseline curve, height, and wall type."""
        # Calculate wall bottom center line curve
        bbox = geom_feet.GetBoundingBox(True)
        wall_height = bbox.Max.Z - bbox.Min.Z
        if wall_height <= 0.1:
            wall_height = 10.0  # Default 10 feet (~3.0m)

        # Extract bottom baseline
        base_z = bbox.Min.Z
        pt_start = DB.XYZ(bbox.Min.X, 0.5 * (bbox.Min.Y + bbox.Max.Y), base_z)
        pt_end = DB.XYZ(bbox.Max.X, 0.5 * (bbox.Min.Y + bbox.Max.Y), base_z)

        # Orientation check: if dy > dx, wall runs in Y direction
        if (bbox.Max.Y - bbox.Min.Y) > (bbox.Max.X - bbox.Min.X):
            pt_start = DB.XYZ(0.5 * (bbox.Min.X + bbox.Max.X), bbox.Min.Y, base_z)
            pt_end = DB.XYZ(0.5 * (bbox.Min.X + bbox.Max.X), bbox.Max.Y, base_z)

        # Check line length
        if pt_start.DistanceTo(pt_end) < 0.1:
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

        base_line = DB.Line.CreateBound(pt_start, pt_end)
        wall_type_id = self.default_wall_type

        try:
            wall = DB.Wall.Create(self.doc, base_line, wall_type_id, level_id, wall_height, 0.0, False, False)
            return wall
        except Exception as ex:
            res.warnings.append("Wall.Create by curve failed: {}. Falling back to Profile or DirectShape.".format(ex))
            return self._pipeline_4_wall_by_profile(scanned_obj, geom_feet, level_id, profile_rule, res)

    # =========================================================================
    # PIPELINE 4: Walls by Profile (with Fallback)
    # =========================================================================
    def _pipeline_4_wall_by_profile(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates wall with non-rectangular profile (arches, vaults, sloped tops) or falls back cleanly."""
        working_brep = geom_feet if isinstance(geom_feet, rg.Brep) else (geom_feet.ToBrep() if hasattr(geom_feet, "ToBrep") else None)
        if not working_brep:
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

        # Find largest vertical face for wall profile
        best_face = None
        max_area = float("-inf")
        for face in working_brep.Faces:
            u_mid = 0.5 * (face.Domain(0).Min + face.Domain(0).Max)
            v_mid = 0.5 * (face.Domain(1).Min + face.Domain(1).Max)
            norm = face.NormalAt(u_mid, v_mid)
            norm.Unitize()
            if abs(norm.Z) < 0.1:  # Vertical face
                mp = rg.MassProperties.Compute(face)
                area = mp.Area if mp else 0.0
                if area > max_area:
                    max_area = area
                    best_face = face

        if not best_face:
            res.warnings.append("No vertical profile face found. Falling back to DirectShape.")
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

        try:
            profile_loops = List[DB.CurveLoop]()
            outer_cl = self._rg_loop_to_revit_curveloop(best_face.OuterLoop)
            if outer_cl and not outer_cl.IsOpen():
                profile_loops.Add(outer_cl)

                # Inner openings / cutouts
                for loop in best_face.Loops:
                    if loop.LoopType == rg.BrepLoopType.Inner:
                        inner_cl = self._rg_loop_to_revit_curveloop(loop)
                        if inner_cl and not inner_cl.IsOpen():
                            profile_loops.Add(inner_cl)

                wall = DB.Wall.Create(self.doc, profile_loops, self.default_wall_type, level_id, False)
                return wall
        except Exception as ex:
            res.warnings.append("Wall profile creation rejected by Revit: {}. Cleanly falling back to DirectShape.".format(ex))

        # Clean fallback to DirectShape
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

    # =========================================================================
    # PIPELINE 5: Native Ceilings
    # =========================================================================
    def _pipeline_5_ceiling_native(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native ceiling from horizontal boundary curves."""
        try:
            bbox = geom_feet.GetBoundingBox(True)
            loops = List[DB.CurveLoop]()
            pt0 = DB.XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
            pt1 = DB.XYZ(bbox.Max.X, bbox.Min.Y, bbox.Min.Z)
            pt2 = DB.XYZ(bbox.Max.X, bbox.Max.Y, bbox.Min.Z)
            pt3 = DB.XYZ(bbox.Min.X, bbox.Max.Y, bbox.Min.Z)

            cl = DB.CurveLoop()
            cl.Append(DB.Line.CreateBound(pt0, pt1))
            cl.Append(DB.Line.CreateBound(pt1, pt2))
            cl.Append(DB.Line.CreateBound(pt2, pt3))
            cl.Append(DB.Line.CreateBound(pt3, pt0))
            loops.Add(cl)

            if hasattr(DB, "Ceiling") and hasattr(DB.Ceiling, "Create"):
                ceiling = DB.Ceiling.Create(self.doc, loops, self.default_ceiling_type, level_id)
                return ceiling
        except Exception as ex:
            res.warnings.append("Native Ceiling.Create failed: {}. Falling back to DirectShape.".format(ex))

        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Ceilings))

    # =========================================================================
    # PIPELINE 6: Native Footprint Roof
    # =========================================================================
    def _pipeline_6_roof_native(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native footprint roof from horizontal boundary."""
        try:
            bbox = geom_feet.GetBoundingBox(True)
            pt0 = DB.XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
            pt1 = DB.XYZ(bbox.Max.X, bbox.Min.Y, bbox.Min.Z)
            pt2 = DB.XYZ(bbox.Max.X, bbox.Max.Y, bbox.Min.Z)
            pt3 = DB.XYZ(bbox.Min.X, bbox.Max.Y, bbox.Min.Z)

            crv_array = DB.CurveArray()
            crv_array.Append(DB.Line.CreateBound(pt0, pt1))
            crv_array.Append(DB.Line.CreateBound(pt1, pt2))
            crv_array.Append(DB.Line.CreateBound(pt2, pt3))
            crv_array.Append(DB.Line.CreateBound(pt3, pt0))

            level_elem = self.doc.GetElement(level_id)
            footprint_map = DB.ModelCurveArray()
            roof = self.doc.Create.NewFootPrintRoof(crv_array, level_elem, self.doc.GetElement(self.default_roof_type), footprint_map)
            return roof
        except Exception as ex:
            res.warnings.append("NewFootPrintRoof failed: {}. Falling back to DirectShape.".format(ex))

        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Roofs))

    # =========================================================================
    # PIPELINE 7: Roofs by Host / Face
    # =========================================================================
    def _pipeline_7_roof_by_host(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates freeform roof as DirectShape with OST_Roofs."""
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Roofs))

    # =========================================================================
    # PIPELINE 8A: Adaptive Component Placement (Multi-Point Facade Panels / Struts)
    # =========================================================================
    def _find_adaptive_symbol_with_point_count(self, num_pts, requested_name=None):
        """Finds a loaded Adaptive FamilySymbol that has exactly num_pts placement points."""
        if not hasattr(DB, "AdaptiveComponentInstanceUtils"):
            return None

        requested_clean = (requested_name or "").strip().lower()

        # Check cache if not searching for an explicitly requested type
        if not requested_clean and num_pts in self.adaptive_family_cache:
            cached_id = self.adaptive_family_cache[num_pts]
            if cached_id:
                try:
                    elem = self.doc.GetElement(cached_id)
                    if elem and isinstance(elem, DB.FamilySymbol):
                        return elem
                except Exception:
                    pass

        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.FamilySymbol)
        candidates = []
        for s in collector:
            try:
                if not DB.AdaptiveComponentInstanceUtils.IsAdaptiveFamily(s.Family):
                    continue
                s_name = (s.Name or "").lower()
                f_name = (s.FamilyName or "").lower()
                if requested_clean:
                    if requested_clean in s_name or requested_clean in f_name:
                        candidates.insert(0, s)
                    else:
                        candidates.append(s)
                else:
                    candidates.append(s)
            except Exception:
                continue

        # Test candidate symbols to verify exact placement point count
        for sym in candidates:
            test_inst = None
            try:
                if not sym.IsActive:
                    sym.Activate()
                test_inst = DB.AdaptiveComponentInstanceUtils.CreateAdaptiveComponentInstance(self.doc, sym)
                pt_elem_ids = DB.AdaptiveComponentInstanceUtils.GetPlacementPointElementIds(test_inst)
                pt_count = len(pt_elem_ids) if pt_elem_ids else 0
                self.doc.Delete(test_inst.Id)
                test_inst = None

                if pt_count == num_pts:
                    if not requested_clean:
                        self.adaptive_family_cache[num_pts] = sym.Id
                    return sym
            except Exception:
                if test_inst:
                    try:
                        self.doc.Delete(test_inst.Id)
                    except Exception:
                        pass
                continue

        return None

    def _pipeline_8a_adaptive_component(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Places multi-point Adaptive Component instances with strict point count validation."""
        pts_rhino = getattr(scanned_obj, "adaptive_points", [])
        num_pts = len(pts_rhino)
        if num_pts not in (2, 3, 4):
            res.warnings.append("AdaptiveComponent: Object has {} placement points (requires 2, 3, or 4). Falling back to DirectShape.".format(num_pts))
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res)

        # Scale coordinates into Revit internal feet
        scale = self.unit_scale
        pts_feet = [DB.XYZ(p[0] * scale, p[1] * scale, p[2] * scale) for p in pts_rhino]

        requested_type = profile_rule.revit_type if (profile_rule and profile_rule.revit_type) else ""
        target_sym = self._find_adaptive_symbol_with_point_count(num_pts, requested_type)

        if target_sym and hasattr(DB, "AdaptiveComponentInstanceUtils"):
            try:
                if not target_sym.IsActive:
                    target_sym.Activate()
                inst = DB.AdaptiveComponentInstanceUtils.CreateAdaptiveComponentInstance(self.doc, target_sym)
                pt_elem_ids = DB.AdaptiveComponentInstanceUtils.GetPlacementPointElementIds(inst)

                if len(pt_elem_ids) == num_pts:
                    for idx, pid in enumerate(pt_elem_ids):
                        pt_elem = self.doc.GetElement(pid)
                        if pt_elem and hasattr(pt_elem, "Position"):
                            pt_elem.Position = pts_feet[idx]
                    return inst
                else:
                    self.doc.Delete(inst.Id)
                    res.warnings.append("Adaptive Family '{}' point count mismatch ({} in family vs {} required).".format(
                        target_sym.Name, len(pt_elem_ids), num_pts
                    ))
            except Exception as ex:
                res.warnings.append("AdaptiveComponentInstance placement notice: {}. Falling back to DirectShape.".format(ex))

        # Safe fallback if no matching adaptive family is loaded
        res.warnings.append("No {}-point Adaptive Family loaded in project. Transferred via DirectShape.".format(num_pts))
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_GenericModel))

    # =========================================================================
    # PIPELINE 8B: Repetitive Family Instancing (Blocks & Repeated Signatures)
    # =========================================================================
    def _rhino_xform_to_revit_transform(self, xform):
        """Converts a Rhino.Geometry.Transform into an Autodesk.Revit.DB.Transform."""
        if not REVIT_AVAILABLE or not xform:
            return None
        try:
            scale = self.unit_scale
            tf = DB.Transform.Identity
            tf.Origin = DB.XYZ(xform.M03 * scale, xform.M13 * scale, xform.M23 * scale)
            tf.BasisX = DB.XYZ(xform.M00, xform.M10, xform.M20)
            tf.BasisY = DB.XYZ(xform.M01, xform.M11, xform.M21)
            tf.BasisZ = DB.XYZ(xform.M02, xform.M12, xform.M22)
            return tf
        except Exception:
            return None

    def _pipeline_8b_repetitive_family(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates a shared reusable DirectShapeType definition and places transformed instances.
        
        Guarantees that rotated loose copies preserve their exact world-space orientation by
        routing to standard DirectShape whenever orientation differs from canonical definition.
        """
        cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
        if profile_rule and profile_rule.revit_category:
            cat_id = self._category_str_to_id(profile_rule.revit_category)

        sig = scanned_obj.repetition_signature or ("Block_" + scanned_obj.block_name if scanned_obj.block_name else None)
        if not sig:
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=cat_id)

        type_key = "R2R_" + re.sub(r'[^a-zA-Z0-9_]+', '_', sig)[:45]
        is_block = (scanned_obj.object_type == "InstanceReference")

        # Try Revit DirectShapeType + DirectShapeLibrary instancing
        try:
            if hasattr(DB, "DirectShapeLibrary") and hasattr(DB, "DirectShapeType"):
                lib = DB.DirectShapeLibrary.GetDirectShapeLibrary(self.doc)
                type_id = None
                if lib.ContainsType(type_key):
                    type_id = lib.FindDefinitionType(type_key)
                elif type_key in self.repetitive_type_cache:
                    type_id = self.repetitive_type_cache[type_key]

                if not type_id:
                    # Canonical definition: local origin (0, 0, 0) in native Rhino units and Revit feet
                    cx_raw = scanned_obj.centroid[0]
                    cy_raw = scanned_obj.centroid[1]
                    cz_raw = scanned_obj.centroid[2]
                    canonical_raw = scanned_obj.raw_geometry.Duplicate()
                    if hasattr(canonical_raw, "Translate"):
                        canonical_raw.Translate(-cx_raw, -cy_raw, -cz_raw)

                    cx_feet = cx_raw * self.unit_scale
                    cy_feet = cy_raw * self.unit_scale
                    cz_feet = cz_raw * self.unit_scale
                    canonical_feet = geom_feet.Duplicate()
                    if hasattr(canonical_feet, "Translate"):
                        canonical_feet.Translate(-cx_feet, -cy_feet, -cz_feet)

                    revit_geoms = self._convert_rhino_to_revit_geometry(canonical_raw, geom_feet=canonical_feet)
                    if revit_geoms and len(revit_geoms) > 0:
                        dst = DB.DirectShapeType.Create(self.doc, type_key, cat_id)
                        dst.SetShape(revit_geoms)
                        lib.AddDefinitionType(type_key, dst.Id)
                        type_id = dst.Id
                        self.repetitive_type_cache[type_key] = type_id
                        self.canonical_repetition_cache[type_key] = (scanned_obj.dx, scanned_obj.dy, scanned_obj.dz)

                if type_id:
                    # Case 1: Block Instance with full 3D Transform
                    if is_block and getattr(scanned_obj, "instance_transform", None):
                        tf = self._rhino_xform_to_revit_transform(scanned_obj.instance_transform)
                        if tf:
                            inst = DB.DirectShape.CreateElementInstance(self.doc, type_id, cat_id, type_key, tf)
                            return inst

                    # Case 2: Loose geometry - verify that orientation matches canonical baseline
                    canon_dims = self.canonical_repetition_cache.get(type_key)
                    if canon_dims:
                        # Check if bounding dimensions match within tolerance (pure translation)
                        is_pure_translation = (
                            abs(scanned_obj.dx - canon_dims[0]) < 0.05 and
                            abs(scanned_obj.dy - canon_dims[1]) < 0.05 and
                            abs(scanned_obj.dz - canon_dims[2]) < 0.05
                        )
                        if is_pure_translation:
                            cx = scanned_obj.centroid[0] * self.unit_scale
                            cy = scanned_obj.centroid[1] * self.unit_scale
                            cz = scanned_obj.centroid[2] * self.unit_scale
                            tf = DB.Transform.CreateTranslation(DB.XYZ(cx, cy, cz))
                            inst = DB.DirectShape.CreateElementInstance(self.doc, type_id, cat_id, type_key, tf)
                            return inst
                        else:
                            # Orientation differs: fall back to DirectShape to guarantee exact world rotation
                            res.warnings.append("Loose copy has distinct spatial orientation. Placed via DirectShape to preserve rotation.")
                            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=cat_id)

                    # Default translation placement if no baseline check was needed
                    cx = scanned_obj.centroid[0] * self.unit_scale
                    cy = scanned_obj.centroid[1] * self.unit_scale
                    cz = scanned_obj.centroid[2] * self.unit_scale
                    tf = DB.Transform.CreateTranslation(DB.XYZ(cx, cy, cz))
                    if scanned_obj.rotation_angle:
                        rot = DB.Transform.CreateRotation(DB.XYZ.BasisZ, scanned_obj.rotation_angle)
                        tf = tf.Multiply(rot)
                    inst = DB.DirectShape.CreateElementInstance(self.doc, type_id, cat_id, type_key, tf)
                    return inst

        except Exception as ex:
            res.warnings.append("Repetitive DirectShapeType notice: {}. Using standard DirectShape.".format(ex))

        # DirectShape fallback (preserves exact world-space geometry and rotation)
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=cat_id)

    # =========================================================================
    # PIPELINE 8C: Dimension-Driven Parametric Family Placement
    # =========================================================================
    def _pipeline_8c_parametric_family(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Places a loadable FamilyInstance and sets dimensional parameters (Width, Depth, Height)."""
        cat_str = profile_rule.revit_category if (profile_rule and profile_rule.revit_category) else "OST_GenericModel"
        requested_type = profile_rule.revit_type if (profile_rule and profile_rule.revit_type) else ""
        target_cat_id = self._category_str_to_id(cat_str)

        target_sym = None
        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.FamilySymbol)
        for s in collector:
            try:
                if requested_type and (s.Name == requested_type or s.FamilyName == requested_type):
                    target_sym = s
                    break
                if not requested_type and s.Category and s.Category.Id == target_cat_id:
                    target_sym = s
                    break
            except Exception:
                continue

        if target_sym:
            try:
                if not target_sym.IsActive:
                    target_sym.Activate()
                cx = scanned_obj.centroid[0] * self.unit_scale
                cy = scanned_obj.centroid[1] * self.unit_scale
                cz = scanned_obj.centroid[2] * self.unit_scale
                pt = DB.XYZ(cx, cy, cz)
                level_elem = self.doc.GetElement(level_id)
                inst = self.doc.Create.NewFamilyInstance(pt, target_sym, level_elem, DB.Structure.StructuralType.NonStructural)

                # Rotate if needed
                if scanned_obj.rotation_angle and inst:
                    axis = DB.Line.CreateBound(pt, DB.XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    DB.ElementTransformUtils.RotateElement(self.doc, inst.Id, axis, scanned_obj.rotation_angle)

                # Drive instance/type dimensional parameters with multilingual aliases
                dims = getattr(scanned_obj, "parametric_dims", {})
                param_aliases = {
                    "Width": ["Width", "b", "w", "Ancho", "Anchura", "Amplada"],
                    "Depth": ["Depth", "d", "Largo", "Profundidad", "Profunditat", "Length"],
                    "Height": ["Height", "h", "H", "Altura", "Alto", "Alçada"],
                    "Length": ["Length", "Longitud", "L"],
                    "Thickness": ["Thickness", "Espesor", "Grosor", "Gruix", "t"]
                }
                for dim_key, p_val in dims.items():
                    val_feet = p_val * self.unit_scale
                    aliases = param_aliases.get(dim_key, [dim_key])
                    for alias in aliases:
                        param = inst.LookupParameter(alias)
                        if not param:
                            param = target_sym.LookupParameter(alias)
                        if param and not param.IsReadOnly and param.StorageType == DB.StorageType.Double:
                            try:
                                param.Set(val_feet)
                                break
                            except Exception:
                                pass
                return inst
            except Exception as ex:
                res.warnings.append("ParametricFamily placement notice: {}. Falling back to DirectShape.".format(ex))

        # Fallback to direct shape if no suitable family is loaded
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=target_cat_id)

    # =========================================================================
    # PIPELINE 8: Generic Family Delegator
    # =========================================================================
    def _pipeline_8_family_placement(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Routes family placement to Adaptive, Repetitive, or Parametric pipeline."""
        if getattr(scanned_obj, "adaptive_point_count", 0) in (2, 3, 4):
            return self._pipeline_8a_adaptive_component(scanned_obj, geom_feet, level_id, profile_rule, res)
        if scanned_obj.object_type == "InstanceReference" or getattr(scanned_obj, "repetition_count", 1) >= 2:
            return self._pipeline_8b_repetitive_family(scanned_obj, geom_feet, level_id, profile_rule, res)
        return self._pipeline_8c_parametric_family(scanned_obj, geom_feet, level_id, profile_rule, res)

    # =========================================================================
    # PIPELINE 9: DirectShape Fallback (Facade Mesh / Screen, SubD, Double-Curved Breps)
    # =========================================================================
    def _pipeline_9_direct_shape(self, scanned_obj, geom_feet, classification_result=None, profile_rule=None, res=None, category_id=None):
        """Creates a high-fidelity DB.DirectShape element."""
        if not category_id:
            if profile_rule and profile_rule.revit_category:
                category_id = self._category_str_to_id(profile_rule.revit_category)
            elif classification_result:
                category_id = self._category_str_to_id(classification_result.revit_category_name)
            else:
                category_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)

        # Convert Rhino geometry to Revit DB.GeometryObject without double scaling
        revit_geoms = self._convert_rhino_to_revit_geometry(scanned_obj.raw_geometry, geom_feet=geom_feet)
        if not revit_geoms or len(revit_geoms) == 0:
            if res:
                res.status = "FAILED"
                res.error_message = "DirectShape geometry conversion failed (zero solids/meshes produced)"
            return None

        # Create DirectShape
        ds = DB.DirectShape.CreateElement(self.doc, category_id)
        ds.SetShape(revit_geoms)

        # Set Name
        name = scanned_obj.object_name or scanned_obj.layer_name or "R2R_Transferred_Object"
        name_param = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if name_param and not name_param.IsReadOnly:
            name_param.Set(name)

        return ds

    # =========================================================================
    # PIPELINE 10: Auto-BUA / GFA Area Plan Generator
    # =========================================================================
    def _pipeline_10_auto_bua(self, scanned_obj, geom_feet, level_id, res):
        """Generates Area Boundary Lines under dedicated 'BUA / GFA Assessment' Scheme."""
        try:
            # 1. Locate or create BUA Area Scheme
            scheme = self._get_or_create_bua_area_scheme()
            if not scheme:
                return

            # 2. Locate or create Area Plan view for level
            area_view = self._get_or_create_area_plan_view(level_id, scheme.Id)
            if not area_view:
                return

            # 3. Project outer boundary curves as Area Boundary Lines
            bbox = geom_feet.GetBoundingBox(True)
            pt0 = DB.XYZ(bbox.Min.X, bbox.Min.Y, 0.0)
            pt1 = DB.XYZ(bbox.Max.X, bbox.Min.Y, 0.0)
            pt2 = DB.XYZ(bbox.Max.X, bbox.Max.Y, 0.0)
            pt3 = DB.XYZ(bbox.Min.X, bbox.Max.Y, 0.0)

            lines = [
                DB.Line.CreateBound(pt0, pt1),
                DB.Line.CreateBound(pt1, pt2),
                DB.Line.CreateBound(pt2, pt3),
                DB.Line.CreateBound(pt3, pt0)
            ]

            sketch_plane = area_view.SketchPlane
            if not sketch_plane:
                plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
                sketch_plane = DB.SketchPlane.Create(self.doc, plane)

            for line in lines:
                self.doc.Create.NewAreaBoundaryLine(sketch_plane, line, area_view)

        except Exception as ex:
            res.warnings.append("Auto-BUA boundary generation notice: {}".format(ex))

    # =========================================================================
    # Geometry Helpers & Encoders
    # =========================================================================
    def _convert_rhino_to_revit_geometry(self, rh_geom, geom_feet=None):
        """Converts Rhino Brep, Extrusion, Surface, SubD, Mesh, or Curve into IList of Revit DB.GeometryObject.

        Prevents double-scaling:
        - Passes native unscaled Rhino geometry to Rhino.Inside GeometryEncoder
          (which handles active RhinoDoc units -> Revit internal feet conversion automatically).
        - Applies manual scale override ratio if user overrode document units in the UI.
        - Uses geom_feet (scaled by unit_scale) for native TessellatedShapeBuilder fallback.
        """
        geom_list = List[DB.GeometryObject]()
        if not rh_geom:
            return geom_list

        # Determine if a manual unit scale override is in effect
        scale_ratio = 1.0
        if getattr(self, "natural_unit_scale", 1.0) > 0 and getattr(self, "unit_scale", 1.0) > 0:
            scale_ratio = self.unit_scale / self.natural_unit_scale

        working_geom = rh_geom
        if abs(scale_ratio - 1.0) > 0.001:
            working_geom = rh_geom.Duplicate()
            if hasattr(working_geom, "Scale"):
                working_geom.Scale(scale_ratio)

        # Normalize Extrusions, Surfaces, SubDs to Brep if needed
        if not isinstance(working_geom, (rg.Brep, rg.Mesh)) and hasattr(working_geom, "ToBrep"):
            try:
                brep_conv = working_geom.ToBrep()
                if brep_conv:
                    working_geom = brep_conv
            except Exception:
                pass

        # 1. Try RIR GeometryEncoder using native Rhino geometry (GeometryEncoder automatically scales to feet)
        try:
            if hasattr(GeometryEncoder, "ToGeometryObject"):
                rvt_obj = GeometryEncoder.ToGeometryObject(working_geom)
                if rvt_obj:
                    geom_list.Add(rvt_obj)
                    return geom_list
        except Exception:
            pass

        # 2. Try specific ToSolid / ToMesh encoders
        try:
            if isinstance(working_geom, rg.Brep):
                if hasattr(GeometryEncoder, "ToSolid"):
                    solid = GeometryEncoder.ToSolid(working_geom)
                    if solid:
                        geom_list.Add(solid)
                        return geom_list
            elif isinstance(working_geom, rg.Mesh):
                if hasattr(GeometryEncoder, "ToMesh"):
                    rvt_mesh = GeometryEncoder.ToMesh(working_geom)
                    if rvt_mesh:
                        geom_list.Add(rvt_mesh)
                        return geom_list
        except Exception:
            pass

        # 3. Native Revit TessellatedShapeBuilder fallback (uses geom_feet with explicit feet coordinates)
        try:
            working_feet = geom_feet
            if not working_feet:
                working_feet = working_geom.Duplicate()
                if hasattr(working_feet, "Scale"):
                    working_feet.Scale(self.unit_scale)

            meshes = []
            if isinstance(working_feet, rg.Mesh):
                meshes = [working_feet]
            elif isinstance(working_feet, rg.Brep):
                mp = rg.MeshingParameters.Default
                brep_meshes = rg.Mesh.CreateFromBrep(working_feet, mp)
                if brep_meshes:
                    meshes = list(brep_meshes)

            if meshes:
                tsb = DB.TessellatedShapeBuilder()
                tsb.OpenConnectedFaceSet(False)
                face_added = False
                for m in meshes:
                    m.Faces.ConvertQuadsToTriangles()
                    verts = m.Vertices
                    for i in range(m.Faces.Count):
                        f = m.Faces[i]
                        p0 = verts[f.A]
                        p1 = verts[f.B]
                        p2 = verts[f.C]
                        v0 = DB.XYZ(float(p0.X), float(p0.Y), float(p0.Z))
                        v1 = DB.XYZ(float(p1.X), float(p1.Y), float(p1.Z))
                        v2 = DB.XYZ(float(p2.X), float(p2.Y), float(p2.Z))
                        if v0.DistanceTo(v1) > 0.001 and v1.DistanceTo(v2) > 0.001 and v2.DistanceTo(v0) > 0.001:
                            loop_pts = List[DB.XYZ]()
                            loop_pts.Add(v0)
                            loop_pts.Add(v1)
                            loop_pts.Add(v2)
                            tsb.AddFace(DB.TessellatedFace(loop_pts, DB.ElementId.InvalidElementId))
                            face_added = True
                tsb.CloseConnectedFaceSet()
                if face_added:
                    tsb.Target = DB.TessellatedShapeBuilderTarget.AnyGeometry
                    tsb.Fallback = DB.TessellatedShapeBuilderFallback.Mesh
                    tsb.Build()
                    res_objs = tsb.GetBuildResult().GetGeometricalObjects()
                    if res_objs:
                        for go in res_objs:
                            geom_list.Add(go)
        except Exception:
            pass

        return geom_list

    def _rg_loop_to_revit_curveloop(self, rg_loop):
        """Converts a Rhino BrepLoop to a Revit DB.CurveLoop."""
        cl = DB.CurveLoop()
        loop_curve = rg_loop.To3dCurve()
        if not loop_curve:
            return None

        # Explode into segments
        segments = loop_curve.DuplicateSegments()
        if not segments or len(segments) == 0:
            segments = [loop_curve]

        for seg in segments:
            p0 = DB.XYZ(seg.PointAtStart.X, seg.PointAtStart.Y, seg.PointAtStart.Z)
            p1 = DB.XYZ(seg.PointAtEnd.X, seg.PointAtEnd.Y, seg.PointAtEnd.Z)
            if p0.DistanceTo(p1) > 0.001:
                if seg.IsLinear(0.001):
                    cl.Append(DB.Line.CreateBound(p0, p1))
                else:
                    pm = DB.XYZ(seg.PointAt(0.5 * (seg.Domain.Min + seg.Domain.Max)).X,
                                seg.PointAt(0.5 * (seg.Domain.Min + seg.Domain.Max)).Y,
                                seg.PointAt(0.5 * (seg.Domain.Min + seg.Domain.Max)).Z)
                    try:
                        arc = DB.Arc.Create(p0, p1, pm)
                        cl.Append(arc)
                    except Exception:
                        cl.Append(DB.Line.CreateBound(p0, p1))

        return cl

    def _curve_loops_to_curve_array(self, curve_loops):
        """Converts IList<CurveLoop> to legacy CurveArray for older Revit APIs."""
        arr = DB.CurveArray()
        for cl in curve_loops:
            for c in cl:
                arr.Append(c)
        return arr

    def _category_str_to_id(self, cat_name):
        """Maps category name string to Revit ElementId."""
        cat_map = {
            "OST_Floors": DB.BuiltInCategory.OST_Floors,
            "OST_Walls": DB.BuiltInCategory.OST_Walls,
            "OST_Roofs": DB.BuiltInCategory.OST_Roofs,
            "OST_Ceilings": DB.BuiltInCategory.OST_Ceilings,
            "OST_Columns": DB.BuiltInCategory.OST_Columns,
            "OST_StructuralColumns": DB.BuiltInCategory.OST_StructuralColumns,
            "OST_Doors": DB.BuiltInCategory.OST_Doors,
            "OST_Windows": DB.BuiltInCategory.OST_Windows,
            "OST_Furniture": DB.BuiltInCategory.OST_Furniture,
            "OST_GenericModel": DB.BuiltInCategory.OST_GenericModel
        }
        bic = cat_map.get(cat_name, DB.BuiltInCategory.OST_GenericModel)
        return DB.ElementId(bic)

    def _get_or_create_bua_area_scheme(self):
        """Finds or creates the 'BUA / GFA Assessment' Area Scheme in Revit."""
        schemes = DB.FilteredElementCollector(self.doc).OfClass(DB.AreaScheme)
        for s in schemes:
            if s.Name == "BUA / GFA Assessment" or s.Name == "Gross Building":
                return s
        return schemes.FirstElement() if schemes.GetElementCount() > 0 else None

    def _get_or_create_area_plan_view(self, level_id, scheme_id):
        """Finds or creates an Area Plan view for the specified level and scheme."""
        views = DB.FilteredElementCollector(self.doc).OfClass(DB.ViewPlan)
        for v in views:
            if v.ViewType == DB.ViewType.AreaPlan and v.GenLevel and v.GenLevel.Id == level_id:
                return v

        try:
            return DB.ViewPlan.CreateAreaPlan(self.doc, scheme_id, level_id)
        except Exception:
            return None
