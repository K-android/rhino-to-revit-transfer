# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Strategy Rule Engine
strategy_rules.py - Declarative Compatibility Rules, Editability Metadata & Mixed-Layer Bucketing

Defines deterministic per-object eligibility rules for every Revit conversion strategy,
explicitly distinguishes Editable vs. Non-Editable Families, detects mixed geometry
within a single Rhino layer, and splits heterogeneous layers into homogeneous
virtual sub-layers.
"""


SYSTEM_ONLY_CATEGORIES = set([
    "OST_Roofs", "OST_Floors", "OST_Walls", "OST_Ceilings", "OST_Toposolid",
    "roofs", "roof", "floors", "floor", "walls", "wall", "ceilings", "ceiling", "toposolid"
])


def _is_system_only_category(category_name):
    """Returns True if category_name refers to a Revit System-only category unsuitable for .rfa families."""
    low = str(category_name or "").strip().lower()
    if not low or "curtain" in low:
        return False
    if low in ("ost_roofs", "ost_floors", "ost_walls", "ost_ceilings", "ost_toposolid"):
        return True
    for tok in ("roof", "floor", "wall", "ceiling", "toposolid"):
        if tok in low:
            return True
    return False


STRATEGY_METADATA = {
    "DirectShape": {
        "display_name": "DirectShape (Any 3D Geometry)",
        "editability": "Non-Editable (DirectShape)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "FreeFormFamily": {
        "display_name": "FreeFormFamily (Static Solid .rfa)",
        "editability": "Non-Editable 3D / Editable Metadata & Type",
        "supports_constraints": False,
        "supports_type_params": True
    },
    "FamilyInstance": {
        "display_name": "FamilyInstance (Static Solid .rfa)",
        "editability": "Non-Editable 3D / Editable Metadata & Type",
        "supports_constraints": False,
        "supports_type_params": True
    },
    "ParametricFamily": {
        "display_name": "ParametricFamily (Editable Flexing .rfa)",
        "editability": "Editable 3D Parametric Family",
        "supports_constraints": True,
        "supports_type_params": True
    },
    "RepetitiveFamily": {
        "display_name": "RepetitiveFamily (Instanced Blocks/Copies)",
        "editability": "Non-Editable 3D / Instanced .rfa",
        "supports_constraints": False,
        "supports_type_params": True
    },
    "AdaptiveComponent": {
        "display_name": "AdaptiveComponent (2/3/4-Pt Panel)",
        "editability": "Editable Adaptive Points Family",
        "supports_constraints": True,
        "supports_type_params": True
    },
    "FloorNative": {
        "display_name": "FloorNative (System Slab)",
        "editability": "Revit Native System Family (Sketch/Level)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "FloorByHost": {
        "display_name": "FloorByHost (System Slab)",
        "editability": "Revit Native System Family (Sketch/Level)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "WallByCurve": {
        "display_name": "WallByCurve (System Wall)",
        "editability": "Revit Native System Family (Path/Height)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "WallByProfile": {
        "display_name": "WallByProfile (System Wall)",
        "editability": "Revit Native System Family (Profile)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "RoofNative": {
        "display_name": "RoofNative (System Roof)",
        "editability": "Revit Native System Family (Sketch)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "RoofByHost": {
        "display_name": "RoofByHost (System Roof)",
        "editability": "Revit Native System Family (Sketch)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "CeilingNative": {
        "display_name": "CeilingNative (System Ceiling)",
        "editability": "Revit Native System Family (Sketch)",
        "supports_constraints": False,
        "supports_type_params": False
    },
    "Toposolid": {
        "display_name": "Toposolid (Terrain Surface/Solid)",
        "editability": "Revit Native Site Toposolid",
        "supports_constraints": False,
        "supports_type_params": False
    }
}


class ObjectRuleCheck(object):
    """Per-object rule evaluation result for a target conversion strategy."""

    def __init__(self, rhino_guid, passed, hard_failures=None, warnings=None, fallback="DirectShape"):
        self.rhino_guid = str(rhino_guid)
        self.passed = bool(passed)
        self.hard_failures = list(hard_failures or [])
        self.warnings = list(warnings or [])
        self.fallback = fallback or "DirectShape"


class StrategyEvaluation(object):
    """Aggregated rule-compatibility report for a collection of objects under one strategy."""

    def __init__(
        self,
        strategy,
        status="COMPATIBLE",
        pass_count=0,
        fail_count=0,
        total_count=0,
        compatibility_score=100.0,
        failure_reasons=None,
        warnings=None,
        recommended_strategy="DirectShape",
        recommended_fallback="DirectShape",
        is_mixed_geometry=False,
        bucket_counts=None,
        editability_label="",
        failing_guids=None,
        object_checks=None
    ):
        self.strategy = strategy
        self.status = status
        self.pass_count = int(pass_count)
        self.fail_count = int(fail_count)
        self.total_count = int(total_count)
        self.compatibility_score = float(compatibility_score)
        self.failure_reasons = list(failure_reasons or [])
        self.warnings = list(warnings or [])
        self.recommended_strategy = recommended_strategy or "DirectShape"
        self.recommended_fallback = recommended_fallback or "DirectShape"
        self.is_mixed_geometry = bool(is_mixed_geometry)
        self.bucket_counts = dict(bucket_counts or {})
        self.editability_label = editability_label
        self.failing_guids = list(failing_guids or [])
        self.object_checks = list(object_checks or [])

    def summary_text(self):
        """Returns a concise single-line banner summary for UI display."""
        if self.total_count == 0:
            return "No objects in layer to evaluate."
        if self.status == "COMPATIBLE":
            warn_note = " ({})".format("; ".join(self.warnings[:2])) if self.warnings else ""
            return "{}/{} objects meet all '{}' requirements{}.".format(
                self.pass_count, self.total_count, self.strategy, warn_note
            )
        reason_str = "; ".join(self.failure_reasons[:2]) if self.failure_reasons else "Incompatible geometry"
        if self.status == "PARTIAL_MIXED":
            return "{}/{} compatible with '{}'. {} Failing objects fallback to '{}' (or split layer).".format(
                self.pass_count, self.total_count, self.strategy, reason_str, self.recommended_fallback
            )
        return "0/{} compatible with '{}': {}. Recommended: '{}'.".format(
            self.total_count, self.strategy, reason_str, self.recommended_strategy
        )


class StrategyRuleEngine(object):
    """Deterministic compatibility rule evaluator and mixed-geometry bucketing service."""

    BUCKET_SORT_ORDER = {
        "PARAMETRIC_SOLID": 1,
        "FREEFORM_SOLID": 2,
        "BLOCK": 3,
        "ADAPTIVE": 4,
        "HORIZ_SLAB": 5,
        "VERT_WALL": 6,
        "OPEN_MESH": 7,
        "GENERIC_3D": 8,
        "CURVE": 9
    }

    @classmethod
    def get_metadata(cls, strategy):
        """Returns STRATEGY_METADATA dict for a strategy name."""
        return STRATEGY_METADATA.get(
            strategy,
            {
                "display_name": str(strategy),
                "editability": "Custom / DirectShape",
                "supports_constraints": False,
                "supports_type_params": False
            }
        )

    @classmethod
    def classify_geometry_bucket(cls, obj):
        """Deterministically classifies an object into one canonical geometry bucket.

        :return: (bucket_key, bucket_label)
        """
        prim = getattr(obj, "geometry_primitive_type", "Unknown") or "Unknown"
        otype = getattr(obj, "object_type", "Unknown") or "Unknown"
        is_closed = bool(getattr(obj, "is_closed", False))
        is_horiz = bool(getattr(obj, "is_horizontal", False))
        is_sloped = bool(getattr(obj, "is_planar_sloped", False))
        is_vert = bool(getattr(obj, "is_vertical", False))
        is_planar = bool(getattr(obj, "is_planar", False))
        pts = int(getattr(obj, "adaptive_point_count", 0) or 0)
        is_prismatic = bool(getattr(obj, "is_prismatic", False))

        if prim == "Curve" or otype == "Curve":
            return ("CURVE", "Curve / Non-3D")
        if prim == "BlockInstance" or otype == "InstanceReference":
            return ("BLOCK", "Block Instance")
        if (pts in (2, 3, 4) and not is_closed) or prim == "AdaptivePanel":
            return ("ADAPTIVE", "Adaptive Panel (2-4 Pt)")
        if prim in ("Box", "Cylinder", "ProfileExtrusion") or is_prismatic:
            return ("PARAMETRIC_SOLID", "Parametric Primitive (Box/Cyl/Extrusion)")
        if (is_horiz or is_sloped) and not is_vert and prim != "OpenMesh" and (is_planar or is_closed or otype != "Mesh"):
            return ("HORIZ_SLAB", "Horizontal Slab / Planar")
        if is_vert and not is_horiz:
            return ("VERT_WALL", "Vertical Wall / Surface")
        if prim == "FreeFormSolid" or (is_closed and otype in ("Brep", "Extrusion", "Mesh", "SubD")):
            return ("FREEFORM_SOLID", "Closed FreeForm Solid")
        if prim in ("OpenMesh", "OpenSurface") or (otype in ("Mesh", "Surface", "Brep", "SubD") and not is_closed):
            return ("OPEN_MESH", "Open Surface / Mesh")
        return ("GENERIC_3D", "3D Geometry")

    @classmethod
    def evaluate_object(cls, obj, strategy, current_category=None):
        """Evaluates a single ScannedObject against a conversion strategy.

        :return: (passed, hard_failures, warnings, fallback)
        """
        strat = str(strategy or "DirectShape").strip()
        prim = getattr(obj, "geometry_primitive_type", "Unknown") or "Unknown"
        otype = getattr(obj, "object_type", "Unknown") or "Unknown"
        is_horiz = bool(getattr(obj, "is_horizontal", False))
        is_sloped = bool(getattr(obj, "is_planar_sloped", False))
        is_curved_ext = bool(getattr(obj, "is_curved_extrusion", False))
        is_vert = bool(getattr(obj, "is_vertical", False))
        is_closed_attr = getattr(obj, "is_closed", None)
        is_closed = bool(is_closed_attr)
        has_open_loops = bool(getattr(obj, "has_open_loops", False))
        pts = int(getattr(obj, "adaptive_point_count", 0) or 0)
        rep_cnt = int(getattr(obj, "repetition_count", 1) or 1)
        dims = getattr(obj, "parametric_dims", {}) or {}
        vol = float(getattr(obj, "volume", 0.0) or 0.0)
        loop_cnt = int(getattr(obj, "loop_count", 0) or 0)

        hard_failures = []
        warnings = []
        fallback = "DirectShape"

        if strat in ("FloorNative", "FloorByHost"):
            if otype == "Curve" and not is_closed:
                hard_failures.append("Open curves cannot form a closed Floor boundary")
            elif prim == "Curve" and not is_closed:
                hard_failures.append("Open curves cannot form a closed Floor boundary")
            if prim == "OpenMesh" or (otype == "Mesh" and not is_closed):
                hard_failures.append("Open mesh cannot form a closed native Floor boundary")
            if not (is_horiz or is_sloped):
                hard_failures.append("Geometry is not a horizontal or planar-sloped slab")
            if has_open_loops:
                hard_failures.append("Has non-closed boundary loops")

        elif strat in ("WallByCurve", "WallByProfile"):
            if not is_vert:
                hard_failures.append("Geometry is not vertical wall/profile")
            if prim == "OpenMesh" or (otype == "Mesh" and not is_closed):
                hard_failures.append("Open organic mesh cannot form a system Wall")
            if strat == "WallByCurve" and loop_cnt > 6:
                warnings.append("Wall has openings/cutouts; conversion will use WallByProfile")

        elif strat in ("RoofNative", "RoofByHost"):
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot form a Roof surface")
            if prim == "OpenMesh" or (otype == "Mesh" and not is_closed):
                hard_failures.append("Open or triangulated mesh shell cannot form a sketch-based native Roof; use DirectShape")
            if not (is_horiz or is_sloped or is_curved_ext):
                hard_failures.append("Double-curved or vertical freeform geometry cannot form a native Footprint/Extrusion Roof")
            if has_open_loops and (is_horiz or is_sloped or is_curved_ext):
                warnings.append("Open Brep edge flags present; footprint will extract top boundary loops")

        elif strat == "CeilingNative":
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot form a Ceiling surface")
            if prim == "OpenMesh" or (otype == "Mesh" and not is_closed):
                hard_failures.append("Open mesh cannot form a native Ceiling")
            if not (is_horiz or is_sloped):
                hard_failures.append("Geometry is not horizontal or planar-sloped ceiling")

        elif strat == "Toposolid":
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot generate a Toposolid")
            elif is_vert and not is_horiz:
                hard_failures.append("Vertical geometry cannot form a Toposolid")
            elif not is_horiz:
                warnings.append("Non-horizontal mesh/Brep will use XY bounding surface")

        elif strat == "AdaptiveComponent":
            if pts not in (2, 3, 4):
                hard_failures.append("Requires 2, 3, or 4 boundary/placement points")

        elif strat == "ParametricFamily":
            has_pos_dims = bool(dims) and any(float(v or 0.0) > 0.0 for v in dims.values())
            if prim in ("Box", "Cylinder", "ProfileExtrusion"):
                if not has_pos_dims and not (
                    float(getattr(obj, "dx", 0.0) or 0.0) > 0.0
                    or float(getattr(obj, "dy", 0.0) or 0.0) > 0.0
                    or float(getattr(obj, "dz", 0.0) or 0.0) > 0.0
                ):
                    hard_failures.append("Prismatic solid missing positive Width/Depth/Height dimensions")
            elif prim in ("FreeFormSolid", "OpenMesh", "OpenSurface", "Curve", "AdaptivePanel"):
                hard_failures.append(
                    "Non-prismatic or open geometry ({}) cannot bind to Width/Depth/Height reference planes".format(prim)
                )
                if prim == "Curve":
                    fallback = "Exclude"
                elif prim == "FreeFormSolid":
                    fallback = "FreeFormFamily"
                else:
                    fallback = "DirectShape"
            else:
                # Backward-compatible guard for generic/mock objects with "Unknown" primitive
                if otype == "Curve":
                    hard_failures.append("1D curve cannot build an Editable Parametric Family")
                    fallback = "Exclude"
                elif otype in ("Mesh", "Brep", "Surface", "SubD") and (is_closed_attr is False):
                    hard_failures.append("Open surfaces/meshes cannot form an Editable Parametric Solid")
                elif not has_pos_dims:
                    hard_failures.append("Missing positive parametric dimensions (Width/Depth/Height)")

        elif strat in ("FreeFormFamily", "FamilyInstance"):
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot form a 3D FreeForm Solid Family")
                fallback = "Exclude"
            elif prim in ("OpenMesh", "OpenSurface"):
                hard_failures.append("Open meshes/surfaces cannot form a closed FreeForm Solid in .rfa")
            elif is_closed_attr is False and otype in ("Mesh", "Brep", "Surface", "SubD") and pts not in (2, 3, 4):
                hard_failures.append("Unclosed shell cannot form a solid in .rfa template")
            elif otype != "Unknown" and prim != "Unknown" and vol <= 0.0 and otype != "InstanceReference":
                warnings.append("Zero solid volume detected; FreeForm creation may fall back to DirectShape")

        elif strat == "RepetitiveFamily":
            is_blk = (otype == "InstanceReference" or prim == "BlockInstance")
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot form a Repetitive Family")
                fallback = "Exclude"
            elif prim in ("OpenMesh", "OpenSurface") or (
                not is_blk and is_closed_attr is False and otype in ("Mesh", "Brep", "Surface", "SubD")
            ):
                hard_failures.append("Open meshes/surfaces cannot be loaded as solid RepetitiveFamily")
            elif not is_blk and rep_cnt < 2:
                warnings.append("Unique 1-off object (repetition < 2)")

        elif strat == "DirectShape":
            if otype == "Curve" or prim == "Curve":
                hard_failures.append("1D curve cannot build a 3D DirectShape solid/mesh")
                fallback = "Exclude"

        else:
            # Unknown custom strategy defaults to universal compatibility
            pass

        if strat in ("ParametricFamily", "FreeFormFamily", "FamilyInstance", "RepetitiveFamily"):
            if _is_system_only_category(current_category):
                warnings.append(
                    "System category '{}' cannot host loadable .rfa families; Revit maps to Generic Models unless Native or DirectShape is used".format(current_category)
                )

        passed = (len(hard_failures) == 0)
        return passed, hard_failures, warnings, fallback

    @classmethod
    def recommend_best_strategy(cls, objects, current_category=None):
        """Suggests the highest-compatibility Revit strategy for a group of objects."""
        if not objects:
            return "DirectShape"

        bucket_counts = {}
        for o in objects:
            bk, _ = cls.classify_geometry_bucket(o)
            bucket_counts[bk] = bucket_counts.get(bk, 0) + 1

        dominant = max(bucket_counts.items(), key=lambda kv: kv[1])[0]
        cat_lower = str(current_category or "").lower()

        if dominant == "CURVE":
            return "DirectShape"
        if dominant == "ADAPTIVE":
            return "AdaptiveComponent"
        if dominant == "BLOCK":
            return "RepetitiveFamily"
        if dominant == "PARAMETRIC_SOLID":
            return "ParametricFamily"
        if dominant == "HORIZ_SLAB":
            if "toposolid" in cat_lower or "topograph" in cat_lower or "site" in cat_lower:
                return "Toposolid"
            if "roof" in cat_lower:
                return "RoofNative"
            if "ceiling" in cat_lower:
                return "CeilingNative"
            return "FloorNative"
        if dominant == "VERT_WALL":
            return "WallByCurve"
        if dominant == "FREEFORM_SOLID":
            rep_max = max([int(getattr(o, "repetition_count", 1) or 1) for o in objects] + [1])
            if rep_max >= 2:
                return "RepetitiveFamily"
            return "FreeFormFamily"
        if dominant == "OPEN_MESH":
            return "DirectShape"
        return "DirectShape"

    @classmethod
    def evaluate_layer(cls, objects, strategy, current_category=None):
        """Evaluates all objects in a layer or group against a strategy.

        :return: StrategyEvaluation
        """
        strat = str(strategy or "DirectShape").strip()
        meta = cls.get_metadata(strat)
        total = len(objects) if objects else 0

        if total == 0:
            return StrategyEvaluation(
                strategy=strat,
                status="INCOMPATIBLE",
                pass_count=0,
                fail_count=0,
                total_count=0,
                compatibility_score=0.0,
                failure_reasons=["Layer contains no objects"],
                recommended_strategy="DirectShape",
                editability_label=meta.get("editability", "")
            )

        pass_cnt = 0
        fail_cnt = 0
        reason_counts = {}
        warn_counts = {}
        fallback_counts = {}
        bucket_counts = {}
        failing_guids = []
        checks = []

        for obj in objects:
            _, b_label = cls.classify_geometry_bucket(obj)
            bucket_counts[b_label] = bucket_counts.get(b_label, 0) + 1

            ok, fails, warns, fb = cls.evaluate_object(obj, strat, current_category=current_category)
            guid = getattr(obj, "rhino_guid", "")
            checks.append(ObjectRuleCheck(guid, ok, fails, warns, fb))

            if ok:
                pass_cnt += 1
            else:
                fail_cnt += 1
                failing_guids.append(guid)
                for r in fails:
                    reason_counts[r] = reason_counts.get(r, 0) + 1
                fallback_counts[fb] = fallback_counts.get(fb, 0) + 1

            for w in warns:
                warn_counts[w] = warn_counts.get(w, 0) + 1

        # Determine status
        if fail_cnt == 0:
            status = "COMPATIBLE"
        elif pass_cnt > 0:
            status = "PARTIAL_MIXED"
        else:
            status = "INCOMPATIBLE"

        # Calculate compatibility score preserving calibrated confidence expectations
        ratio = float(pass_cnt) / float(total)
        if fail_cnt == 0:
            if strat == "DirectShape":
                comp_score = 100.0
            elif strat in ("AdaptiveComponent",):
                comp_score = 98.0
            elif strat == "RepetitiveFamily":
                rep_cnt = max([int(getattr(o, "repetition_count", 1) or 1) for o in objects] + [1])
                is_blk = any(getattr(o, "object_type", "") == "InstanceReference" for o in objects)
                comp_score = 98.0 if (rep_cnt >= 2 or is_blk) else 75.0
            elif strat in ("FloorNative", "FloorByHost", "RoofNative", "RoofByHost", "CeilingNative", "WallByCurve", "WallByProfile"):
                comp_score = 96.0
            elif strat == "Toposolid":
                horiz_cnt = sum(1 for o in objects if getattr(o, "is_horizontal", False))
                comp_score = 96.0 if horiz_cnt > 0 else 85.0
            else:
                comp_score = 95.0
        elif pass_cnt > 0:
            base = 85.0 if strat == "AdaptiveComponent" else 80.0
            comp_score = round(ratio * base, 1)
        else:
            comp_score = 0.0

        grouped_reasons = [
            "{} ({}x)".format(r, cnt) for r, cnt in sorted(reason_counts.items(), key=lambda x: -x[1])
        ]
        grouped_warns = [
            "{} ({}x)".format(w, cnt) for w, cnt in sorted(warn_counts.items(), key=lambda x: -x[1])
        ]

        rec_fallback = "DirectShape"
        if fallback_counts:
            rec_fallback = max(fallback_counts.items(), key=lambda kv: kv[1])[0]

        rec_strat = cls.recommend_best_strategy(objects, current_category=current_category)

        return StrategyEvaluation(
            strategy=strat,
            status=status,
            pass_count=pass_cnt,
            fail_count=fail_cnt,
            total_count=total,
            compatibility_score=comp_score,
            failure_reasons=grouped_reasons,
            warnings=grouped_warns,
            recommended_strategy=rec_strat,
            recommended_fallback=rec_fallback,
            is_mixed_geometry=(len(bucket_counts) > 1),
            bucket_counts=bucket_counts,
            editability_label=meta.get("editability", ""),
            failing_guids=failing_guids,
            object_checks=checks
        )

    @classmethod
    def split_objects_by_geometry(cls, layer_name, objects):
        """Splits a mixed-geometry layer into homogeneous virtual sub-layers.

        :return: list of (virtual_layer_name, bucket_key, sub_objects)
        """
        if not objects:
            return []

        groups = {}
        labels = {}
        for o in objects:
            bk, bl = cls.classify_geometry_bucket(o)
            if bk not in groups:
                groups[bk] = []
                labels[bk] = bl
            groups[bk].append(o)

        if len(groups) <= 1:
            only_bk = next(iter(groups.keys()))
            return [(layer_name, only_bk, objects)]

        sorted_keys = sorted(
            groups.keys(),
            key=lambda k: (cls.BUCKET_SORT_ORDER.get(k, 99), labels[k])
        )
        result = []
        for bk in sorted_keys:
            v_name = "{} [{}]".format(layer_name, labels[bk])
            result.append((v_name, bk, groups[bk]))
        return result
