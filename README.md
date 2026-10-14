# One-Click Rhino-to-Revit Transfer (RIR)
### Production-Grade Computational Design & BIM Geometry Pipeline

The **One-Click Rhino-to-Revit Transfer** plugin runs directly inside **Rhino.Inside.Revit (RIR)**. Rather than performing a blind geometry dump or unintelligent mesh import, it implements a controlled 6-stage pipeline:

```
SCAN ➔ CLASSIFY ➔ REVIEW ➔ TRANSFER ➔ VALIDATE ➔ SYNC
```

It infers architectural design intent via deterministic geometry fingerprinting and trilingual metadata scoring (English, Spanish, Catalan), provides a responsive **Eto.Forms** dashboard for user confirmation, executes native Revit element creation across 10 specialized translation pipelines with clean DirectShape fallbacks, conducts dual-status view graphics validation, and guarantees persistent revision synchronization using Revit `ExtensibleStorage`.

---

## 1. Directory Structure

```
r2r_transfer/
├── __init__.py                # Package root & exports
├── main_launcher.py          # Unified entry-point launcher (RIR / pyRevit / Script Editor)
├── scanner.py                # Module 1: Rhino Scanner & Geometry Fingerprinter (SHA-256)
├── classifier.py             # Module 2: Evidence-Based Classification & Confidence Engine
├── ai_assistant.py           # Module 3: LLM Semantic Interpreter (Catalan / Spanish architectural terms)
├── profile_manager.py        # Module 4: Persistent JSON Mapping Profile Manager
├── conversion_router.py      # Module 5: RIR Element Creation Engines (10-Pipeline Dispatcher)
├── graphics_validator.py     # Module 6: Dual-Status BIM & View Graphics Validator
├── sync_engine.py            # Module 7: Persistent Identity (GUID + GeoHash) & Revision Tracker
├── reporting.py              # Module 8: Preflight, BUA/GFA Area Check & QA Exporter (HTML/CSV/JSON)
├── ui_dialog.py              # Module 9: Eto.Forms 5-Stage Multi-Step Wizard Dashboard UI
├── Transfer_Profile.json     # Preconfigured mapping profile
├── test_pipeline.py          # Verification test suite
└── README.md                 # System architecture & deployment documentation
```

---

## 2. Six-Stage Architecture & Pipeline

### Stage 1: SCAN (`scanner.py`)
- Scans `Rhino.RhinoDoc.ActiveDoc.Objects` (supporting hidden/locked layer filters).
- Extracts metadata: GUID, full layer hierarchy, user text dictionary, material name, and object color.
- Computes **Deterministic Geometry Fingerprint**:
  - World `BoundingBox` (`min_pt`, `max_pt`, `centroid`, `dx`, `dy`, `dz` height).
  - Topological flags: `is_closed`, `is_planar`, `is_horizontal` ($|Normal.Z| > 0.95$), `is_vertical` ($|Normal.Z| < 0.05$), `thickness`, and `loop_count` (outer boundary + inner shaft loops).
  - SHA-256 `geo_hash`: Deterministic hash generated from quantized vertices, faces, bounding box dimensions, area, and volume.
- Spatial Context: Finds closest Revit `Level` and computes base offset delta `dz_to_level`. Identifies building zones (`Podium`, `Typical Tower`, `Sky-Pool/Amenity`, `Crown`, `Veil Envelope`).
- Preflight Validation:
  - Catches coordinates $>16\text{ km}$ from Rhino origin (Revit's 10-mile geometry limit).
  - Flags duplicate overlapping geometry with identical hashes.
  - Identifies non-closed planar loops before Revit transactions begin.

### Stage 2: CLASSIFY (`classifier.py`)
Computes an evidence-based classification score ($0\%$ to $100\%$) using weighted architectural scoring:
1. **Layer & Name Match (35% weight):** Trilingual regex matching English, Spanish, and Catalan architectural vocabulary (e.g. *forjat, losa, muro, paret, cel_ras, falso_techo, gelosia, velo, veil*).
2. **Geometry Characteristics (30% weight):**
   - Horizontal closed planar solid/surface ($\Delta Z < 1.0\text{ m}$, $\max(\Delta X, \Delta Y) > 1.5\text{ m}$) $\rightarrow$ Floors / Ceilings / Roofs.
   - Vertical linear/planar extrusion or solid ($\Delta Z \ge 1.5\text{ m}$, thin thickness) $\rightarrow$ Walls.
   - `InstanceReference` (Block) $\rightarrow$ Family.
   - Complex double-curved Brep, Mesh, or SubD $\rightarrow$ DirectShape.
3. **Spatial / Level Relationship (15% weight):** Base $Z$ within $\pm 300\text{ mm}$ of level boosts Floors/Walls; suspended $2.2\text{ m} - 3.8\text{ m}$ boosts Ceilings.
4. **Repetition / Block Evidence (10% weight):** Instance counts $>3$ boost Family classification.
5. **Host / Context Relationship (10% weight):** Zoning and containment logic.

**Confidence Tiers:**
- $\ge 85\%$: `AUTO_READY` (Green badge, pre-checked for transfer)
- $60\% - 84\%$: `SUGGESTED_NEEDS_APPROVAL` (Amber badge, user confirmation)
- $< 60\%$: `MANUAL_OR_EXCLUDED` (Red badge, routed to review queue)

### Stage 3: REVIEW & CONFIGURE (`ui_dialog.py` & `profile_manager.py`)
- Eto.Forms DataGridView with search and multi-criteria filters (Category, Confidence Tier, Sync State).
- Global Execution Mode Selector:
  - **Native BIM Mode:** Prioritizes native Revit elements (`Floor`, `Wall`, `Ceiling`, `Roof`).
  - **Hybrid Mode (Recommended):** Native elements for orthogonal walls/floors, DirectShape for complex curved geometry and facade mesh panels.
  - **Geometry-First Mode:** DirectShape with assigned Category & Material for maximum 3D fidelity.
- Auto-BUA / GFA Area Boundary Line generator toggle.
- `Load Profile` and `Save Profile` to synchronize rule changes to `Transfer_Profile.json`.
- `Run AI Semantic Assist` (`ai_assistant.py`) to parse ambiguous architectural layer names.

### Stage 4: TRANSFER (`conversion_router.py`)
Dispatches across 10 RIR creation pipelines under a single `DB.TransactionGroup`:
1. **Floors (Native Floor):** Extracts top horizontal face boundary and inner void/shaft loops into `DB.CurveLoop` $\rightarrow$ calls `DB.Floor.Create`. Automatically enables `Room Bounding = True` for BUA/GFA area calculations.
2. **Floors by Host:** Sloped / faceted slab surfaces hosted or DirectShape with `OST_Floors`.
3. **Walls by Curve:** Computes bottom centerline curve, height, snaps to base level $\rightarrow$ calls `DB.Wall.Create(doc, line, type, level, height, offset, flip, structural)`.
4. **Walls by Profile (with Fallback):** Non-rectangular wall elevation profiles (arches, vaults, sloped tops) $\rightarrow$ `DB.Wall.Create(doc, profileCurveLoops, type, level, structural)`. If Revit rejects the profile geometry, it logs a warning and cleanly falls back to DirectShape with `OST_Walls`.
5. **Ceilings (Native):** Horizontal perimeter curves $\rightarrow$ `DB.Ceiling.Create`.
6. **Roofs (Native Footprint Roof):** Horizontal boundary curves $\rightarrow$ `doc.Create.NewFootPrintRoof`.
7. **Roofs by Host / Face:** Freeform canopy DirectShape with `OST_Roofs`.
8. **Family Creation & Placement:** Repeated Rhino blocks $\rightarrow$ instance placement using transformation plane (Origin, BasisX, BasisY, Rotation).
9. **DirectShape Fallback:** Facade stainless-steel mesh, SubD, or complex Breps $\rightarrow$ `RhinoInside.Revit.Convert.Geometry.GeometryEncoder.ToGeometryObject` $\rightarrow$ `DB.DirectShape.CreateElement` with mapped Category, Subcategory, and Material.
10. **Auto-BUA / GFA Area Plan Generator:** Ensures Revit Levels exist $\rightarrow$ locates or creates Area Scheme `BUA / GFA Assessment` $\rightarrow$ generates Area Plan view $\rightarrow$ projects closed outer boundary curves as `doc.Create.NewAreaBoundaryLine`.

**Warning Suppression:** Uses `TransferFailuresPreprocessor` (`IFailuresPreprocessor`) to delete non-fatal warnings (e.g. slightly off-axis lines, overlapping room separation lines) without modal dialog popups blocking execution.

### Stage 5: VALIDATE (`graphics_validator.py`)
Conducts dual-status evaluation on every transferred Revit element:
- **BIM STATUS (`PASS` | `FAIL`):**
  - Valid Revit Category, Subcategory, and Material assigned.
  - Geometry integrity: non-empty `element.get_Geometry(Options)` with valid faces/solids/meshes.
- **GRAPHICS STATUS (`PASS` | `REVIEW`):**
  - Visibility tested across 4 documentation views: **Plan, Section, Elevation, 3D** using `element.get_BoundingBox(view)` and `not element.IsHidden(view)`.
  - Flags DirectShapes in Plan and Section views with `WARNING` due to cut representation and tagging limitations.
- **Interactive UI Isolation:** Clicking `"Select / Isolate in Revit"` instantly highlights and isolates elements in the active Revit view.

### Stage 6: SYNC (`sync_engine.py`)
Uses Revit `ExtensibleStorage` (`Schema GUID: 7B89A3E4-4321-4F29-9EBA-D0F568C8B123`) to store tamper-proof identity tags on every transferred element:
- `R2R_SourceDocName`, `R2R_RhinoGUID`, `R2R_LayerPath`, `R2R_GeoHash`, `R2R_MappingVersion`, `R2R_Timestamp`, `R2R_Strategy`.

**On Subsequent Re-runs:**
- `rhino_guid` not in Revit $\rightarrow$ **`CREATE`**
- `rhino_guid` in Revit and `geo_hash` matches $\rightarrow$ **`SKIP_UNCHANGED`** (zero execution overhead)
- `rhino_guid` in Revit and `geo_hash` differs $\rightarrow$ **`UPDATE_MODIFIED`** (in-place update or recreate with identity preservation)
- Revit element has transfer tag missing from Rhino $\rightarrow$ **`FLAG_MISSING`** (flagged in review; never auto-deleted without explicit user confirmation).

---

## 3. Installation & Usage

### Method A: Direct Execution in Rhino.Inside.Revit Script Editor
1. Open your Revit project and start Rhino from the **Rhino.Inside** ribbon tab.
2. In Rhino, open the architectural CAD/Rhino model.
3. Open the Rhino Python Script Editor (`EditPythonScript` or `ScriptEditor` in Rhino 8).
4. Open and run:
   ```python
   # main_launcher.py
   import main_launcher as launcher
   launcher.launch_transfer()
   ```

### Method B: pyRevit Push-Button
1. Create a push-button bundle in your pyRevit extension directory:
   `R2R.extension/R2R.tab/Transfer.panel/OneClick.pushbutton/script.py`
2. Add the following lines to `script.py`:
   ```python
   import sys
   sys.path.append(r"C:\Path\To\RhinoToRevitTransfer")
   import main_launcher
   main_launcher.launch_transfer()
   ```

---

## 4. QA Reporting (`reporting.py`)
Clicking `"Export QA Report (HTML)"` in the dialog exports a modern, responsive HTML report containing:
- Executive summary KPI cards (Total Scanned, Created, Updated, Skipped, Review Queue).
- Built-Up Area (BUA) and Gross Floor Area (GFA) area comparison table with discrepancy percentages.
- Complete Dual-Status and 4-View Matrix verification audit table.
- CSV export for integration with PowerBI, BIM Track, or project audits.
