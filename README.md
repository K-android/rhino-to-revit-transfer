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
rhino-to-revit-transfer/
├── installer/                   # 1-Click Revit Add-in Installer & Setup
│   ├── install.bat             # Double-click Windows batch installer
│   ├── install.ps1             # PowerShell automated version detector & deployer
│   ├── uninstall.bat           # 1-click uninstaller batch file
│   ├── uninstall.ps1           # PowerShell clean removal script
│   └── README.md               # Installer usage guide
├── csharp/                      # Native C# .NET Revit Add-in Solution
│   ├── RhinoToRevitTransfer.csproj  # Multi-targeted .NET SDK project (net48 / net8.0-windows)
│   ├── RhinoToRevitTransfer.addin   # Revit application manifest file
│   ├── R2RTransferApp.cs            # Ribbon Tab ("R2R Transfer") & PushButton registration
│   ├── R2RTransferCommand.cs        # External command & Rhino.Inside binding
│   ├── TransferDashboardWindow.cs   # Native WPF interactive transfer dashboard
│   ├── Scanner.cs                   # C# geometry scanner & SHA-256 fingerprinting
│   ├── Classifier.cs                # C# 5-criteria architectural heuristic engine
│   ├── ConversionRouter.cs          # C# 10-pipeline transaction dispatcher
│   └── SyncEngine.cs                # C# ExtensibleStorage schema & sync tracker
├── __init__.py                  # Package root & exports
├── main_launcher.py            # Unified entry-point launcher (RIR / pyRevit / Script Editor)
├── scanner.py                  # Module 1: Rhino Scanner & Geometry Fingerprinter (SHA-256)
├── classifier.py               # Module 2: Evidence-Based Classification & Confidence Engine
├── ai_assistant.py             # Module 3: LLM Semantic Interpreter (Catalan / Spanish architectural terms)
├── profile_manager.py          # Module 4: Persistent JSON Mapping Profile Manager
├── conversion_router.py        # Module 5: RIR Element Creation Engines (10-Pipeline Dispatcher)
├── graphics_validator.py       # Module 6: Dual-Status BIM & View Graphics Validator
├── sync_engine.py              # Module 7: Persistent Identity (GUID + GeoHash) & Revision Tracker
├── reporting.py                # Module 8: Preflight, BUA/GFA Area Check & QA Exporter (HTML/CSV/JSON)
├── ui_dialog.py                # Module 9: Eto.Forms 5-Stage Multi-Step Wizard Dashboard UI
├── Transfer_Profile.json       # Preconfigured mapping profile
├── test_pipeline.py            # Verification test suite
└── README.md                   # System architecture & deployment documentation
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
- Eto.Forms DataGridView with search, multi-criteria filters (Category, Confidence Tier, Sync State), and real-time **Pattern / Points** geometric signatures (`4-Pt Quad`, `3-Pt Tri`, `2-Pt Strut`, `Block: Name`, `Repetitive (Nx)`).
- **Strategy Override Toolbar:** Allows instant bulk overriding of checked layers to `AdaptiveComponent`, `RepetitiveFamily`, `ParametricFamily`, `FloorNative`, `WallByCurve`, `RoofNative`, `FamilyInstance`, or `DirectShape`.
- **Repetition Grouping Threshold:** Configurable cluster threshold (`>= 2`, `>= 3`, `>= 5`, `>= 10` identical geometries) to dynamically route repeated components to lightweight shared definitions.
- Global Execution Mode Selector:
  - **Native BIM Mode:** Prioritizes native Revit elements (`Floor`, `Wall`, `Ceiling`, `Roof`).
  - **Hybrid Mode (Recommended):** Native elements for orthogonal walls/floors, Adaptive Components for facade panels, DirectShape for complex organic surfaces.
  - **Geometry-First Mode:** DirectShape with assigned Category & Material for maximum 3D fidelity.
- Auto-BUA / GFA Area Boundary Line generator toggle.
- `Load Profile` and `Save Profile` to synchronize rule changes to `Transfer_Profile.json`.
- `Run AI Semantic Assist` (`ai_assistant.py`) to parse ambiguous architectural layer names.

### Stage 4: TRANSFER (`conversion_router.py`)
Dispatches across specialized RIR creation pipelines under a single `DB.TransactionGroup`:
1. **Floors (Native Floor):** Extracts top horizontal face boundary and inner void/shaft loops into `DB.CurveLoop` $\rightarrow$ calls `DB.Floor.Create`. Automatically enables `Room Bounding = True` for BUA/GFA area calculations.
2. **Floors by Host:** Sloped / faceted slab surfaces hosted or DirectShape with `OST_Floors`.
3. **Walls by Curve:** Computes bottom centerline curve, height, snaps to base level $\rightarrow$ calls `DB.Wall.Create(doc, line, type, level, height, offset, flip, structural)`.
4. **Walls by Profile (with Fallback):** Non-rectangular wall elevation profiles (arches, vaults, sloped tops) $\rightarrow$ `DB.Wall.Create(doc, profileCurveLoops, type, level, structural)`. If Revit rejects the profile geometry, it logs a warning and cleanly falls back to DirectShape with `OST_Walls`.
5. **Ceilings (Native):** Horizontal perimeter curves $\rightarrow$ `DB.Ceiling.Create`.
6. **Roofs (Native Footprint Roof):** Horizontal boundary curves $\rightarrow$ `doc.Create.NewFootPrintRoof`.
7. **Roofs by Host / Face:** Freeform canopy DirectShape with `OST_Roofs`.
8. **Adaptive Components (Multi-Point Panels & Struts):** Extracts planar vertices, performs deterministic counter-clockwise winding ordering (starting bottom-left), and places native adaptive instances via `DB.AdaptiveComponentInstanceUtils.CreateAdaptiveComponentInstance`. Gracefully falls back to shared instancing if matching `.rfa` family is not pre-loaded.
9. **Repetitive Family Instancing (Shared DirectShapeType):** For repeated geometry or Rhino block references, creates a single canonical definition in `DB.DirectShapeLibrary` as `DB.DirectShapeType` and places lightweight transformed instances via `DB.DirectShape.CreateElementInstance`. Saves over 90% Revit file size and memory.
10. **Parametric Families (Dimension-Driven):** Places standard loadable `FamilySymbol` (columns, doors, windows, furniture) at centroid and rotation, driving instance parameters (`Width`, `Depth`, `Height`, `Length`, `Thickness`).
11. **DirectShape Fallback:** Freeform double-curved meshes, SubD, or organic Breps $\rightarrow$ `RhinoInside.Revit.Convert.Geometry.GeometryEncoder.ToGeometryObject` $\rightarrow$ `DB.DirectShape.CreateElement` with mapped Category, Subcategory, and Material.
12. **Auto-BUA / GFA Area Plan Generator:** Ensures Revit Levels exist $\rightarrow$ locates or creates Area Scheme `BUA / GFA Assessment` $\rightarrow$ generates Area Plan view $\rightarrow$ projects closed outer boundary curves as `doc.Create.NewAreaBoundaryLine`.

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

## 3. Installation & Setup

### Method 1: 1-Click C# Revit Add-in Installer (Recommended)
This method installs the native compiled Revit add-in, adding a dedicated **"R2R Transfer"** tab and **"One-Click Transfer"** push button directly to your Revit ribbon.

1. **Close Revit** if currently open.
2. Navigate to the `installer/` directory and run **`install.bat`** (or right-click and run with PowerShell `install.ps1`).
3. The installer automatically:
   - Deploys to Autodesk Revit versions 2024, 2025, 2026, and 2027 (automatically matching .NET Framework 4.8 for Revit 2024 and modern .NET 8.0 for Revit 2025, 2026, and 2027).
   - Copies and registers `RhinoToRevitTransfer.addin` into `%APPDATA%\Autodesk\Revit\Addins\<Year>\`.
   - Automatically unblocks all assemblies to prevent Windows Mark-of-the-Web security blocks (0x80131515).
4. Open **Autodesk Revit**.
5. Start **Rhino.Inside** from the `Rhino.Inside` ribbon tab.
6. Click the new **R2R Transfer** ribbon tab and click **One-Click Transfer** to open the dashboard!

> **To Uninstall:** Simply double-click `installer/uninstall.bat` at any time to cleanly remove the add-in and manifest from all Revit versions.

---

### Method 2: Python / Rhino.Inside.Revit Script Editor (Zero-Compilation)
If you prefer running the Python 3 / IronPython version directly without compiling C#:
1. Open your Revit project and start Rhino from the **Rhino.Inside** ribbon tab.
2. In Rhino, open your architectural model.
3. Open the Rhino Python Script Editor (`EditPythonScript` or `ScriptEditor` in Rhino 8).
4. Run:
   ```python
   import main_launcher as launcher
   launcher.launch_transfer()
   ```

---

### Method 3: pyRevit Extension Push-Button (1-Click Automated Setup)
To install the tool as a permanent button on your Revit ribbon via pyRevit:
1. Double-click `installer/install_pyrevit.bat` to automatically register the `R2R` tab and `One-Click Transfer` pushbutton.
2. In Revit, click the **Rhino.Inside** tab to launch Rhino.
3. Switch to the **R2R** tab and click **One-Click Transfer** to launch the dashboard!

*(Manual alternative)*:
Create `R2R.extension/R2R.tab/Transfer.panel/OneClick.pushbutton/script.py` in `%APPDATA%\pyRevit\Extensions\` and import `main_launcher`.

---

### Developer Setup: Compiling the C# Solution from Source
To compile custom modifications to the C# codebase in `csharp/`:
1. Ensure you have the **.NET SDK** (.NET 8.0 or .NET Framework 4.8) installed.
2. Open terminal in the `csharp/` folder:
   ```shell
   dotnet build RhinoToRevitTransfer.csproj -c Release
   ```
3. Run `installer/install.bat` to deploy your newly compiled binary to Revit.

---

## 4. QA Reporting (`reporting.py`)
Clicking `"Export QA Report (HTML)"` in the dialog exports a modern, responsive HTML report containing:
- Executive summary KPI cards (Total Scanned, Created, Updated, Skipped, Review Queue).
- Built-Up Area (BUA) and Gross Floor Area (GFA) area comparison table with discrepancy percentages.
- Complete Dual-Status and 4-View Matrix verification audit table.
- CSV export for integration with PowerBI, BIM Track, or project audits.
