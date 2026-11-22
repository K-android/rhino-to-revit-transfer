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
    def _resolve_revit_spec_and_group(param_type="Length", param_kind="Data"):
        """Resolves ForgeTypeId (2022+) or legacy (ParameterType, BuiltInParameterGroup) tuples safely."""
        pt = str(param_type or "Text").strip().lower()
        is_constraint = (str(param_kind or "").strip().lower() == "constraint")

        if hasattr(DB, "SpecTypeId") and hasattr(DB, "GroupTypeId"):
            st = DB.SpecTypeId
            gt = DB.GroupTypeId
            str_grp = getattr(st, "String", None)
            int_grp = getattr(st, "Int", None)
            bool_grp = getattr(st, "Boolean", None)
            ref_grp = getattr(st, "Reference", None)

            text_spec = getattr(str_grp, "Text", None) or getattr(st, "Text", None)
            int_spec = getattr(int_grp, "Integer", None) or getattr(st, "Integer", None)
            yesno_spec = getattr(bool_grp, "YesNo", None) or getattr(st, "YesNo", None)
            mat_spec = getattr(ref_grp, "Material", None) or getattr(st, "Material", None)

            spec_map = {
                "length": (getattr(st, "Length", text_spec), getattr(gt, "Geometry", getattr(gt, "Data", None))),
                "area": (getattr(st, "Area", text_spec), getattr(gt, "Geometry", getattr(gt, "Data", None))),
                "volume": (getattr(st, "Volume", text_spec), getattr(gt, "Geometry", getattr(gt, "Data", None))),
                "number": (getattr(st, "Number", text_spec), getattr(gt, "Data", getattr(gt, "General", None))),
                "integer": (int_spec, getattr(gt, "Data", getattr(gt, "General", None))),
                "yesno": (yesno_spec, getattr(gt, "Visibility", getattr(gt, "Constraints", getattr(gt, "Data", None)))),
                "material": (mat_spec, getattr(gt, "Materials", getattr(gt, "Geometry", None))),
                "text": (text_spec, getattr(gt, "Data", getattr(gt, "General", None)))
            }
            spec, group = spec_map.get(pt, (text_spec, getattr(gt, "Data", getattr(gt, "General", None))))
            if is_constraint and pt in ("length", "number"):
                group = getattr(gt, "Constraints", group)
            return True, spec, group

        if hasattr(DB, "ParameterType") and hasattr(DB, "BuiltInParameterGroup"):
            pt_cls = DB.ParameterType
            pg_cls = DB.BuiltInParameterGroup
            leg_map = {
                "length": (getattr(pt_cls, "Length", None), getattr(pg_cls, "PG_GEOMETRY", None)),
                "area": (getattr(pt_cls, "Area", None), getattr(pg_cls, "PG_GEOMETRY", None)),
                "volume": (getattr(pt_cls, "Volume", None), getattr(pg_cls, "PG_GEOMETRY", None)),
                "number": (getattr(pt_cls, "Number", None), getattr(pg_cls, "PG_DATA", None)),
                "integer": (getattr(pt_cls, "Integer", None), getattr(pg_cls, "PG_DATA", None)),
                "yesno": (getattr(pt_cls, "YesNo", None), getattr(pg_cls, "PG_VISIBILITY", getattr(pg_cls, "PG_DATA", None))),
                "material": (getattr(pt_cls, "Material", None), getattr(pg_cls, "PG_MATERIALS", None)),
                "text": (getattr(pt_cls, "Text", None), getattr(pg_cls, "PG_DATA", None))
            }
            ptype, pgroup = leg_map.get(pt, (getattr(pt_cls, "Text", None), getattr(pg_cls, "PG_DATA", None)))
            if is_constraint and pt in ("length", "number"):
                pgroup = getattr(pg_cls, "PG_CONSTRAINTS", pgroup)
            return False, ptype, pgroup

        return None, None, None

    @classmethod
    def _add_or_get_parameter(cls, fam_mgr, name, param_type="Length", is_instance=True, external_def=None, param_kind="Data"):
        """Safely creates or retrieves a FamilyParameter across Revit 2020-2027+."""
        for p in fam_mgr.Parameters:
            if p.Definition.Name.lower() == str(name).lower():
                return p

        try:
            use_forge, spec_or_ptype, group = cls._resolve_revit_spec_and_group(param_type, param_kind)
            if use_forge is None:
                return None

            if external_def is not None:
                try:
                    return fam_mgr.AddParameter(external_def, group, bool(is_instance))
                except Exception:
                    pass

            if spec_or_ptype is not None:
                return fam_mgr.AddParameter(name, group, spec_or_ptype, bool(is_instance))
        except Exception:
            return None
        return None

    @staticmethod
    def _resolve_custom_target_spec(custom_parameters, target_name, default_name, default_instance=True):
        """Returns (param_name, is_instance) overridden by a custom Constraint parameter if defined."""
        for cp in (custom_parameters or []):
            t = getattr(cp, "constraint_target", "") or (cp.get("constraint_target", "") if isinstance(cp, dict) else "")
            if str(t).strip().lower() == str(target_name).strip().lower():
                cp_name = getattr(cp, "name", None) or (cp.get("name") if isinstance(cp, dict) else None)
                cp_inst = getattr(cp, "is_instance", default_instance) if not isinstance(cp, dict) else cp.get("is_instance", default_instance)
                if cp_name:
                    return str(cp_name), bool(cp_inst)
        return default_name, bool(default_instance)

    @classmethod
    def _apply_custom_family_parameters(cls, fam_mgr, target_elem, custom_parameters,
                                        shared_param_defs=None, allow_constraints=False):
        """Adds user-defined Data/Constraint parameters, associations, and formulas inside fam_doc."""
        if not fam_mgr or not custom_parameters:
            return

        sp_defs = shared_param_defs or {}
        for cp in custom_parameters:
            try:
                cp_name = getattr(cp, "name", None) or (cp.get("name") if isinstance(cp, dict) else "")
                cp_dtype = getattr(cp, "data_type", "Text") if not isinstance(cp, dict) else cp.get("data_type", "Text")
                cp_kind = getattr(cp, "param_kind", "Data") if not isinstance(cp, dict) else cp.get("param_kind", "Data")
                cp_inst = getattr(cp, "is_instance", True) if not isinstance(cp, dict) else cp.get("is_instance", True)
                cp_target = getattr(cp, "constraint_target", "") if not isinstance(cp, dict) else cp.get("constraint_target", "")
                cp_formula = getattr(cp, "formula", "") if not isinstance(cp, dict) else cp.get("formula", "")
                cp_expr = getattr(cp, "value_expr", "") if not isinstance(cp, dict) else cp.get("value_expr", "")

                if not cp_name:
                    continue

                ext_def = sp_defs.get(cp_name)
                fp = cls._add_or_get_parameter(
                    fam_mgr,
                    cp_name,
                    param_type=cp_dtype,
                    is_instance=cp_inst,
                    external_def=ext_def,
                    param_kind=cp_kind if allow_constraints else "Data"
                )
                if not fp:
                    continue

                if allow_constraints and cp_target and target_elem is not None:
                    t_low = str(cp_target).strip().lower()
                    if t_low == "visibility" and hasattr(DB.BuiltInParameter, "IS_VISIBLE_PARAM"):
                        try:
                            vis_p = target_elem.get_Parameter(DB.BuiltInParameter.IS_VISIBLE_PARAM)
                            if vis_p:
                                fam_mgr.AssociateElementParameterToFamilyParameter(vis_p, fp)
                        except Exception:
                            pass
                    elif t_low == "material" and hasattr(DB.BuiltInParameter, "MATERIAL_ID_PARAM"):
                        try:
                            m_p = target_elem.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                            if m_p:
                                fam_mgr.AssociateElementParameterToFamilyParameter(m_p, fp)
                        except Exception:
                            pass

                if allow_constraints and cp_formula and hasattr(fam_mgr, "SetFormula"):
                    try:
                        fam_mgr.SetFormula(fp, str(cp_formula).strip())
                    except Exception as ex_f:
                        print("[FamilyFactory Formula Notice '{}']: {}".format(cp_name, ex_f))
                elif cp_expr and not str(cp_expr).startswith("@") and "{" not in str(cp_expr) and not cp_target:
                    # Static literal default inside family
                    try:
                        dt_low = str(cp_dtype).lower()
                        if dt_low == "yesno":
                            val = 1 if str(cp_expr).strip().lower() in ("1", "true", "yes", "y", "on") else 0
                            fam_mgr.Set(fp, int(val))
                        elif dt_low == "integer":
                            fam_mgr.Set(fp, int(float(cp_expr)))
                        elif dt_low in ("number",):
                            fam_mgr.Set(fp, float(cp_expr))
                        elif dt_low == "length":
                            fam_mgr.Set(fp, float(cp_expr) / 0.3048)
                        elif dt_low == "area":
                            fam_mgr.Set(fp, float(cp_expr) / 0.09290304)
                        elif dt_low == "volume":
                            fam_mgr.Set(fp, float(cp_expr) / 0.028316846592)
                        elif dt_low == "text":
                            fam_mgr.Set(fp, str(cp_expr))
                    except Exception:
                        pass
            except Exception:
                pass

    @classmethod
    def _find_plan_view(cls, fam_doc):
        """Locates a floor/ref-level ViewPlan inside a family document."""
        try:
            if not hasattr(DB, "FilteredElementCollector") or not hasattr(DB, "ViewPlan"):
                return None
            col = DB.FilteredElementCollector(fam_doc).OfClass(DB.ViewPlan)
            for v in col:
                if v and not getattr(v, "IsTemplate", False):
                    return v
        except Exception:
            pass
        return None

    @classmethod
    def _lock_box_to_reference_planes(cls, fam_doc, fam_mgr, extrusion, p_width, p_depth, hw, hd):
        """Builds Center/Edge Reference Planes, EQ & Width/Depth labeled dimensions, and locks extrusion faces."""
        plan_view = cls._find_plan_view(fam_doc)
        if not plan_view or not extrusion or not hasattr(fam_doc, "FamilyCreate"):
            return

        fc = fam_doc.FamilyCreate
        if not hasattr(fc, "NewReferencePlane"):
            return

        # 1. Create Left / Right / Center_LR and Front / Back / Center_FB reference planes
        rp_left = fc.NewReferencePlane(DB.XYZ(-hw, -hd - 1.0, 0.0), DB.XYZ(-hw, hd + 1.0, 0.0), DB.XYZ.BasisZ, plan_view)
        rp_right = fc.NewReferencePlane(DB.XYZ(hw, -hd - 1.0, 0.0), DB.XYZ(hw, hd + 1.0, 0.0), DB.XYZ.BasisZ, plan_view)
        rp_ctr_lr = fc.NewReferencePlane(DB.XYZ(0.0, -hd - 1.0, 0.0), DB.XYZ(0.0, hd + 1.0, 0.0), DB.XYZ.BasisZ, plan_view)

        rp_front = fc.NewReferencePlane(DB.XYZ(-hw - 1.0, -hd, 0.0), DB.XYZ(hw + 1.0, -hd, 0.0), DB.XYZ.BasisZ, plan_view)
        rp_back = fc.NewReferencePlane(DB.XYZ(-hw - 1.0, hd, 0.0), DB.XYZ(hw + 1.0, hd, 0.0), DB.XYZ.BasisZ, plan_view)
        rp_ctr_fb = fc.NewReferencePlane(DB.XYZ(-hw - 1.0, 0.0, 0.0), DB.XYZ(hw + 1.0, 0.0, 0.0), DB.XYZ.BasisZ, plan_view)

        for rp_name, rp_obj in (
            ("R2R_Left", rp_left), ("R2R_Right", rp_right), ("R2R_Center_LR", rp_ctr_lr),
            ("R2R_Front", rp_front), ("R2R_Back", rp_back), ("R2R_Center_FB", rp_ctr_fb)
        ):
            try:
                if rp_obj:
                    rp_obj.Name = rp_name
            except Exception:
                pass

        for ctr_rp in (rp_ctr_lr, rp_ctr_fb):
            try:
                if ctr_rp and hasattr(ctr_rp, "Pinned"):
                    ctr_rp.Pinned = True
            except Exception:
                pass

        fam_doc.Regenerate()

        # 2. Create EQ + Labeled Dimensions if ReferenceArray & NewDimension exist
        if hasattr(DB, "ReferenceArray") and hasattr(fc, "NewDimension"):
            if p_width and rp_left and rp_ctr_lr and rp_right:
                try:
                    ra_eq_w = DB.ReferenceArray()
                    ra_eq_w.Append(rp_left.GetReference())
                    ra_eq_w.Append(rp_ctr_lr.GetReference())
                    ra_eq_w.Append(rp_right.GetReference())
                    dim_eq_w = fc.NewDimension(plan_view, DB.Line.CreateBound(DB.XYZ(-hw, 0.0, 0.0), DB.XYZ(hw, 0.0, 0.0)), ra_eq_w)
                    if dim_eq_w and hasattr(dim_eq_w, "AreSegmentsEqual"):
                        dim_eq_w.AreSegmentsEqual = True

                    ra_w = DB.ReferenceArray()
                    ra_w.Append(rp_left.GetReference())
                    ra_w.Append(rp_right.GetReference())
                    dim_w = fc.NewDimension(plan_view, DB.Line.CreateBound(DB.XYZ(-hw, hd + 0.5, 0.0), DB.XYZ(hw, hd + 0.5, 0.0)), ra_w)
                    if dim_w:
                        dim_w.FamilyLabel = p_width
                except Exception:
                    pass

            if p_depth and rp_front and rp_ctr_fb and rp_back:
                try:
                    ra_eq_d = DB.ReferenceArray()
                    ra_eq_d.Append(rp_front.GetReference())
                    ra_eq_d.Append(rp_ctr_fb.GetReference())
                    ra_eq_d.Append(rp_back.GetReference())
                    dim_eq_d = fc.NewDimension(plan_view, DB.Line.CreateBound(DB.XYZ(0.0, -hd, 0.0), DB.XYZ(0.0, hd, 0.0)), ra_eq_d)
                    if dim_eq_d and hasattr(dim_eq_d, "AreSegmentsEqual"):
                        dim_eq_d.AreSegmentsEqual = True

                    ra_d = DB.ReferenceArray()
                    ra_d.Append(rp_front.GetReference())
                    ra_d.Append(rp_back.GetReference())
                    dim_d = fc.NewDimension(plan_view, DB.Line.CreateBound(DB.XYZ(hw + 0.5, -hd, 0.0), DB.XYZ(hw + 0.5, hd, 0.0)), ra_d)
                    if dim_d:
                        dim_d.FamilyLabel = p_depth
                except Exception:
                    pass

        # 3. Align & lock the 4 vertical planar faces of the extrusion to the edge reference planes
        if hasattr(fc, "NewAlignment") and hasattr(DB, "Options"):
            try:
                opt = DB.Options()
                opt.ComputeReferences = True
                geom_elem = extrusion.get_Geometry(opt)
                if geom_elem:
                    for g in geom_elem:
                        if not hasattr(g, "Faces"):
                            continue
                        for f in g.Faces:
                            if not getattr(f, "Reference", None) or not hasattr(f, "FaceNormal"):
                                continue
                            n = f.FaceNormal
                            if n.X < -0.95 and abs(n.Z) < 0.05 and rp_left:
                                fc.NewAlignment(plan_view, rp_left.GetReference(), f.Reference)
                            elif n.X > 0.95 and abs(n.Z) < 0.05 and rp_right:
                                fc.NewAlignment(plan_view, rp_right.GetReference(), f.Reference)
                            elif n.Y < -0.95 and abs(n.Z) < 0.05 and rp_front:
                                fc.NewAlignment(plan_view, rp_front.GetReference(), f.Reference)
                            elif n.Y > 0.95 and abs(n.Z) < 0.05 and rp_back:
                                fc.NewAlignment(plan_view, rp_back.GetReference(), f.Reference)
            except Exception:
                pass

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
        target_bic=None,
        primitive_type="Box",
        radius=None,
        base_offset=0.0,
        custom_parameters=None,
        shared_param_defs=None,
        is_type_driven=False
    ):
        """Mode A: Creates a true dimension-driven, reference-plane locked editable .rfa family.

        Strictly supports verified Axis-Aligned Box (Width/Depth/Height on Reference Planes),
        Vertical Cylinder (Radius/Height), and Vertical ProfileExtrusion (explicit profile_curve_loops
        + Height/Base_Offset). Refuses to synthesize fallback bounding boxes for non-matching geometry.
        """
        if not REVIT_AVAILABLE or not app or not project_doc:
            return None

        has_profile_loops = bool(profile_curve_loops and len(profile_curve_loops) > 0)
        prim_lower = str(primitive_type or "").strip().lower()
        if prim_lower not in ("box", "cylinder", "profileextrusion"):
            return None
        if prim_lower == "profileextrusion" and not has_profile_loops:
            return None

        is_profile = has_profile_loops or (prim_lower == "profileextrusion")
        is_cyl = (not is_profile) and (prim_lower == "cylinder")
        is_box = (not is_profile) and (not is_cyl) and (prim_lower == "box")

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

            sp_defs = shared_param_defs or {}
            def_inst = not bool(is_type_driven)

            # Resolve optional user overrides for driver parameter names/scopes
            w_name, w_inst = cls._resolve_custom_target_spec(custom_parameters, "Width", "Width", def_inst)
            d_name, d_inst = cls._resolve_custom_target_spec(custom_parameters, "Depth", "Depth", def_inst)
            h_name, h_inst = cls._resolve_custom_target_spec(custom_parameters, "Height", "Height", def_inst)
            bo_name, bo_inst = cls._resolve_custom_target_spec(custom_parameters, "Base_Offset", "Base_Offset", def_inst)
            r_name, r_inst = cls._resolve_custom_target_spec(custom_parameters, "Radius", "Radius", def_inst)
            m_name, m_inst = cls._resolve_custom_target_spec(custom_parameters, "Material", "Element_Material", True)

            p_width = None
            p_depth = None
            p_radius = None
            if is_box:
                p_width = cls._add_or_get_parameter(fam_mgr, w_name, "Length", is_instance=w_inst, external_def=sp_defs.get(w_name), param_kind="Constraint")
                p_depth = cls._add_or_get_parameter(fam_mgr, d_name, "Length", is_instance=d_inst, external_def=sp_defs.get(d_name), param_kind="Constraint")
            elif is_cyl:
                p_radius = cls._add_or_get_parameter(fam_mgr, r_name, "Length", is_instance=r_inst, external_def=sp_defs.get(r_name), param_kind="Constraint")

            p_height = cls._add_or_get_parameter(fam_mgr, h_name, "Length", is_instance=h_inst, external_def=sp_defs.get(h_name), param_kind="Constraint")
            p_base_off = cls._add_or_get_parameter(fam_mgr, bo_name, "Length", is_instance=bo_inst, external_def=sp_defs.get(bo_name), param_kind="Constraint")
            p_mat = cls._add_or_get_parameter(fam_mgr, m_name, "Material", is_instance=m_inst, external_def=sp_defs.get(m_name))

            rad_val = float(radius) if (radius and float(radius) > 0.001) else max(0.05, float(width) / 2.0)

            # Set initial parameter values
            try:
                if is_box and p_width and width > 0.001:
                    fam_mgr.Set(p_width, float(width))
                if is_box and p_depth and depth > 0.001:
                    fam_mgr.Set(p_depth, float(depth))
                if is_cyl and p_radius and rad_val > 0.001:
                    fam_mgr.Set(p_radius, float(rad_val))
                if p_height and height > 0.001:
                    fam_mgr.Set(p_height, float(height))
                if p_base_off:
                    fam_mgr.Set(p_base_off, float(base_offset or 0.0))
            except Exception:
                pass

            # 2. Build 2D Extrusion Profile centered at (0, 0, 0)
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
            sketch_plane = DB.SketchPlane.Create(fam_doc, plane)
            curve_arr_array = DB.CurveArrArray()

            hw = max(0.05, float(width) / 2.0)
            hd = max(0.05, float(depth) / 2.0)

            if is_profile:
                for cl in profile_curve_loops:
                    ca = DB.CurveArray()
                    for c in cl:
                        ca.Append(c)
                    curve_arr_array.Append(ca)
            elif is_cyl:
                # Circular profile (2 semi-circular arcs) for Cylinder primitives
                ca = DB.CurveArray()
                ca.Append(DB.Arc.Create(DB.XYZ(-rad_val, 0.0, 0.0), DB.XYZ(rad_val, 0.0, 0.0), DB.XYZ(0.0, rad_val, 0.0)))
                ca.Append(DB.Arc.Create(DB.XYZ(rad_val, 0.0, 0.0), DB.XYZ(-rad_val, 0.0, 0.0), DB.XYZ(0.0, -rad_val, 0.0)))
                curve_arr_array.Append(ca)
            elif is_box:
                # Rectangular box profile centered at (0,0)
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

            # 4. True Reference-Plane Locking for Axis-Aligned Box primitives (Width / Depth flex)
            if extrusion and is_box:
                try:
                    cls._lock_box_to_reference_planes(fam_doc, fam_mgr, extrusion, p_width, p_depth, hw, hd)
                except Exception:
                    pass

            # Associate Extrusion End Offset & Start Offset to Height / Base_Offset
            if extrusion and p_height:
                try:
                    end_param = extrusion.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_OFFSET)
                    if end_param:
                        fam_mgr.AssociateElementParameterToFamilyParameter(end_param, p_height)
                except Exception:
                    pass

            if extrusion and p_base_off and hasattr(DB.BuiltInParameter, "EXTRUSION_START_OFFSET"):
                try:
                    start_param = extrusion.get_Parameter(DB.BuiltInParameter.EXTRUSION_START_OFFSET)
                    if start_param:
                        fam_mgr.AssociateElementParameterToFamilyParameter(start_param, p_base_off)
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

            # 5. Apply User-Defined Custom Data & Constraint Parameters + Formulas
            if custom_parameters:
                cls._apply_custom_family_parameters(
                    fam_mgr, extrusion, custom_parameters,
                    shared_param_defs=sp_defs, allow_constraints=True
                )

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
        unit_scale=1.0,
        default_material=None,
        custom_parameters=None,
        shared_param_defs=None,
        *args,
        **kwargs
    ):
        """Mode B: Creates an exact-geometry non-editable 3D native .rfa family.

        Uses multi-solid DB.FreeFormElement.Create for closed Breps/Solids/Extrusions/Meshes,
        or an in-family exact DirectShape container when geometry is open/non-watertight.
        Never runs SubD edge-melting or bounding-box approximation; preserves Edit Type, Material,
        and Instance/Type custom Data parameters.
        """
        if not REVIT_AVAILABLE or not app or not project_doc or not rh_brep_or_solid:
            return None

        tmpl_path = FamilyTemplateLocator.find_template(app, category_hint=category_hint)
        if not tmpl_path:
            return None

        clean_fam_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(family_name).strip()) or "R2R_Bespoke_Solid"

        revit_solids = []
        revit_fallback_geoms = []

        if isinstance(rh_brep_or_solid, DB.Solid):
            if getattr(rh_brep_or_solid, "Volume", 0.0) > 0.000001:
                revit_solids.append(rh_brep_or_solid)
        elif RHINO_AVAILABLE:
            try:
                working = rh_brep_or_solid
                if not isinstance(working, (rg.Brep, rg.Mesh)) and hasattr(working, "ToBrep"):
                    b = working.ToBrep()
                    if b:
                        working = b

                # Priority 1: Multi-solid Brep -> DB.Solid (exact NURBS geometry, no SubD smoothing!)
                if hasattr(GeometryEncoder, "ToSolid") and isinstance(working, rg.Brep):
                    comps = []
                    try:
                        if hasattr(working, "GetConnectedComponents"):
                            comps = list(working.GetConnectedComponents() or [])
                    except Exception:
                        comps = []

                    if comps and len(comps) > 1:
                        for comp in comps:
                            try:
                                s_c = GeometryEncoder.ToSolid(comp)
                                if s_c and getattr(s_c, "Volume", 0.0) > 0.000001:
                                    revit_solids.append(s_c)
                            except Exception:
                                pass

                    if not revit_solids:
                        try:
                            s_all = GeometryEncoder.ToSolid(working)
                            if s_all and getattr(s_all, "Volume", 0.0) > 0.000001:
                                revit_solids.append(s_all)
                        except Exception:
                            pass
            except Exception:
                pass

            # Priority 2: TessellatedShapeBuilder directly on exact meshes / Brep faces (in Revit feet)
            if not revit_solids and hasattr(DB, "TessellatedShapeBuilder"):
                try:
                    working_tsb = geom_feet
                    if not working_tsb:
                        working_tsb = rh_brep_or_solid.Duplicate() if hasattr(rh_brep_or_solid, "Duplicate") else rh_brep_or_solid
                        if hasattr(working_tsb, "Scale") and abs(float(unit_scale or 1.0) - 1.0) > 0.00001:
                            working_tsb.Scale(float(unit_scale))

                    if not isinstance(working_tsb, (rg.Brep, rg.Mesh)) and hasattr(working_tsb, "ToBrep"):
                        try:
                            b_tsb = working_tsb.ToBrep()
                            if b_tsb:
                                working_tsb = b_tsb
                        except Exception:
                            pass

                    meshes = []
                    if isinstance(working_tsb, rg.Mesh):
                        meshes = [working_tsb]
                    elif isinstance(working_tsb, rg.Brep):
                        mp = rg.MeshingParameters.Default
                        b_meshes = rg.Mesh.CreateFromBrep(working_tsb, mp)
                        if b_meshes:
                            meshes = list(b_meshes)

                    def _run_tsb(target_mode, fallback_mode, is_closed_set):
                        tsb = DB.TessellatedShapeBuilder()
                        tsb.OpenConnectedFaceSet(bool(is_closed_set))
                        added = False
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
                                    added = True
                                if hasattr(f, "IsQuad") and f.IsQuad:
                                    p3 = verts[f.D]
                                    v3 = DB.XYZ(float(p3.X), float(p3.Y), float(p3.Z))
                                    if v0.DistanceTo(v2) > 0.0001 and v2.DistanceTo(v3) > 0.0001 and v3.DistanceTo(v0) > 0.0001:
                                        loop_pts2 = List[DB.XYZ]()
                                        loop_pts2.Add(v0)
                                        loop_pts2.Add(v2)
                                        loop_pts2.Add(v3)
                                        tsb.AddFace(DB.TessellatedFace(loop_pts2, DB.ElementId.InvalidElementId))
                                        added = True
                        tsb.CloseConnectedFaceSet()
                        if not added:
                            return []
                        tsb.Target = target_mode
                        tsb.Fallback = fallback_mode
                        tsb.Build()
                        b_res = tsb.GetBuildResult()
                        if b_res:
                            res_objs = b_res.GetGeometricalObjects()
                            if res_objs:
                                return list(res_objs)
                        return []

                    if meshes:
                        salvage_fb = getattr(DB.TessellatedShapeBuilderFallback, "Salvage", DB.TessellatedShapeBuilderFallback.Abort)
                        solid_objs = _run_tsb(DB.TessellatedShapeBuilderTarget.Solid, salvage_fb, True)
                        for go in solid_objs:
                            if isinstance(go, DB.Solid) and getattr(go, "Volume", 0.0) > 0.000001:
                                revit_solids.append(go)

                        # Pass 2: Open / non-watertight exact mesh container inside .rfa family
                        if not revit_solids:
                            any_tgt = getattr(DB.TessellatedShapeBuilderTarget, "AnyGeometry", DB.TessellatedShapeBuilderTarget.Mesh)
                            mesh_fb = getattr(DB.TessellatedShapeBuilderFallback, "Mesh", DB.TessellatedShapeBuilderFallback.Salvage)
                            mesh_objs = _run_tsb(any_tgt, mesh_fb, False)
                            for go in mesh_objs:
                                if go is not None:
                                    revit_fallback_geoms.append(go)
                except Exception as ex_tsb:
                    print("[FamilyFactory TSB Exact Geometry Notice]: {}".format(ex_tsb))

        if not revit_solids and not revit_fallback_geoms:
            return None

        fam_doc = None
        try:
            fam_doc = app.NewFamilyDocument(tmpl_path)
            if not fam_doc:
                return None

            fam_mgr = fam_doc.FamilyManager

            # 1. Start Transaction inside Family Document
            t = DB.Transaction(fam_doc, "Create Native Exact-Geometry Family")
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
            fam_cat_id = fam_cat.Id if fam_cat else DB.ElementId(DB.BuiltInCategory.OST_GenericModel)

            # Add Material Parameter
            sp_defs = shared_param_defs or {}
            m_name, m_inst = cls._resolve_custom_target_spec(custom_parameters, "Material", "Element_Material", True)
            p_mat = cls._add_or_get_parameter(fam_mgr, m_name, "Material", is_instance=m_inst, external_def=sp_defs.get(m_name))

            primary_elem = None
            if revit_solids:
                clean_subcat = re.sub(r'[^a-zA-Z0-9_\- ]', '_', str(subcategory_name).strip()) or "R2R_Geometry"
                subcat_elem = None
                if fam_cat and subcategory_name:
                    try:
                        subcats = fam_cat.SubCategories
                        if subcats.Contains(clean_subcat):
                            subcat_elem = subcats.get_Item(clean_subcat)
                        else:
                            subcat_elem = fam_doc.Settings.Categories.NewSubcategory(fam_cat, clean_subcat)
                    except Exception:
                        subcat_elem = None

                for s_item in revit_solids:
                    ff_elem = DB.FreeFormElement.Create(fam_doc, s_item)
                    if not primary_elem:
                        primary_elem = ff_elem
                    if ff_elem and subcat_elem:
                        try:
                            ff_elem.Subcategory = subcat_elem
                        except Exception:
                            pass
                    if ff_elem and p_mat:
                        try:
                            mat_param = ff_elem.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                            if mat_param:
                                fam_mgr.AssociateElementParameterToFamilyParameter(mat_param, p_mat)
                        except Exception:
                            pass
            elif revit_fallback_geoms and hasattr(DB, "DirectShape"):
                try:
                    ds_fam = DB.DirectShape.CreateElement(fam_doc, fam_cat_id)
                    ds_fam.SetShape(revit_fallback_geoms)
                    primary_elem = ds_fam
                    if ds_fam and p_mat:
                        try:
                            mat_param = ds_fam.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                            if mat_param and fam_mgr.CanElementParameterBeAssociated(mat_param):
                                fam_mgr.AssociateElementParameterToFamilyParameter(mat_param, p_mat)
                        except Exception:
                            pass
                except Exception:
                    primary_elem = None

            if not primary_elem:
                try:
                    t.RollBack()
                except Exception:
                    pass
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass
                return None

            # Apply User-Defined Deterministic Data Parameters (Instance & Type)
            if custom_parameters:
                cls._apply_custom_family_parameters(
                    fam_mgr, primary_elem, custom_parameters,
                    shared_param_defs=sp_defs, allow_constraints=False
                )

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
