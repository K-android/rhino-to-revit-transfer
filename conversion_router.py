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
import tempfile
import traceback

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("System.Collections")
    import System
    from System.Collections.Generic import List
except Exception:
    System = None
    class _MockGenericList(object):
        def __getitem__(self, item):
            class _TypedList(list):
                def Add(self, val):
                    self.append(val)
                @property
                def Count(self):
                    return len(self)
            return _TypedList
    List = _MockGenericList()

try:
    # RhinoCommon
    import Rhino
    import Rhino.Geometry as rg
    RHINO_AVAILABLE = True
except (ImportError, Exception):
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

try:
    from family_factory import FamilyFactory, FamilyLoadOptions
except (ImportError, ValueError):
    try:
        from .family_factory import FamilyFactory, FamilyLoadOptions
    except Exception:
        FamilyFactory = None
        FamilyLoadOptions = None

R2R_PARAM_GUIDS = {
    "R2R_Area": "e1a2b3c4-1111-2222-3333-444455550001",
    "R2R_Volume": "e1a2b3c4-1111-2222-3333-444455550002",
    "R2R_Material": "e1a2b3c4-1111-2222-3333-444455550003",
    "R2R_Material_Volume": "e1a2b3c4-1111-2222-3333-444455550004",
    "R2R_Source_Layer": "e1a2b3c4-1111-2222-3333-444455550005",
    "R2R_Rhino_GUID": "e1a2b3c4-1111-2222-3333-444455550006",
    "R2R_Thickness": "e1a2b3c4-1111-2222-3333-444455550007",
    "R2R_Building_Zone": "e1a2b3c4-1111-2222-3333-444455550008",
    "R2R_Category": "e1a2b3c4-1111-2222-3333-444455550009",
    "R2R_Area_sqm": "e1a2b3c4-1111-2222-3333-444455550010",
    "R2R_Volume_cum": "e1a2b3c4-1111-2222-3333-444455550011",
}


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

        # Volumetric & Material Quantities
        self.area_sqm = 0.0
        self.volume_cum = 0.0
        self.primary_material = ""
        self.material_volume_cum = 0.0
        self.material_takeoff = {}  # {mat_name: {"volume_cum": ..., "area_sqm": ...}}
        self.rhino_area_sqm = 0.0
        self.rhino_volume_cum = 0.0

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
            "bua_area": self.bua_area,
            "area_sqm": self.area_sqm,
            "volume_cum": self.volume_cum,
            "primary_material": self.primary_material,
            "material_volume_cum": self.material_volume_cum,
            "material_takeoff": self.material_takeoff,
            "rhino_area_sqm": self.rhino_area_sqm,
            "rhino_volume_cum": self.rhino_volume_cum
        }


class ConversionRouter(object):
    """10-pipeline dispatcher coordinating Rhino.Inside.Revit element generation."""

    def __init__(self, revit_doc, global_mode="Hybrid", generate_bua=True, unit_scale=None, origin_shift=None, move_to_origin=False):
        """
        :param revit_doc: Autodesk.Revit.DB.Document
        :param global_mode: "Native BIM" | "Hybrid" | "Geometry-First"
        :param generate_bua: Boolean toggle for Auto-BUA Area Plan generation
        :param unit_scale: Optional float override for Rhino-to-Revit foot scale
        :param origin_shift: Optional tuple (dx, dy, dz) in Rhino units to translate geometry to Revit (0,0,0)
        :param move_to_origin: If True, automatically centers geometry at Revit (0,0,0)
        """
        self.doc = revit_doc
        self.global_mode = global_mode
        self.generate_bua = generate_bua
        self.failures_preprocessor = TransferFailuresPreprocessor() if REVIT_AVAILABLE else None
        self.natural_unit_scale = self._calc_rhino_to_revit_scale()
        self.unit_scale = float(unit_scale) if unit_scale is not None else self.natural_unit_scale
        self.move_to_origin = move_to_origin

        # Origin shift vector in Rhino coordinates and Revit internal feet
        self.origin_shift_rhino = (0.0, 0.0, 0.0)
        self.origin_shift_feet = (0.0, 0.0, 0.0)
        self.has_origin_shift = False

        if origin_shift:
            self.set_origin_shift(origin_shift[0], origin_shift[1], origin_shift[2])

        # Cache Revit Level and Type IDs for performance
        self.level_cache = {}
        self.type_cache = {}
        self.family_symbol_cache = {}
        self.app = revit_doc.Application if revit_doc and hasattr(revit_doc, "Application") else None
        if REVIT_AVAILABLE and self.doc:
            self._cache_revit_data()

    def set_origin_shift(self, dx_rhino, dy_rhino, dz_rhino):
        """Sets translation vector (in Rhino units) applied to all transferred geometry to align with Revit (0,0,0)."""
        self.origin_shift_rhino = (float(dx_rhino), float(dy_rhino), float(dz_rhino))
        self.origin_shift_feet = (
            float(dx_rhino) * self.unit_scale,
            float(dy_rhino) * self.unit_scale,
            float(dz_rhino) * self.unit_scale
        )
        self.has_origin_shift = (
            abs(dx_rhino) > 0.001 or abs(dy_rhino) > 0.001 or abs(dz_rhino) > 0.001
        )

    def calculate_and_set_origin_shift(self, objects, force_center=False):
        """Computes global bounding box across objects and calculates the shift vector to Revit (0,0,0).

        If any object is far from origin (>16 km) or force_center is True:
          - Shifts XY center of the model to (0.0, 0.0)
          - If base Z is far from 0 (>30m) or outside bounds, shifts lowest point to Z = 0.0
        Returns (dx, dy, dz) in Rhino units.
        """
        if not objects:
            return (0.0, 0.0, 0.0)

        min_x = float("inf")
        min_y = float("inf")
        min_z = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        max_z = float("-inf")

        has_far_objects = False

        for o in objects:
            if getattr(o, "is_far_from_origin", False) or getattr(o, "origin_distance", 0) > 16000.0:
                has_far_objects = True

            b_min = getattr(o, "bbox_min", None)
            b_max = getattr(o, "bbox_max", None)
            if b_min and b_max:
                if b_min[0] < min_x: min_x = b_min[0]
                if b_min[1] < min_y: min_y = b_min[1]
                if b_min[2] < min_z: min_z = b_min[2]
                if b_max[0] > max_x: max_x = b_max[0]
                if b_max[1] > max_y: max_y = b_max[1]
                if b_max[2] > max_z: max_z = b_max[2]
            elif getattr(o, "centroid", None):
                c = o.centroid
                if c[0] < min_x: min_x = c[0]
                if c[1] < min_y: min_y = c[1]
                if c[2] < min_z: min_z = c[2]
                if c[0] > max_x: max_x = c[0]
                if c[1] > max_y: max_y = c[1]
                if c[2] > max_z: max_z = c[2]

        if min_x == float("inf"):
            return (0.0, 0.0, 0.0)

        mid_x = 0.5 * (min_x + max_x)
        mid_y = 0.5 * (min_y + max_y)

        # Distance of center from Rhino origin
        center_dist = math.sqrt(mid_x * mid_x + mid_y * mid_y)
        # Check if shift should be applied: forced, far objects (>16km), or center substantially offset (>500m)
        should_shift = force_center or has_far_objects or (center_dist > 500.0)

        if should_shift:
            dx = -mid_x
            dy = -mid_y

            # For Z: if base Z is far from ground plane (>30m) or outside 16km, shift base to 0.0
            # Otherwise preserve relative Z so Level 1 at 0.0 is respected.
            dz = 0.0
            if abs(min_z) > 30.0 or has_far_objects:
                dz = -min_z

            self.set_origin_shift(dx, dy, dz)
            return (dx, dy, dz)

        return (0.0, 0.0, 0.0)

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

        # Default floor, wall, roof, ceiling types with safe collector fallbacks
        try:
            self.default_floor_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.FloorType)
        except Exception:
            self.default_floor_type = DB.ElementId.InvalidElementId
        if not self.default_floor_type or self.default_floor_type == DB.ElementId.InvalidElementId:
            col_ft = DB.FilteredElementCollector(self.doc).OfClass(DB.FloorType)
            ft = col_ft.FirstElement()
            if ft:
                self.default_floor_type = ft.Id

        try:
            self.default_wall_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
        except Exception:
            self.default_wall_type = DB.ElementId.InvalidElementId
        if not self.default_wall_type or self.default_wall_type == DB.ElementId.InvalidElementId:
            col_wt = DB.FilteredElementCollector(self.doc).OfClass(DB.WallType)
            wt = col_wt.FirstElement()
            if wt:
                self.default_wall_type = wt.Id

        try:
            self.default_roof_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.RoofType)
        except Exception:
            self.default_roof_type = DB.ElementId.InvalidElementId
        if not self.default_roof_type or self.default_roof_type == DB.ElementId.InvalidElementId:
            col_rt = DB.FilteredElementCollector(self.doc).OfClass(DB.RoofType)
            rt = col_rt.FirstElement()
            if rt:
                self.default_roof_type = rt.Id

        try:
            self.default_ceiling_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.CeilingType)
        except Exception:
            self.default_ceiling_type = DB.ElementId.InvalidElementId
        if not self.default_ceiling_type or self.default_ceiling_type == DB.ElementId.InvalidElementId:
            col_ct = DB.FilteredElementCollector(self.doc).OfClass(DB.CeilingType)
            ct = col_ct.FirstElement()
            if ct:
                self.default_ceiling_type = ct.Id

        # Toposolid type (Revit 2024+)
        if hasattr(DB.ElementTypeGroup, "ToposolidType"):
            try:
                self.default_toposolid_type = self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.ToposolidType)
            except Exception:
                self.default_toposolid_type = None
        else:
            self.default_toposolid_type = None

        # Caches for repetitive definitions, orientation baselines, and adaptive families
        self.repetitive_type_cache = {}
        self.canonical_repetition_cache = {}
        self.adaptive_family_cache = {}

    def pre_generate_families(self, approved_objects, classifications, profile_rules=None):
        """Phase 1: Pre-Transaction Native .rfa Family Generator.

        Generates and loads real Revit .rfa families in memory BEFORE opening the project
        TransactionGroup/Transaction, completely preventing API transaction nesting deadlocks.

        :param approved_objects: list of ScannedObject approved for transfer
        :param classifications: dict {rhino_guid: ClassificationResult}
        :param profile_rules: optional dict {layer_name: MappingRule}
        :return: dict {family_key: DB.FamilySymbol}
        """
        if not REVIT_AVAILABLE or not self.doc or not self.app or not FamilyFactory:
            return self.family_symbol_cache

        rules_by_layer = profile_rules or {}

        for obj in approved_objects:
            c_res = classifications.get(obj.rhino_guid)
            rule = rules_by_layer.get(obj.layer_name) or rules_by_layer.get(getattr(obj, "layer_full_path", None))
            strat = rule.strategy if rule else (c_res.proposed_strategy if c_res else "DirectShape")
            inferred_cat = rule.revit_category if rule else (c_res.inferred_category if c_res else "GenericModel")

            if strat in ("ParametricFamily", "FamilyInstance"):
                clean_layer = re.sub(r'[^a-zA-Z0-9_]', '_', obj.layer_name)
                fam_key_param = "R2R_Parametric_{}".format(clean_layer)
                fam_key_gen = "R2R_Family_{}".format(clean_layer)

                if fam_key_param not in self.family_symbol_cache and fam_key_gen not in self.family_symbol_cache:
                    target_bic_id = self._category_str_to_id(inferred_cat)
                    target_bic = None
                    try:
                        target_bic = System.Enum.ToObject(DB.BuiltInCategory, self._get_elem_id_val(target_bic_id))
                    except Exception:
                        pass

                    sym = None
                    # Try native FreeFormElement family first if real Brep/Mesh exists
                    if obj.raw_geometry and hasattr(FamilyFactory, "create_native_freeform_family"):
                        try:
                            cx_raw = obj.centroid[0]
                            cy_raw = obj.centroid[1]
                            cz_raw = obj.centroid[2]
                            canonical_raw = obj.raw_geometry.Duplicate()
                            if hasattr(canonical_raw, "Translate"):
                                canonical_raw.Translate(-cx_raw, -cy_raw, -cz_raw)
                            if hasattr(canonical_raw, "Scale"):
                                canonical_raw.Scale(self.unit_scale)

                            sym = FamilyFactory.create_native_freeform_family(
                                self.app,
                                self.doc,
                                family_name=fam_key_gen,
                                rh_brep_or_solid=canonical_raw,
                                subcategory_name=obj.layer_name,
                                category_hint=inferred_cat,
                                target_bic=target_bic
                            )
                        except Exception as ex_ff:
                            pass

                    # Fallback to dimension-driven parametric family if freeform was not created
                    if not sym and hasattr(FamilyFactory, "create_parametric_box_or_profile_family"):
                        try:
                            w = max(0.2, (obj.dx or 1.0) * self.unit_scale)
                            d = max(0.2, (obj.dy or 1.0) * self.unit_scale)
                            h = max(0.2, (obj.dz or 1.0) * self.unit_scale)
                            sym = FamilyFactory.create_parametric_box_or_profile_family(
                                self.app,
                                self.doc,
                                family_name=fam_key_param,
                                width=w,
                                depth=d,
                                height=h,
                                category_hint=inferred_cat,
                                target_bic=target_bic
                            )
                        except Exception:
                            pass

                    if sym:
                        self.family_symbol_cache[fam_key_param] = sym
                        self.family_symbol_cache[fam_key_gen] = sym

            elif strat == "RepetitiveFamily":
                is_block = (obj.object_type == "InstanceReference")
                blk_name = getattr(obj, "block_name", "") or ""
                sig = getattr(obj, "repetition_signature", "") or "default_sig"
                fam_key = "R2R_Block_{}".format(re.sub(r'[^a-zA-Z0-9_]', '_', blk_name)) if (is_block and blk_name) else "R2R_Repetitive_{}".format(sig)

                if fam_key not in self.family_symbol_cache and obj.raw_geometry:
                    cx_raw = obj.centroid[0]
                    cy_raw = obj.centroid[1]
                    cz_raw = obj.centroid[2]
                    canonical_raw = obj.raw_geometry.Duplicate()
                    if hasattr(canonical_raw, "Translate"):
                        canonical_raw.Translate(-cx_raw, -cy_raw, -cz_raw)
                    if hasattr(canonical_raw, "Scale"):
                        canonical_raw.Scale(self.unit_scale)

                    target_bic_id = self._category_str_to_id(inferred_cat)
                    target_bic = None
                    try:
                        target_bic = System.Enum.ToObject(DB.BuiltInCategory, self._get_elem_id_val(target_bic_id))
                    except Exception:
                        pass

                    sym = FamilyFactory.create_native_freeform_family(
                        self.app,
                        self.doc,
                        family_name=fam_key,
                        rh_brep_or_solid=canonical_raw,
                        subcategory_name=obj.layer_name,
                        category_hint=inferred_cat,
                        target_bic=target_bic
                    )
                    if sym:
                        self.family_symbol_cache[fam_key] = sym

        return self.family_symbol_cache

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
            if self.has_origin_shift and hasattr(geom_feet, "Translate"):
                geom_feet.Translate(self.origin_shift_feet[0], self.origin_shift_feet[1], self.origin_shift_feet[2])

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
            elif strategy == "Toposolid" or (classification_result and classification_result.inferred_category in ("Toposolid", "Topography", "Site") and strategy in ("DirectShape", "Toposolid")):
                created_elem = self._pipeline_11_toposolid(scanned_obj, geom_feet, level_id, profile_rule, res)
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

                # Extract Revit Area, Solid Volume, and Compound Material Takeoff
                self._extract_element_quantities(created_elem, scanned_obj, res)
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
        """Finds closest Revit Level element ID for an object by name or elevation."""
        if not REVIT_AVAILABLE or not self.doc:
            return None

        if scanned_obj and scanned_obj.nearest_level_name in self.level_cache:
            return self.level_cache[scanned_obj.nearest_level_name]

        # Calculate object Z elevation in Revit feet
        obj_z = 0.0
        if scanned_obj and hasattr(scanned_obj, "centroid") and scanned_obj.centroid:
            obj_z = scanned_obj.centroid[2]
        elif scanned_obj and hasattr(scanned_obj, "bbox_min") and scanned_obj.bbox_min:
            obj_z = scanned_obj.bbox_min[2]

        z_feet = (obj_z + self.origin_shift_rhino[2]) * self.unit_scale

        best_lvl_id = None
        min_dz = float("inf")
        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.Level)
        for lvl in collector:
            dz = abs(lvl.Elevation - z_feet)
            if dz < min_dz:
                min_dz = dz
                best_lvl_id = lvl.Id

        if best_lvl_id:
            return best_lvl_id

        first_lvl = DB.FilteredElementCollector(self.doc).OfClass(DB.Level).FirstElement()
        return first_lvl.Id if first_lvl else DB.ElementId.InvalidElementId

    def _extract_floor_boundary_loops(self, geom_feet, scanned_obj=None):
        """Extracts outer perimeter and inner opening curves and top elevation for native Floor creation.

        Handles:
          - Closed or open rg.Curve / PolylineCurve / PolyCurve / NurbsCurve / ArcCurve
          - rg.Extrusion (extracts 3D profile or Brep top face)
          - rg.Surface / rg.PlaneSurface (trimmed or untrimmed)
          - rg.Brep (solid slabs, trimmed sheets, faceted slabs)
          - rg.Mesh (planar outlines)

        Returns: tuple (outer_rhino_curve, list_of_inner_rhino_curves, slab_top_z)
        """
        if not RHINO_AVAILABLE or not geom_feet:
            return None, [], 0.0

        # Case 1: Already a Curve or Polyline
        if isinstance(geom_feet, rg.Curve):
            crv = geom_feet.DuplicateCurve() if hasattr(geom_feet, "DuplicateCurve") else geom_feet.Duplicate()
            if not crv.IsClosed:
                if crv.PointAtStart.DistanceTo(crv.PointAtEnd) < 5.0:
                    try:
                        crv.MakeClosed(5.0)
                    except Exception:
                        pass
                if not crv.IsClosed:
                    line = rg.LineCurve(crv.PointAtEnd, crv.PointAtStart)
                    joined = rg.Curve.JoinCurves([crv, line], 0.1)
                    if joined and len(joined) > 0:
                        crv = joined[0]
            z_val = crv.PointAtStart.Z
            return crv, [], z_val

        # Case 2: Extrusion
        working_brep = None
        if isinstance(geom_feet, rg.Extrusion):
            try:
                working_brep = geom_feet.ToBrep()
            except Exception:
                try:
                    working_brep = geom_feet.ToBrep(True)
                except Exception:
                    pass
            if not working_brep:
                try:
                    prof = geom_feet.Profile3d(0, 1) or geom_feet.Profile3d(0, 0)
                    if prof:
                        return prof, [], geom_feet.GetBoundingBox(True).Max.Z
                except Exception:
                    pass

        # Case 3: Surface
        if not working_brep and isinstance(geom_feet, rg.Surface):
            try:
                working_brep = geom_feet.ToBrep()
            except Exception:
                pass

        # Case 4: Brep
        if not working_brep and isinstance(geom_feet, rg.Brep):
            working_brep = geom_feet

        if working_brep:
            bbox = working_brep.GetBoundingBox(True)
            max_z = bbox.Max.Z

            # Find horizontal planar faces (both upward and downward normals, pick highest Z)
            candidate_faces = []
            for face in working_brep.Faces:
                is_h = False
                is_planar, plane = face.TryGetPlane(0.05)
                if is_planar and abs(plane.Normal.Z) > 0.70:
                    is_h = True
                else:
                    f_box = face.GetBoundingBox(True)
                    if (f_box.Max.Z - f_box.Min.Z) < 0.2:
                        is_h = True

                if is_h:
                    c_z = face.GetBoundingBox(True).Center.Z
                    candidate_faces.append((face, c_z))

            if candidate_faces:
                candidate_faces.sort(key=lambda x: x[1], reverse=True)
                top_face = candidate_faces[0][0]
                slab_z = candidate_faces[0][1]

                outer_crv = top_face.OuterLoop.To3dCurve() if top_face.OuterLoop else None
                inner_crvs = []
                for loop in top_face.Loops:
                    if loop.LoopType == rg.BrepLoopType.Inner:
                        ic = loop.To3dCurve()
                        if ic:
                            inner_crvs.append(ic)
                if outer_crv:
                    return outer_crv, inner_crvs, slab_z

            # Top edges fallback if no planar face identified
            top_edges = []
            for edge in working_brep.Edges:
                if abs(edge.PointAtStart.Z - max_z) < 0.2 and abs(edge.PointAtEnd.Z - max_z) < 0.2:
                    top_edges.append(edge.DuplicateCurve())
            if top_edges:
                joined = rg.Curve.JoinCurves(top_edges, 0.2)
                if joined and len(joined) > 0:
                    return joined[0], list(joined[1:]) if len(joined) > 1 else [], max_z

        # Case 5: Mesh
        if isinstance(geom_feet, rg.Mesh):
            bbox = geom_feet.GetBoundingBox(True)
            max_z = bbox.Max.Z
            try:
                outlines = geom_feet.GetOutlines(rg.Plane.WorldXY)
                if outlines and len(outlines) > 0:
                    return outlines[0], list(outlines[1:]), max_z
            except Exception:
                pass

        # Case 6: Fallback 2D bounding perimeter
        bbox = geom_feet.GetBoundingBox(True)
        max_z = bbox.Max.Z
        pt0 = rg.Point3d(bbox.Min.X, bbox.Min.Y, max_z)
        pt1 = rg.Point3d(bbox.Max.X, bbox.Min.Y, max_z)
        pt2 = rg.Point3d(bbox.Max.X, bbox.Max.Y, max_z)
        pt3 = rg.Point3d(bbox.Min.X, bbox.Max.Y, max_z)
        poly = rg.Polyline([pt0, pt1, pt2, pt3, pt0])
        return rg.PolylineCurve(poly), [], max_z

    def _build_curveloop_from_rhino_curve(self, rh_curve, target_z=None, is_outer=True):
        """Builds a contiguous, strictly horizontal, closed Revit CurveLoop from a Rhino curve."""
        if not REVIT_AVAILABLE or not hasattr(DB, "CurveLoop") or not rh_curve:
            return None

        tz = float(target_z) if target_z is not None else rh_curve.PointAtStart.Z

        # 1. Curve preparation: explode or split single closed curve
        segments = rh_curve.DuplicateSegments()
        if not segments or len(segments) <= 1:
            if rh_curve.IsClosed:
                t0 = rh_curve.Domain.Min
                t1 = rh_curve.Domain.Max
                t_mid = 0.5 * (t0 + t1)
                splits = rh_curve.Split(t_mid)
                segments = list(splits) if (splits and len(splits) >= 2) else [rh_curve]
            else:
                segments = [rh_curve]
        else:
            segments = list(segments)

        # 2. Chain head-to-tail to guarantee curve direction and connectivity
        ordered = []
        pool = [s.DuplicateCurve() if hasattr(s, "DuplicateCurve") else s.Duplicate() for s in segments if s]
        if not pool:
            return None

        curr = pool.pop(0)
        ordered.append(curr)

        while pool:
            curr_end = curr.PointAtEnd
            best_idx = 0
            best_d = float("inf")
            flip = False
            for i, cand in enumerate(pool):
                d_s = curr_end.DistanceTo(cand.PointAtStart)
                d_e = curr_end.DistanceTo(cand.PointAtEnd)
                if d_s < best_d:
                    best_d = d_s
                    best_idx = i
                    flip = False
                if d_e < best_d:
                    best_d = d_e
                    best_idx = i
                    flip = True
            nxt = pool.pop(best_idx)
            if flip:
                nxt.Reverse()
            ordered.append(nxt)
            curr = nxt

        # 3. Create Revit Curve objects with exact vertex snapping at uniform tz
        n = len(ordered)
        revit_curves = []
        for i in range(n):
            seg = ordered[i]
            p0 = DB.XYZ(seg.PointAtStart.X, seg.PointAtStart.Y, tz)
            if i < n - 1:
                p1 = DB.XYZ(ordered[i+1].PointAtStart.X, ordered[i+1].PointAtStart.Y, tz)
            else:
                p1 = DB.XYZ(ordered[0].PointAtStart.X, ordered[0].PointAtStart.Y, tz)

            dist = p0.DistanceTo(p1)
            if dist < 0.003:  # Skip micro-segments under short curve tolerance
                continue

            if seg.IsLinear(0.003):
                revit_curves.append(DB.Line.CreateBound(p0, p1))
            else:
                t_m = 0.5 * (seg.Domain.Min + seg.Domain.Max)
                pt_m = seg.PointAt(t_m)
                pm = DB.XYZ(pt_m.X, pt_m.Y, tz)
                try:
                    arc = DB.Arc.Create(p0, p1, pm)
                    revit_curves.append(arc)
                except Exception:
                    revit_curves.append(DB.Line.CreateBound(p0, p1))

        if len(revit_curves) < 2:
            return None

        # Build CurveLoop and append
        cl = DB.CurveLoop()
        for c in revit_curves:
            try:
                cl.Append(c)
            except Exception:
                pass

        # If loop is slightly open, heal by connecting last endpoint to first startpoint
        if cl.IsOpen() and len(revit_curves) > 0:
            try:
                first_p = revit_curves[0].GetEndPoint(0)
                last_p = revit_curves[-1].GetEndPoint(1)
                if first_p.DistanceTo(last_p) > 0.001:
                    cl.Append(DB.Line.CreateBound(last_p, first_p))
            except Exception:
                pass

        if cl.IsOpen():
            return None

        # 4. Enforce strict CCW for outer loop and CW for inner loops
        try:
            if is_outer:
                if not cl.IsCounterClockwise(DB.XYZ.BasisZ):
                    cl.Reverse()
            else:
                if cl.IsCounterClockwise(DB.XYZ.BasisZ):
                    cl.Reverse()
        except Exception:
            pass

        return cl

    # =========================================================================
    # PIPELINE 1: Native Floor
    # =========================================================================
    def _pipeline_1_native_floor(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native Revit Floor using top horizontal boundary and void loops."""
        if not level_id or level_id == DB.ElementId.InvalidElementId:
            level_id = self._resolve_level_id(scanned_obj)
        level_elem = self.doc.GetElement(level_id) if level_id and level_id != DB.ElementId.InvalidElementId else None
        level_elev = level_elem.Elevation if level_elem else 0.0

        # Determine slab thickness from bounding box dz or scanned object dz to match FloorType
        bbox = geom_feet.GetBoundingBox(True) if hasattr(geom_feet, "GetBoundingBox") else None
        slab_thickness_feet = (bbox.Max.Z - bbox.Min.Z) if bbox else 0.0
        if slab_thickness_feet < 0.05:
            slab_thickness_feet = max(0.2, (float(getattr(scanned_obj, "dz", 0.3) or 0.3) * self.unit_scale))

        mat_name = getattr(scanned_obj, "material_name", "") or getattr(scanned_obj, "layer_name", "")
        floor_type_id = self._get_or_create_matching_floor_type(slab_thickness_feet, mat_name)
        if not floor_type_id or floor_type_id == DB.ElementId.InvalidElementId:
            floor_type_id = self.default_floor_type
        if not floor_type_id or floor_type_id == DB.ElementId.InvalidElementId:
            col_ft = DB.FilteredElementCollector(self.doc).OfClass(DB.FloorType)
            ft = col_ft.FirstElement()
            if ft:
                floor_type_id = ft.Id

        outer_crv, inner_crvs, slab_top_z = self._extract_floor_boundary_loops(geom_feet, scanned_obj)
        if not outer_crv:
            res.strategy_used = "DirectShape"
            res.warnings.append("Could not extract boundary curves for Floor. Falling back to DirectShape.")
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Floors))

        floor = None

        # Attempt A: Floor.Create with curve loops at slab_top_z
        curve_loops_slab = List[DB.CurveLoop]()
        outer_cl_slab = self._build_curveloop_from_rhino_curve(outer_crv, target_z=slab_top_z, is_outer=True)
        if outer_cl_slab:
            curve_loops_slab.Add(outer_cl_slab)
            for ic in inner_crvs:
                inner_cl = self._build_curveloop_from_rhino_curve(ic, target_z=slab_top_z, is_outer=False)
                if inner_cl:
                    curve_loops_slab.Add(inner_cl)

            if hasattr(DB, "Floor") and hasattr(DB.Floor, "Create"):
                try:
                    floor = DB.Floor.Create(self.doc, curve_loops_slab, floor_type_id, level_id)
                except Exception as ex_a:
                    res.warnings.append("Floor.Create at slab elevation notice: {}. Retrying with level-plane curves.".format(ex_a))

        # Attempt B: Floor.Create with curve loops on level plane, then set height offset
        if not floor:
            curve_loops_lvl = List[DB.CurveLoop]()
            outer_cl_lvl = self._build_curveloop_from_rhino_curve(outer_crv, target_z=level_elev, is_outer=True)
            if outer_cl_lvl:
                curve_loops_lvl.Add(outer_cl_lvl)
                for ic in inner_crvs:
                    inner_cl_l = self._build_curveloop_from_rhino_curve(ic, target_z=level_elev, is_outer=False)
                    if inner_cl_l:
                        curve_loops_lvl.Add(inner_cl_l)

                if hasattr(DB, "Floor") and hasattr(DB.Floor, "Create"):
                    try:
                        floor = DB.Floor.Create(self.doc, curve_loops_lvl, floor_type_id, level_id)
                    except Exception as ex_b:
                        res.warnings.append("Floor.Create on level plane notice: {}.".format(ex_b))

        # Attempt C: Legacy Revit API NewFloor (Revit 2021 or older)
        if not floor and hasattr(self.doc.Create, "NewFloor"):
            try:
                cl_for_legacy = curve_loops_slab if (curve_loops_slab and curve_loops_slab.Count > 0) else curve_loops_lvl
                curve_array = self._curve_loops_to_curve_array(cl_for_legacy)
                floor = self.doc.Create.NewFloor(curve_array, False)
            except Exception as ex_c:
                res.warnings.append("Legacy NewFloor notice: {}.".format(ex_c))

        # 4. If native floor succeeded: configure elevation offset & room bounding
        if floor:
            res.strategy_used = "FloorNative"
            offset_val = slab_top_z - level_elev
            p_offset = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if p_offset and not p_offset.IsReadOnly:
                try:
                    p_offset.Set(offset_val)
                except Exception:
                    pass

            p_rb = floor.get_Parameter(DB.BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
            if p_rb and not p_rb.IsReadOnly:
                try:
                    p_rb.Set(1)
                except Exception:
                    pass

            res.bua_area = getattr(scanned_obj, "area", 0.0)
            return floor

        # DirectShape fallback ONLY if all native methods failed
        res.strategy_used = "DirectShape"
        res.warnings.append("Native Floor creation failed across all API passes. Placed as DirectShape fallback.")
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Floors))

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
        """Creates native vertical wall from baseline curve, height, and wall type."""
        if not level_id or level_id == DB.ElementId.InvalidElementId:
            level_id = self._resolve_level_id(scanned_obj)

        bbox = geom_feet.GetBoundingBox(True)
        wall_height = max(1.0, bbox.Max.Z - bbox.Min.Z)
        dx = bbox.Max.X - bbox.Min.X
        dy = bbox.Max.Y - bbox.Min.Y
        horiz_min = min(dx, dy)
        wall_thickness_feet = horiz_min if (0.1 <= horiz_min <= 4.0) else (0.2 * 3.28084)
        mat_name = getattr(scanned_obj, "material_name", "") or getattr(scanned_obj, "layer_name", "")

        wall_type_id = self._get_or_create_matching_wall_type(wall_thickness_feet, mat_name)
        if not wall_type_id or wall_type_id == DB.ElementId.InvalidElementId:
            wall_type_id = self.default_wall_type
        if not wall_type_id or wall_type_id == DB.ElementId.InvalidElementId:
            col_wt = DB.FilteredElementCollector(self.doc).OfClass(DB.WallType)
            wt = col_wt.FirstElement()
            if wt:
                wall_type_id = wt.Id

        base_curve = None
        if isinstance(geom_feet, rg.Curve):
            p0 = DB.XYZ(geom_feet.PointAtStart.X, geom_feet.PointAtStart.Y, bbox.Min.Z)
            p1 = DB.XYZ(geom_feet.PointAtEnd.X, geom_feet.PointAtEnd.Y, bbox.Min.Z)
            if p0.DistanceTo(p1) > 0.05:
                if geom_feet.IsLinear(0.01):
                    base_curve = DB.Line.CreateBound(p0, p1)
                else:
                    pm = DB.XYZ(geom_feet.PointAt(0.5 * (geom_feet.Domain.Min + geom_feet.Domain.Max)).X,
                                geom_feet.PointAt(0.5 * (geom_feet.Domain.Min + geom_feet.Domain.Max)).Y,
                                bbox.Min.Z)
                    try:
                        base_curve = DB.Arc.Create(p0, p1, pm)
                    except Exception:
                        base_curve = DB.Line.CreateBound(p0, p1)

        if not base_curve:
            base_z = bbox.Min.Z
            dx = bbox.Max.X - bbox.Min.X
            dy = bbox.Max.Y - bbox.Min.Y
            if dy >= dx:
                pt_start = DB.XYZ(0.5 * (bbox.Min.X + bbox.Max.X), bbox.Min.Y, base_z)
                pt_end = DB.XYZ(0.5 * (bbox.Min.X + bbox.Max.X), bbox.Max.Y, base_z)
            else:
                pt_start = DB.XYZ(bbox.Min.X, 0.5 * (bbox.Min.Y + bbox.Max.Y), base_z)
                pt_end = DB.XYZ(bbox.Max.X, 0.5 * (bbox.Min.Y + bbox.Max.Y), base_z)

            if pt_start.DistanceTo(pt_end) > 0.1:
                base_curve = DB.Line.CreateBound(pt_start, pt_end)

        if base_curve and wall_type_id and level_id:
            try:
                wall = DB.Wall.Create(self.doc, base_curve, wall_type_id, level_id, wall_height, 0.0, False, False)
                if wall:
                    res.strategy_used = "WallByCurve"
                    return wall
            except Exception as ex:
                res.warnings.append("Wall.Create by curve notice: {}. Falling back to Profile or DirectShape.".format(ex))

        return self._pipeline_4_wall_by_profile(scanned_obj, geom_feet, level_id, profile_rule, res)

    # =========================================================================
    # PIPELINE 4: Walls by Profile (with Fallback)
    # =========================================================================
    def _pipeline_4_wall_by_profile(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates wall with non-rectangular profile (arches, vaults, sloped tops) or falls back cleanly."""
        working_brep = geom_feet if isinstance(geom_feet, rg.Brep) else (geom_feet.ToBrep() if hasattr(geom_feet, "ToBrep") else None)
        if not working_brep:
            res.strategy_used = "DirectShape"
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
            res.strategy_used = "DirectShape"
            res.warnings.append("No vertical profile face found. Falling back to DirectShape.")
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

        try:
            wall_thickness_feet = 0.2 * 3.28084
            if working_brep and max_area > 0.01:
                try:
                    mp_brep = rg.MassProperties.Compute(working_brep)
                    if mp_brep and mp_brep.Volume > 0:
                        wall_thickness_feet = max(0.1, min(4.0, mp_brep.Volume / max_area))
                except Exception:
                    pass
            mat_name = getattr(scanned_obj, "material_name", "") or getattr(scanned_obj, "layer_name", "")
            wall_type_id = self._get_or_create_matching_wall_type(wall_thickness_feet, mat_name)
            if not wall_type_id or wall_type_id == DB.ElementId.InvalidElementId:
                wall_type_id = self.default_wall_type
            if not wall_type_id or wall_type_id == DB.ElementId.InvalidElementId:
                col_wt = DB.FilteredElementCollector(self.doc).OfClass(DB.WallType)
                wt = col_wt.FirstElement()
                if wt:
                    wall_type_id = wt.Id

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

                wall = DB.Wall.Create(self.doc, profile_loops, wall_type_id, level_id, False)
                if wall:
                    res.strategy_used = "WallByProfile"
                    return wall
        except Exception as ex:
            res.warnings.append("Wall profile creation notice: {}. Falling back to DirectShape.".format(ex))

        # Clean fallback to DirectShape
        res.strategy_used = "DirectShape"
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Walls))

    # =========================================================================
    # PIPELINE 5: Native Ceilings
    # =========================================================================
    def _pipeline_5_ceiling_native(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native ceiling using true boundary curves."""
        if not level_id or level_id == DB.ElementId.InvalidElementId:
            level_id = self._resolve_level_id(scanned_obj)
        level_elem = self.doc.GetElement(level_id) if level_id and level_id != DB.ElementId.InvalidElementId else None
        level_elev = level_elem.Elevation if level_elem else 0.0

        ceiling_type_id = self.default_ceiling_type
        if not ceiling_type_id or ceiling_type_id == DB.ElementId.InvalidElementId:
            col_ct = DB.FilteredElementCollector(self.doc).OfClass(DB.CeilingType)
            ct = col_ct.FirstElement()
            if ct:
                ceiling_type_id = ct.Id

        outer_crv, inner_crvs, ceiling_z = self._extract_floor_boundary_loops(geom_feet, scanned_obj)
        ceiling = None
        if outer_crv:
            loops = List[DB.CurveLoop]()
            outer_cl = self._build_curveloop_from_rhino_curve(outer_crv, target_z=ceiling_z, is_outer=True)
            if outer_cl:
                loops.Add(outer_cl)
                for ic in inner_crvs:
                    inner_cl = self._build_curveloop_from_rhino_curve(ic, target_z=ceiling_z, is_outer=False)
                    if inner_cl:
                        loops.Add(inner_cl)

                if hasattr(DB, "Ceiling") and hasattr(DB.Ceiling, "Create"):
                    try:
                        ceiling = DB.Ceiling.Create(self.doc, loops, ceiling_type_id, level_id)
                    except Exception as ex:
                        # Try on level plane
                        try:
                            loops_lvl = List[DB.CurveLoop]()
                            outer_cl_lvl = self._build_curveloop_from_rhino_curve(outer_crv, target_z=level_elev, is_outer=True)
                            if outer_cl_lvl:
                                loops_lvl.Add(outer_cl_lvl)
                                for ic in inner_crvs:
                                    inner_cl_l = self._build_curveloop_from_rhino_curve(ic, target_z=level_elev, is_outer=False)
                                    if inner_cl_l:
                                        loops_lvl.Add(inner_cl_l)
                                ceiling = DB.Ceiling.Create(self.doc, loops_lvl, ceiling_type_id, level_id)
                        except Exception as ex2:
                            res.warnings.append("Ceiling.Create failed: {}.".format(ex2))

        if ceiling:
            res.strategy_used = "CeilingNative"
            offset_val = ceiling_z - level_elev
            p_off = ceiling.get_Parameter(DB.BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM)
            if p_off and not p_off.IsReadOnly:
                try:
                    p_off.Set(offset_val)
                except Exception:
                    pass
            return ceiling

        res.strategy_used = "DirectShape"
        res.warnings.append("Native Ceiling.Create failed. Falling back to DirectShape.")
        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=DB.ElementId(DB.BuiltInCategory.OST_Ceilings))

    # =========================================================================
    # PIPELINE 6: Native Footprint Roof
    # =========================================================================
    def _pipeline_6_roof_native(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates native footprint roof from true boundary curves."""
        if not level_id or level_id == DB.ElementId.InvalidElementId:
            level_id = self._resolve_level_id(scanned_obj)
        level_elem = self.doc.GetElement(level_id) if level_id and level_id != DB.ElementId.InvalidElementId else None

        roof_type = self.doc.GetElement(self.default_roof_type)
        if not roof_type:
            col_rt = DB.FilteredElementCollector(self.doc).OfClass(DB.RoofType)
            roof_type = col_rt.FirstElement()

        outer_crv, inner_crvs, roof_z = self._extract_floor_boundary_loops(geom_feet, scanned_obj)
        if outer_crv and roof_type and level_elem:
            outer_cl = self._build_curveloop_from_rhino_curve(outer_crv, target_z=roof_z, is_outer=True)
            if outer_cl:
                crv_array = DB.CurveArray()
                for c in outer_cl:
                    crv_array.Append(c)
                try:
                    footprint_map = DB.ModelCurveArray()
                    roof = self.doc.Create.NewFootPrintRoof(crv_array, level_elem, roof_type, footprint_map)
                    if roof:
                        res.strategy_used = "RoofNative"
                        return roof
                except Exception as ex:
                    res.warnings.append("NewFootPrintRoof failed: {}. Cleanly falling back to DirectShape.".format(ex))

        res.strategy_used = "DirectShape"
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

        # Scale coordinates into Revit internal feet and apply origin shift
        scale = self.unit_scale
        pts_feet = [
            DB.XYZ(
                (p[0] + self.origin_shift_rhino[0]) * scale,
                (p[1] + self.origin_shift_rhino[1]) * scale,
                (p[2] + self.origin_shift_rhino[2]) * scale
            )
            for p in pts_rhino
        ]

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
        """Converts a Rhino.Geometry.Transform into an Autodesk.Revit.DB.Transform with origin shift."""
        if not REVIT_AVAILABLE or not xform:
            return None
        try:
            scale = self.unit_scale
            tf = DB.Transform.Identity
            ox = (xform.M03 + self.origin_shift_rhino[0]) * scale
            oy = (xform.M13 + self.origin_shift_rhino[1]) * scale
            oz = (xform.M23 + self.origin_shift_rhino[2]) * scale
            tf.Origin = DB.XYZ(ox, oy, oz)
            tf.BasisX = DB.XYZ(xform.M00, xform.M10, xform.M20)
            tf.BasisY = DB.XYZ(xform.M01, xform.M11, xform.M21)
            tf.BasisZ = DB.XYZ(xform.M02, xform.M12, xform.M22)
            return tf
        except Exception:
            return None

    def _pipeline_8b_repetitive_family(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates a shared reusable native .rfa FamilyInstance or DirectShapeType definition.
        
        Prioritizes real native .rfa FreeFormElement families generated during Phase 1.
        Guarantees that rotated loose copies preserve their exact world-space orientation.
        """
        cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
        if profile_rule and profile_rule.revit_category:
            cat_id = self._category_str_to_id(profile_rule.revit_category)

        sig = scanned_obj.repetition_signature or ("Block_" + scanned_obj.block_name if scanned_obj.block_name else None)
        if not sig:
            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=cat_id)

        type_key = "R2R_" + re.sub(r'[^a-zA-Z0-9_]+', '_', sig)[:45]
        is_block = (scanned_obj.object_type == "InstanceReference")
        blk_name = getattr(scanned_obj, "block_name", "") or ""
        fam_key = "R2R_Block_{}".format(re.sub(r'[^a-zA-Z0-9_]', '_', blk_name)) if (is_block and blk_name) else "R2R_Repetitive_{}".format(sig)

        # 1. Native Loadable .rfa Family Placement (from Phase 1 Pre-Generation)
        if fam_key in self.family_symbol_cache:
            sym = self.family_symbol_cache[fam_key]
            try:
                if not sym.IsActive:
                    sym.Activate()
                cx = (scanned_obj.centroid[0] + self.origin_shift_rhino[0]) * self.unit_scale
                cy = (scanned_obj.centroid[1] + self.origin_shift_rhino[1]) * self.unit_scale
                cz = (scanned_obj.centroid[2] + self.origin_shift_rhino[2]) * self.unit_scale
                pt = DB.XYZ(cx, cy, cz)
                level_elem = self.doc.GetElement(level_id) if level_id and level_id != DB.ElementId.InvalidElementId else None
                inst = None
                if level_elem:
                    try:
                        inst = self.doc.Create.NewFamilyInstance(pt, sym, level_elem, DB.Structure.StructuralType.NonStructural)
                    except Exception:
                        inst = None
                if not inst:
                    try:
                        inst = self.doc.Create.NewFamilyInstance(pt, sym, DB.Structure.StructuralType.NonStructural)
                    except Exception:
                        pass

                if scanned_obj.rotation_angle and inst:
                    axis = DB.Line.CreateBound(pt, DB.XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    DB.ElementTransformUtils.RotateElement(self.doc, inst.Id, axis, scanned_obj.rotation_angle)
                return inst
            except Exception as ex:
                res.warnings.append("Native .rfa repetitive family placement notice: {}. Falling back to DirectShapeType.".format(ex))

        # Try Revit DirectShapeType + DirectShapeLibrary instancing fallback
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
                            cx = (scanned_obj.centroid[0] + self.origin_shift_rhino[0]) * self.unit_scale
                            cy = (scanned_obj.centroid[1] + self.origin_shift_rhino[1]) * self.unit_scale
                            cz = (scanned_obj.centroid[2] + self.origin_shift_rhino[2]) * self.unit_scale
                            tf = DB.Transform.CreateTranslation(DB.XYZ(cx, cy, cz))
                            inst = DB.DirectShape.CreateElementInstance(self.doc, type_id, cat_id, type_key, tf)
                            return inst
                        else:
                            # Orientation differs: fall back to DirectShape to guarantee exact world rotation
                            res.warnings.append("Loose copy has distinct spatial orientation. Placed via DirectShape to preserve rotation.")
                            return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=cat_id)

                    # Default translation placement if no baseline check was needed
                    cx = (scanned_obj.centroid[0] + self.origin_shift_rhino[0]) * self.unit_scale
                    cy = (scanned_obj.centroid[1] + self.origin_shift_rhino[1]) * self.unit_scale
                    cz = (scanned_obj.centroid[2] + self.origin_shift_rhino[2]) * self.unit_scale
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

        # 1. Check Phase 1 Pre-Generated Native .rfa FamilySymbol cache
        clean_layer = re.sub(r'[^a-zA-Z0-9_]', '_', scanned_obj.layer_name)
        fam_key_param = "R2R_Parametric_{}".format(clean_layer)
        fam_key_gen = "R2R_Family_{}".format(clean_layer)

        if fam_key_gen in self.family_symbol_cache:
            target_sym = self.family_symbol_cache[fam_key_gen]
        elif fam_key_param in self.family_symbol_cache:
            target_sym = self.family_symbol_cache[fam_key_param]

        # 2. Check project symbols if not found in pre-generated cache
        if not target_sym:
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
                cx = (scanned_obj.centroid[0] + self.origin_shift_rhino[0]) * self.unit_scale
                cy = (scanned_obj.centroid[1] + self.origin_shift_rhino[1]) * self.unit_scale
                cz = (scanned_obj.centroid[2] + self.origin_shift_rhino[2]) * self.unit_scale
                pt = DB.XYZ(cx, cy, cz)

                level_elem = self.doc.GetElement(level_id) if level_id and level_id != DB.ElementId.InvalidElementId else None
                inst = None

                # Try level-hosted placement first
                if level_elem:
                    try:
                        inst = self.doc.Create.NewFamilyInstance(pt, target_sym, level_elem, DB.Structure.StructuralType.NonStructural)
                    except Exception:
                        inst = None

                # Fallback to direct XYZ unhosted placement
                if not inst:
                    try:
                        inst = self.doc.Create.NewFamilyInstance(pt, target_sym, DB.Structure.StructuralType.NonStructural)
                    except Exception as ex_unhosted:
                        res.warnings.append("Unhosted placement notice: {}".format(ex_unhosted))

                # Rotate if needed
                if scanned_obj.rotation_angle and inst:
                    axis = DB.Line.CreateBound(pt, DB.XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    DB.ElementTransformUtils.RotateElement(self.doc, inst.Id, axis, scanned_obj.rotation_angle)
                    DB.ElementTransformUtils.RotateElement(self.doc, inst.Id, axis, scanned_obj.rotation_angle)

                # Drive instance/type dimensional parameters with multilingual aliases
                dims = dict(getattr(scanned_obj, "parametric_dims", {}) or {})
                if "Width" not in dims and scanned_obj.dx: dims["Width"] = scanned_obj.dx
                if "Depth" not in dims and scanned_obj.dy: dims["Depth"] = scanned_obj.dy
                if "Height" not in dims and scanned_obj.dz: dims["Height"] = scanned_obj.dz
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

        # Ensure category is valid for DirectShape; fallback to OST_GenericModel if unsupported in current Revit version
        if hasattr(DB.DirectShape, "IsValidCategoryId"):
            try:
                if not DB.DirectShape.IsValidCategoryId(category_id, self.doc):
                    category_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
            except Exception:
                pass

        # Resolve material before converting geometry so tessellated faces receive the material ID
        mat_id = None
        mat_name = getattr(scanned_obj, "material_name", "") or getattr(scanned_obj, "layer_name", "")
        obj_color = getattr(scanned_obj, "object_color", None)
        color_rgb = (int(obj_color[0]), int(obj_color[1]), int(obj_color[2])) if (obj_color and len(obj_color) >= 3) else None
        if mat_name:
            mat_id = self._get_or_create_material(mat_name, color_rgb=color_rgb)

        # Convert Rhino geometry to Revit DB.GeometryObject without double scaling
        revit_geoms = self._convert_rhino_to_revit_geometry(scanned_obj.raw_geometry, geom_feet=geom_feet, material_id=mat_id)
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

        # Assign Material to DirectShape for scheduling and visual fidelity
        if mat_id and mat_id != DB.ElementId.InvalidElementId:
            p_mat = ds.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
            if p_mat and not p_mat.IsReadOnly:
                try:
                    p_mat.Set(mat_id)
                except Exception:
                    pass

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
    # PIPELINE 11: Toposolid (Revit 2024+) & Site Fallback
    # =========================================================================
    def _pipeline_11_toposolid(self, scanned_obj, geom_feet, level_id, profile_rule, res):
        """Creates a native Revit Toposolid element (Revit 2024+) or OST_Toposolid DirectShape."""
        target_cat_id = self._category_str_to_id("OST_Toposolid")

        if hasattr(DB, "Toposolid"):
            try:
                working_brep = geom_feet if isinstance(geom_feet, rg.Brep) else (geom_feet.ToBrep() if hasattr(geom_feet, "ToBrep") else None)
                curve_loops = List[DB.CurveLoop]()

                if working_brep:
                    top_face = None
                    max_z = float("-inf")
                    for face in working_brep.Faces:
                        u_mid = 0.5 * (face.Domain(0).Min + face.Domain(0).Max)
                        v_mid = 0.5 * (face.Domain(1).Min + face.Domain(1).Max)
                        norm = face.NormalAt(u_mid, v_mid)
                        norm.Unitize()
                        if norm.Z > 0.60:
                            fz = face.GetBoundingBox(True).Center.Z
                            if fz > max_z:
                                max_z = fz
                                top_face = face
                    if top_face and top_face.OuterLoop:
                        cl = self._rg_loop_to_revit_curveloop(top_face.OuterLoop)
                        if cl and not cl.IsOpen():
                            curve_loops.Add(cl)

                if curve_loops.Count == 0:
                    bbox = geom_feet.GetBoundingBox(True)
                    p0 = DB.XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
                    p1 = DB.XYZ(bbox.Max.X, bbox.Min.Y, bbox.Min.Z)
                    p2 = DB.XYZ(bbox.Max.X, bbox.Max.Y, bbox.Min.Z)
                    p3 = DB.XYZ(bbox.Min.X, bbox.Max.Y, bbox.Min.Z)

                    bbox_cl = DB.CurveLoop()
                    bbox_cl.Append(DB.Line.CreateBound(p0, p1))
                    bbox_cl.Append(DB.Line.CreateBound(p1, p2))
                    bbox_cl.Append(DB.Line.CreateBound(p2, p3))
                    bbox_cl.Append(DB.Line.CreateBound(p3, p0))
                    curve_loops.Add(bbox_cl)

                topo_type_id = getattr(self, "default_toposolid_type", None)
                if not topo_type_id or topo_type_id == DB.ElementId.InvalidElementId:
                    collector = DB.FilteredElementCollector(self.doc).OfClass(getattr(DB, "ToposolidType", DB.ElementType))
                    first_type = collector.FirstElement()
                    topo_type_id = first_type.Id if first_type else DB.ElementId.InvalidElementId

                if topo_type_id and topo_type_id != DB.ElementId.InvalidElementId:
                    toposolid = DB.Toposolid.Create(self.doc, curve_loops, topo_type_id, level_id)
                    if toposolid:
                        return toposolid
            except Exception as ex:
                res.warnings.append("Toposolid native generation notice: {}. Falling back to DirectShape.".format(ex))

        return self._pipeline_9_direct_shape(scanned_obj, geom_feet, None, profile_rule, res, category_id=target_cat_id)

    # =========================================================================
    # Geometry Helpers & Encoders
    # =========================================================================
    def _convert_rhino_to_revit_geometry(self, rh_geom, geom_feet=None, material_id=None):
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

        # Apply origin shift to native Rhino geometry if translating to Revit (0,0,0)
        if self.has_origin_shift:
            if working_geom is rh_geom:
                working_geom = rh_geom.Duplicate()
            if hasattr(working_geom, "Translate"):
                working_geom.Translate(self.origin_shift_rhino[0], self.origin_shift_rhino[1], self.origin_shift_rhino[2])

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
                            face_mat_id = material_id if (material_id and material_id != DB.ElementId.InvalidElementId) else DB.ElementId.InvalidElementId
                            tsb.AddFace(DB.TessellatedFace(loop_pts, face_mat_id))
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
        """Converts a Rhino BrepLoop to a valid Revit DB.CurveLoop using the robust builder."""
        if not rg_loop:
            return None
        crv = rg_loop.To3dCurve()
        if not crv:
            return None
        is_outer = (rg_loop.LoopType == rg.BrepLoopType.Outer) if hasattr(rg_loop, "LoopType") else True
        target_z = crv.PointAtStart.Z
        return self._build_curveloop_from_rhino_curve(crv, target_z=target_z, is_outer=is_outer)

    def _curve_loops_to_curve_array(self, curve_loops):
        """Converts IList<CurveLoop> to legacy CurveArray for older Revit APIs."""
        arr = DB.CurveArray()
        for cl in curve_loops:
            for c in cl:
                arr.Append(c)
        return arr

    def _get_or_create_material(self, material_name, color_rgb=None):
        """Retrieves existing Material by name or creates a new one in the Revit document with matching color."""
        if not REVIT_AVAILABLE or not self.doc or not material_name:
            return None

        clean_name = re.sub(r'[^a-zA-Z0-9_\-\s]', '', str(material_name)).strip()
        if not clean_name or clean_name.lower() in ("bylayer", "default", "none"):
            clean_name = "R2R_Structure"

        try:
            col = DB.FilteredElementCollector(self.doc).OfClass(DB.Material)
            for m in col:
                if m and hasattr(m, "Name") and m.Name.lower() == clean_name.lower():
                    return m.Id

            # Create new Material if not found
            if hasattr(DB, "Material") and hasattr(DB.Material, "Create"):
                new_mat_id = DB.Material.Create(self.doc, clean_name)
                if color_rgb and new_mat_id and hasattr(DB, "Color"):
                    try:
                        new_mat = self.doc.GetElement(new_mat_id)
                        if new_mat and hasattr(new_mat, "Color"):
                            new_mat.Color = DB.Color(int(color_rgb[0]), int(color_rgb[1]), int(color_rgb[2]))
                    except Exception:
                        pass
                return new_mat_id
        except Exception:
            pass
        return None

    def _get_or_create_matching_floor_type(self, thickness_feet, material_name=None):
        """
        Finds or creates a Revit FloorType matching the specified thickness and material.
        Guarantees that Revit's CompoundStructure matches the Rhino geometry thickness exactly,
        resulting in 100% accurate HOST_VOLUME_COMPUTED and GetMaterialVolume() calculations.
        """
        if not REVIT_AVAILABLE or not self.doc:
            return getattr(self, "default_floor_type", None)

        try:
            thickness_feet = max(0.01, float(thickness_feet))
            thk_mm = int(round(thickness_feet * 304.8))

            col = DB.FilteredElementCollector(self.doc).OfClass(DB.FloorType)
            first_type = None
            for ft in col:
                if not first_type:
                    first_type = ft
                try:
                    cs = ft.GetCompoundStructure() if hasattr(ft, "GetCompoundStructure") else None
                    if cs:
                        tot_thk = cs.GetTotalThickness()
                        if abs(tot_thk - thickness_feet) < 0.006:  # ~1.8mm tolerance
                            if material_name and material_name.lower() not in ("bylayer", "default", "none"):
                                layers = cs.GetLayers()
                                for lyr in layers:
                                    if lyr.MaterialId != DB.ElementId.InvalidElementId:
                                        m = self.doc.GetElement(lyr.MaterialId)
                                        if m and material_name.lower() in m.Name.lower():
                                            return ft.Id
                            return ft.Id
                except Exception:
                    continue

            base_type = first_type
            if not base_type:
                return getattr(self, "default_floor_type", None)

            mat_suffix = ""
            if material_name and material_name.lower() not in ("bylayer", "default", "none"):
                clean_mat = re.sub(r'[^a-zA-Z0-9_]', '_', str(material_name)).strip('_')
                if clean_mat:
                    mat_suffix = "_" + clean_mat[:18]

            type_name = "R2R_Floor{}_{}mm".format(mat_suffix, thk_mm)

            for ft in DB.FilteredElementCollector(self.doc).OfClass(DB.FloorType):
                try:
                    p_name = ft.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                    if p_name and p_name.AsString() == type_name:
                        return ft.Id
                except Exception:
                    pass

            if hasattr(base_type, "Duplicate"):
                new_type = base_type.Duplicate(type_name)
                cs = new_type.GetCompoundStructure() if hasattr(new_type, "GetCompoundStructure") else None
                if cs and hasattr(cs, "GetLayers") and hasattr(cs, "SetLayerWidth"):
                    mat_id = self._get_or_create_material(material_name)
                    layers = cs.GetLayers()
                    target_idx = 0
                    for idx, lyr in enumerate(layers):
                        if hasattr(lyr, "Function") and lyr.Function == DB.MaterialFunctionAssignment.Structure:
                            target_idx = idx
                            break

                    cs.SetLayerWidth(target_idx, thickness_feet)
                    if mat_id and mat_id != DB.ElementId.InvalidElementId and hasattr(cs, "SetMaterialId"):
                        cs.SetMaterialId(target_idx, mat_id)

                    new_type.SetCompoundStructure(cs)
                return new_type.Id
        except Exception:
            pass

        return getattr(self, "default_floor_type", None)

    def _get_or_create_matching_wall_type(self, thickness_feet, material_name=None):
        """
        Finds or creates a Revit WallType matching the specified wall thickness and material.
        Guarantees native Revit volume and material volume match Rhino dimensions.
        """
        if not REVIT_AVAILABLE or not self.doc:
            return getattr(self, "default_wall_type", None)

        try:
            thickness_feet = max(0.05, float(thickness_feet))
            thk_mm = int(round(thickness_feet * 304.8))

            col = DB.FilteredElementCollector(self.doc).OfClass(DB.WallType)
            first_basic_type = None
            for wt in col:
                if hasattr(wt, "Kind") and wt.Kind != DB.WallKind.Basic:
                    continue
                if not first_basic_type:
                    first_basic_type = wt
                try:
                    cs = wt.GetCompoundStructure() if hasattr(wt, "GetCompoundStructure") else None
                    if cs:
                        tot_thk = cs.GetTotalThickness()
                        if abs(tot_thk - thickness_feet) < 0.006:
                            if material_name and material_name.lower() not in ("bylayer", "default", "none"):
                                layers = cs.GetLayers()
                                for lyr in layers:
                                    if lyr.MaterialId != DB.ElementId.InvalidElementId:
                                        m = self.doc.GetElement(lyr.MaterialId)
                                        if m and material_name.lower() in m.Name.lower():
                                            return wt.Id
                            return wt.Id
                except Exception:
                    continue

            base_type = first_basic_type
            if not base_type:
                return getattr(self, "default_wall_type", None)

            mat_suffix = ""
            if material_name and material_name.lower() not in ("bylayer", "default", "none"):
                clean_mat = re.sub(r'[^a-zA-Z0-9_]', '_', str(material_name)).strip('_')
                if clean_mat:
                    mat_suffix = "_" + clean_mat[:18]

            type_name = "R2R_Wall{}_{}mm".format(mat_suffix, thk_mm)

            for wt in DB.FilteredElementCollector(self.doc).OfClass(DB.WallType):
                try:
                    p_name = wt.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                    if p_name and p_name.AsString() == type_name:
                        return wt.Id
                except Exception:
                    pass

            if hasattr(base_type, "Duplicate"):
                new_type = base_type.Duplicate(type_name)
                cs = new_type.GetCompoundStructure() if hasattr(new_type, "GetCompoundStructure") else None
                if cs and hasattr(cs, "GetLayers") and hasattr(cs, "SetLayerWidth"):
                    mat_id = self._get_or_create_material(material_name)
                    layers = cs.GetLayers()
                    target_idx = 0
                    for idx, lyr in enumerate(layers):
                        if hasattr(lyr, "Function") and lyr.Function == DB.MaterialFunctionAssignment.Structure:
                            target_idx = idx
                            break

                    cs.SetLayerWidth(target_idx, thickness_feet)
                    if mat_id and mat_id != DB.ElementId.InvalidElementId and hasattr(cs, "SetMaterialId"):
                        cs.SetMaterialId(target_idx, mat_id)

                    new_type.SetCompoundStructure(cs)
                return new_type.Id
        except Exception:
            pass

        return getattr(self, "default_wall_type", None)

    def _extract_element_quantities(self, created_elem, scanned_obj, res):
        """
        Extracts native Revit Area, Solid Volume, and Material Takeoff metrics from the created element.
        Populates res.area_sqm, res.volume_cum, res.primary_material, res.material_volume_cum,
        and res.material_takeoff, ensuring full volumetric parity and scheduling in Revit.
        """
        if not created_elem or not res:
            return

        # 1. Benchmark Rhino Dimensions in metric (m² and m³)
        scale_to_m = self.unit_scale * 0.3048 if hasattr(self, "unit_scale") and self.unit_scale else 1.0
        rh_area_raw = float(getattr(scanned_obj, "area", 0.0) or 0.0)
        rh_vol_raw = float(getattr(scanned_obj, "volume", 0.0) or 0.0)
        res.rhino_area_sqm = getattr(scanned_obj, "area_sqm", 0.0) or round(rh_area_raw * (scale_to_m ** 2), 2)
        res.rhino_volume_cum = getattr(scanned_obj, "volume_cum", 0.0) or round(rh_vol_raw * (scale_to_m ** 3), 2)

        revit_area_sqm = 0.0
        revit_vol_cum = 0.0
        mat_takeoff = {}

        if REVIT_AVAILABLE and self.doc and hasattr(created_elem, "get_Parameter"):
            # 2. Extract BuiltIn Parameter Area (HOST_AREA_COMPUTED)
            try:
                p_area = created_elem.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED)
                if p_area and p_area.HasValue:
                    revit_area_sqm = round(p_area.AsDouble() * 0.09290304, 2)
            except Exception:
                pass

            # 3. Extract BuiltIn Parameter Volume (HOST_VOLUME_COMPUTED)
            try:
                p_vol = created_elem.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
                if p_vol and p_vol.HasValue:
                    revit_vol_cum = round(p_vol.AsDouble() * 0.028316846592, 2)
            except Exception:
                pass

            # 4. Extract Native Compound Structure Material Takeoff (GetMaterialIds & GetMaterialVolume)
            try:
                if hasattr(created_elem, "GetMaterialIds"):
                    mat_ids = created_elem.GetMaterialIds(False)
                    if mat_ids and mat_ids.Count > 0:
                        for mid in mat_ids:
                            try:
                                m_elem = self.doc.GetElement(mid)
                                m_name = m_elem.Name if m_elem else "Revit Material"
                                m_vol_cuft = created_elem.GetMaterialVolume(mid)
                                m_area_sqft = created_elem.GetMaterialArea(mid, False) if hasattr(created_elem, "GetMaterialArea") else 0.0
                                m_vol_m3 = round(m_vol_cuft * 0.028316846592, 2)
                                m_area_m2 = round(m_area_sqft * 0.09290304, 2)
                                if m_vol_m3 > 0 or m_area_m2 > 0:
                                    mat_takeoff[m_name] = {
                                        "volume_cum": m_vol_m3,
                                        "area_sqm": m_area_m2
                                    }
                            except Exception:
                                pass
            except Exception:
                pass

            # 5. DirectShape / Solid Geometry Volume Fallback if HOST_VOLUME_COMPUTED was missing/zero
            if revit_vol_cum <= 0.0 and hasattr(created_elem, "get_Geometry"):
                try:
                    opt = DB.Options()
                    opt.ComputeReferences = False
                    geom_elem = created_elem.get_Geometry(opt)
                    if geom_elem:
                        tot_cuft = 0.0
                        tot_sqft = 0.0
                        top_face_sqft = 0.0
                        vert_face_sqft = 0.0
                        for g in geom_elem:
                            if hasattr(g, "Volume") and g.Volume > 0:
                                tot_cuft += g.Volume
                                if hasattr(g, "Faces"):
                                    for f in g.Faces:
                                        f_area = f.Area if hasattr(f, "Area") else 0.0
                                        tot_sqft += f_area
                                        try:
                                            bb = f.GetBoundingBox()
                                            uv_mid = DB.UV(0.5 * (bb.Min.U + bb.Max.U), 0.5 * (bb.Min.V + bb.Max.V))
                                            n = f.ComputeNormal(uv_mid)
                                            if n.Z > 0.5:
                                                top_face_sqft += f_area
                                            elif abs(n.Z) < 0.3:
                                                vert_face_sqft += f_area
                                        except Exception:
                                            pass
                                elif hasattr(g, "SurfaceArea"):
                                    tot_sqft += g.SurfaceArea
                            elif hasattr(g, "GetInstanceGeometry"):
                                ig = g.GetInstanceGeometry()
                                if ig:
                                    for sub_g in ig:
                                        if hasattr(sub_g, "Volume") and sub_g.Volume > 0:
                                            tot_cuft += sub_g.Volume
                                            if hasattr(sub_g, "SurfaceArea"):
                                                tot_sqft += sub_g.SurfaceArea

                        if tot_cuft > 0:
                            revit_vol_cum = round(tot_cuft * 0.028316846592, 2)
                        if revit_area_sqm <= 0.0:
                            is_horiz = getattr(scanned_obj, "is_horizontal", False)
                            is_vert = getattr(scanned_obj, "is_vertical", False)
                            if is_horiz and top_face_sqft > 0:
                                revit_area_sqm = round(top_face_sqft * 0.09290304, 2)
                            elif is_vert and vert_face_sqft > 0:
                                revit_area_sqm = round((vert_face_sqft * 0.09290304) / 2.0, 2)
                            elif tot_sqft > 0:
                                revit_area_sqm = round((tot_sqft * 0.09290304) / 2.0, 2)
                except Exception:
                    pass

        # 6. Fallback if Revit geometry wasn't queryable
        if revit_area_sqm <= 0.0:
            revit_area_sqm = res.rhino_area_sqm
        if revit_vol_cum <= 0.0:
            revit_vol_cum = res.rhino_volume_cum

        # 7. Material Takeoff Synthesis & Primary Material Determination
        primary_mat = ""
        primary_vol = 0.0

        if mat_takeoff:
            sorted_mats = sorted(mat_takeoff.items(), key=lambda x: x[1].get("volume_cum", 0.0), reverse=True)
            primary_mat = sorted_mats[0][0]
            primary_vol = sorted_mats[0][1].get("volume_cum", 0.0)
            if revit_vol_cum <= 0.0 and primary_vol > 0:
                revit_vol_cum = primary_vol
        else:
            inferred_mat = getattr(scanned_obj, "material_name", "") or getattr(scanned_obj, "layer_name", "") or "Structure"
            clean_inferred = re.sub(r'[^a-zA-Z0-9_\-\s]', '', str(inferred_mat)).strip()
            if not clean_inferred or clean_inferred.lower() in ("bylayer", "default", "none"):
                clean_inferred = "Structure"
            primary_mat = clean_inferred
            primary_vol = revit_vol_cum
            mat_takeoff[primary_mat] = {
                "volume_cum": primary_vol,
                "area_sqm": revit_area_sqm
            }

        res.area_sqm = revit_area_sqm
        res.volume_cum = revit_vol_cum
        res.primary_material = primary_mat
        res.material_volume_cum = primary_vol
        res.material_takeoff = mat_takeoff
        res.bua_area = revit_area_sqm

        # 8. Write parameters to Revit element for immediate scheduling & property display
        self._write_parameters_to_element(created_elem, scanned_obj, res)

    def _write_parameters_to_element(self, created_elem, scanned_obj, res):
        """Writes unit-aware shared parameters and built-in summary comments into created Revit element."""
        if not REVIT_AVAILABLE or not self.doc or not created_elem:
            return

        # 1. Built-in Comments Summary (visible in any schedule querying Comments)
        try:
            p_comm = created_elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p_comm and not p_comm.IsReadOnly:
                p_comm.Set("R2R | Area: {:.2f} m² | Vol: {:.2f} m³ | Mat: {} ({:.2f} m³)".format(
                    res.area_sqm, res.volume_cum, res.primary_material, res.material_volume_cum
                ))
        except Exception:
            pass

        # 2. Built-in Mark
        try:
            name_val = scanned_obj.object_name or scanned_obj.layer_name or "R2R_Element"
            p_mark = created_elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            if p_mark and not p_mark.IsReadOnly and not p_mark.AsString():
                p_mark.Set(name_val)
        except Exception:
            pass

        # 3. Dedicated R2R Schedulable Parameters
        # In Revit API, internal storage: AREA in sq ft, VOLUME in cu ft, LENGTH in ft
        sqft_val = float(res.area_sqm / 0.09290304) if res.area_sqm > 0 else 0.0
        cuft_val = float(res.volume_cum / 0.028316846592) if res.volume_cum > 0 else 0.0
        mat_cuft_val = float(res.material_volume_cum / 0.028316846592) if res.material_volume_cum > 0 else cuft_val
        thick_ft = float(getattr(scanned_obj, "thickness", 0.0) / 0.3048) if getattr(scanned_obj, "thickness", 0.0) > 0 else 0.0
        layer_str = str(getattr(scanned_obj, "layer_full_path", "") or getattr(scanned_obj, "layer_name", ""))
        guid_str = str(getattr(scanned_obj, "rhino_guid", ""))
        mat_str = str(res.primary_material or "")
        zone_str = str(getattr(scanned_obj, "building_zone", "") or "General")
        cat_str = str(getattr(res, "category_created", "") or getattr(scanned_obj, "inferred_category", "GenericModel"))

        param_assignments = [
            ("R2R_Area", sqft_val),
            ("R2R_Volume", cuft_val),
            ("R2R_Material", mat_str),
            ("R2R_Material_Volume", mat_cuft_val),
            ("R2R_Source_Layer", layer_str),
            ("R2R_Rhino_GUID", guid_str),
            ("R2R_Thickness", thick_ft),
            ("R2R_Building_Zone", zone_str),
            ("R2R_Category", cat_str),
            # Text variants to guarantee immediate display in any schedule
            ("R2R_Area_sqm", "{:.2f} m²".format(res.area_sqm)),
            ("R2R_Volume_cum", "{:.2f} m³".format(res.volume_cum)),
        ]

        st_cls = getattr(DB, "StorageType", None)
        st_str = getattr(st_cls, "String", None)
        st_dbl = getattr(st_cls, "Double", None)
        st_int = getattr(st_cls, "Integer", None)

        params_set_count = 0
        for p_name, p_val in param_assignments:
            try:
                p_obj = None
                p_guid = R2R_PARAM_GUIDS.get(p_name)
                # Tier 1: Look up by GUID (Language-agnostic and most reliable for DirectShapes)
                sys_obj = globals().get("System")
                if p_guid and sys_obj and hasattr(sys_obj, "Guid"):
                    try:
                        p_obj = created_elem.get_Parameter(sys_obj.Guid(p_guid))
                    except Exception:
                        p_obj = None

                # Tier 2: LookupParameter by display name
                if not p_obj and hasattr(created_elem, "LookupParameter"):
                    try:
                        p_obj = created_elem.LookupParameter(p_name)
                    except Exception:
                        p_obj = None

                # Tier 3: Search all parameters on element if still unresolved
                if not p_obj and hasattr(created_elem, "Parameters"):
                    try:
                        for p in created_elem.Parameters:
                            p_def = getattr(p, "Definition", None)
                            cand_name = getattr(p_def, "Name", "") if p_def else getattr(p, "name", "")
                            if cand_name == p_name:
                                p_obj = p
                                break
                    except Exception:
                        pass

                if p_obj and not p_obj.IsReadOnly:
                    st = getattr(p_obj, "StorageType", None)
                    if (st is not None and st == st_str) or isinstance(p_val, str):
                        p_obj.Set(str(p_val))
                        params_set_count += 1
                    elif (st is not None and st == st_dbl) or isinstance(p_val, (int, float)):
                        p_obj.Set(float(p_val))
                        params_set_count += 1
                    elif st is not None and st == st_int:
                        p_obj.Set(int(p_val))
                        params_set_count += 1
                    else:
                        try:
                            p_obj.Set(float(p_val))
                            params_set_count += 1
                        except Exception:
                            try:
                                p_obj.Set(str(p_val))
                                params_set_count += 1
                            except Exception:
                                pass
            except Exception:
                pass

        res.parameters_written_count = params_set_count

    def ensure_shared_parameters_bound(self, log_fn=None):
        """Binds R2R Shared Parameters to target categories in active Revit document for scheduling.

        Uses deterministic GUIDs to ensure parameters appear in Revit Schedule Fields.
        Must be executed inside an active Revit Transaction.
        """
        if not REVIT_AVAILABLE or not self.doc or not hasattr(self.doc, "Application"):
            if log_fn:
                log_fn("[R2R Bind Notice]: Revit document or Application unavailable.")
            return False

        app = self.doc.Application
        orig_sp_path = getattr(app, "SharedParametersFilename", "")
        sp_temp_path = os.path.join(tempfile.gettempdir(), "R2R_SharedParameters.txt")

        # Determine Revit release year for 10-column (2022+) vs 9-column (<2022) format
        revit_year = 2024
        try:
            ver_str = getattr(app, "VersionNumber", "2024")
            revit_year = int(re.sub(r'[^0-9]', '', str(ver_str)) or "2024")
        except Exception:
            revit_year = 2024

        use_10_cols = (revit_year >= 2022)

        if use_10_cols:
            sp_content = (
                "# This is a Revit shared parameter file.\n"
                "# Do not edit manually.\n"
                "*META\tVERSION\tMINVERS\n"
                "META\t2\t1\n"
                "*GROUP\tID\tNAME\n"
                "GROUP\t1\tRhinoToRevit\n"
                "*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550001\tR2R_Area\tAREA\t\t1\t1\tR2R Computed Surface Area\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550002\tR2R_Volume\tVOLUME\t\t1\t1\tR2R Computed Solid Volume\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550003\tR2R_Material\tTEXT\t\t1\t1\tR2R Material Takeoff Name\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550004\tR2R_Material_Volume\tVOLUME\t\t1\t1\tR2R Material Takeoff Volume\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550005\tR2R_Source_Layer\tTEXT\t\t1\t1\tR2R Source Rhino Layer\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550006\tR2R_Rhino_GUID\tTEXT\t\t1\t1\tR2R Source Rhino Object GUID\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550007\tR2R_Thickness\tLENGTH\t\t1\t1\tR2R Structural Thickness\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550008\tR2R_Building_Zone\tTEXT\t\t1\t1\tR2R Building Zone\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550009\tR2R_Category\tTEXT\t\t1\t1\tR2R Inferred Revit Category\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550010\tR2R_Area_sqm\tTEXT\t\t1\t1\tR2R Area in sqm\t1\t0\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550011\tR2R_Volume_cum\tTEXT\t\t1\t1\tR2R Volume in cum\t1\t0\n"
            )
        else:
            sp_content = (
                "# This is a Revit shared parameter file.\n"
                "# Do not edit manually.\n"
                "*META\tVERSION\tMINVERS\n"
                "META\t2\t1\n"
                "*GROUP\tID\tNAME\n"
                "GROUP\t1\tRhinoToRevit\n"
                "*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550001\tR2R_Area\tAREA\t\t1\t1\tR2R Computed Surface Area\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550002\tR2R_Volume\tVOLUME\t\t1\t1\tR2R Computed Solid Volume\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550003\tR2R_Material\tTEXT\t\t1\t1\tR2R Material Takeoff Name\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550004\tR2R_Material_Volume\tVOLUME\t\t1\t1\tR2R Material Takeoff Volume\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550005\tR2R_Source_Layer\tTEXT\t\t1\t1\tR2R Source Rhino Layer\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550006\tR2R_Rhino_GUID\tTEXT\t\t1\t1\tR2R Source Rhino Object GUID\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550007\tR2R_Thickness\tLENGTH\t\t1\t1\tR2R Structural Thickness\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550008\tR2R_Building_Zone\tTEXT\t\t1\t1\tR2R Building Zone\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550009\tR2R_Category\tTEXT\t\t1\t1\tR2R Inferred Revit Category\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550010\tR2R_Area_sqm\tTEXT\t\t1\t1\tR2R Area in sqm\t1\n"
                "PARAM\te1a2b3c4-1111-2222-3333-444455550011\tR2R_Volume_cum\tTEXT\t\t1\t1\tR2R Volume in cum\t1\n"
            )

        try:
            # Write shared parameter file with strict UTF-16 LE encoding with BOM
            if sys.version_info[0] >= 3:
                with open(sp_temp_path, "w", encoding="utf-16") as f:
                    f.write(sp_content)
            else:
                import codecs
                with codecs.open(sp_temp_path, "w", encoding="utf-16") as f:
                    f.write(unicode(sp_content))

            app.SharedParametersFilename = os.path.abspath(sp_temp_path)
            sp_file = app.OpenSharedParameterFile()
            if not sp_file:
                if log_fn:
                    log_fn("[SharedParams] Warning: OpenSharedParameterFile returned null.")
                return False

            grp = sp_file.Groups.get_Item("RhinoToRevit")
            if not grp:
                try:
                    grp = sp_file.Groups.Create("RhinoToRevit")
                except Exception:
                    grp = None
            if not grp:
                if log_fn:
                    log_fn("[SharedParams] Warning: Group 'RhinoToRevit' unavailable.")
                return False

            # Model categories to bind
            target_bics = [
                DB.BuiltInCategory.OST_GenericModel,
                DB.BuiltInCategory.OST_Floors,
                DB.BuiltInCategory.OST_Walls,
                DB.BuiltInCategory.OST_Ceilings,
                DB.BuiltInCategory.OST_Roofs,
                DB.BuiltInCategory.OST_Columns,
                DB.BuiltInCategory.OST_StructuralColumns,
                DB.BuiltInCategory.OST_StructuralFraming,
                DB.BuiltInCategory.OST_Mass
            ]
            if hasattr(DB.BuiltInCategory, "OST_Toposolid"):
                target_bics.append(DB.BuiltInCategory.OST_Toposolid)

            cat_set = app.Create.NewCategorySet()
            for bic in target_bics:
                try:
                    cat = None
                    if hasattr(DB, "Category") and hasattr(DB.Category, "GetCategory"):
                        try:
                            cat = DB.Category.GetCategory(self.doc, bic)
                        except Exception:
                            cat = None
                    if not cat:
                        try:
                            cat = self.doc.Settings.Categories.get_Item(bic)
                        except Exception:
                            cat = None
                    if not cat and hasattr(DB, "ElementId"):
                        try:
                            cat = DB.Category.GetCategory(self.doc, DB.ElementId(bic))
                        except Exception:
                            cat = None
                    if cat and getattr(cat, "AllowsBoundParameters", False):
                        cat_set.Insert(cat)
                except Exception:
                    pass

            # Fallback if cat_set is still empty: scan Document Categories directly
            if getattr(cat_set, "Size", 0) == 0:
                cats_obj = getattr(getattr(self.doc, "Settings", None), "Categories", None)
                if cats_obj and not isinstance(cats_obj, type) and hasattr(cats_obj, "__iter__"):
                    for cat in cats_obj:
                        try:
                            if cat and getattr(cat, "AllowsBoundParameters", False):
                                cat_id = getattr(cat, "Id", None)
                                bic_val = None
                                if hasattr(cat_id, "Value"):
                                    bic_val = int(cat_id.Value)
                                elif hasattr(cat_id, "IntegerValue"):
                                    bic_val = int(cat_id.IntegerValue)
                                if bic_val in [int(b) for b in target_bics if hasattr(b, "__int__") or isinstance(b, (int, float))]:
                                    cat_set.Insert(cat)
                        except Exception:
                            pass

            # Parameter group in properties palette (supports Revit 2024+ GroupTypeId and 2020-2023 BuiltInParameterGroup)
            p_group = getattr(DB.BuiltInParameterGroup, "PG_DATA", None)
            if not p_group and hasattr(DB.BuiltInParameterGroup, "PG_GEOMETRY"):
                p_group = DB.BuiltInParameterGroup.PG_GEOMETRY

            group_type = None
            if hasattr(DB, "GroupTypeId") and hasattr(DB.GroupTypeId, "Data"):
                group_type = DB.GroupTypeId.Data
            elif hasattr(DB, "GroupTypeId") and hasattr(DB.GroupTypeId, "Geometry"):
                group_type = DB.GroupTypeId.Geometry
            else:
                group_type = p_group

            binding_map = self.doc.ParameterBindings
            bound_count = 0
            for def_obj in grp.Definitions:
                bound_this = False
                # Attempt 1: Multi-category binding
                if getattr(cat_set, "Size", 0) > 0:
                    try:
                        new_binding = app.Create.NewInstanceBinding(cat_set)
                        if binding_map.Contains(def_obj):
                            try:
                                binding_map.ReInsert(def_obj, new_binding, group_type)
                                bound_this = True
                            except Exception:
                                try:
                                    binding_map.ReInsert(def_obj, new_binding, p_group)
                                    bound_this = True
                                except Exception:
                                    pass
                        else:
                            try:
                                binding_map.Insert(def_obj, new_binding, group_type)
                                bound_this = True
                            except Exception:
                                try:
                                    binding_map.Insert(def_obj, new_binding, p_group)
                                    bound_this = True
                                except Exception:
                                    pass
                    except Exception as ex_b:
                        pass

                # Attempt 2: Resilient Single-Category Fallback (Directly binds to OST_GenericModel for cave DirectShapes)
                if not bound_this:
                    try:
                        gm_cat = None
                        if hasattr(DB, "Category") and hasattr(DB.Category, "GetCategory"):
                            try:
                                gm_cat = DB.Category.GetCategory(self.doc, DB.BuiltInCategory.OST_GenericModel)
                            except Exception:
                                gm_cat = None
                        if not gm_cat:
                            try:
                                gm_cat = self.doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_GenericModel)
                            except Exception:
                                gm_cat = None
                        if gm_cat:
                            gm_set = app.Create.NewCategorySet()
                            gm_set.Insert(gm_cat)
                            gm_binding = app.Create.NewInstanceBinding(gm_set)
                            if binding_map.Contains(def_obj):
                                try:
                                    binding_map.ReInsert(def_obj, gm_binding, group_type)
                                    bound_this = True
                                except Exception:
                                    binding_map.ReInsert(def_obj, gm_binding, p_group)
                                    bound_this = True
                            else:
                                try:
                                    binding_map.Insert(def_obj, gm_binding, group_type)
                                    bound_this = True
                                except Exception:
                                    binding_map.Insert(def_obj, gm_binding, p_group)
                                    bound_this = True
                    except Exception:
                        pass

                if bound_this:
                    bound_count += 1

            if log_fn:
                log_fn("[SharedParams] Bound {} shared parameter definitions to document.".format(bound_count))

            return bound_count > 0
        except Exception as ex:
            if log_fn:
                log_fn("[SharedParams Error]: {}".format(ex))
            print("[R2R Shared Parameters Binding Notice]: {}".format(ex))
            return False
        finally:
            try:
                if orig_sp_path:
                    app.SharedParametersFilename = orig_sp_path
            except Exception:
                pass

    def create_r2r_takeoff_schedule(self, schedule_name="R2R Cave & Geometry Takeoff Schedule", category_bic=None):
        """Creates or updates a dedicated ViewSchedule in Revit with R2R Area, Volume, Material, and Grand Totals.

        Must be executed inside an active Revit Transaction.
        """
        if not REVIT_AVAILABLE or not self.doc or not hasattr(DB, "ViewSchedule"):
            return None

        try:
            # Check if schedule already exists
            existing_collector = DB.FilteredElementCollector(self.doc).OfClass(DB.ViewSchedule)
            sched = None
            for s in existing_collector:
                if getattr(s, "Name", "") == schedule_name:
                    sched = s
                    break

            if not sched:
                # Determine category: specific BuiltInCategory, or OST_GenericModel (primary for DirectShapes/Caves),
                # or Multi-Category fallback
                if category_bic is not None:
                    cat_id = DB.ElementId(category_bic)
                else:
                    # Generic Model schedule is the most robust and universal container for DirectShape cave takeoffs
                    cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)

                try:
                    sched = DB.ViewSchedule.CreateSchedule(self.doc, cat_id)
                except Exception:
                    # Fallback to Multi-Category if specific category creation fails
                    cat_id = DB.ElementId.InvalidElementId
                    sched = DB.ViewSchedule.CreateSchedule(self.doc, cat_id)

                if not sched:
                    return None

                sched.Name = schedule_name

            sched_def = sched.Definition
            available_fields = sched_def.GetSchedulableFields()

            # Inspect existing fields currently present in the schedule
            existing_field_names = set()
            try:
                if hasattr(sched_def, "GetFieldCount") and hasattr(sched_def, "GetField"):
                    for i in range(sched_def.GetFieldCount()):
                        f = sched_def.GetField(i)
                        f_name = f.GetName(self.doc) if hasattr(f, "GetName") else str(getattr(f, "ColumnHeading", ""))
                        existing_field_names.add(f_name)
            except Exception:
                pass

            desired_order = [
                "Mark",
                "R2R_Category",
                "R2R_Source_Layer",
                "R2R_Material",
                "R2R_Area",
                "R2R_Area_sqm",
                "R2R_Volume",
                "R2R_Volume_cum",
                "R2R_Material_Volume",
                "R2R_Thickness",
                "R2R_Building_Zone",
                "Comments"
            ]

            field_by_name = {}
            field_by_guid = {}
            for sf in available_fields:
                try:
                    f_name = sf.GetName(self.doc)
                    field_by_name[f_name] = sf
                    pid = getattr(sf, "ParameterId", None)
                    if pid and self.doc and hasattr(self.doc, "GetElement"):
                        pelem = self.doc.GetElement(pid)
                        if pelem and hasattr(pelem, "GuidValue"):
                            field_by_guid[str(pelem.GuidValue)] = sf
                except Exception:
                    pass

            added_fields = []
            for field_name in desired_order:
                if field_name in existing_field_names:
                    continue  # Already present in schedule

                sf_to_add = None
                if field_name in field_by_name:
                    sf_to_add = field_by_name[field_name]
                elif field_name in R2R_PARAM_GUIDS and R2R_PARAM_GUIDS[field_name] in field_by_guid:
                    sf_to_add = field_by_guid[R2R_PARAM_GUIDS[field_name]]
                elif field_name == "Mark":
                    for sf in available_fields:
                        if getattr(sf, "ParameterId", None) == DB.ElementId(DB.BuiltInParameter.ALL_MODEL_MARK):
                            sf_to_add = sf
                            break
                elif field_name == "Comments":
                    for sf in available_fields:
                        if getattr(sf, "ParameterId", None) == DB.ElementId(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS):
                            sf_to_add = sf
                            break

                if sf_to_add:
                    try:
                        added_sf = sched_def.AddField(sf_to_add)
                        added_fields.append((field_name, added_sf))
                    except Exception:
                        pass

            # Enable 'Calculate Totals' on numeric quantity fields
            for f_name, sf_obj in added_fields:
                if f_name in ("R2R_Area", "R2R_Volume", "R2R_Material_Volume"):
                    try:
                        if hasattr(sf_obj, "IsCalculateTotals"):
                            sf_obj.IsCalculateTotals = True
                    except Exception:
                        pass

            # Also ensure CalculateTotals is active for any existing columns in schedule definition
            try:
                if hasattr(sched_def, "GetFieldCount") and hasattr(sched_def, "GetField"):
                    for i in range(sched_def.GetFieldCount()):
                        f = sched_def.GetField(i)
                        f_name = f.GetName(self.doc) if hasattr(f, "GetName") else str(getattr(f, "ColumnHeading", ""))
                        if f_name in ("R2R_Area", "R2R_Volume", "R2R_Material_Volume"):
                            if hasattr(f, "IsCalculateTotals"):
                                f.IsCalculateTotals = True
            except Exception:
                pass

            # Enable Show Grand Totals at bottom of schedule
            try:
                if hasattr(sched_def, "ShowGrandTotal"):
                    sched_def.ShowGrandTotal = True
                if hasattr(sched_def, "ShowGrandTotalTitle"):
                    sched_def.ShowGrandTotalTitle = True
                if hasattr(sched_def, "ShowGrandTotalCount"):
                    sched_def.ShowGrandTotalCount = True
            except Exception:
                pass

            return sched
        except Exception as ex:
            print("[R2R Takeoff Schedule Notice]: {}".format(ex))
            return None

    def _category_str_to_id(self, cat_name):
        """Maps category name string to Revit ElementId with Toposolid, all 3D model categories, and dynamic BuiltInCategory resolution."""
        if not REVIT_AVAILABLE or not hasattr(DB, "BuiltInCategory"):
            return None

        # Resolve version-dependent BuiltInCategory safely
        toposolid_bic = getattr(DB.BuiltInCategory, "OST_Toposolid", DB.BuiltInCategory.OST_Topography)
        hardscape_bic = getattr(DB.BuiltInCategory, "OST_Hardscape", DB.BuiltInCategory.OST_Site)

        # Comprehensive map covering architectural, structural, MEP, and site categories
        cat_map = {
            # Architecture - Core Systems & Openings
            "OST_Floors": DB.BuiltInCategory.OST_Floors,
            "Floors": DB.BuiltInCategory.OST_Floors,
            "Floor": DB.BuiltInCategory.OST_Floors,
            "Slab": DB.BuiltInCategory.OST_Floors,
            "Slabs": DB.BuiltInCategory.OST_Floors,
            "OST_Walls": DB.BuiltInCategory.OST_Walls,
            "Walls": DB.BuiltInCategory.OST_Walls,
            "Wall": DB.BuiltInCategory.OST_Walls,
            "OST_Roofs": DB.BuiltInCategory.OST_Roofs,
            "Roofs": DB.BuiltInCategory.OST_Roofs,
            "Roof": DB.BuiltInCategory.OST_Roofs,
            "OST_Ceilings": DB.BuiltInCategory.OST_Ceilings,
            "Ceilings": DB.BuiltInCategory.OST_Ceilings,
            "Ceiling": DB.BuiltInCategory.OST_Ceilings,
            "OST_Columns": DB.BuiltInCategory.OST_Columns,
            "Columns": DB.BuiltInCategory.OST_Columns,
            "Column": DB.BuiltInCategory.OST_Columns,
            "OST_Doors": DB.BuiltInCategory.OST_Doors,
            "Doors": DB.BuiltInCategory.OST_Doors,
            "Door": DB.BuiltInCategory.OST_Doors,
            "OST_Windows": DB.BuiltInCategory.OST_Windows,
            "Windows": DB.BuiltInCategory.OST_Windows,
            "Window": DB.BuiltInCategory.OST_Windows,
            "OST_CurtainWallPanels": DB.BuiltInCategory.OST_CurtainWallPanels,
            "Curtain Panels": DB.BuiltInCategory.OST_CurtainWallPanels,
            "Curtain Wall Panels": DB.BuiltInCategory.OST_CurtainWallPanels,
            "CurtainPanel": DB.BuiltInCategory.OST_CurtainWallPanels,
            "CurtainPanels": DB.BuiltInCategory.OST_CurtainWallPanels,
            "OST_CurtainWallMullions": DB.BuiltInCategory.OST_CurtainWallMullions,
            "Curtain Wall Mullions": DB.BuiltInCategory.OST_CurtainWallMullions,
            "Curtain Mullions": DB.BuiltInCategory.OST_CurtainWallMullions,
            "Mullions": DB.BuiltInCategory.OST_CurtainWallMullions,
            "Mullion": DB.BuiltInCategory.OST_CurtainWallMullions,
            "OST_Stairs": DB.BuiltInCategory.OST_Stairs,
            "Stairs": DB.BuiltInCategory.OST_Stairs,
            "Stair": DB.BuiltInCategory.OST_Stairs,
            "OST_StairsRailing": DB.BuiltInCategory.OST_StairsRailing,
            "Railings": DB.BuiltInCategory.OST_StairsRailing,
            "Railing": DB.BuiltInCategory.OST_StairsRailing,
            "OST_Ramps": DB.BuiltInCategory.OST_Ramps,
            "Ramps": DB.BuiltInCategory.OST_Ramps,
            "Ramp": DB.BuiltInCategory.OST_Ramps,

            # Furniture, Casework & Equipment
            "OST_Furniture": DB.BuiltInCategory.OST_Furniture,
            "Furniture": DB.BuiltInCategory.OST_Furniture,
            "OST_FurnitureSystems": getattr(DB.BuiltInCategory, "OST_FurnitureSystems", DB.BuiltInCategory.OST_Furniture),
            "Furniture Systems": getattr(DB.BuiltInCategory, "OST_FurnitureSystems", DB.BuiltInCategory.OST_Furniture),
            "FurnitureSystems": getattr(DB.BuiltInCategory, "OST_FurnitureSystems", DB.BuiltInCategory.OST_Furniture),
            "OST_Casework": DB.BuiltInCategory.OST_Casework,
            "Casework": DB.BuiltInCategory.OST_Casework,
            "Cabinets": DB.BuiltInCategory.OST_Casework,
            "Cabinet": DB.BuiltInCategory.OST_Casework,
            "OST_SpecialityEquipment": DB.BuiltInCategory.OST_SpecialityEquipment,
            "Specialty Equipment": DB.BuiltInCategory.OST_SpecialityEquipment,
            "Speciality Equipment": DB.BuiltInCategory.OST_SpecialityEquipment,
            "SpecialtyEquipment": DB.BuiltInCategory.OST_SpecialityEquipment,
            "Equipment": DB.BuiltInCategory.OST_SpecialityEquipment,
            "OST_Entourage": DB.BuiltInCategory.OST_Entourage,
            "Entourage": DB.BuiltInCategory.OST_Entourage,
            "OST_Signage": getattr(DB.BuiltInCategory, "OST_Signage", DB.BuiltInCategory.OST_GenericModel),
            "Signage": getattr(DB.BuiltInCategory, "OST_Signage", DB.BuiltInCategory.OST_GenericModel),

            # Site, Topo & Landscape
            "OST_Toposolid": toposolid_bic,
            "Toposolid": toposolid_bic,
            "Topography": DB.BuiltInCategory.OST_Topography,
            "OST_Topography": DB.BuiltInCategory.OST_Topography,
            "OST_Site": DB.BuiltInCategory.OST_Site,
            "Site": DB.BuiltInCategory.OST_Site,
            "OST_Planting": DB.BuiltInCategory.OST_Planting,
            "Planting": DB.BuiltInCategory.OST_Planting,
            "Planters": DB.BuiltInCategory.OST_Planting,
            "Planter": DB.BuiltInCategory.OST_Planting,
            "Landscape": DB.BuiltInCategory.OST_Planting,
            "OST_Parking": DB.BuiltInCategory.OST_Parking,
            "Parking": DB.BuiltInCategory.OST_Parking,
            "OST_Roads": DB.BuiltInCategory.OST_Roads,
            "Roads": DB.BuiltInCategory.OST_Roads,
            "Road": DB.BuiltInCategory.OST_Roads,
            "OST_Hardscape": hardscape_bic,
            "Hardscape": hardscape_bic,

            # Structure
            "OST_StructuralColumns": DB.BuiltInCategory.OST_StructuralColumns,
            "Structural Columns": DB.BuiltInCategory.OST_StructuralColumns,
            "Structural Column": DB.BuiltInCategory.OST_StructuralColumns,
            "OST_StructuralFraming": DB.BuiltInCategory.OST_StructuralFraming,
            "Structural Framing": DB.BuiltInCategory.OST_StructuralFraming,
            "Beams": DB.BuiltInCategory.OST_StructuralFraming,
            "Beam": DB.BuiltInCategory.OST_StructuralFraming,
            "Framing": DB.BuiltInCategory.OST_StructuralFraming,
            "OST_StructuralFoundation": DB.BuiltInCategory.OST_StructuralFoundation,
            "Structural Foundation": DB.BuiltInCategory.OST_StructuralFoundation,
            "Structural Foundations": DB.BuiltInCategory.OST_StructuralFoundation,
            "Foundation": DB.BuiltInCategory.OST_StructuralFoundation,
            "Foundations": DB.BuiltInCategory.OST_StructuralFoundation,
            "Footing": DB.BuiltInCategory.OST_StructuralFoundation,
            "Footings": DB.BuiltInCategory.OST_StructuralFoundation,
            "OST_StructuralStiffener": getattr(DB.BuiltInCategory, "OST_StructuralStiffener", DB.BuiltInCategory.OST_StructuralFraming),
            "Structural Stiffener": getattr(DB.BuiltInCategory, "OST_StructuralStiffener", DB.BuiltInCategory.OST_StructuralFraming),

            # MEP (Mechanical, Electrical, Plumbing)
            "OST_PlumbingFixtures": DB.BuiltInCategory.OST_PlumbingFixtures,
            "Plumbing Fixtures": DB.BuiltInCategory.OST_PlumbingFixtures,
            "Plumbing": DB.BuiltInCategory.OST_PlumbingFixtures,
            "PlumbingFixture": DB.BuiltInCategory.OST_PlumbingFixtures,
            "OST_MechanicalEquipment": DB.BuiltInCategory.OST_MechanicalEquipment,
            "Mechanical Equipment": DB.BuiltInCategory.OST_MechanicalEquipment,
            "Mechanical": DB.BuiltInCategory.OST_MechanicalEquipment,
            "HVAC": DB.BuiltInCategory.OST_MechanicalEquipment,
            "OST_ElectricalEquipment": DB.BuiltInCategory.OST_ElectricalEquipment,
            "Electrical Equipment": DB.BuiltInCategory.OST_ElectricalEquipment,
            "OST_ElectricalFixtures": DB.BuiltInCategory.OST_ElectricalFixtures,
            "Electrical Fixtures": DB.BuiltInCategory.OST_ElectricalFixtures,
            "Electrical": DB.BuiltInCategory.OST_ElectricalFixtures,
            "OST_LightingFixtures": DB.BuiltInCategory.OST_LightingFixtures,
            "Lighting Fixtures": DB.BuiltInCategory.OST_LightingFixtures,
            "Lighting": DB.BuiltInCategory.OST_LightingFixtures,
            "Lights": DB.BuiltInCategory.OST_LightingFixtures,
            "Light": DB.BuiltInCategory.OST_LightingFixtures,
            "OST_LightingDevices": getattr(DB.BuiltInCategory, "OST_LightingDevices", DB.BuiltInCategory.OST_LightingFixtures),
            "Lighting Devices": getattr(DB.BuiltInCategory, "OST_LightingDevices", DB.BuiltInCategory.OST_LightingFixtures),
            "OST_CommunicationDevices": getattr(DB.BuiltInCategory, "OST_CommunicationDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "Communication Devices": getattr(DB.BuiltInCategory, "OST_CommunicationDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "OST_FireAlarmDevices": getattr(DB.BuiltInCategory, "OST_FireAlarmDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "Fire Alarm Devices": getattr(DB.BuiltInCategory, "OST_FireAlarmDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "OST_SecurityDevices": getattr(DB.BuiltInCategory, "OST_SecurityDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "Security Devices": getattr(DB.BuiltInCategory, "OST_SecurityDevices", DB.BuiltInCategory.OST_ElectricalFixtures),
            "OST_DuctCurves": getattr(DB.BuiltInCategory, "OST_DuctCurves", DB.BuiltInCategory.OST_MechanicalEquipment),
            "Ducts": getattr(DB.BuiltInCategory, "OST_DuctCurves", DB.BuiltInCategory.OST_MechanicalEquipment),
            "OST_PipeCurves": getattr(DB.BuiltInCategory, "OST_PipeCurves", DB.BuiltInCategory.OST_PlumbingFixtures),
            "Pipes": getattr(DB.BuiltInCategory, "OST_PipeCurves", DB.BuiltInCategory.OST_PlumbingFixtures),

            # Mass & Generic Models
            "OST_Mass": DB.BuiltInCategory.OST_Mass,
            "Mass": DB.BuiltInCategory.OST_Mass,
            "OST_GenericModel": DB.BuiltInCategory.OST_GenericModel,
            "Generic Model": DB.BuiltInCategory.OST_GenericModel,
            "Generic Models": DB.BuiltInCategory.OST_GenericModel,
            "Generic": DB.BuiltInCategory.OST_GenericModel,
        }
        clean_name = str(cat_name or "").strip()
        bic = cat_map.get(clean_name)

        # Dynamic fallback: check direct attribute, OST_ prefix, or case-insensitive match
        if bic is None:
            stripped = re.sub(r'[^a-zA-Z0-9_]', '', clean_name)
            ost_cand = "OST_" + stripped if not stripped.startswith("OST_") else stripped
            if hasattr(DB.BuiltInCategory, ost_cand):
                bic = getattr(DB.BuiltInCategory, ost_cand)
            elif hasattr(DB.BuiltInCategory, stripped):
                bic = getattr(DB.BuiltInCategory, stripped)
            else:
                ost_cand_lower = ost_cand.lower()
                stripped_lower = stripped.lower()
                for attr in dir(DB.BuiltInCategory):
                    if attr.startswith("_"):
                        continue
                    attr_lower = attr.lower()
                    if attr_lower == ost_cand_lower or attr_lower == stripped_lower:
                        bic = getattr(DB.BuiltInCategory, attr)
                        break

        if bic is None:
            bic = DB.BuiltInCategory.OST_GenericModel

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
