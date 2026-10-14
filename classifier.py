"""
One-Click Rhino-to-Revit Transfer (RIR): Module 2
classifier.py — Evidence-Based Classification & Confidence Scoring Engine

Calculates an evidence-based classification score (0% to 100%) for each object
or layer group using exact weighted scoring criteria:
  1. Layer & Name Match (35%)
  2. Geometry Characteristics (30%)
  3. Spatial / Level Relationship (15%)
  4. Repetition & Block Evidence (10%)
  5. Host & Context Relationship (10%)

Categorizes objects into Floors, Walls, Roofs, Ceilings, Family, or DirectShape,
selects the proposed conversion strategy, and assigns confidence tiers.
"""

import re
import math


class ClassificationResult(object):
    """Holds detailed classification metrics and proposed strategy for an object."""

    def __init__(self, rhino_guid):
        self.rhino_guid = str(rhino_guid)
        self.inferred_category = "DirectShape"  # Default safe fallback
        self.revit_category_name = "OST_GenericModel"
        self.proposed_strategy = "DirectShape"
        self.confidence_score = 0.0  # 0.0 to 100.0
        self.confidence_tier = "MANUAL_OR_EXCLUDED"  # AUTO_READY | SUGGESTED_NEEDS_APPROVAL | MANUAL_OR_EXCLUDED
        self.is_prechecked = False
        self.score_breakdown = {
            "layer_name_score": 0.0,      # max 35.0
            "geometry_score": 0.0,        # max 30.0
            "spatial_level_score": 0.0,   # max 15.0
            "repetition_score": 0.0,      # max 10.0
            "context_score": 0.0          # max 10.0
        }
        self.category_scores = {}
        self.explanation = ""

    def to_dict(self):
        return {
            "rhino_guid": self.rhino_guid,
            "inferred_category": self.inferred_category,
            "revit_category_name": self.revit_category_name,
            "proposed_strategy": self.proposed_strategy,
            "confidence_score": round(self.confidence_score, 1),
            "confidence_tier": self.confidence_tier,
            "is_prechecked": self.is_prechecked,
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
            "category_scores": {k: round(v, 1) for k, v in self.category_scores.items()},
            "explanation": self.explanation
        }


class AecClassifier(object):
    """Evidence-based classification engine for architectural CAD/BIM Rhino models."""

    # Exact scoring weights
    W_LAYER_NAME = 35.0
    W_GEOMETRY = 30.0
    W_SPATIAL_LEVEL = 15.0
    W_REPETITION = 10.0
    W_CONTEXT = 10.0

    # Confidence Tiers
    TIER_AUTO_READY = "AUTO_READY"                        # >= 85%
    TIER_SUGGESTED = "SUGGESTED_NEEDS_APPROVAL"           # 60% - 84%
    TIER_MANUAL = "MANUAL_OR_EXCLUDED"                    # < 60%

    # Trilingual Architectural Vocabulary (English, Spanish, Catalan)
    REGEX_DICTIONARY = {
        "Floors": re.compile(
            r"\b(slab|floor|forjado|losa|deck|pavement|bua|nivel|paviment|solera|sostre|forjat|losas|paviments|terrat)\b",
            re.IGNORECASE
        ),
        "Walls": re.compile(
            r"\b(wall|muro|tabique|core|nucleo|shear|partition|fachada|paret|tancament|façana|facana|mur|murs|parets|pantalla)\b",
            re.IGNORECASE
        ),
        "Roofs": re.compile(
            r"\b(roof|cubierta|canopy|crown|top|coberta|teulada|cobertes|teulades|marquesina)\b",
            re.IGNORECASE
        ),
        "Ceilings": re.compile(
            r"\b(ceiling|falso_techo|falsotecho|soffit|plafond|cel_ras|celras|fals_sostre|falssostre|sostre_acustic)\b",
            re.IGNORECASE
        ),
        "Family": re.compile(
            r"\b(block|col|pilar|column|mullion|montante|door|puerta|window|ventana|furniture|module|porta|finestra|mobiliari|columna|pilars|portes|finestres|carpinteria|fusteria)\b",
            re.IGNORECASE
        ),
        "DirectShape": re.compile(
            r"\b(veil|velo|mesh|malla|screen|celosia|freeform|subd|gelosia|cortina|escultura|reixa|inox|perforated|perforat|organic)\b",
            re.IGNORECASE
        )
    }

    REVIT_CATEGORY_MAP = {
        "Floors": "OST_Floors",
        "Walls": "OST_Walls",
        "Roofs": "OST_Roofs",
        "Ceilings": "OST_Ceilings",
        "Family": "OST_GenericModel",  # or OST_Columns / Doors etc.
        "DirectShape": "OST_GenericModel"
    }

    def __init__(self, mode="Hybrid"):
        """
        :param mode: Global execution mode ("Native BIM", "Hybrid", "Geometry-First")
        """
        self.mode = mode
        self.signature_counts = {}

    def prepare_repetition_cache(self, scanned_objects):
        """Precomputes frequency of repetition signatures across scanned objects."""
        self.signature_counts = {}
        for obj in scanned_objects:
            sig = obj.repetition_signature
            if sig:
                self.signature_counts[sig] = self.signature_counts.get(sig, 0) + 1

    def classify_object(self, obj, all_objects=None):
        """Calculates evidence score and determines target category and strategy for an object."""
        result = ClassificationResult(obj.rhino_guid)

        candidates = ["Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape"]
        candidate_scores = {cat: 0.0 for cat in candidates}
        breakdowns = {cat: {} for cat in candidates}

        # 1. Layer & Name Match (35%)
        layer_scores = self._score_layer_and_name(obj)

        # 2. Geometry Characteristics (30%)
        geom_scores = self._score_geometry(obj)

        # 3. Spatial / Level Relationship (15%)
        spatial_scores = self._score_spatial_level(obj)

        # 4. Repetition & Block Evidence (10%)
        rep_scores = self._score_repetition(obj)

        # 5. Host & Context Relationship (10%)
        ctx_scores = self._score_context(obj, all_objects)

        for cat in candidates:
            l_pts = layer_scores.get(cat, 0.0) * self.W_LAYER_NAME
            g_pts = geom_scores.get(cat, 0.0) * self.W_GEOMETRY
            s_pts = spatial_scores.get(cat, 0.0) * self.W_SPATIAL_LEVEL
            r_pts = rep_scores.get(cat, 0.0) * self.W_REPETITION
            c_pts = ctx_scores.get(cat, 0.0) * self.W_CONTEXT

            total = l_pts + g_pts + s_pts + r_pts + c_pts
            candidate_scores[cat] = min(100.0, total)
            breakdowns[cat] = {
                "layer_name_score": l_pts,
                "geometry_score": g_pts,
                "spatial_level_score": s_pts,
                "repetition_score": r_pts,
                "context_score": c_pts
            }

        # Pick winning category
        best_cat = max(candidates, key=lambda c: candidate_scores[c])
        best_score = candidate_scores[best_cat]

        # If best category score is low or ambiguous, fallback to DirectShape
        if best_score < 40.0:
            best_cat = "DirectShape"
            best_score = max(best_score, candidate_scores.get("DirectShape", 30.0))

        result.inferred_category = best_cat
        result.confidence_score = best_score
        result.category_scores = candidate_scores
        result.score_breakdown = breakdowns[best_cat]
        result.revit_category_name = self.REVIT_CATEGORY_MAP.get(best_cat, "OST_GenericModel")

        # Refine Revit category if Family is specific
        if best_cat == "Family":
            result.revit_category_name = self._refine_family_category(obj)

        # Determine Confidence Tier
        if best_score >= 85.0:
            result.confidence_tier = self.TIER_AUTO_READY
            result.is_prechecked = True
        elif best_score >= 60.0:
            result.confidence_tier = self.TIER_SUGGESTED
            result.is_prechecked = False
        else:
            result.confidence_tier = self.TIER_MANUAL
            result.is_prechecked = False

        # Select Proposed Strategy based on Global Mode & Geometry
        result.proposed_strategy = self._select_strategy(obj, best_cat)
        result.explanation = self._build_explanation(result, obj)

        return result

    def _score_layer_and_name(self, obj):
        """Scores 0.0 to 1.0 for each category based on layer full path and object name."""
        scores = {cat: 0.05 for cat in self.REGEX_DICTIONARY.keys()}
        raw_text = "{} {} {}".format(
            obj.layer_full_path,
            obj.layer_name,
            obj.object_name
        )
        # Normalize underscores, colons, dashes to spaces so \bword\b matches CAD naming
        clean_search = re.sub(r"[^a-zA-Z0-9]+", " ", raw_text)

        for cat, regex in self.REGEX_DICTIONARY.items():
            matches = regex.findall(clean_search)
            if matches:
                # Strong match if keyword present
                scores[cat] = 1.0
            elif any(token.lower() in clean_search.lower() for token in cat.lower().split()):
                scores[cat] = 0.6

        return scores

    def _score_geometry(self, obj):
        """Scores 0.0 to 1.0 for each category based on geometry orientation, dimensions, and topology."""
        scores = {
            "Floors": 0.05,
            "Walls": 0.05,
            "Roofs": 0.05,
            "Ceilings": 0.05,
            "Family": 0.05,
            "DirectShape": 0.1
        }

        # 1. Blocks / Instances
        if obj.object_type == "InstanceReference":
            scores["Family"] = 1.0
            scores["DirectShape"] = 0.3
            return scores

        # 2. Mesh or SubD or complex non-closed
        if obj.object_type in ["Mesh", "SubD"]:
            scores["DirectShape"] = 1.0
            if obj.is_horizontal:
                scores["Floors"] = 0.4
            return scores

        # 3. Horizontal elements (Slabs, Ceilings, Roofs)
        # Typically dz < 1.0m, horizontal face normal, dx/dy > 1.5m
        if obj.is_horizontal or (obj.dz < 1.0 and max(obj.dx, obj.dy) > 1.5):
            scores["Floors"] = 1.0 if (obj.is_closed or obj.is_horizontal) else 0.90
            scores["Ceilings"] = 0.80
            scores["Roofs"] = 0.70
            scores["Walls"] = 0.05

        # 4. Vertical elements (Walls, Partitions)
        # Typically dz >= 1.5m, thin thickness min(dx,dy) < 0.6m
        min_horizontal_dim = min(obj.dx, obj.dy)
        if obj.is_vertical or (obj.dz >= 1.5 and min_horizontal_dim <= 0.8):
            scores["Walls"] = 0.95
            scores["Floors"] = 0.05
            scores["Ceilings"] = 0.05

        # 5. Curve elements
        if obj.object_type == "Curve":
            if obj.is_horizontal:
                scores["Floors"] = 0.7  # Potential boundary curve
            elif obj.is_vertical:
                scores["Walls"] = 0.7

        return scores

    def _score_spatial_level(self, obj):
        """Scores 0.0 to 1.0 based on vertical relation to closest Revit Level."""
        scores = {
            "Floors": 0.1,
            "Walls": 0.1,
            "Roofs": 0.1,
            "Ceilings": 0.1,
            "Family": 0.2,
            "DirectShape": 0.2
        }

        # Base offset to nearest level
        dz = abs(obj.dz_to_level)

        # Floor slabs usually sit directly on or within ±300mm (0.3m) of a Level
        if dz <= 0.3:
            scores["Floors"] = 1.0
            scores["Walls"] = 0.9  # Walls also spring from a level base

        # Ceilings usually sit 2.2m to 3.8m above a level
        if 2.1 <= obj.dz_to_level <= 4.0:
            scores["Ceilings"] = 1.0

        # Roofs typically at top building zone
        if obj.building_zone == "Crown":
            scores["Roofs"] = 0.95
            scores["Floors"] = 0.5

        # Veil facade envelope
        if obj.building_zone == "Veil Envelope":
            scores["DirectShape"] = 1.0

        return scores

    def _score_repetition(self, obj):
        """Scores 0.0 to 1.0 based on repetition frequency across the project."""
        scores = {cat: 0.1 for cat in ["Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape"]}

        if obj.object_type == "InstanceReference":
            scores["Family"] = 1.0
            return scores

        count = self.signature_counts.get(obj.repetition_signature, 1)
        if count >= 4:
            # High repetition strongly indicates a loadable Family / module
            scores["Family"] = 0.9
            scores["DirectShape"] = 0.4
        elif count >= 2:
            scores["Family"] = 0.6
        else:
            scores["Family"] = 0.1

        return scores

    def _score_context(self, obj, all_objects):
        """Scores 0.0 to 1.0 based on spatial context and zone."""
        scores = {cat: 0.2 for cat in ["Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape"]}

        if obj.is_horizontal:
            scores["Floors"] = 0.8
            scores["Ceilings"] = 0.5
            scores["Roofs"] = 0.5
        elif obj.is_vertical:
            scores["Walls"] = 0.8

        # Architectural Stainless Steel Veil / Screen
        if obj.building_zone == "Veil Envelope":
            scores["DirectShape"] = 1.0
            scores["Walls"] = 0.3

        # Podium or Tower cores
        if obj.building_zone in ["Podium", "Typical Tower"]:
            if obj.is_horizontal:
                scores["Floors"] = 0.9
            if obj.is_vertical:
                scores["Walls"] = 0.9

        return scores

    def _refine_family_category(self, obj):
        """Determines specific Revit category for a Family item based on name."""
        name = "{} {}".format(obj.layer_full_path, obj.object_name).lower()
        if any(k in name for k in ["col", "pilar", "column"]):
            return "OST_Columns"
        if any(k in name for k in ["door", "puerta", "porta"]):
            return "OST_Doors"
        if any(k in name for k in ["window", "ventana", "finestra"]):
            return "OST_Windows"
        if any(k in name for k in ["mullion", "montante"]):
            return "OST_CurtainWallMullions"
        if any(k in name for k in ["furniture", "mobiliari"]):
            return "OST_Furniture"
        return "OST_GenericModel"

    def _select_strategy(self, obj, category):
        """Selects the conversion strategy under the active Global Execution Mode."""
        if self.mode == "Geometry-First":
            # DirectShape priority for maximum 3D fidelity
            if category == "Floors" and obj.is_horizontal and obj.is_closed:
                return "FloorNative"  # Native floors retained for room bounding
            return "DirectShape"

        if category == "Floors":
            if obj.is_horizontal and (obj.is_closed or obj.object_type == "Curve"):
                return "FloorNative"
            else:
                return "FloorByHost"

        elif category == "Walls":
            if self.mode == "Native BIM":
                # Linear rectangular vs profiled wall
                if obj.is_vertical and obj.is_closed:
                    return "WallByCurve"
                return "WallByProfile"
            else:  # Hybrid
                if obj.is_vertical and obj.is_closed and obj.thickness <= 0.6:
                    return "WallByCurve"
                return "DirectShape"

        elif category == "Roofs":
            return "RoofNative" if obj.is_horizontal else "RoofByHost"

        elif category == "Ceilings":
            return "CeilingNative"

        elif category == "Family":
            return "FamilyInstance"

        elif category == "DirectShape":
            return "DirectShape"

        return "DirectShape"

    def _build_explanation(self, result, obj):
        """Generates clear, human-readable rationale for the assigned classification."""
        reasons = []
        if result.score_breakdown["layer_name_score"] >= 20.0:
            reasons.append("layer/name keyword match")
        if result.score_breakdown["geometry_score"] >= 20.0:
            if obj.is_horizontal:
                reasons.append("horizontal planar geometry")
            elif obj.is_vertical:
                reasons.append("vertical planar geometry")
            elif obj.object_type == "InstanceReference":
                reasons.append("block instance reference")
        if result.score_breakdown["spatial_level_score"] >= 10.0:
            reasons.append("aligned with Level '{}' (dz={:.2f})".format(obj.nearest_level_name, obj.dz_to_level))
        if result.score_breakdown["repetition_score"] >= 6.0:
            reasons.append("repeated pattern (count={})".format(self.signature_counts.get(obj.repetition_signature, 1)))

        return "Inferred as {} ({:.1f}%) via: {}.".format(
            result.inferred_category,
            result.confidence_score,
            ", ".join(reasons) if reasons else "geometry heuristics"
        )
