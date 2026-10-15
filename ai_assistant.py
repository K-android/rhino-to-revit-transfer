# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 3
ai_assistant.py - Optional LLM Semantic Interpreter

Interprets ambiguous architectural naming conventions, with specialized heuristics
and optional LLM translation for Spanish and Catalan terms commonly used in
international architectural CAD/BIM project archives.

Operates with full offline heuristic capability and supports optional external
LLM provider calls (OpenAI/Anthropic/Gemini) if an API key is provided.
"""

import os
import json
import re

try:
    # Standard Python 3 / IronPython compatible HTTP imports
    import urllib.request as urllib_req
    import urllib.parse as urllib_parse
    HTTP_AVAILABLE = True
except ImportError:
    try:
        import urllib2 as urllib_req
        import urllib as urllib_parse
        HTTP_AVAILABLE = True
    except ImportError:
        HTTP_AVAILABLE = False


class SemanticInterpretation(object):
    """Holds semantic decoding and architectural intent for an ambiguous layer or block."""

    def __init__(self, raw_name):
        self.raw_name = raw_name
        self.english_translation = raw_name
        self.inferred_category = "DirectShape"
        self.suggested_revit_type = "Default"
        self.architectural_intent = ""
        self.confidence_boost = 0.0
        self.source = "Heuristic"  # "Heuristic" | "LLM" | "Cached"

    def to_dict(self):
        return {
            "raw_name": self.raw_name,
            "english_translation": self.english_translation,
            "inferred_category": self.inferred_category,
            "suggested_revit_type": self.suggested_revit_type,
            "architectural_intent": self.architectural_intent,
            "confidence_boost": self.confidence_boost,
            "source": self.source
        }


class AISemanticInterpreter(object):
    """Interprets Catalan/Spanish architectural strings into BIM categories and Revit types."""

    # Curated Catalan & Spanish architectural lexicon (International standard)
    OFFLINE_DICTIONARY = {
        # Floors & Slabs
        "forjat": ("Floor Slab", "Floors", "Generic Floor 300mm", "Structural concrete floor slab"),
        "forjado": ("Floor Slab", "Floors", "Generic Floor 300mm", "Cast-in-place concrete slab"),
        "losa": ("Solid Slab", "Floors", "Solid Concrete Slab 250mm", "Reinforced concrete slab"),
        "llosa": ("Solid Slab", "Floors", "Solid Concrete Slab 250mm", "Reinforced concrete slab"),
        "paviment": ("Pavement / Finish", "Floors", "Floor Finish 50mm", "Architectural floor finish / screed"),
        "pavimento": ("Pavement / Finish", "Floors", "Floor Finish 50mm", "Architectural floor finish"),
        "solera": ("Ground Slab", "Floors", "Slab on Grade 200mm", "Cast slab on grade / subfloor"),
        "voladis": ("Cantilever Slab", "Floors", "Cantilever Slab 300mm", "Projecting balcony or overhang"),
        "voladizo": ("Cantilever Slab", "Floors", "Cantilever Slab 300mm", "Projecting balcony or overhang"),
        "reticular": ("Waffle Slab", "Floors", "Waffle Slab 400mm", "Two-way ribbed waffle slab"),

        # Walls & Partitions
        "mur": ("Wall", "Walls", "Basic Wall - Concrete 200mm", "Structural or architectural wall"),
        "muro": ("Wall", "Walls", "Basic Wall - Concrete 200mm", "Structural or architectural wall"),
        "paret": ("Partition Wall", "Walls", "Drywall Partition 120mm", "Interior partition wall"),
        "tabique": ("Partition Wall", "Walls", "Drywall Partition 120mm", "Interior partition wall"),
        "tancament": ("Exterior Enclosure", "Walls", "Exterior Curtain Wall / Enclosure", "Building envelope enclosure"),
        "cerramiento": ("Exterior Enclosure", "Walls", "Exterior Curtain Wall / Enclosure", "Building envelope enclosure"),
        "pantalla": ("Shear Wall", "Walls", "Retaining / Shear Wall 400mm", "Concrete shear or retaining wall"),
        "formigo": ("Exposed Concrete Wall", "Walls", "Architectural Concrete Wall 250mm", "RCR board-marked concrete wall"),
        "hormigon": ("Concrete Wall", "Walls", "Concrete Wall 250mm", "Concrete wall"),
        "nucleo": ("Core Wall", "Walls", "Shear Core Wall 350mm", "Elevator and stair shear core"),
        "nucli": ("Core Wall", "Walls", "Shear Core Wall 350mm", "Elevator and stair shear core"),

        # Facade Envelope & Stainless Steel Veils
        "velo": ("Stainless Steel Veil Screen", "DirectShape", "Facade Screen Panel - Inox", "Custom stainless-steel shading screen"),
        "veil": ("Stainless Steel Veil Screen", "DirectShape", "Facade Screen Panel - Inox", "Custom stainless-steel shading screen"),
        "gelosia": ("Louver Screen", "DirectShape", "Architectural Screen / Louver", "Metal louver / shading trellis"),
        "celosia": ("Louver Screen", "DirectShape", "Architectural Screen / Louver", "Metal louver / shading trellis"),
        "reixa": ("Metal Grate", "DirectShape", "Metal Grille Screen", "Metal decorative grille or security screen"),
        "inox": ("Stainless Steel Element", "DirectShape", "Stainless Steel Cladding", "High-grade stainless-steel architectural element"),
        "vidre": ("Glazing", "Walls", "Curtain Wall - Glazed", "Glass curtain wall or window wall"),
        "vidrio": ("Glazing", "Walls", "Curtain Wall - Glazed", "Glass curtain wall or window wall"),

        # Ceilings & Soffits
        "cel_ras": ("Suspended Ceiling", "Ceilings", "Compound Ceiling - Acoustic", "Acoustic or timber suspended ceiling"),
        "falso_techo": ("Suspended Ceiling", "Ceilings", "Compound Ceiling - Acoustic", "Suspended ceiling"),
        "sostre": ("Ceiling Soffit", "Ceilings", "Plasterboard Ceiling 12.5mm", "Ceiling soffit finish"),
        "plafond": ("Ceiling Soffit", "Ceilings", "Plasterboard Ceiling 12.5mm", "Ceiling finish"),

        # Roofs & Crowns
        "coberta": ("Roof", "Roofs", "Warm Flat Roof 300mm", "Building roof assembly"),
        "cubierta": ("Roof", "Roofs", "Warm Flat Roof 300mm", "Building roof assembly"),
        "teulada": ("Sloped Roof", "Roofs", "Sloped Roof Assembly", "Sloped roof or canopy"),
        "marquesina": ("Canopy", "Roofs", "Entrance Canopy", "Cantilevered entry canopy"),

        # Columns, Openings & Modules
        "pilar": ("Structural Column", "Family", "Concrete Column 400x400", "Vertical structural loadbearing column"),
        "columna": ("Structural Column", "Family", "Concrete Column 400x400", "Vertical structural column"),
        "porta": ("Door", "Family", "Single Flush Door", "Hinged or sliding door"),
        "puerta": ("Door", "Family", "Single Flush Door", "Hinged or sliding door"),
        "finestra": ("Window", "Family", "Fixed Glazed Window", "Exterior or interior window"),
        "ventana": ("Window", "Family", "Fixed Glazed Window", "Exterior or interior window"),
        "fusteria": ("Joinery / Millwork", "Family", "Architectural Millwork", "Custom wood joinery / cabinetry"),
        "carpinteria": ("Joinery / Millwork", "Family", "Architectural Millwork", "Custom joinery / casework")
    }

    def __init__(self, api_key=None, api_endpoint=None):
        self.api_key = api_key or os.environ.get("R2R_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.api_endpoint = api_endpoint or os.environ.get("R2R_AI_ENDPOINT")
        self.cache = {}

    def interpret(self, raw_layer_name):
        """Translates and categorizes an architectural layer or block name.

        Uses local cache -> rule-based lexicon -> optional LLM endpoint.
        """
        clean_key = raw_layer_name.strip()
        if clean_key in self.cache:
            cached_res = self.cache[clean_key]
            cached_res.source = "Cached"
            return cached_res

        # 1. Offline heuristic scan
        heuristic_res = self._interpret_heuristically(clean_key)
        if heuristic_res and heuristic_res.confidence_boost >= 0.70:
            self.cache[clean_key] = heuristic_res
            return heuristic_res

        # 2. Try LLM endpoint if configured
        if self.api_key and HTTP_AVAILABLE:
            try:
                llm_res = self._call_llm_api(clean_key)
                if llm_res:
                    self.cache[clean_key] = llm_res
                    return llm_res
            except Exception as ex:
                # Silently fallback to heuristic result on network/API failure
                pass

        # 3. Fallback to best heuristic match or default
        res = heuristic_res if heuristic_res else SemanticInterpretation(clean_key)
        self.cache[clean_key] = res
        return res

    def _interpret_heuristically(self, raw_name):
        """Dissects layer name using tokenization and multilingual dictionary matching."""
        res = SemanticInterpretation(raw_name)
        norm_name = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name.lower())
        tokens = [t for t in norm_name.split("_") if len(t) >= 2]

        matched_entries = []
        clean_norm_name = norm_name.replace("_", "")
        for dict_key, (eng, cat, rvt_type, intent) in self.OFFLINE_DICTIONARY.items():
            clean_dict_key = dict_key.lower().replace("_", "")
            if dict_key in norm_name or clean_dict_key in clean_norm_name or any(token == dict_key or dict_key in token for token in tokens):
                matched_entries.append((eng, cat, rvt_type, intent, len(dict_key)))

        if not matched_entries:
            return None

        # Sort by longest keyword match to favor more specific architectural terms
        matched_entries.sort(key=lambda x: x[4], reverse=True)
        best = matched_entries[0]

        res.english_translation = best[0]
        res.inferred_category = best[1]
        res.suggested_revit_type = best[2]
        res.architectural_intent = best[3]
        res.confidence_boost = 0.85
        res.source = "Heuristic"
        return res

    def _call_llm_api(self, raw_name):
        """Optional JSON-based call to an external LLM endpoint."""
        prompt = (
            "You are an AEC BIM architect. Decode this Catalan/Spanish architectural CAD layer name from RCR Arquitectes:\n"
            "Layer: '{}'\n"
            "Return strictly valid JSON with keys: 'english_translation', 'inferred_category' "
            "(one of: Floors, Walls, Roofs, Ceilings, Family, DirectShape), 'suggested_revit_type', and 'architectural_intent'."
        ).format(raw_name)

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(self.api_key)
        }

        endpoint = self.api_endpoint or "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a bilingual Spanish/Catalan AEC BIM specialist."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }

        req = urllib_req.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )

        with urllib_req.urlopen(req, timeout=5) as response:
            if response.status == 200:
                raw_body = response.read().decode("utf-8")
                data = json.loads(raw_body)
                content = data["choices"][0]["message"]["content"]
                # Parse JSON block
                json_match = re.search(r"\{.*\}", content, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    res = SemanticInterpretation(raw_name)
                    res.english_translation = parsed.get("english_translation", raw_name)
                    res.inferred_category = parsed.get("inferred_category", "DirectShape")
                    res.suggested_revit_type = parsed.get("suggested_revit_type", "Default")
                    res.architectural_intent = parsed.get("architectural_intent", "Interpreted by LLM")
                    res.confidence_boost = 0.95
                    res.source = "LLM"
                    return res

        return None
