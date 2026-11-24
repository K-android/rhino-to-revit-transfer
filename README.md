# One-Click Rhino-to-Revit Transfer (RIR)
### Production-Grade Computational Design & BIM Geometry Pipeline

The **One-Click Rhino-to-Revit Transfer (R2R)** plugin runs directly inside **Rhino.Inside.Revit (RIR)**. Rather than performing a blind geometry dump or unintelligent mesh import, it implements a controlled 6-stage pipeline:

```
SCAN ➔ CLASSIFY ➔ REVIEW ➔ TRANSFER ➔ VALIDATE ➔ SYNC
```

It infers architectural design intent via deterministic geometry fingerprinting and trilingual metadata scoring (English, Spanish, Catalan), enforces per-object compatibility via a declarative **Strategy Rule Engine**, presents a responsive **3-step Master–Detail WPF XAML** dashboard, builds native Revit system elements and exact-geometry `.rfa` families across specialized pipelines with clean `DirectShape` fallbacks, conducts dual-status view graphics validation, and tracks persistent revision state in Revit `ExtensibleStorage`.

---

## 1. Directory Structure

```
rhino-to-revit-transfer/
├── installer/                     # 1-Click pyRevit & Revit Add-in Installers
│   ├── install_pyrevit.bat        # 1-click pyRevit deployer (.py, .xaml, templates)
│   ├── uninstall_pyrevit.bat      # Clean pyRevit extension removal
│   ├── script.py                  # pyRevit pushbutton entry script
│   ├── bundle.yaml                # pyRevit ribbon button metadata
│   ├── install.bat / install.ps1  # Native C# Revit add-in installer
│   └── uninstall.bat / .ps1       # Native C# Revit add-in uninstaller
├── templates/                     # Bundled Revit Family (.rfa/.rft) & Shared Parameter files
│   ├── Generic Model.rfa / .rft
│   ├── Metric Generic Model.rfa / .rft
│   ├── Mass.rft / Metric Mass.rft
│   └── R2R_SharedParameters.txt
├── csharp/                        # Optional Native C# .NET Revit Add-in Solution
├── __init__.py                    # Package root & exports
├── main_launcher.py               # Unified entry-point launcher (pyRevit / RIR / Script Editor)
├── scanner.py                     # Module 1: Rhino Scanner, Primitive Classifier & SHA-256 Hasher
├── classifier.py                  # Module 2: Evidence-Based Architectural Classifier
├── strategy_rules.py              # Module 2B: Compatibility Rule Engine & Mixed-Layer Splitter
├── ai_assistant.py                # Module 3: Optional LLM Semantic Interpreter
├── profile_manager.py             # Module 4: JSON Mapping Profiles & Custom Parameter Specs
├── family_factory.py              # Module 4B: Native .rfa Family Builder (Parametric & FreeForm)
├── conversion_router.py           # Module 5: RIR Element Creation Pipelines & Geometry Encoders
├── graphics_validator.py          # Module 6: Dual-Status BIM & 4-View Graphics Validator
├── sync_engine.py                 # Module 7: Persistent Identity (GUID + GeoHash) & Sync Tracker
├── reporting.py                   # Module 8: Preflight, BUA/GFA Area Check & HTML/CSV/JSON Reports
├── ui_dialog.py                   # Module 9: Pure-Python WPF XAML Controller (IronPython 2.7 / .NET 8)
├── ui_dashboard.xaml              # 3-Step Master–Detail Main Dashboard Layout
├── ui_layer_config.xaml           # Per-Layer Mapping & Compatibility Modal Layout
├── ui_custom_params.xaml          # Custom Parameters & Family Constraints Modal Layout
├── ui_settings.xaml               # Execution Mode, Origin Shift, Units & AI Settings Modal
├── Transfer_Profile.json          # Preconfigured mapping profile
├── test_pipeline.py               # Automated unit test suite (60 tests)
└── README.md                      # System architecture & deployment documentation
```

---

## 2. Six-Stage Architecture & Pipeline

### Stage 1: SCAN (`scanner.py`)
- Scans `Rhino.RhinoDoc.ActiveDoc.Objects` (supporting hidden/locked layer filters).
- Extracts metadata: GUID, full layer hierarchy, object name, block name, user text dictionary, material name, and object color.
- Computes **Deterministic Geometry Fingerprint**:
  - World `BoundingBox` (`bbox_min`, `bbox_max`, `centroid`, `dx`, `dy`, `dz`).
  - Orientation & slope flags: `is_closed`, `is_planar`, `is_horizontal` ($|\text{Normal}.Z| > 0.95$), `is_vertical` ($|\text{Normal}.Z| < 0.05$), `is_planar_sloped` ($0.20 \le |\text{Normal}.Z| \le 0.95$ with `dominant_normal` and `slope_ratio`), `is_curved_extrusion` (horizontal barrel/vaulted extrusion detection), `thickness`, and `loop_count`.
  - Strict geometric primitive classification (`Box`, `Cylinder`, `ProfileExtrusion`, `AdaptivePanel`, `FreeFormSolid`, `OpenMesh`, `OpenSurface`, `BlockInstance`, `Curve`).
  - SHA-256 `geo_hash` and rotation-invariant `repetition_signature`.
- Spatial & Preflight Context: Resolves nearest Revit `Level` (`dz_to_level`), flags coordinates $>16\text{ km}$ from origin, detects duplicates, and flags open loops.

### Stage 2: CLASSIFY & RULE ENGINE (`classifier.py` & `strategy_rules.py`)
- **`AecClassifier`:** Weighted architectural scoring across Layer/Name Match (35%, English/Spanish/Catalan), Geometry Characteristics (30%, including planar-sloped and vaulted roof heuristics), Spatial/Level Relationship (15%), Repetition/Blocks (10%), and Context (10%).
- **`StrategyRuleEngine`:**
  - Evaluates every object against its target strategy (`FloorNative`, `FloorByHost`, `WallByCurve`, `WallByProfile`, `CeilingNative`, `RoofNative`, `RoofByHost`, `Toposolid`, `ParametricFamily`, `FreeFormFamily`, `RepetitiveFamily`, `AdaptiveComponent`, `DirectShape`).
  - Blocks physically incompatible geometry (e.g., `OpenMesh` or double-curved shells on sketch-based `RoofNative`/`FloorNative`/`CeilingNative`, or non-prismatic solids on `ParametricFamily`) with clear reasons and safe fallbacks.
  - Respects target Revit category when recommending strategies (e.g. `Roofs` layers recommend `RoofNative` instead of `FloorNative`) and warns when pairing loadable `.rfa` families with system-only categories.
  - Supports **Mixed-Layer Splitting** (`split_objects_by_geometry`), dividing heterogeneous Rhino layers into homogeneous virtual sub-layers.

### Stage 3: REVIEW & CONFIGURE (`ui_dialog.py`, `ui_*.xaml`, `profile_manager.py`)
Pure-Python WPF XAML Master–Detail UI compatible with **Revit 2020–2024 (.NET Framework 4.8)** and **Revit 2025–2026 (.NET 8)** via pyRevit (`IPY2712PR`):
- **Step 1 — Review & Map:**
  - Searchable, filterable layer list with live status badges (`OK`, `MIXED`, `BLOCKED`), target summary, and object counts.
  - **Right-Side Live Inspector (Single & Bulk Edit):** Edit Revit Category (auto-syncing compatible native strategies), Conversion Strategy, Revit Material, Include toggle, **Split Mixed Layer**, and **Custom Parameters**.
  - **Custom Parameters (`CustomParametersDialog`):** Deterministic UUIDv5 shared parameters supporting `Data` parameters (`Text`, `Number`, `Integer`, `YesNo`, `Length`, `Material` with `{tokens}` and `@usertext:Key`) and editable family `Constraint` formulas.
- **Step 2 — Transfer Progress:** Live per-layer/object progress tracking and execution log.
- **Step 3 — Validate & QA:** Dual-status audit grid, view matrix (`Plan` / `Section`), Revit element selection/isolation, and HTML/CSV/JSON report export.
- **Settings (`SettingsDialog`):** Global Mode (`Native BIM`, `Hybrid`, `Geometry-First`), Mixed-Geometry Policy (`AutoFallback` vs. `StrictSkip`), Auto-BUA/GFA toggle, Move to Revit Origin `(0,0,0)`, unit override, Repetition Threshold, and `AI Config...` (Gemini, OpenAI, DeepSeek, OpenRouter, local Ollama/LM Studio, or offline architectural lexicon).

### Stage 4: TRANSFER (`conversion_router.py` & `family_factory.py`)
Executes in two phases: **Phase 1** pre-generates and loads `.rfa` families before opening the project `TransactionGroup`, preventing Revit API nested-transaction deadlocks; **Phase 2** creates all project elements cleanly inside a single `TransactionGroup`:
1. **Native Floors (`FloorNative` / `FloorByHost`):** Extracts outer perimeter and inner void `CurveLoop`s, tessellates NURBS/splines cleanly into chords (avoiding 3-point arc distortion), creates thickness/material-matched `FloorType`s, supports 7-arg sloped `Floor.Create` for planar ramps/slabs, and enables `Room Bounding`.
2. **Native Walls (`WallByCurve` / `WallByProfile`):** Computes true diagonal/rotated and curved centerlines, perpendicular thickness, and `base_offset` (`bbox.Min.Z - level_elev`); automatically routes walls with inner window/door openings or sloped/arched tops to `WallByProfile` using full 3D `CurveLoop`s centered on the wall plane.
3. **Native Ceilings (`CeilingNative`):** Matches `CeilingType` thickness/material and applies dual-elevation `Ceiling.Create` + height offset.
4. **Native Roofs (`RoofNative` / `RoofByHost`):**
   - Builds `NewFootPrintRoof` curves strictly on `level_elem.Elevation`, disables Revit's default 30° hip slope on all edges, applies single-eave slope (`Rise/Run`) on planar pitched roofs, sets `ROOF_LEVEL_OFFSET_PARAM` from soffit elevation, matches `RoofType` thickness/material, and cuts inner openings via `NewOpening`.
   - Supports horizontal barrel/vaulted extrusions via `NewExtrusionRoof`.
5. **Toposolid (`Toposolid`):** Generates horizontal perimeter `CurveLoop`s and extracts 3D elevation points for 5-arg `DB.Toposolid.Create` (Revit 2024+) with `OST_Toposolid` fallback.
6. **Families (`ParametricFamily`, `FreeFormFamily`, `RepetitiveFamily`, `AdaptiveComponent`):**
   - `ParametricFamily` (Mode A): Editable `.rfa` extrusions strictly for verified `Box`, `Cylinder`, and `ProfileExtrusion` primitives (never coerces freeform solids into boxes).
   - `FreeFormFamily` (Mode B): Non-editable exact-geometry `.rfa` using `DB.FreeFormElement` for closed solids and in-family `DirectShape` for open/non-watertight geometry.
   - `AdaptiveComponent`: Deterministic CCW point ordering + `AdaptiveComponentInstanceUtils`.
   - `RepetitiveFamily`: Shared `DirectShapeType` / `FamilySymbol` instancing.
7. **Exact `DirectShape` Fallback:** Preserves closed and open NURBS Breps via `GeometryEncoder.ToSolid` / `ToBrep` / `ToGeometryBase`, and uses a two-pass `TessellatedShapeBuilder` (`Fallback.Abort` on closed inputs only, followed by `OpenConnectedFaceSet(False)` + `AnyGeometry`/`Mesh`) without `SubD` smoothing or `Salvage` face tearing.
8. **Auto-BUA / GFA:** Generates Area Plan boundary lines under `BUA / GFA Assessment`.

### Stage 5 & 6: VALIDATE & SYNC (`graphics_validator.py` & `sync_engine.py`)
- **Dual-Status Validation:** Checks `BIM STATUS` (Category, Material, non-empty Geometry) and `GRAPHICS STATUS` across **Plan, Section, Elevation, and 3D** views.
- **Persistent Revision Sync:** Stores `ExtensibleStorage` tags (`R2R_RhinoGUID`, `R2R_GeoHash`, `R2R_LayerPath`, `R2R_Strategy`) to classify re-runs into `CREATE`, `SKIP_UNCHANGED`, `UPDATE_MODIFIED`, or `FLAG_MISSING`.

---

## 3. Installation & Setup

### Method 1: pyRevit Extension Push-Button (Recommended)
Works across Revit 2020–2026 (including `.NET 8` in Revit 2025/2026) with zero compilation:
1. Close Revit or prepare to reload pyRevit.
2. Double-click **`installer/install_pyrevit.bat`** — it deploys all `.py` modules, `.xaml` UI layouts, `templates/`, and `Transfer_Profile.json` into `%APPDATA%\pyRevit\Extensions\R2R.extension\`.
3. In Revit, start **Rhino.Inside** from the `Rhino.Inside` ribbon tab.
4. Switch to the **R2R** tab and click **One-Click Transfer** to launch the dashboard.

> **To Uninstall:** Run `installer/uninstall_pyrevit.bat`.

### Method 2: Rhino Python Script Editor
1. Open Revit and start **Rhino.Inside**.
2. Open your `.3dm` model in Rhino and run in `EditPythonScript` / `ScriptEditor`:
   ```python
   import main_launcher as launcher
   launcher.launch_transfer()
   ```

### Method 3: Native C# Revit Add-in (Optional)
Run `installer/install.bat` (or build `csharp/RhinoToRevitTransfer.csproj` via `dotnet build -c Release`).

---

## 4. Running Tests

Run the automated test suite (requires Python 3; uses mocked Revit/Rhino APIs):
```shell
python test_pipeline.py
```
Covers 60 unit tests across scanning, primitive detection, trilingual classification, `StrategyRuleEngine`, mixed-layer splitting, deterministic custom parameters, WPF XAML `FindName` bindings, installer completeness, family geometry accuracy, `RoofNative` / `WallByCurve` / `WallByProfile` / `CurveLoop` builders, and `DirectShape` tessellation.
