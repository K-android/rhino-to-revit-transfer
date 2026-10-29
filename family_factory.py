# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (R2R)
family_factory.py - Native Revit .rfa Family Generator

Implements Phase 1 of the Two-Phase Execution Pattern:
Generates true native .rfa family documents in memory via app.NewFamilyDocument(),
populates them with dimension-driven parametric geometry or native FreeFormElement solids,
binds Revit Family Parameters (Width, Depth, Height, Element_Material), and loads
them into the target Project Document via famDoc.LoadFamily(projectDoc, FamilyLoadOptions()).

Runs strictly BEFORE project TransactionGroup/Transaction is opened, preventing
Revit API transaction nesting deadlocks.
"""

import os
import re
import traceback

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("System.Collections")
    import System
    from System.Collections.Generic import List
except Exception:
    pass

try:
    import Autodesk.Revit.DB as DB
    REVIT_AVAILABLE = True
except Exception:
    REVIT_AVAILABLE = False

try:
    import Rhino.Geometry as rg
    from RhinoInside.Revit.Convert.Geometry import GeometryEncoder
    RHINO_AVAILABLE = True
except Exception:
    RHINO_AVAILABLE = False


class FamilyLoadOptions(DB.IFamilyLoadOptions if REVIT_AVAILABLE else object):
    """Custom IFamilyLoadOptions implementation that always overwrites existing definitions."""

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            if hasattr(overwriteParameterValues, "Value"):
                overwriteParameterValues.Value = True
        except Exception:
            pass
        return True, True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        src = getattr(DB.FamilySource, "Family", 0) if REVIT_AVAILABLE else 0
        try:
            if hasattr(source, "Value"):
                source.Value = src
            if hasattr(overwriteParameterValues, "Value"):
                overwriteParameterValues.Value = True
        except Exception:
            pass
        return True, src, True


class FamilyTemplateLocator(object):
    """Discovers and caches valid Revit Family Template (.rft) paths on the host machine."""

    _cached_templates = {}

    @classmethod
    def find_template(cls, app, category_hint="Generic Model"):
        """Locates an appropriate .rft template for the requested category.

        :param app: Autodesk.Revit.ApplicationServices.Application
        :param category_hint: Category name hint ("Generic Model", "Column", "Curtain Panel", etc.)
        :return: String path to existing .rft file, or None if not found
        """
        hint_key = category_hint.strip().lower()
        if hint_key in cls._cached_templates:
            cached_path = cls._cached_templates[hint_key]
            if os.path.exists(cached_path):
                return cached_path

        # Determine target file names to search for
        target_filenames = []
        if "column" in hint_key:
            target_filenames.extend(["Metric Structural Column.rft", "Structural Column.rft", "Metric Column.rft", "Column.rft"])
        elif "curtain" in hint_key or "panel" in hint_key:
            target_filenames.extend(["Metric Curtain Wall Panel.rft", "Curtain Wall Panel.rft"])
        elif "mass" in hint_key:
            target_filenames.extend(["Metric Mass.rft", "Mass.rft"])
        
        # Always add Generic Model as top standard fallback
        target_filenames.extend([
            "Metric Generic Model.rft",
            "Generic Model.rft",
            "Metric Model.rft"
        ])

        # Candidate search directories
        search_dirs = []

        # 0. Bundled & Local extension templates (Primary, guaranteed self-contained across all Revit setups)
        this_file_dir = os.path.dirname(os.path.abspath(__file__))
        bundled_dirs = [
            os.path.join(this_file_dir, "templates"),
            os.path.join(os.path.dirname(this_file_dir), "templates"),
            os.path.join(os.path.dirname(os.path.dirname(this_file_dir)), "templates"),
            r"C:\Users\User\.gemini\antigravity\scratch\rhino-to-revit-transfer\templates",
            r"C:\Users\User\.gemini\antigravity\scratch\ParametricFamilyForge\Templates",
            os.path.join(os.environ.get("APPDATA", ""), r"pyRevit\Extensions\R2R.extension\templates"),
            os.path.join(os.environ.get("APPDATA", ""), r"pyRevit\Extensions\R2R.extension\lib\templates"),
        ]
        for bd in bundled_dirs:
            if os.path.isdir(bd) and bd not in search_dirs:
                search_dirs.append(bd)

        # 1. Configured FamilyTemplatePath from active Revit Application
        if app and hasattr(app, "FamilyTemplatePath") and app.FamilyTemplatePath:
            user_tmpl = app.FamilyTemplatePath
            if os.path.isdir(user_tmpl):
                search_dirs.append(user_tmpl)
                for sub in ["English", "English-Imperial", "Metric", "Templates", ""]:
                    sub_p = os.path.join(user_tmpl, sub)
                    if os.path.isdir(sub_p) and sub_p not in search_dirs:
                        search_dirs.append(sub_p)

        # 2. Standard ProgramData RVT directories across Revit 2027 to 2022
        prog_data = os.environ.get("ProgramData", r"C:\ProgramData")
        for yr in ["2027", "2026", "2025", "2024", "2023", "2022"]:
            rvt_tmpl_root = os.path.join(prog_data, "Autodesk", "RVT {}".format(yr), "Family Templates")
            if os.path.isdir(rvt_tmpl_root):
                search_dirs.append(rvt_tmpl_root)
                for sub in ["English", "English-Imperial", "Metric"]:
                    sub_p = os.path.join(rvt_tmpl_root, sub)
                    if os.path.isdir(sub_p) and sub_p not in search_dirs:
                        search_dirs.append(sub_p)

        # 3. Addins Forma fallback templates
        forma_tmpl = os.path.join(prog_data, "Autodesk", "Revit", "Addins", "2026", "Forma", "Templates")
        if os.path.isdir(forma_tmpl) and forma_tmpl not in search_dirs:
            search_dirs.append(forma_tmpl)

        # Execute direct lookup
        for d in search_dirs:
            for fname in target_filenames:
                p = os.path.join(d, fname)
                if os.path.exists(p) and os.path.isfile(p):
                    cls._cached_templates[hint_key] = p
                    return p

        # Execute shallow recursive walk if direct match failed
        for d in search_dirs:
            try:
                for root, _, files in os.walk(d):
                    for f in files:
                        for target in target_filenames:
                            if f.lower() == target.lower():
                                full_p = os.path.join(root, f)
                                cls._cached_templates[hint_key] = full_p
                                return full_p
            except Exception:
                continue

        # If still not found, search for ANY .rft file in candidate dirs
        for d in search_dirs:
            try:
                for root, _, files in os.walk(d):
                    for f in files:
                        if f.lower().endswith(".rft") and not f.startswith("."):
                            full_p = os.path.join(root, f)
                            cls._cached_templates[hint_key] = full_p
                            return full_p
            except Exception:
                continue

        return None


class FamilyFactory(object):
    """Factory service creating real native loadable .rfa families in Revit."""

    @staticmethod
    def _add_or_get_parameter(fam_mgr, name, param_type="Length", is_instance=True):
        """Safely creates or retrieves a FamilyParameter across Revit 2021-2027+."""
        for p in fam_mgr.Parameters:
            if p.Definition.Name.lower() == name.lower():
                return p

        try:
            if hasattr(DB, "SpecTypeId") and hasattr(DB, "GroupTypeId"):
                # Revit 2022+ ForgeTypeId API
                if param_type == "Length":
                    spec = DB.SpecTypeId.Length
                    group = getattr(DB.GroupTypeId, "Geometry", getattr(DB.GroupTypeId, "Data", None))
                elif param_type == "Material":
                    spec = DB.SpecTypeId.Reference.Material
                    group = getattr(DB.GroupTypeId, "Materials", getattr(DB.GroupTypeId, "Geometry", None))
                else:
                    spec = DB.SpecTypeId.String.Text
                    group = getattr(DB.GroupTypeId, "General", getattr(DB.GroupTypeId, "Data", None))

                return fam_mgr.AddParameter(name, group, spec, is_instance)
            else:
                # Legacy Revit 2020-2021 ParameterType API
                if param_type == "Length":
                    ptype = DB.ParameterType.Length
                    pgroup = DB.BuiltInParameterGroup.PG_GEOMETRY
                elif param_type == "Material":
                    ptype = DB.ParameterType.Material
                    pgroup = DB.BuiltInParameterGroup.PG_MATERIALS
                else:
                    ptype = DB.ParameterType.Text
                    pgroup = DB.BuiltInParameterGroup.PG_DATA

                return fam_mgr.AddParameter(name, pgroup, ptype, is_instance)
        except Exception:
            return None

    @classmethod
    def create_parametric_box_or_profile_family(
        cls,
        app,
        project_doc,
        family_name,
        width=1.0,
        depth=1.0,
        height=1.0,
        profile_curve_loops=None,
        category_hint="Generic Model",
        target_bic=None
    ):
        """Mode A: Creates a true dimension-driven native .rfa family with Width/Depth/Height parameters.

        :param app: Autodesk.Revit.ApplicationServices.Application
        :param project_doc: Target project Document
        :param family_name: Desired family name in project
        :param width: Initial width in feet
        :param depth: Initial depth in feet
        :param height: Initial height/offset in feet
        :param profile_curve_loops: Optional list of closed DB.CurveLoop or Rhino curves for custom profiles
        :param category_hint: Category hint for template selection
        :param target_bic: Optional DB.BuiltInCategory override
        :return: Loaded DB.FamilySymbol, or None on failure
        """
        if not REVIT_AVAILABLE or not app or not project_doc:
            return None

        tmpl_path = FamilyTemplateLocator.find_template(app, category_hint=category_hint)
        if not tmpl_path:
            return None

        clean_fam_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(family_name).strip()) or "R2R_Parametric_Component"
        fam_doc = None
        try:
            fam_doc = app.NewFamilyDocument(tmpl_path)
            if not fam_doc:
                return None

            fam_mgr = fam_doc.FamilyManager

            # 1. Start Transaction inside Family Document
            t = DB.Transaction(fam_doc, "Build Parametric Family Geometry")
            t.Start()

            # Set Family Category if target_bic provided
            if target_bic and hasattr(fam_doc, "OwnerFamily"):
                try:
                    target_cat = fam_doc.Settings.Categories.get_Item(target_bic)
                    if target_cat:
                        fam_doc.OwnerFamily.FamilyCategory = target_cat
                except Exception:
                    pass

            # Add Family Parameters
            p_width = cls._add_or_get_parameter(fam_mgr, "Width", "Length", is_instance=True)
            p_depth = cls._add_or_get_parameter(fam_mgr, "Depth", "Length", is_instance=True)
            p_height = cls._add_or_get_parameter(fam_mgr, "Height", "Length", is_instance=True)
            p_mat = cls._add_or_get_parameter(fam_mgr, "Element_Material", "Material", is_instance=True)

            # Set initial values
            try:
                if p_width and width > 0.001:
                    fam_mgr.Set(p_width, float(width))
                if p_depth and depth > 0.001:
                    fam_mgr.Set(p_depth, float(depth))
                if p_height and height > 0.001:
                    fam_mgr.Set(p_height, float(height))
            except Exception:
                pass

            # 2. Build 2D Extrusion Profile centered at (0, 0, 0)
            sketch_plane = None
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
            sketch_plane = DB.SketchPlane.Create(fam_doc, plane)

            curve_arr_array = DB.CurveArrArray()

            if profile_curve_loops and len(profile_curve_loops) > 0:
                # Custom 2D profile loop provided
                for cl in profile_curve_loops:
                    ca = DB.CurveArray()
                    for c in cl:
                        ca.Append(c)
                    curve_arr_array.Append(ca)
            else:
                # Rectangular box profile centered at (0,0)
                hw = max(0.05, float(width) / 2.0)
                hd = max(0.05, float(depth) / 2.0)
                pt0 = DB.XYZ(-hw, -hd, 0.0)
                pt1 = DB.XYZ(hw, -hd, 0.0)
                pt2 = DB.XYZ(hw, hd, 0.0)
                pt3 = DB.XYZ(-hw, hd, 0.0)

                ca = DB.CurveArray()
                ca.Append(DB.Line.CreateBound(pt0, pt1))
                ca.Append(DB.Line.CreateBound(pt1, pt2))
                ca.Append(DB.Line.CreateBound(pt2, pt3))
                ca.Append(DB.Line.CreateBound(pt3, pt0))
                curve_arr_array.Append(ca)

            # 3. Create Native Revit Extrusion
            ext_height = max(0.1, float(height))
            extrusion = fam_doc.FamilyCreate.NewExtrusion(True, curve_arr_array, sketch_plane, ext_height)

            # 4. Associate Extrusion End Offset to Height Parameter
            if extrusion and p_height:
                try:
                    end_param = extrusion.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_OFFSET)
                    if end_param:
                        fam_mgr.AssociateElementParameterToFamilyParameter(end_param, p_height)
                except Exception:
                    pass

            # Associate Material to Element_Material Parameter
            if extrusion and p_mat:
                try:
                    mat_param = extrusion.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                    if mat_param:
                        fam_mgr.AssociateElementParameterToFamilyParameter(mat_param, p_mat)
                except Exception:
                    pass

            t.Commit()

            # 5. Load Family into Project Document (OVERWRITE)
            load_opts = FamilyLoadOptions()
            loaded_fam = None
            try:
                loaded_fam = fam_doc.LoadFamily(project_doc, load_opts)
            except Exception as load_ex:
                # Some Revit versions require SaveAs before LoadFamily if NewFamilyDocument was used
                temp_dir = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "R2R_Families")
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                temp_file = os.path.join(temp_dir, "{}.rfa".format(clean_fam_name))
                save_opts = DB.SaveAsOptions()
                save_opts.OverwriteExistingFile = True
                fam_doc.SaveAs(temp_file, save_opts)
                loaded_fam = project_doc.LoadFamily(temp_file, load_opts)

            # Close family document without saving to disk
            try:
                fam_doc.Close(False)
            except Exception:
                pass
            fam_doc = None

            # 6. Locate and activate FamilySymbol in Project Document
            if loaded_fam:
                fam_obj = loaded_fam
                if isinstance(fam_obj, tuple):
                    for item in fam_obj:
                        if hasattr(item, "GetFamilySymbolIds"):
                            fam_obj = item
                            break
                if hasattr(fam_obj, "GetFamilySymbolIds"):
                    for sym_id in fam_obj.GetFamilySymbolIds():
                        sym = project_doc.GetElement(sym_id)
                        if sym and isinstance(sym, DB.FamilySymbol):
                            if hasattr(sym, "IsActive") and not sym.IsActive:
                                try:
                                    sym.Activate()
                                except Exception:
                                    pass
                            return sym

            # Fallback search by family name
            collector = DB.FilteredElementCollector(project_doc).OfClass(DB.FamilySymbol)
            for s in collector:
                if s.FamilyName == clean_fam_name or clean_fam_name in s.FamilyName or getattr(s, "Name", "") == clean_fam_name:
                    if hasattr(s, "IsActive") and not s.IsActive:
                        try:
                            s.Activate()
                        except Exception:
                            pass
                    return s

            return None

        except Exception as ex:
            if fam_doc:
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass
            print("[FamilyFactory Mode A Exception]: {}".format(ex))
            print(traceback.format_exc())
            return None

    @classmethod
    def create_native_freeform_family(
        cls,
        app,
        project_doc,
        family_name,
        rh_brep_or_solid,
        subcategory_name="R2R_Component",
        category_hint="Generic Model",
        target_bic=None,
        geom_feet=None,
        unit_scale=1.0
    ):
        """Mode B: Creates a bespoke 3D solid native .rfa family using DB.FreeFormElement.Create.

        :param app: Autodesk.Revit.ApplicationServices.Application
        :param project_doc: Target project Document
        :param family_name: Desired family name in project
        :param rh_brep_or_solid: Rhino Brep, Mesh, or pre-converted DB.Solid
        :param subcategory_name: Subcategory name to assign within family
        :param category_hint: Category hint for template selection
        :param target_bic: Optional DB.BuiltInCategory override
        :param geom_feet: Optional geometry pre-scaled to Revit internal feet for TSB
        :param unit_scale: Scale factor from Rhino document units to Revit internal feet
        :return: Loaded DB.FamilySymbol, or None on failure
        """
        if not REVIT_AVAILABLE or not app or not project_doc or not rh_brep_or_solid:
            return None

        tmpl_path = FamilyTemplateLocator.find_template(app, category_hint=category_hint)
        if not tmpl_path:
            return None

        clean_fam_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(family_name).strip()) or "R2R_Bespoke_Solid"

        # Convert input geometry to DB.Solid
        revit_solid = None
        if isinstance(rh_brep_or_solid, DB.Solid):
            revit_solid = rh_brep_or_solid
        elif RHINO_AVAILABLE:
            try:
                working = rh_brep_or_solid
                if not isinstance(working, (rg.Brep, rg.Mesh)) and hasattr(working, "ToBrep"):
                    b = working.ToBrep()
                    if b:
                        working = b

                # Priority 1: If Mesh, attempt smooth SubD -> Brep conversion for continuous NURBS solid (NO wire lines)
                if isinstance(working, rg.Mesh) and hasattr(rg, "SubD") and hasattr(rg.SubD, "CreateFromMesh"):
                    try:
                        subd = rg.SubD.CreateFromMesh(working)
                        if subd and getattr(subd, "IsValid", False):
                            b_subd = subd.ToBrep()
                            if b_subd and getattr(b_subd, "IsValid", False) and hasattr(GeometryEncoder, "ToSolid"):
                                s_subd = GeometryEncoder.ToSolid(b_subd)
                                if s_subd and getattr(s_subd, "Volume", 0.0) > 0.00001:
                                    revit_solid = s_subd
                    except Exception:
                        pass

                # Priority 2: Brep ToSolid conversion
                if not revit_solid and hasattr(GeometryEncoder, "ToSolid") and isinstance(working, rg.Brep):
                    try:
                        revit_solid = GeometryEncoder.ToSolid(working)
                    except Exception:
                        revit_solid = None
            except Exception:
                pass

            # Fallback to TessellatedShapeBuilder Solid construction (handles Meshes & complex Breps)
            if (not revit_solid or getattr(revit_solid, "Volume", 0.0) <= 0.00001) and hasattr(DB, "TessellatedShapeBuilder"):
                try:
                    # TessellatedShapeBuilder requires coordinates in Revit internal feet
                    working_tsb = geom_feet
                    if not working_tsb:
                        working_tsb = working.Duplicate() if hasattr(working, "Duplicate") else working
                        if hasattr(working_tsb, "Scale") and abs(float(unit_scale or 1.0) - 1.0) > 0.00001:
                            working_tsb.Scale(float(unit_scale))

                    meshes = []
                    if isinstance(working_tsb, rg.Mesh):
                        meshes = [working_tsb]
                    elif isinstance(working_tsb, rg.Brep):
                        mp = rg.MeshingParameters.Default
                        b_meshes = rg.Mesh.CreateFromBrep(working_tsb, mp)
                        if b_meshes:
                            meshes = list(b_meshes)

                    if meshes:
                        tsb = DB.TessellatedShapeBuilder()
                        tsb.OpenConnectedFaceSet(True)
                        face_added = False
                        for m in meshes:
                            m_copy = m.Duplicate()
                            if hasattr(m_copy.Faces, "ConvertQuadsToTriangles"):
                                m_copy.Faces.ConvertQuadsToTriangles()
                            verts = m_copy.Vertices
                            for i in range(m_copy.Faces.Count):
                                f = m_copy.Faces[i]
                                p0 = verts[f.A]
                                p1 = verts[f.B]
                                p2 = verts[f.C]
                                v0 = DB.XYZ(float(p0.X), float(p0.Y), float(p0.Z))
                                v1 = DB.XYZ(float(p1.X), float(p1.Y), float(p1.Z))
                                v2 = DB.XYZ(float(p2.X), float(p2.Y), float(p2.Z))
                                if v0.DistanceTo(v1) > 0.0001 and v1.DistanceTo(v2) > 0.0001 and v2.DistanceTo(v0) > 0.0001:
                                    loop_pts = List[DB.XYZ]()
                                    loop_pts.Add(v0)
                                    loop_pts.Add(v1)
                                    loop_pts.Add(v2)
                                    tsb.AddFace(DB.TessellatedFace(loop_pts, DB.ElementId.InvalidElementId))
                                    face_added = True
                                if hasattr(f, "IsQuad") and f.IsQuad:
                                    p3 = verts[f.D]
                                    v3 = DB.XYZ(float(p3.X), float(p3.Y), float(p3.Z))
                                    if v0.DistanceTo(v2) > 0.0001 and v2.DistanceTo(v3) > 0.0001 and v3.DistanceTo(v0) > 0.0001:
                                        loop_pts2 = List[DB.XYZ]()
                                        loop_pts2.Add(v0)
                                        loop_pts2.Add(v2)
                                        loop_pts2.Add(v3)
                                        tsb.AddFace(DB.TessellatedFace(loop_pts2, DB.ElementId.InvalidElementId))
                                        face_added = True
                        tsb.CloseConnectedFaceSet()
                        if face_added:
                            tsb.Target = DB.TessellatedShapeBuilderTarget.Solid
                            tsb.Fallback = DB.TessellatedShapeBuilderFallback.Abort
                            tsb.Build()
                            b_res = tsb.GetBuildResult()
                            if b_res:
                                res_objs = b_res.GetGeometricalObjects()
                                if res_objs:
                                    for go in res_objs:
                                        if isinstance(go, DB.Solid) and getattr(go, "Volume", 0.0) > 0.00001:
                                            revit_solid = go
                                            break
                except Exception as ex_tsb:
                    print("[FamilyFactory TSB Solid Notice]: {}".format(ex_tsb))

        if not revit_solid or getattr(revit_solid, "Volume", 0.0) <= 0.00001:
            return None

        fam_doc = None
        try:
            fam_doc = app.NewFamilyDocument(tmpl_path)
            if not fam_doc:
                return None

            fam_mgr = fam_doc.FamilyManager

            # 1. Start Transaction inside Family Document
            t = DB.Transaction(fam_doc, "Create Native FreeForm Solid")
            t.Start()

            # Set Family Category if target_bic provided
            fam_cat = fam_doc.OwnerFamily.FamilyCategory if hasattr(fam_doc, "OwnerFamily") else None
            if target_bic and hasattr(fam_doc, "OwnerFamily"):
                try:
                    target_cat = fam_doc.Settings.Categories.get_Item(target_bic)
                    if target_cat:
                        fam_doc.OwnerFamily.FamilyCategory = target_cat
                        fam_cat = target_cat
                except Exception:
                    pass

            # Add Material Parameter
            p_mat = cls._add_or_get_parameter(fam_mgr, "Element_Material", "Material", is_instance=True)

            # 2. Create Native Revit FreeFormElement
            free_form = DB.FreeFormElement.Create(fam_doc, revit_solid)

            # 3. Create and assign Subcategory
            if free_form and fam_cat and subcategory_name:
                try:
                    subcats = fam_cat.SubCategories
                    clean_subcat = re.sub(r'[^a-zA-Z0-9_\- ]', '_', str(subcategory_name).strip()) or "R2R_Geometry"
                    subcat_elem = None
                    if subcats.Contains(clean_subcat):
                        subcat_elem = subcats.get_Item(clean_subcat)
                    else:
                        subcat_elem = fam_doc.Settings.Categories.NewSubcategory(fam_cat, clean_subcat)
                    if subcat_elem:
                        free_form.Subcategory = subcat_elem
                except Exception:
                    pass

            # 4. Associate Material Parameter
            if free_form and p_mat:
                try:
                    mat_param = free_form.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                    if mat_param:
                        fam_mgr.AssociateElementParameterToFamilyParameter(mat_param, p_mat)
                except Exception:
                    pass

            t.Commit()

            # 5. Load Family into Project Document (OVERWRITE)
            load_opts = FamilyLoadOptions()
            loaded_fam = None
            try:
                loaded_fam = fam_doc.LoadFamily(project_doc, load_opts)
            except Exception:
                temp_dir = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "R2R_Families")
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                temp_file = os.path.join(temp_dir, "{}.rfa".format(clean_fam_name))
                save_opts = DB.SaveAsOptions()
                save_opts.OverwriteExistingFile = True
                fam_doc.SaveAs(temp_file, save_opts)
                loaded_fam = project_doc.LoadFamily(temp_file, load_opts)

            try:
                fam_doc.Close(False)
            except Exception:
                pass
            fam_doc = None

            # 6. Locate and activate FamilySymbol in Project Document
            if loaded_fam:
                fam_obj = loaded_fam
                if isinstance(fam_obj, tuple):
                    for item in fam_obj:
                        if hasattr(item, "GetFamilySymbolIds"):
                            fam_obj = item
                            break
                if hasattr(fam_obj, "GetFamilySymbolIds"):
                    for sym_id in fam_obj.GetFamilySymbolIds():
                        sym = project_doc.GetElement(sym_id)
                        if sym and isinstance(sym, DB.FamilySymbol):
                            if hasattr(sym, "IsActive") and not sym.IsActive:
                                try:
                                    sym.Activate()
                                except Exception:
                                    pass
                            return sym

            collector = DB.FilteredElementCollector(project_doc).OfClass(DB.FamilySymbol)
            for s in collector:
                if s.FamilyName == clean_fam_name or clean_fam_name in s.FamilyName or getattr(s, "Name", "") == clean_fam_name:
                    if hasattr(s, "IsActive") and not s.IsActive:
                        try:
                            s.Activate()
                        except Exception:
                            pass
                    return s

            return None

        except Exception as ex:
            if fam_doc:
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass
            print("[FamilyFactory Mode B Exception]: {}".format(ex))
            print(traceback.format_exc())
            return None
