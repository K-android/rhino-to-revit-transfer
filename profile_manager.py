# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 4
profile_manager.py - Persistent JSON Mapping Profile

Manages serialization, loading, and auto-matching of mapping profiles (Transfer_Profile.json).
Caches user-approved mappings between Rhino layers/wildcards and Revit Target Categories,
Family/Type names, Conversion Strategies, Level snapping rules, and Room Bounding properties.
"""

import os
import json
import re
import hashlib
import uuid


_R2R_CUSTOM_PARAM_NS = uuid.UUID("7f3b9c1a-4d2e-4f8a-9b6c-1e2d3c4b5a6f").bytes


def deterministic_param_guid(param_name):
    """Generates a stable UUIDv5 string from a parameter name (consistent across IronPython 2.7 & CPython 3)."""
    clean = str(param_name or "").strip().lower()
    digest = hashlib.sha1(_R2R_CUSTOM_PARAM_NS + clean.encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=5))


class CustomParameterSpec(object):
    """User-defined deterministic parameter definition (Data or Editable Family Constraint)."""

    VALID_DATA_TYPES = ("Text", "Number", "Integer", "YesNo", "Length", "Area", "Volume", "Material")
    VALID_KINDS = ("Data", "Constraint")
    VALID_CONSTRAINT_TARGETS = ("", "Width", "Depth", "Height", "Base_Offset", "Radius", "Visibility", "Material")

    _REVIT_SP_TYPES = {
        "text": "TEXT",
        "number": "NUMBER",
        "integer": "INTEGER",
        "yesno": "YESNO",
        "length": "LENGTH",
        "area": "AREA",
        "volume": "VOLUME",
        "material": "MATERIAL"
    }

    def __init__(self, name, data_type="Text", param_kind="Data", is_instance=True,
                 value_expr="", formula="", constraint_target="", guid=""):
        self.name = re.sub(r'[^a-zA-Z0-9_\-\s]', '_', str(name or "").strip()) or "R2R_Custom_Param"
        self.data_type = str(data_type or "Text").strip()
        if self.data_type not in self.VALID_DATA_TYPES:
            self.data_type = "Text"
        self.param_kind = str(param_kind or "Data").strip()
        if self.param_kind not in self.VALID_KINDS:
            self.param_kind = "Data"
        self.is_instance = bool(is_instance)
        self.value_expr = str(value_expr or "")
        self.formula = str(formula or "").strip()
        self.constraint_target = str(constraint_target or "").strip()
        if self.constraint_target and self.param_kind != "Constraint":
            self.param_kind = "Constraint"
        self.guid = str(guid).strip() if guid else deterministic_param_guid(self.name)

    def revit_sp_datatype(self):
        """Maps data_type to Revit Shared Parameter file DATATYPE token."""
        return self._REVIT_SP_TYPES.get(self.data_type.lower(), "TEXT")

    def resolve_value(self, scanned_obj, conv_result=None):
        """Deterministically resolves the parameter value for a scanned object and conversion result."""
        expr = self.value_expr.strip()
        raw_val = expr

        if expr.lower().startswith("@usertext:"):
            ut_key = expr[10:].strip()
            user_text = getattr(scanned_obj, "user_text", {}) or {}
            # Exact match first, then case-insensitive match
            if ut_key in user_text:
                raw_val = user_text[ut_key]
            else:
                low_key = ut_key.lower()
                raw_val = ""
                for k, v in user_text.items():
                    if str(k).strip().lower() == low_key:
                        raw_val = v
                        break
        elif "{" in expr and "}" in expr:
            dims = getattr(scanned_obj, "parametric_dims", {}) or {}
            tokens = {
                "layer": str(getattr(scanned_obj, "layer_name", "") or ""),
                "layer_path": str(getattr(scanned_obj, "layer_full_path", "") or ""),
                "name": str(getattr(scanned_obj, "object_name", "") or getattr(scanned_obj, "layer_name", "") or ""),
                "level": str(getattr(scanned_obj, "nearest_level_name", "") or ""),
                "zone": str(getattr(scanned_obj, "building_zone", "") or "General"),
                "material": str(
                    getattr(conv_result, "primary_material", "")
                    or getattr(scanned_obj, "material_name", "")
                    or ""
                ),
                "area_sqm": "{:.2f}".format(float(getattr(conv_result, "area_sqm", 0.0) or getattr(scanned_obj, "area_sqm", 0.0) or 0.0)),
                "volume_cum": "{:.2f}".format(float(getattr(conv_result, "volume_cum", 0.0) or getattr(scanned_obj, "volume_cum", 0.0) or 0.0)),
                "width": "{:.3f}".format(float(dims.get("Width", getattr(scanned_obj, "dx", 0.0)) or 0.0)),
                "depth": "{:.3f}".format(float(dims.get("Depth", getattr(scanned_obj, "dy", 0.0)) or 0.0)),
                "height": "{:.3f}".format(float(dims.get("Height", getattr(scanned_obj, "dz", 0.0)) or 0.0)),
                "guid": str(getattr(scanned_obj, "rhino_guid", "") or "")
            }
            resolved = expr
            for tk, tv in tokens.items():
                resolved = resolved.replace("{" + tk + "}", tv)
            raw_val = resolved

        # Convert raw_val according to declared data_type
        dt = self.data_type
        if dt == "YesNo":
            return 1 if str(raw_val).strip().lower() in ("1", "true", "yes", "y", "on") else 0
        if dt == "Integer":
            try:
                return int(float(str(raw_val).strip() or "0"))
            except Exception:
                return 0
        if dt == "Number":
            try:
                return float(str(raw_val).strip() or "0.0")
            except Exception:
                return 0.0
        if dt == "Length":
            try:
                return float(str(raw_val).strip() or "0.0") / 0.3048
            except Exception:
                return 0.0
        if dt == "Area":
            try:
                return float(str(raw_val).strip() or "0.0") / 0.09290304
            except Exception:
                return 0.0
        if dt == "Volume":
            try:
                return float(str(raw_val).strip() or "0.0") / 0.028316846592
            except Exception:
                return 0.0
        return str(raw_val)

    def to_dict(self):
        return {
            "name": self.name,
            "data_type": self.data_type,
            "param_kind": self.param_kind,
            "is_instance": self.is_instance,
            "value_expr": self.value_expr,
            "formula": self.formula,
            "constraint_target": self.constraint_target,
            "guid": self.guid
        }

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, cls):
            return data
        return cls(
            name=data.get("name", "R2R_Custom_Param"),
            data_type=data.get("data_type", "Text"),
            param_kind=data.get("param_kind", "Data"),
            is_instance=data.get("is_instance", True),
            value_expr=data.get("value_expr", ""),
            formula=data.get("formula", ""),
            constraint_target=data.get("constraint_target", ""),
            guid=data.get("guid", "")
        )


class MappingRule(object):
    """Defines a deterministic transfer mapping rule from Rhino layer to Revit BIM entity."""

    def __init__(self, layer_pattern, revit_category="OST_GenericModel",
                 revit_type="Default", strategy="DirectShape",
                 level_rule="Nearest", base_offset=0.0,
                 room_bounding=True, material="ByLayer",
                 subcategory="", custom_parameters=None,
                 mixed_policy="AutoFallback"):
        self.layer_pattern = layer_pattern
        self.revit_category = revit_category
        self.revit_type = revit_type
        self.strategy = strategy  # FloorNative, WallByCurve, WallByProfile, CeilingNative, RoofNative, FreeFormFamily, ParametricFamily, DirectShape
        self.level_rule = level_rule  # "Nearest", "Explicit", "BoundingBoxBase"
        self.base_offset = float(base_offset)
        self.room_bounding = bool(room_bounding)
        self.material = material
        self.subcategory = subcategory
        self.mixed_policy = mixed_policy or "AutoFallback"
        self.custom_parameters = [
            CustomParameterSpec.from_dict(cp) for cp in (custom_parameters or [])
        ]
        self.approved = True

    def matches(self, layer_full_path):
        """Checks if a layer name matches the rule pattern (exact or wildcard regex)."""
        pattern = self.layer_pattern.strip()
        # Wildcard pattern conversion
        regex_pat = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
        return bool(re.search(regex_pat, layer_full_path, re.IGNORECASE))

    def to_dict(self):
        return {
            "layer_pattern": self.layer_pattern,
            "revit_category": self.revit_category,
            "revit_type": self.revit_type,
            "strategy": self.strategy,
            "level_rule": self.level_rule,
            "base_offset": self.base_offset,
            "room_bounding": self.room_bounding,
            "material": self.material,
            "subcategory": self.subcategory,
            "mixed_policy": self.mixed_policy,
            "custom_parameters": [cp.to_dict() for cp in self.custom_parameters],
            "approved": self.approved
        }

    @classmethod
    def from_dict(cls, data):
        rule = cls(
            layer_pattern=data.get("layer_pattern", "*"),
            revit_category=data.get("revit_category", "OST_GenericModel"),
            revit_type=data.get("revit_type", "Default"),
            strategy=data.get("strategy", "DirectShape"),
            level_rule=data.get("level_rule", "Nearest"),
            base_offset=data.get("base_offset", 0.0),
            room_bounding=data.get("room_bounding", True),
            material=data.get("material", "ByLayer"),
            subcategory=data.get("subcategory", ""),
            custom_parameters=data.get("custom_parameters", []),
            mixed_policy=data.get("mixed_policy", "AutoFallback")
        )
        rule.approved = data.get("approved", True)
        return rule


class ProfileManager(object):
    """Loads, saves, and applies persistent mapping configurations for R2R Transfer."""

    DEFAULT_PROFILE_NAME = "Transfer_Profile.json"

    def __init__(self, profile_path=None):
        if profile_path:
            self.profile_path = profile_path
        else:
            # Dedicated user config path outside the .extension directory
            # Prevents touching .extension files, keeping pyRevit binary cache 100% valid
            appdata_root = os.environ.get("APPDATA") or os.path.expanduser("~")
            user_config_dir = os.path.join(appdata_root, "pyRevit", "R2R")
            user_profile = os.path.join(user_config_dir, self.DEFAULT_PROFILE_NAME)

            # Bundled profile shipping with modules
            module_dir = os.path.dirname(os.path.abspath(__file__))
            bundled_profile = os.path.join(module_dir, self.DEFAULT_PROFILE_NAME)

            if os.path.exists(user_profile):
                self.profile_path = user_profile
            elif os.path.exists(bundled_profile):
                self.profile_path = user_profile
                try:
                    if not os.path.exists(user_config_dir):
                        os.makedirs(user_config_dir)
                    import shutil
                    shutil.copyfile(bundled_profile, user_profile)
                except Exception:
                    self.profile_path = bundled_profile
            else:
                self.profile_path = user_profile

        self.rules = []
        self.project_custom_parameters = []
        self.metadata = {
            "profile_name": "Standard Architectural Transfer Profile",
            "author": "AEC Computational Design & BIM Team",
            "version": "1.0.0",
            "project": "Architectural Geometry Transfer"
        }

        if os.path.exists(self.profile_path):
            self.load()
        else:
            self.load_defaults()

    def load_defaults(self):
        """Initializes default pre-configured rules for architectural layer conventions."""
        defaults = [
            # Facade Screen / Mesh Panel
            MappingRule("*veil*", "OST_GenericModel", "Facade Screen Panel - Inox", "DirectShape",
                        "Nearest", 0.0, False, "Stainless Steel - Brushed", "Veil Facade"),
            MappingRule("*velo*", "OST_GenericModel", "Facade Screen Panel - Inox", "DirectShape",
                        "Nearest", 0.0, False, "Stainless Steel - Brushed", "Veil Facade"),
            MappingRule("*gelosia*", "OST_GenericModel", "Architectural Screen", "DirectShape",
                        "Nearest", 0.0, False, "Anodized Aluminum", "Sun Shading"),

            # Slabs & Floors (RCR Catalan & Standard)
            MappingRule("*forjat*", "OST_Floors", "Generic Floor 300mm", "FloorNative",
                        "Nearest", 0.0, True, "Concrete - Cast-in-Place"),
            MappingRule("*forjado*", "OST_Floors", "Generic Floor 300mm", "FloorNative",
                        "Nearest", 0.0, True, "Concrete - Cast-in-Place"),
            MappingRule("*slab*", "OST_Floors", "Generic Floor 300mm", "FloorNative",
                        "Nearest", 0.0, True, "Concrete - Cast-in-Place"),
            MappingRule("*floor*", "OST_Floors", "Generic Floor 300mm", "FloorNative",
                        "Nearest", 0.0, True, "Concrete - Cast-in-Place"),
            MappingRule("*podium_slab*", "OST_Floors", "Podium Slab 400mm", "FloorNative",
                        "Nearest", 0.0, True, "Concrete - Cast-in-Place"),
            MappingRule("*sky_pool*", "OST_Floors", "Amenity Slab 450mm", "FloorByHost",
                        "Nearest", 0.0, True, "Concrete - Waterproof"),

            # Walls & Cores
            MappingRule("*core*", "OST_Walls", "Core Shear Wall 350mm", "WallByCurve",
                        "Nearest", 0.0, True, "Concrete - Structural"),
            MappingRule("*nucleo*", "OST_Walls", "Core Shear Wall 350mm", "WallByCurve",
                        "Nearest", 0.0, True, "Concrete - Structural"),
            MappingRule("*nucli*", "OST_Walls", "Core Shear Wall 350mm", "WallByCurve",
                        "Nearest", 0.0, True, "Concrete - Structural"),
            MappingRule("*wall*", "OST_Walls", "Basic Wall 200mm", "WallByCurve",
                        "Nearest", 0.0, True, "Concrete"),
            MappingRule("*muro*", "OST_Walls", "Basic Wall 200mm", "WallByCurve",
                        "Nearest", 0.0, True, "Concrete"),
            MappingRule("*mur_formigo*", "OST_Walls", "Exposed Concrete 250mm", "WallByCurve",
                        "Nearest", 0.0, True, "Architectural Concrete"),
            MappingRule("*partition*", "OST_Walls", "Drywall Partition 120mm", "WallByCurve",
                        "Nearest", 0.0, True, "Gypsum Wall Board"),
            MappingRule("*tabique*", "OST_Walls", "Drywall Partition 120mm", "WallByCurve",
                        "Nearest", 0.0, True, "Gypsum Wall Board"),

            # Glazed Facades & Enclosures
            MappingRule("*fachada*", "OST_Walls", "Storefront Curtain Wall", "WallByProfile",
                        "Nearest", 0.0, True, "Glass"),
            MappingRule("*tancament_vidre*", "OST_Walls", "Glazed Curtain Wall", "WallByProfile",
                        "Nearest", 0.0, True, "Glass"),

            # Ceilings
            MappingRule("*ceiling*", "OST_Ceilings", "Compound Ceiling - Acoustic", "CeilingNative",
                        "Nearest", 2.8, False, "Acoustic Tile"),
            MappingRule("*cel_ras*", "OST_Ceilings", "Compound Ceiling - Timber Slats", "CeilingNative",
                        "Nearest", 2.8, False, "Wood - Teak"),
            MappingRule("*falso_techo*", "OST_Ceilings", "Plasterboard Ceiling 12.5mm", "CeilingNative",
                        "Nearest", 2.7, False, "Gypsum"),

            # Roofs & Crown
            MappingRule("*roof*", "OST_Roofs", "Warm Flat Roof 300mm", "RoofNative",
                        "Nearest", 0.0, True, "Roofing Membrane"),
            MappingRule("*cubierta*", "OST_Roofs", "Warm Flat Roof 300mm", "RoofNative",
                        "Nearest", 0.0, True, "Roofing Membrane"),
            MappingRule("*coberta*", "OST_Roofs", "Warm Flat Roof 300mm", "RoofNative",
                        "Nearest", 0.0, True, "Roofing Membrane"),
            MappingRule("*crown*", "OST_Roofs", "Architectural Crown Roof", "RoofByHost",
                        "Nearest", 0.0, True, "Aluminum Composite"),

            # Structural Columns & Modules
            MappingRule("*col*", "OST_Columns", "Concrete Column 600x600", "FamilyInstance",
                        "Nearest", 0.0, True, "Concrete"),
            MappingRule("*pilar*", "OST_Columns", "Concrete Column 600x600", "FamilyInstance",
                        "Nearest", 0.0, True, "Concrete")
        ]
        self.rules = defaults
        self.save()

    def find_matching_rule(self, layer_full_path):
        """Finds the most specific rule matching a given Rhino layer full path."""
        for rule in self.rules:
            if rule.matches(layer_full_path):
                return rule
        return None

    def apply_profile(self, scanned_objects, classification_results):
        """Overrides or boosts classification results using approved profile rules.

        :param scanned_objects: list of ScannedObject
        :param classification_results: dict {rhino_guid: ClassificationResult}
        :return: count of objects matched by profile
        """
        match_count = 0
        for obj in scanned_objects:
            rule = self.find_matching_rule(obj.layer_full_path)
            if not rule:
                continue

            guid = obj.rhino_guid
            c_res = classification_results.get(guid)
            if c_res:
                c_res.revit_category_name = rule.revit_category
                c_res.proposed_strategy = rule.strategy
                c_res.confidence_score = 95.0
                c_res.confidence_tier = "AUTO_READY"
                c_res.is_prechecked = True
                c_res.explanation = "Matched active Transfer Profile rule: '{}'".format(rule.layer_pattern)
                match_count += 1

        return match_count

    def add_rule(self, rule):
        """Appends or replaces a mapping rule."""
        # Replace if exact layer_pattern already exists
        for idx, r in enumerate(self.rules):
            if r.layer_pattern.lower() == rule.layer_pattern.lower():
                self.rules[idx] = rule
                return
        self.rules.append(rule)

    def save(self, filepath=None):
        """Saves current profile to JSON on disk."""
        dest = filepath or self.profile_path
        payload = {
            "metadata": self.metadata,
            "rules": [r.to_dict() for r in self.rules],
            "project_custom_parameters": [
                CustomParameterSpec.from_dict(cp).to_dict()
                for cp in (self.project_custom_parameters or [])
            ]
        }
        try:
            dest_dir = os.path.dirname(os.path.abspath(dest))
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir)
            with open(dest, "w") as f:
                json.dump(payload, f, indent=2)
            return True
        except Exception:
            return False

    def load(self, filepath=None):
        """Loads profile from JSON file."""
        src = filepath or self.profile_path
        if not os.path.exists(src):
            return False

        try:
            with open(src, "r") as f:
                payload = json.load(f)
            self.metadata = payload.get("metadata", self.metadata)
            raw_rules = payload.get("rules", [])
            self.rules = [MappingRule.from_dict(r) for r in raw_rules]
            raw_pcp = payload.get("project_custom_parameters", [])
            self.project_custom_parameters = [CustomParameterSpec.from_dict(cp) for cp in raw_pcp]
            return True
        except Exception:
            return False
