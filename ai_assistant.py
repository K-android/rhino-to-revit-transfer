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


PROVIDER_PRESETS = {
    "OpenAI": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "o3-mini", "gpt-3.5-turbo"]
    },
    "Google Gemini": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "default_model": "gemini-3.8-flash",
        "models": [
            "gemini-3.8-flash",
            "gemini-3.8",
            "gemini-3.6-flash",
            "gemini-3.6",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ]
    },
    "DeepSeek": {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"]
    },
    "OpenRouter": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "google/gemini-3.8-flash",
        "models": [
            "google/gemini-3.8-flash",
            "google/gemini-3.6-flash",
            "google/gemini-2.0-flash-exp:free",
            "anthropic/claude-3.5-sonnet",
            "meta-llama/llama-3.3-70b-instruct"
        ]
    },
    "Local Ollama / LM Studio": {
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "default_model": "llama3",
        "models": ["llama3", "mistral", "qwen2.5", "phi3"]
    },
    "Custom / Other": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "custom-model",
        "models": ["custom-model"]
    }
}


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
        "jardinera": ("Planter Box", "Family", "OST_Planting", "Architectural planter container"),
        "plantador": ("Planter", "Family", "OST_Planting", "Vegetation container"),
        "terreny": ("Topography / Site", "Toposolid", "Toposolid: Earth", "Site terrain / toposolid surface"),
        "topografia": ("Topography", "Toposolid", "Toposolid: Natural Earth", "Topographical site mesh"),
        "sofa": ("Lounge Seating", "Family", "OST_Furniture", "Architectural furniture seating"),
        "banc": ("Bench / Seating", "Family", "OST_Furniture", "Fixed or loose bench seating"),
        "cadira": ("Chair", "Family", "OST_Furniture", "Seating furniture"),
        "taula": ("Table", "Family", "OST_Furniture", "Surface furniture"),
        "fusteria": ("Joinery / Millwork", "Family", "OST_Casework", "Custom wood joinery / cabinetry"),
        "carpinteria": ("Joinery / Millwork", "Family", "OST_Casework", "Custom joinery / casework"),
        "armari": ("Cabinet / Closet", "Family", "OST_Casework", "Built-in wardrobe or casework"),
        "armario": ("Cabinet / Closet", "Family", "OST_Casework", "Built-in wardrobe or casework"),
        "cuina": ("Kitchen Casework", "Family", "OST_Casework", "Kitchen cabinetry and counter"),
        "cocina": ("Kitchen Casework", "Family", "OST_Casework", "Kitchen cabinetry and counter"),
        "sanitari": ("Plumbing Fixture", "Family", "OST_PlumbingFixtures", "Bathroom sanitary fixture"),
        "sanitario": ("Plumbing Fixture", "Family", "OST_PlumbingFixtures", "Bathroom sanitary fixture"),
        "lavabo": ("Washbasin", "Family", "OST_PlumbingFixtures", "Bathroom sink / washbasin"),
        "inodoro": ("Water Closet / Toilet", "Family", "OST_PlumbingFixtures", "Toilet fixture"),
        "clima": ("HVAC Equipment", "Family", "OST_MechanicalEquipment", "Air conditioning / mechanical equipment"),
        "climatizacion": ("HVAC Equipment", "Family", "OST_MechanicalEquipment", "Mechanical climate control equipment"),
        "conducte": ("HVAC Ductwork", "Family", "OST_MechanicalEquipment", "Ventilation ductwork"),
        "conducto": ("HVAC Ductwork", "Family", "OST_MechanicalEquipment", "Ventilation ductwork"),
        "llum": ("Lighting Fixture", "Family", "OST_LightingFixtures", "Architectural luminaire / light"),
        "luz": ("Lighting Fixture", "Family", "OST_LightingFixtures", "Architectural luminaire / light"),
        "luminaria": ("Lighting Fixture", "Family", "OST_LightingFixtures", "Ceiling or wall luminaire"),
        "electricitat": ("Electrical Fixture", "Family", "OST_ElectricalFixtures", "Electrical device / receptacle"),
        "electricidad": ("Electrical Fixture", "Family", "OST_ElectricalFixtures", "Electrical device / receptacle"),
        "barana": ("Railing / Balustrade", "Family", "OST_StairsRailing", "Safety guardrail or handrail"),
        "barandilla": ("Railing / Balustrade", "Family", "OST_StairsRailing", "Safety guardrail or handrail"),
        "rampa": ("Ramp", "Family", "OST_Ramps", "Access ramp assembly"),
        "aparcament": ("Parking", "Family", "OST_Parking", "Parking bay / stall"),
        "aparcamiento": ("Parking", "Family", "OST_Parking", "Parking bay / stall"),
        "viga": ("Structural Beam", "Family", "OST_StructuralFraming", "Horizontal structural framing beam"),
        "jassera": ("Structural Girder", "Family", "OST_StructuralFraming", "Primary loadbearing concrete girder"),
        "fonament": ("Foundation Footing", "Family", "OST_StructuralFoundation", "Concrete pad / strip footing"),
        "zapata": ("Foundation Footing", "Family", "OST_StructuralFoundation", "Concrete foundation footing"),
        "ascensor": ("Elevator / Lift", "Family", "OST_SpecialityEquipment", "Passenger or service elevator cab")
    }

    def __init__(self, api_key=None, api_endpoint=None, model=None, provider=None):
        self.provider = provider or "OpenAI"
        self.model = model or "gpt-4o-mini"
        self.api_key = api_key or os.environ.get("R2R_AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.api_endpoint = api_endpoint or os.environ.get("R2R_AI_ENDPOINT") or "https://api.openai.com/v1/chat/completions"
        self.cache = {}

        # Load persisted settings if available
        self.load_config()

    @classmethod
    def get_config_path(cls):
        """Returns isolated persistent path in AppData/pyRevit/R2R/ai_config.json."""
        base_dir = os.path.join(os.environ.get("APPDATA", ""), "pyRevit", "R2R")
        if not os.path.exists(base_dir):
            try:
                os.makedirs(base_dir)
            except Exception:
                pass
        return os.path.join(base_dir, "ai_config.json")

    def load_config(self):
        """Loads saved provider, model, api_key, and endpoint from disk."""
        cfg_path = self.get_config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    data = json.load(f)
                    if data.get("provider"):
                        self.provider = data["provider"]
                    if data.get("model"):
                        self.model = data["model"]
                    if data.get("api_key"):
                        self.api_key = data["api_key"]
                    if data.get("api_endpoint"):
                        self.api_endpoint = data["api_endpoint"]
                    return True
            except Exception:
                pass
        return False

    def save_config(self):
        """Saves current provider, model, api_key, and endpoint to disk."""
        cfg_path = self.get_config_path()
        try:
            data = {
                "provider": getattr(self, "provider", "OpenAI"),
                "model": getattr(self, "model", "gpt-4o-mini"),
                "api_key": getattr(self, "api_key", ""),
                "api_endpoint": getattr(self, "api_endpoint", "https://api.openai.com/v1/chat/completions")
            }
            with open(cfg_path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False

    def test_connection(self, api_key=None, api_endpoint=None, model=None, provider=None):
        """Tests live connectivity to the chosen LLM provider and model."""
        key = api_key if api_key is not None else self.api_key
        endpoint = api_endpoint or self.api_endpoint or "https://api.openai.com/v1/chat/completions"
        target_model = model or self.model or "gpt-4o-mini"
        prov = provider or self.provider or "AI"

        is_local = any(loc in endpoint.lower() for loc in ["localhost", "127.0.0.1", "0.0.0.0"])
        if not key and not is_local:
            return False, "API Key is required for {}".format(prov)

        if not HTTP_AVAILABLE:
            return False, "HTTP client module is not available in this Python environment."

        headers = {
            "Content-Type": "application/json"
        }
        if key:
            headers["Authorization"] = "Bearer {}".format(key.strip())

        payload = {
            "model": target_model.strip(),
            "messages": [
                {"role": "user", "content": "Respond with the word OK."}
            ],
            "max_tokens": 10,
            "temperature": 0.0
        }

        try:
            req = urllib_req.Request(
                endpoint.strip(),
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib_req.urlopen(req, timeout=8) as response:
                status_code = getattr(response, "status", getattr(response, "code", 200))
                if status_code in (200, 201):
                    raw_body = response.read().decode("utf-8")
                    data = json.loads(raw_body)
                    if "choices" in data and len(data["choices"]) > 0:
                        return True, "Connected successfully to '{}' via {}!".format(target_model, prov)
                    return True, "Endpoint responded with status 200 OK."
                return False, "HTTP status code: {}".format(status_code)
        except Exception as ex:
            err_msg = str(ex)
            if hasattr(ex, "read"):
                try:
                    err_msg += " - " + ex.read().decode("utf-8")[:200]
                except Exception:
                    pass
            return False, "Connection failed: {}".format(err_msg)

    def interpret(self, raw_layer_name):
        """Translates and categorizes an architectural layer or block name.

        Uses local cache -> optional LLM endpoint (if key provided) -> rule-based lexicon.
        """
        clean_key = raw_layer_name.strip()
        if clean_key in self.cache:
            cached_res = self.cache[clean_key]
            cached_res.source = "Cached"
            return cached_res

        # 1. Try LLM endpoint if configured with an API key
        if self.api_key and HTTP_AVAILABLE:
            try:
                llm_res = self._call_llm_api(clean_key)
                if llm_res:
                    self.cache[clean_key] = llm_res
                    return llm_res
            except Exception:
                pass

        # 2. Offline heuristic scan
        heuristic_res = self._interpret_heuristically(clean_key)
        if heuristic_res:
            self.cache[clean_key] = heuristic_res
            return heuristic_res

        # 3. Default fallback
        res = SemanticInterpretation(clean_key)
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
            "You are an AEC BIM architect. Decode this architectural CAD/Rhino layer name:\n"
            "Layer: '{}'\n"
            "Return strictly valid JSON with keys: 'english_translation', 'inferred_category' "
            "(one of: Floors, Walls, Roofs, Ceilings, Columns, Structural Columns, Doors, Windows, Curtain Panels, Stairs, Railings, Ramps, Furniture, Furniture Systems, Casework, Specialty Equipment, Plumbing Fixtures, Mechanical Equipment, Electrical Fixtures, Lighting Fixtures, Structural Framing, Structural Foundation, Toposolid, Site, Planting, Parking, Roads, Mass, Entourage, Signage, Generic Models), "
            "'suggested_revit_type', and 'architectural_intent'."
        ).format(raw_name)

        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = "Bearer {}".format(self.api_key.strip())

        endpoint = self.api_endpoint or "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": getattr(self, "model", "gpt-4o-mini").strip(),
            "messages": [
                {"role": "system", "content": "You are a bilingual multilingual AEC BIM specialist. Output only JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }

        req = urllib_req.Request(
            endpoint.strip(),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )

        with urllib_req.urlopen(req, timeout=6) as response:
            status_code = getattr(response, "status", getattr(response, "code", 200))
            if status_code in (200, 201):
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
