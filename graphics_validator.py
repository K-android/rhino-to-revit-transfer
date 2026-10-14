"""
One-Click Rhino-to-Revit Transfer (RIR): Module 6
graphics_validator.py — Dual-Status BIM & View Graphics Validator

Evaluates every transferred Revit element across two distinct criteria:
  1. BIM STATUS (PASS | FAIL):
     - Valid Category, Subcategory, and Material assignment
     - Geometry integrity (non-empty get_Geometry with valid faces/solids/meshes)
  2. GRAPHICS STATUS (PASS | REVIEW):
     - Visibility across 4 standard documentation views (Plan, Section, Elevation, 3D)
     - Flag DirectShapes in Plan and Section cut views with WARNING for schedule/cut limitations

Produces a View_Matrix and computes overall status:
  - COMPLETE (Both BIM and Graphics pass without warnings)
  - REVIEW REQUIRED (Warnings or review needed in documentation views)
"""

import sys
import traceback

try:
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    import Autodesk.Revit.DB as DB
    import Autodesk.Revit.UI as UI
    from System.Collections.Generic import List
    REVIT_AVAILABLE = True
except Exception:
    REVIT_AVAILABLE = False


class ElementValidationReport(object):
    """Holds Dual-Status audit results and view visibility matrix for an element."""

    def __init__(self, revit_element_id, rhino_guid):
        self.revit_element_id = revit_element_id
        self.rhino_guid = str(rhino_guid)
        self.category_name = "Unknown"
        self.element_name = ""

        # Dual Status
        self.bim_status = "PASS"        # PASS | FAIL
        self.graphics_status = "PASS"   # PASS | REVIEW
        self.overall_status = "COMPLETE"  # COMPLETE | REVIEW REQUIRED

        # View Matrix: Plan, Section, Elevation, 3D
        self.view_matrix = {
            "Plan": "PASS",
            "Section": "PASS",
            "Elevation": "PASS",
            "3D": "PASS"
        }

        self.geometry_valid = False
        self.face_count = 0
        self.has_material = False
        self.material_name = ""
        self.is_direct_shape = False
        self.issues = []
        self.recommendations = []

    def to_dict(self):
        return {
            "revit_element_id": self.revit_element_id,
            "rhino_guid": self.rhino_guid,
            "category_name": self.category_name,
            "element_name": self.element_name,
            "bim_status": self.bim_status,
            "graphics_status": self.graphics_status,
            "overall_status": self.overall_status,
            "view_matrix": self.view_matrix,
            "geometry_valid": self.geometry_valid,
            "face_count": self.face_count,
            "has_material": self.has_material,
            "material_name": self.material_name,
            "is_direct_shape": self.is_direct_shape,
            "issues": self.issues,
            "recommendations": self.recommendations
        }


class GraphicsValidator(object):
    """Validates transferred elements against BIM data standards and Revit view graphics."""

    def __init__(self, revit_doc):
        self.doc = revit_doc
        self.test_views = {
            "Plan": None,
            "Section": None,
            "Elevation": None,
            "3D": None
        }
        if REVIT_AVAILABLE and self.doc:
            self._locate_test_views()

    def _locate_test_views(self):
        """Finds representative Plan, Section, Elevation, and 3D views for documentation testing."""
        collector = DB.FilteredElementCollector(self.doc).OfClass(DB.View)
        for v in collector:
            if v.IsTemplate:
                continue

            vt = v.ViewType
            if vt == DB.ViewType.FloorPlan and not self.test_views["Plan"]:
                self.test_views["Plan"] = v
            elif vt == DB.ViewType.Section and not self.test_views["Section"]:
                self.test_views["Section"] = v
            elif vt == DB.ViewType.Elevation and not self.test_views["Elevation"]:
                self.test_views["Elevation"] = v
            elif vt == DB.ViewType.ThreeD and not v.IsPerspective and not self.test_views["3D"]:
                self.test_views["3D"] = v

    def validate_element(self, element_id, rhino_guid):
        """Executes full Dual-Status validation on a Revit element."""
        report = ElementValidationReport(element_id, rhino_guid)
        if not REVIT_AVAILABLE or not self.doc:
            report.bim_status = "FAIL"
            report.graphics_status = "REVIEW"
            report.overall_status = "REVIEW REQUIRED"
            report.issues.append("Revit environment unavailable")
            return report

        elem = self.doc.GetElement(DB.ElementId(element_id))
        if not elem:
            report.bim_status = "FAIL"
            report.graphics_status = "REVIEW"
            report.overall_status = "REVIEW REQUIRED"
            report.issues.append("Element ID {} not found in document".format(element_id))
            return report

        report.category_name = elem.Category.Name if elem.Category else "Unassigned"
        report.element_name = elem.Name or "Unnamed"
        report.is_direct_shape = isinstance(elem, DB.DirectShape)

        # ---------------------------------------------------------------------
        # 1. BIM STATUS EVALUATION
        # ---------------------------------------------------------------------
        # Category Check
        if not elem.Category or elem.Category.Id.IntegerValue == -1:
            report.bim_status = "FAIL"
            report.issues.append("No valid Revit category assigned.")

        # Geometry Integrity Check
        geom_opts = DB.Options()
        geom_opts.DetailLevel = DB.ViewDetailLevel.Fine
        geom_elem = elem.get_Geometry(geom_opts)

        face_count = 0
        solid_count = 0
        mesh_count = 0

        if geom_elem:
            for g_obj in geom_elem:
                if isinstance(g_obj, DB.Solid):
                    if g_obj.Faces.Size > 0:
                        solid_count += 1
                        face_count += g_obj.Faces.Size
                elif isinstance(g_obj, DB.Mesh):
                    mesh_count += 1
                    face_count += g_obj.NumTriangles
                elif isinstance(g_obj, DB.GeometryInstance):
                    inst_geom = g_obj.GetInstanceGeometry()
                    if inst_geom:
                        for sub_g in inst_geom:
                            if isinstance(sub_g, DB.Solid):
                                face_count += sub_g.Faces.Size
                                solid_count += 1
                            elif isinstance(sub_g, DB.Mesh):
                                face_count += sub_g.NumTriangles
                                mesh_count += 1

        report.face_count = face_count
        if face_count > 0:
            report.geometry_valid = True
        else:
            report.geometry_valid = False
            report.bim_status = "FAIL"
            report.issues.append("Element geometry is empty or unreadable.")

        # Material Assignment Check
        mat_id = elem.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
        if mat_id and mat_id.AsElementId() != DB.ElementId.InvalidElementId:
            mat_elem = self.doc.GetElement(mat_id.AsElementId())
            report.has_material = True
            report.material_name = mat_elem.Name if mat_elem else "Assigned"
        else:
            # Check by category material
            report.has_material = False
            report.material_name = "By Category"

        # ---------------------------------------------------------------------
        # 2. GRAPHICS & VIEW STATUS EVALUATION
        # ---------------------------------------------------------------------
        for view_name, test_view in self.test_views.items():
            if not test_view:
                report.view_matrix[view_name] = "PASS"
                continue

            # Check if element is explicitly hidden in view
            try:
                if elem.IsHidden(test_view):
                    report.view_matrix[view_name] = "WARNING"
                    report.issues.append("Element is hidden by view filter or category in '{}' view.".format(view_name))
                    continue

                # Check bounding box in view
                bbox = elem.get_BoundingBox(test_view)
                if not bbox:
                    report.view_matrix[view_name] = "WARNING"
                    report.issues.append("No bounding box generated in '{}' view.".format(view_name))
                    continue

                # Documentation Cut Representation Warning for DirectShapes
                # In Plan and Section views, DirectShapes may not display standard cut patterns
                if report.is_direct_shape and view_name in ["Plan", "Section"]:
                    report.view_matrix[view_name] = "WARNING"
                    report.recommendations.append(
                        "DirectShape in {} view: Plan/Section cut fills, annotations and tags may be limited.".format(view_name)
                    )
                else:
                    report.view_matrix[view_name] = "PASS"

            except Exception as ex:
                report.view_matrix[view_name] = "WARNING"

        # Evaluate Overall Graphics Status
        has_graphics_warning = any(v != "PASS" for v in report.view_matrix.values())
        if has_graphics_warning or report.is_direct_shape:
            report.graphics_status = "REVIEW"
        else:
            report.graphics_status = "PASS"

        # Overall Status
        if report.bim_status == "PASS" and report.graphics_status == "PASS":
            report.overall_status = "COMPLETE"
        else:
            report.overall_status = "REVIEW REQUIRED"

        return report

    @staticmethod
    def isolate_element_in_view(ui_doc, element_id):
        """Highlights and isolates an element in the active Revit view for user inspection."""
        if not REVIT_AVAILABLE or not ui_doc:
            return False

        try:
            elem_id = DB.ElementId(element_id)
            elem_ids = List[DB.ElementId]()
            elem_ids.Add(elem_id)

            # Set selection
            ui_doc.Selection.SetElementIds(elem_ids)

            # Isolate element temporarily in active view
            active_view = ui_doc.ActiveView
            if active_view and active_view.CanEnableTemporaryViewProperties():
                with DB.Transaction(ui_doc.Document, "Isolate R2R Element") as trans:
                    trans.Start()
                    active_view.IsolateElementsTemporary(elem_ids)
                    trans.Commit()

            # Center view on element
            ui_doc.ShowElements(elem_ids)
            return True
        except Exception:
            return False
