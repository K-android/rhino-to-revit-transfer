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


class MappingRule(object):
    """Defines a deterministic transfer mapping rule from Rhino layer to Revit BIM entity."""

    def __init__(self, layer_pattern, revit_category="OST_GenericModel",
                 revit_type="Default", strategy="DirectShape",
                 level_rule="Nearest", base_offset=0.0,
                 room_bounding=True, material="ByLayer",
                 subcategory=""):
        self.layer_pattern = layer_pattern
        self.revit_category = revit_category
        self.revit_type = revit_type
        self.strategy = strategy  # FloorNative, WallByCurve, WallByProfile, CeilingNative, RoofNative, FamilyInstance, DirectShape
        self.level_rule = level_rule  # "Nearest", "Explicit", "BoundingBoxBase"
        self.base_offset = float(base_offset)
        self.room_bounding = bool(room_bounding)
        self.material = material
        self.subcategory = subcategory
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
            subcategory=data.get("subcategory", "")
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
            module_dir = os.path.dirname(os.path.abspath(__file__))
            self.profile_path = os.path.join(module_dir, self.DEFAULT_PROFILE_NAME)

        self.rules = []
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
            "rules": [r.to_dict() for r in self.rules]
        }
        with open(dest, "w") as f:
            json.dump(payload, f, indent=2)

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
            return True
        except Exception:
            return False
