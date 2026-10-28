# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 1
scanner.py - Rhino Scanner & Geometry Fingerprinter

Scans Rhino.RhinoDoc.ActiveDoc objects, extracts deep metadata, computes
deterministic geometry fingerprints (including SHA-256 geo_hash), evaluates
spatial context against Revit levels, and performs preflight sanity checks.
"""

import sys
import math
import hashlib
import json

try:
    import Rhino
    import Rhino.Geometry as rg
    import Rhino.DocObjects as rd
    RHINO_AVAILABLE = True
except ImportError:
    RHINO_AVAILABLE = False
    class _MockRG(object):
        class Brep(object): pass
        class Extrusion(object): pass
        class Surface(object): pass
        class Mesh(object): pass
        class SubD(object): pass
        class Curve(object): pass
        class InstanceReferenceGeometry(object): pass
    rg = _MockRG()


class ScannedObject(object):
    """Encapsulates all extracted metadata, topological fingerprint, and spatial context

    for a single Rhino document object.
    """

    def __init__(self, rhino_guid):
        self.rhino_guid = str(rhino_guid)
        self.layer_full_path = ""
        self.layer_name = ""
        self.object_name = ""
        self.block_name = ""
        self.object_type = "Unknown"
        self.user_text = {}
        self.material_name = ""
        self.object_color = (128, 128, 128, 255)

        # Geometry Fingerprint
        self.bbox_min = (0.0, 0.0, 0.0)
        self.bbox_max = (0.0, 0.0, 0.0)
        self.centroid = (0.0, 0.0, 0.0)
        self.dx = 0.0
        self.dy = 0.0
        self.dz = 0.0
        self.area = 0.0
        self.volume = 0.0
        self.area_sqm = 0.0
        self.volume_cum = 0.0

        # Topological flags
        self.is_closed = False
        self.is_planar = False
        self.is_horizontal = False
        self.is_vertical = False
        self.thickness = 0.0
        self.loop_count = 0
        self.repetition_signature = ""
        self.repetition_count = 1
        self.geo_hash = ""

        # Adaptive & Parametric Family Metadata
        self.adaptive_points = []       # Ordered list of (x, y, z) tuples in Rhino units
        self.adaptive_point_count = 0   # 2, 3, 4, or N adaptive placement points
        self.rotation_angle = 0.0       # Yaw rotation in radians (XY plane)
        self.instance_transform = None  # Optional Rhino.Geometry.Transform for blocks
        self.parametric_dims = {
            "Width": 0.0,
            "Depth": 0.0,
            "Height": 0.0,
            "Length": 0.0,
            "Thickness": 0.0
        }

        # Spatial Context
        self.nearest_level_name = "Unassigned"
        self.nearest_level_elevation = 0.0
        self.dz_to_level = 0.0
        self.building_zone = "General"

        # Preflight status
        self.origin_distance = 0.0
        self.is_far_from_origin = False
        self.is_duplicate = False
        self.duplicate_of = ""
        self.has_open_loops = False
        self.preflight_warnings = []

        # Rhino Geometry Reference (transient, for in-session conversion)
        self.raw_geometry = None
        self.rhino_object = None

    def to_dict(self):
        """Serializes scanned object metadata for reporting and debugging."""
        return {
            "rhino_guid": self.rhino_guid,
            "layer_full_path": self.layer_full_path,
            "layer_name": self.layer_name,
            "object_name": self.object_name,
            "block_name": self.block_name,
            "object_type": self.object_type,
            "user_text": self.user_text,
            "material_name": self.material_name,
            "bbox_min": list(self.bbox_min),
            "bbox_max": list(self.bbox_max),
            "centroid": list(self.centroid),
            "dimensions": {"dx": self.dx, "dy": self.dy, "dz": self.dz},
            "parametric_dims": dict(self.parametric_dims),
            "area": self.area,
            "volume": self.volume,
            "area_sqm": self.area_sqm,
            "volume_cum": self.volume_cum,
            "is_closed": self.is_closed,
            "is_planar": self.is_planar,
            "is_horizontal": self.is_horizontal,
            "is_vertical": self.is_vertical,
            "thickness": self.thickness,
            "loop_count": self.loop_count,
            "repetition_signature": self.repetition_signature,
            "repetition_count": self.repetition_count,
            "adaptive_point_count": self.adaptive_point_count,
            "adaptive_points": [list(pt) for pt in self.adaptive_points],
            "rotation_angle": self.rotation_angle,
            "geo_hash": self.geo_hash,
            "nearest_level_name": self.nearest_level_name,
            "dz_to_level": self.dz_to_level,
            "building_zone": self.building_zone,
            "origin_distance": self.origin_distance,
            "is_duplicate": self.is_duplicate,
            "preflight_warnings": self.preflight_warnings
        }


class ScannerPreflightReport(object):
    """Aggregates project-level preflight validation metrics."""

    def __init__(self):
        self.rhino_unit_system = "Unknown"
        self.unit_scale_to_meters = 1.0
        self.total_scanned = 0
        self.total_duplicates = 0
        self.total_far_from_origin = 0
        self.total_non_closed_loops = 0
        self.total_repetitive_groups = 0
        self.total_adaptive_candidates = 0
        self.warnings = []
        self.levels_detected = []
        self.zones_detected = {}


class RhinoScanner(object):
    """Production scanner and geometry fingerprinter for active Rhino document."""

    # 16 km (16,000 meters) Revit coordinate origin boundary limit
    ORIGIN_LIMIT_METERS = 16000.0

    def __init__(self, rhino_doc=None):
        self._explicit_doc = rhino_doc
        self.doc = None
        self._refresh_active_doc()
        self.preflight = ScannerPreflightReport()

    def _refresh_active_doc(self):
        """Safely resolves the active Rhino document if Rhino.Inside is initialized."""
        if self._explicit_doc is not None:
            self.doc = self._explicit_doc
            return
        if RHINO_AVAILABLE:
            try:
                self.doc = Rhino.RhinoDoc.ActiveDoc
            except Exception:
                self.doc = None
        else:
            self.doc = None

    def get_unit_scale_to_meters(self):
        """Calculates scale factor from active Rhino document units to meters."""
        self._refresh_active_doc()
        if not RHINO_AVAILABLE or not self.doc:
            return 1.0

        try:
            unit = self.doc.ModelUnitSystem
            self.preflight.rhino_unit_system = str(unit)

            # Rhino.UnitSystem enum mapping
            scales = {
                Rhino.UnitSystem.Millimeters: 0.001,
                Rhino.UnitSystem.Centimeters: 0.01,
                Rhino.UnitSystem.Meters: 1.0,
                Rhino.UnitSystem.Inches: 0.0254,
                Rhino.UnitSystem.Feet: 0.3048,
            }
            return scales.get(unit, 1.0)
        except Exception:
            return 1.0

    def scan_all(self, include_hidden=False, include_locked=False, revit_levels=None):
        """Scans the active Rhino document and returns a list of ScannedObject instances.

        :param include_hidden: If True, scans objects on hidden layers or hidden objects.
        :param include_locked: If True, scans objects on locked layers.
        :param revit_levels: List of dicts [{'name': 'L01', 'elevation': 0.0}, ...] in Rhino units.
        :return: list of ScannedObject
        """
        self.preflight = ScannerPreflightReport()
        self._refresh_active_doc()
        if not RHINO_AVAILABLE or not self.doc:
            return []

        scale_to_m = self.get_unit_scale_to_meters()
        self.preflight.unit_scale_to_meters = scale_to_m
        revit_levels = revit_levels or []
        self.preflight.levels_detected = [lvl.get('name', 'Level') for lvl in revit_levels]

        # Scan settings
        settings = rd.ObjectEnumeratorSettings()
        settings.IncludeLights = False
        settings.IncludeGrips = False
        settings.NormalObjects = True
        settings.HiddenObjects = include_hidden
        settings.LockedObjects = include_locked

        # Filter for 3D physical transferable geometry types at C++ kernel level
        # Skips annotations, dimensions, text, hatches, leaders, centermarks, point clouds, clipping planes
        try:
            settings.ObjectTypeFilter = (
                rd.ObjectType.Brep |
                rd.ObjectType.Extrusion |
                rd.ObjectType.Mesh |
                rd.ObjectType.Surface |
                rd.ObjectType.SubD |
                rd.ObjectType.Curve |
                rd.ObjectType.InstanceReference
            )
        except Exception:
            pass

        try:
            rhino_objects = list(self.doc.Objects.GetObjectList(settings))
        except Exception as ex_enum:
            self.preflight.warnings.append("Notice: Document object enumeration fallback triggered: {}".format(ex_enum))
            rhino_objects = list(self.doc.Objects)
        scanned_list = []
        hash_registry = {}  # geo_hash -> ScannedObject for duplicate detection

        # Determine document bounds for building zone vertical normalization
        min_doc_z = float("inf")
        max_doc_z = float("-inf")

        # Initial pass: gather raw objects and determine project height envelope
        valid_objs = []
        for obj in rhino_objects:
            if not obj:
                continue

            try:
                # Safe layer validation check
                layer_idx = obj.Attributes.LayerIndex
                if layer_idx < 0 or layer_idx >= self.doc.Layers.Count:
                    continue
                layer = self.doc.Layers[layer_idx]
                if not layer:
                    continue
                if not include_hidden and (not layer.IsVisible or obj.IsHidden):
                    continue
                if not include_locked and (layer.IsLocked or obj.IsLocked):
                    continue

                geom = obj.Geometry
                if not geom:
                    continue

                # Skip invalid/corrupt geometry upfront to prevent OpenNURBS unmanaged C++ crashes
                if not getattr(geom, "IsValid", True):
                    self.preflight.warnings.append("Notice: Skipped invalid/corrupted object ID {}".format(getattr(obj, "Id", "Unknown")))
                    continue

                bbox = geom.GetBoundingBox(True)
                if not bbox.IsValid:
                    continue

                if bbox.Min.Z < min_doc_z:
                    min_doc_z = bbox.Min.Z
                if bbox.Max.Z > max_doc_z:
                    max_doc_z = bbox.Max.Z

                valid_objs.append(obj)
            except Exception:
                continue

        total_height = max(1.0, max_doc_z - min_doc_z) if valid_objs else 1.0

        for obj in valid_objs:
            try:
                scanned = self._process_rhino_object(obj, scale_to_m, revit_levels, min_doc_z, total_height)
                if not scanned:
                    continue
            except Exception as ex_obj:
                try:
                    obj_id = str(obj.Id) if hasattr(obj, 'Id') else "Unknown"
                except Exception:
                    obj_id = "Unknown"
                self.preflight.warnings.append("Notice: Skipped object {}: {}".format(obj_id, ex_obj))
                continue

            # Duplicate detection based on deterministic geometry hash
            if scanned.geo_hash in hash_registry:
                scanned.is_duplicate = True
                scanned.duplicate_of = hash_registry[scanned.geo_hash].rhino_guid
                scanned.preflight_warnings.append(
                    "Duplicate geometry detected matching GUID: {}".format(scanned.duplicate_of)
                )
                self.preflight.total_duplicates += 1
            else:
                hash_registry[scanned.geo_hash] = scanned

            # Origin distance check (Revit 16 km warning)
            if scanned.is_far_from_origin:
                self.preflight.total_far_from_origin += 1

            if scanned.has_open_loops:
                self.preflight.total_non_closed_loops += 1

            # Zone accounting
            zone = scanned.building_zone
            self.preflight.zones_detected[zone] = self.preflight.zones_detected.get(zone, 0) + 1

            scanned_list.append(scanned)

        # Repetition frequency count aggregation & preflight metrics
        sig_counts = {}
        for s in scanned_list:
            if s.repetition_signature:
                sig_counts[s.repetition_signature] = sig_counts.get(s.repetition_signature, 0) + 1

        rep_groups = 0
        adaptive_count = 0
        for s in scanned_list:
            if s.repetition_signature:
                s.repetition_count = sig_counts[s.repetition_signature]
            if s.adaptive_point_count in (2, 3, 4):
                adaptive_count += 1
        for sig, cnt in sig_counts.items():
            if cnt >= 2:
                rep_groups += 1

        self.preflight.total_repetitive_groups = rep_groups
        self.preflight.total_adaptive_candidates = adaptive_count
        self.preflight.total_scanned = len(scanned_list)
        return scanned_list

    def _process_rhino_object(self, obj, scale_to_m, revit_levels, min_doc_z, total_height):
        """Extracts metadata, computes geometry fingerprint, and evaluates preflight rules."""
        guid = obj.Id
        scanned = ScannedObject(guid)
        scanned.rhino_object = obj
        geom = obj.Geometry
        scanned.raw_geometry = geom

        # Layer & Naming Metadata
        layer_idx = obj.Attributes.LayerIndex
        layer = self.doc.Layers[layer_idx]
        scanned.layer_full_path = layer.FullPath
        scanned.layer_name = layer.Name
        scanned.object_name = obj.Attributes.Name or ""

        # User Text (Rhino key-value pairs)
        user_strings = obj.Attributes.GetUserStrings()
        if user_strings and user_strings.Count > 0:
            for i in range(user_strings.Count):
                key = user_strings.GetKey(i)
                val = user_strings.Get(i)
                scanned.user_text[key] = val

        # Material & Color
        mat_idx = obj.Attributes.MaterialIndex
        if mat_idx >= 0 and mat_idx < self.doc.Materials.Count:
            scanned.material_name = self.doc.Materials[mat_idx].Name
        else:
            layer_mat_idx = getattr(layer, "RenderMaterialIndex", -1)
            if layer_mat_idx >= 0 and layer_mat_idx < self.doc.Materials.Count:
                scanned.material_name = self.doc.Materials[layer_mat_idx].Name
            elif hasattr(layer, "RenderMaterial") and layer.RenderMaterial:
                scanned.material_name = layer.RenderMaterial.Name
            else:
                scanned.material_name = layer.Name

        color = obj.Attributes.ObjectColor
        scanned.object_color = (color.R, color.G, color.B, color.A)

        # Object Type
        obj_type_str = type(geom).__name__
        is_block_instance = isinstance(obj, rd.InstanceObject) or isinstance(geom, rg.InstanceReferenceGeometry)
        if is_block_instance:
            scanned.object_type = "InstanceReference"
            if isinstance(obj, rd.InstanceObject):
                instance_def = obj.InstanceDefinition
                scanned.block_name = instance_def.Name if instance_def else ""
                try:
                    scanned.instance_transform = obj.InstanceXform
                    scanned.rotation_angle = math.atan2(obj.InstanceXform.M10, obj.InstanceXform.M00)
                except Exception:
                    pass
            elif isinstance(geom, rg.InstanceReferenceGeometry):
                idef = self.doc.InstanceDefinitions.FindId(geom.ParentIdefId) if hasattr(self.doc, "InstanceDefinitions") else None
                scanned.block_name = idef.Name if idef else ""
                try:
                    scanned.instance_transform = geom.Xform
                    scanned.rotation_angle = math.atan2(geom.Xform.M10, geom.Xform.M00)
                except Exception:
                    pass

            # Extract real 3D geometry from block definition for accurate conversion and volume
            try:
                target_idef = instance_def if isinstance(obj, rd.InstanceObject) else (self.doc.InstanceDefinitions.FindId(geom.ParentIdefId) if hasattr(self.doc, "InstanceDefinitions") else None)
                if target_idef and not getattr(target_idef, "IsDeleted", False) and hasattr(target_idef, "GetObjects"):
                    sub_objs = target_idef.GetObjects()
                    if sub_objs and len(sub_objs) > 0:
                        for sub in sub_objs:
                            if not sub:
                                continue
                            sub_g = getattr(sub, "Geometry", None)
                            if sub_g and getattr(sub_g, "IsValid", True) and isinstance(sub_g, (rg.Brep, rg.Mesh, rg.Extrusion, rg.Surface, rg.SubD)):
                                dup = sub_g.Duplicate()
                                if scanned.instance_transform and hasattr(dup, "Transform"):
                                    dup.Transform(scanned.instance_transform)
                                scanned.raw_geometry = dup
                                geom = dup
                                break
            except Exception:
                pass
        elif isinstance(geom, rg.Brep):
            scanned.object_type = "Brep"
        elif isinstance(geom, rg.Extrusion):
            scanned.object_type = "Extrusion"
        elif isinstance(geom, rg.Surface):
            scanned.object_type = "Surface"
        elif isinstance(geom, rg.Mesh):
            scanned.object_type = "Mesh"
        elif isinstance(geom, rg.SubD):
            scanned.object_type = "SubD"
        elif isinstance(geom, rg.Curve):
            scanned.object_type = "Curve"
        else:
            scanned.object_type = obj_type_str

        # Bounding Box & Dimensions
        bbox = geom.GetBoundingBox(True)
        if not bbox.IsValid:
            return None

        scanned.bbox_min = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
        scanned.bbox_max = (bbox.Max.X, bbox.Max.Y, bbox.Max.Z)
        center = bbox.Center
        scanned.centroid = (center.X, center.Y, center.Z)
        scanned.dx = max(0.0001, bbox.Max.X - bbox.Min.X)
        scanned.dy = max(0.0001, bbox.Max.Y - bbox.Min.Y)
        scanned.dz = max(0.0001, bbox.Max.Z - bbox.Min.Z)

        # Distance from origin (Preflight check for >16km Revit limits)
        dist_from_origin = math.sqrt(center.X * center.X + center.Y * center.Y + center.Z * center.Z)
        dist_meters = dist_from_origin * scale_to_m
        scanned.origin_distance = dist_meters
        if dist_meters > self.ORIGIN_LIMIT_METERS:
            scanned.is_far_from_origin = True
            scanned.preflight_warnings.append(
                "Coordinate warning: Object center is {:.1f} km from Rhino origin (exceeds Revit 16 km limit)".format(
                    dist_meters / 1000.0
                )
            )

        # Topological Fingerprinting
        self._compute_topology_fingerprint(scanned, geom, scale_to_m)

        # Spatial Level Mapping
        self._compute_level_relationship(scanned, revit_levels)

        # Building Zone Classification
        self._classify_building_zone(scanned, min_doc_z, total_height)

        # Compute Deterministic SHA-256 Geo Hash
        scanned.geo_hash = self._compute_geo_hash(scanned, geom)

        return scanned

    def _compute_topology_fingerprint(self, scanned, geom, scale_to_m=1.0):
        """Analyzes topological orientation, thickness, planarity, and loop count with native C++ guards."""
        try:
            scale_to_m = float(scale_to_m) if scale_to_m else self.get_unit_scale_to_meters()
        except Exception:
            scale_to_m = 1.0

        # Brep / Extrusion
        working_brep = None
        if isinstance(geom, rg.Brep):
            working_brep = geom
        elif isinstance(geom, rg.Extrusion):
            try:
                working_brep = geom.ToBrep()
            except Exception:
                working_brep = None

        if working_brep:
            try:
                is_valid_brep = bool(getattr(working_brep, "IsValid", True))
                face_count = working_brep.Faces.Count if hasattr(working_brep, "Faces") else 0
            except Exception:
                is_valid_brep = False
                face_count = 0

            scanned.is_closed = bool(getattr(working_brep, "IsSolid", False))

            # Approximate volume and area - SAFE OPENNURBS GUARDS
            # Only run MassProperties.Compute if the Brep is valid and face count is reasonable (<= 1000)
            if is_valid_brep and face_count <= 1000:
                try:
                    mp = rg.MassProperties.Compute(working_brep)
                    if mp:
                        scanned.area = round(mp.Area, 3)
                        scanned.volume = round(mp.Volume, 3) if scanned.is_closed else 0.0
                except Exception:
                    scanned.area = 0.0
                    scanned.volume = 0.0
            else:
                # Fast fallback for massive Breps (>1000 faces) or degenerate Breps: bounding box estimate
                if scanned.is_closed:
                    scanned.volume = round(scanned.dx * scanned.dy * scanned.dz, 3)
                scanned.area = round(2.0 * (scanned.dx * scanned.dy + scanned.dy * scanned.dz + scanned.dx * scanned.dz), 3)
                if face_count > 1000:
                    scanned.preflight_warnings.append(
                        "Complex Brep ({} faces): Mass properties approximated to prevent unmanaged OpenNURBS crash.".format(face_count)
                    )

            # Orientation analysis: inspect face normals safely (sample at most 50 faces)
            horiz_face_count = 0
            vert_face_count = 0
            sample_count = 0
            try:
                if face_count > 0:
                    step = max(1, face_count // 50)
                    for i in range(0, face_count, step):
                        face = working_brep.Faces[i]
                        sample_count += 1
                        # Fast planar normal check first
                        plane_success, plane = face.TryGetPlane(0.01) if hasattr(face, "TryGetPlane") else (False, None)
                        if plane_success and plane:
                            normal = plane.Normal
                        else:
                            d0 = face.Domain(0)
                            d1 = face.Domain(1)
                            if d0.Length <= 0 or d1.Length <= 0:
                                continue
                            u_mid = 0.5 * (d0.Min + d0.Max)
                            v_mid = 0.5 * (d1.Min + d1.Max)
                            normal = face.NormalAt(u_mid, v_mid)

                        if normal.Length > 0.0001:
                            normal.Unitize()
                            # Parallel to World Z (Horizontal plane)
                            if abs(normal.Z) > 0.95:
                                horiz_face_count += 1
                            # Perpendicular to World Z (Vertical wall)
                            elif abs(normal.Z) < 0.05:
                                vert_face_count += 1
            except Exception:
                pass

            if sample_count > 0:
                scanned.is_horizontal = (horiz_face_count / float(sample_count)) > 0.3 or (scanned.dz < min(scanned.dx, scanned.dy) * 0.4 and horiz_face_count >= 1)
                scanned.is_vertical = (vert_face_count / float(sample_count)) > 0.3 or (scanned.dz >= 1.5 and vert_face_count >= 1)

            # Architectural surface area adjustment for solid slabs/walls:
            # Half of the total 3D Brep surface area is the standard architectural single-face area!
            if scanned.is_closed and face_count > 1 and scanned.area > 0:
                scanned.area = round(scanned.area / 2.0, 3)

            # Planarity check
            try:
                scanned.is_planar = (face_count == 1) and bool(working_brep.Faces[0].IsPlanar(0.01))
            except Exception:
                scanned.is_planar = False

            # Loop count (outer + inner holes) - guard against corrupt loop structures
            total_loops = 0
            try:
                for face in working_brep.Faces:
                    if hasattr(face, "Loops"):
                        total_loops += face.Loops.Count
            except Exception:
                pass
            scanned.loop_count = total_loops

            # Thickness estimate
            sorted_dims = sorted([scanned.dx, scanned.dy, scanned.dz])
            scanned.thickness = sorted_dims[0]

            if not scanned.is_closed and (scanned.volume > 0 or face_count > 1):
                scanned.has_open_loops = True
                scanned.preflight_warnings.append("Non-manifold or open Brep edges detected.")

        elif isinstance(geom, rg.Mesh):
            mesh_faces = geom.Faces.Count if hasattr(geom, "Faces") else 0
            mesh_verts = geom.Vertices.Count if hasattr(geom, "Vertices") else 0
            is_valid_mesh = bool(getattr(geom, "IsValid", True))
            scanned.is_closed = bool(getattr(geom, "IsClosed", False))
            scanned.is_planar = False

            # Safe Mesh Mass Properties (cap at 30,000 faces to prevent OpenNURBS freeze/crash)
            if is_valid_mesh and mesh_faces <= 30000:
                try:
                    amp = rg.AreaMassProperties.Compute(geom)
                    scanned.area = round(amp.Area, 3) if amp else 0.0
                except Exception:
                    scanned.area = 0.0
                try:
                    if scanned.is_closed:
                        vmp = rg.VolumeMassProperties.Compute(geom)
                        scanned.volume = round(vmp.Volume, 3) if vmp else 0.0
                    else:
                        scanned.volume = 0.0
                except Exception:
                    scanned.volume = 0.0
            else:
                # Heavy mesh fallback (> 30k faces or corrupted)
                scanned.area = round(2.0 * (scanned.dx * scanned.dy + scanned.dy * scanned.dz + scanned.dx * scanned.dz), 3)
                scanned.volume = round(scanned.dx * scanned.dy * scanned.dz, 3) if scanned.is_closed else 0.0
                if mesh_faces > 30000:
                    scanned.preflight_warnings.append(
                        "Heavy mesh ({} faces): Mass properties approximated via bounding envelope to prevent crash.".format(mesh_faces)
                    )

            scanned.thickness = min(scanned.dx, scanned.dy, scanned.dz)
            scanned.repetition_signature = "Mesh_V{}_F{}".format(mesh_verts, mesh_faces)

        elif isinstance(geom, rg.Curve):
            is_valid_crv = bool(getattr(geom, "IsValid", True))
            scanned.is_closed = bool(getattr(geom, "IsClosed", False))
            try:
                scanned.is_planar = bool(geom.IsPlanar(0.001)) if is_valid_crv else False
            except Exception:
                scanned.is_planar = False
            scanned.loop_count = 1 if scanned.is_closed else 0
            scanned.area = 0.0
            scanned.volume = 0.0
            if not scanned.is_closed:
                scanned.has_open_loops = True
                scanned.preflight_warnings.append("Open curve detected (cannot form closed boundary).")

        elif scanned.object_type == "InstanceReference":
            scanned.is_closed = True
            scanned.repetition_signature = "Block_{}".format(scanned.block_name)

        # Extract Adaptive Points and Parametric Dimensions
        self._extract_adaptive_and_parametric_features(scanned, geom, working_brep)

        if not scanned.repetition_signature:
            # Normalized shape signature: plan dimensions sorted (rotation invariant) + Z height + volume + area
            plan_dims = sorted([round(scanned.dx, 2), round(scanned.dy, 2)])
            scanned.repetition_signature = "{:.2f}x{:.2f}x{:.2f}_V{:.2f}_A{:.2f}".format(
                plan_dims[0], plan_dims[1], round(scanned.dz, 2), round(scanned.volume, 2), round(scanned.area, 2)
            )

        # Normalized metric quantities in m² and m³
        scanned.area_sqm = round(scanned.area * (scale_to_m ** 2), 3)
        scanned.volume_cum = round(scanned.volume * (scale_to_m ** 3), 3)

    @staticmethod
    def order_adaptive_points_ccw(points):
        """Deterministically orders 2, 3, or 4 spatial points into counter-clockwise winding order.

        For 3 or 4 points:
          - Computes face plane normal and local orthonormal basis (u, v).
          - Sorts vertices counter-clockwise by polar angle around the centroid.
          - Rotates the sequence so index 0 is consistently the bottom-left corner.
        For 2 points:
          - Orders from bottom / start to top / end.

        :param points: list of (x, y, z) tuples or Point3d-like objects.
        :return: list of (x, y, z) float tuples.
        """
        if not points:
            return []

        clean_pts = []
        for p in points:
            try:
                clean_pts.append((float(p[0]), float(p[1]), float(p[2])))
            except Exception:
                try:
                    clean_pts.append((float(p.X), float(p.Y), float(p.Z)))
                except Exception:
                    pass

        count = len(clean_pts)
        if count <= 1:
            return clean_pts

        if count == 2:
            return sorted(clean_pts, key=lambda p: (round(p[2], 4), round(p[0], 4), round(p[1], 4)))

        # 3 or 4 points: compute centroid
        cx = sum(p[0] for p in clean_pts) / float(count)
        cy = sum(p[1] for p in clean_pts) / float(count)
        cz = sum(p[2] for p in clean_pts) / float(count)

        # Estimate face normal from first three non-collinear vectors
        p0 = clean_pts[0]
        p1 = clean_pts[1]
        p2 = clean_pts[2]
        v1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        v2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])

        nx = v1[1] * v2[2] - v1[2] * v2[1]
        ny = v1[2] * v2[0] - v1[0] * v2[2]
        nz = v1[0] * v2[1] - v1[1] * v2[0]
        len_n = math.sqrt(nx * nx + ny * ny + nz * nz)

        if len_n < 1e-9:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx /= len_n
            ny /= len_n
            nz /= len_n

        # Standardize normal direction towards World +Z (or +Y if nearly vertical)
        if abs(nz) > 0.4:
            if nz < 0:
                nx, ny, nz = -nx, -ny, -nz
        elif abs(ny) > 0.4:
            if ny < 0:
                nx, ny, nz = -nx, -ny, -nz
        elif nx < 0:
            nx, ny, nz = -nx, -ny, -nz

        # Construct local orthonormal basis (u, v) on the panel plane
        ref_up = (0.0, 1.0, 0.0) if abs(nz) > 0.9 else (0.0, 0.0, 1.0)
        ux = ref_up[1] * nz - ref_up[2] * ny
        uy = ref_up[2] * nx - ref_up[0] * nz
        uz = ref_up[0] * ny - ref_up[1] * nx
        len_u = math.sqrt(ux * ux + uy * uy + uz * uz)
        if len_u < 1e-9:
            ux, uy, uz = 1.0, 0.0, 0.0
        else:
            ux /= len_u
            uy /= len_u
            uz /= len_u

        vx = ny * uz - nz * uy
        vy = nz * ux - nx * uz
        vz = nx * uy - ny * ux

        # Project points to 2D (u, v) and sort counter-clockwise by polar angle
        projected = []
        for p in clean_pts:
            dx = p[0] - cx
            dy = p[1] - cy
            dz = p[2] - cz
            u_coord = dx * ux + dy * uy + dz * uz
            v_coord = dx * vx + dy * vy + dz * vz
            ang = math.atan2(v_coord, u_coord)
            projected.append((ang, u_coord, v_coord, p))

        # Sort counter-clockwise
        projected.sort(key=lambda item: item[0])

        # Rotate sequence so index 0 is consistently bottom-left (lowest v, then lowest u)
        min_idx = min(range(len(projected)), key=lambda i: (projected[i][2], projected[i][1]))
        ordered = projected[min_idx:] + projected[:min_idx]
        return [item[3] for item in ordered]

    def _extract_adaptive_and_parametric_features(self, scanned, geom, working_brep):
        """Extracts candidate adaptive component vertices and dimension-driving parameters."""
        # 1. Parametric driving dimensions
        scanned.parametric_dims = {
            "Width": round(scanned.dx, 4),
            "Depth": round(scanned.dy, 4),
            "Height": round(scanned.dz, 4),
            "Length": round(max(scanned.dx, scanned.dy, scanned.dz), 4),
            "Thickness": round(scanned.thickness if scanned.thickness > 0 else min(scanned.dx, scanned.dy, scanned.dz), 4)
        }

        # 2. Extract Adaptive Points
        raw_pts = []
        try:
            # Case A: Single-face Brep or Surface (e.g. quad or triangular facade panels)
            if working_brep and hasattr(working_brep, "Faces") and working_brep.Faces.Count == 1:
                face = working_brep.Faces[0]
                loop = face.OuterLoop
                if loop and hasattr(loop, "Trims"):
                    for trim in loop.Trims:
                        pt = trim.PointAtStart
                        raw_pts.append((pt.X, pt.Y, pt.Z))
                elif hasattr(working_brep, "Vertices") and working_brep.Vertices.Count in (3, 4):
                    for v in working_brep.Vertices:
                        pt = v.Location
                        raw_pts.append((pt.X, pt.Y, pt.Z))

            # Case B: Mesh Face (single quad or triangle mesh)
            elif isinstance(geom, rg.Mesh):
                if hasattr(geom, "Vertices") and geom.Vertices.Count in (3, 4) and geom.Faces.Count <= 2:
                    for v in geom.Vertices:
                        raw_pts.append((v.X, v.Y, v.Z))

            # Case C: Curves (Polyline with 3-4 segments, or 2-point LineCurve for struts)
            elif isinstance(geom, rg.Curve):
                if hasattr(geom, "ToPolyline"):
                    poly = geom.ToPolyline()
                    if poly:
                        poly_pts = [(p.X, p.Y, p.Z) for p in poly]
                        if len(poly_pts) >= 4 and poly_pts[0] == poly_pts[-1]:
                            poly_pts = poly_pts[:-1]
                        if len(poly_pts) in (2, 3, 4):
                            raw_pts = poly_pts
                if not raw_pts and hasattr(geom, "PointAtStart") and hasattr(geom, "PointAtEnd"):
                    p0 = geom.PointAtStart
                    p1 = geom.PointAtEnd
                    raw_pts = [(p0.X, p0.Y, p0.Z), (p1.X, p1.Y, p1.Z)]

        except Exception:
            pass

        # Deduplicate sequential floating coordinates
        dedup_pts = []
        for p in raw_pts:
            if not dedup_pts or any(abs(p[i] - dedup_pts[-1][i]) > 1e-4 for i in range(3)):
                dedup_pts.append(p)
        if len(dedup_pts) >= 3 and dedup_pts[0] == dedup_pts[-1]:
            dedup_pts = dedup_pts[:-1]

        if len(dedup_pts) in (2, 3, 4):
            ordered_pts = self.order_adaptive_points_ccw(dedup_pts)
            scanned.adaptive_points = ordered_pts
            scanned.adaptive_point_count = len(ordered_pts)

    def _compute_level_relationship(self, scanned, revit_levels):
        """Finds nearest Revit Level and calculates vertical offset delta (dz_to_level)."""
        if not revit_levels:
            scanned.nearest_level_name = "Level 1"
            scanned.nearest_level_elevation = 0.0
            scanned.dz_to_level = scanned.bbox_min[2]
            return

        base_z = scanned.bbox_min[2]
        closest_level = None
        min_delta = float("inf")

        for lvl in revit_levels:
            elev = lvl.get("elevation", 0.0)
            delta = abs(base_z - elev)
            if delta < min_delta:
                min_delta = delta
                closest_level = lvl

        if closest_level:
            scanned.nearest_level_name = closest_level.get("name", "Level")
            scanned.nearest_level_elevation = closest_level.get("elevation", 0.0)
            scanned.dz_to_level = round(base_z - scanned.nearest_level_elevation, 4)

    def _classify_building_zone(self, scanned, min_doc_z, total_height):
        """Identifies architectural building zone based on elevation and layer semantics."""
        layer_lower = scanned.layer_full_path.lower()
        obj_name_lower = scanned.object_name.lower()

        # 1. Veil / Screen Envelope Check (Architectural facade screen / mesh envelope)
        veil_keywords = ["veil", "velo", "mesh", "malla", "screen", "gelosia", "celosia", "facade_screen", "inox"]
        if any(kw in layer_lower or kw in obj_name_lower for kw in veil_keywords):
            scanned.building_zone = "Veil Envelope"
            return

        # 2. Amenity / Sky-Pool Check
        amenity_keywords = ["pool", "piscina", "amenity", "spa", "gym", "sky_pool", "terraza", "terrace"]
        if any(kw in layer_lower or kw in obj_name_lower for kw in amenity_keywords):
            scanned.building_zone = "Sky-Pool/Amenity"
            return

        # 3. Elevation envelope percentile
        base_z = scanned.bbox_min[2]
        norm_z = (base_z - min_doc_z) / max(1.0, total_height)

        if norm_z <= 0.20 or any(kw in layer_lower for kw in ["podium", "pb", "planta_baixa", "ground", "lobby"]):
            scanned.building_zone = "Podium"
        elif norm_z >= 0.88 or any(kw in layer_lower for kw in ["crown", "roof", "cubierta", "coberta", "top"]):
            scanned.building_zone = "Crown"
        else:
            scanned.building_zone = "Typical Tower"

    def _compute_geo_hash(self, scanned, geom):
        """Computes deterministic SHA-256 fingerprint from geometry topology, coordinates and metrics."""
        hasher = hashlib.sha256()

        # Type token
        hasher.update(scanned.object_type.encode("utf-8"))

        # Quantized Bounding Box Coordinates (rounded to 2 decimal places to absorb floating epsilon)
        bbox_str = "{:.2f},{:.2f},{:.2f}_{:.2f},{:.2f},{:.2f}".format(
            scanned.bbox_min[0], scanned.bbox_min[1], scanned.bbox_min[2],
            scanned.bbox_max[0], scanned.bbox_max[1], scanned.bbox_max[2]
        )
        hasher.update(bbox_str.encode("utf-8"))

        # Quantized Metrics
        metric_str = "A{:.2f}_V{:.2f}_L{}_{}".format(
            scanned.area, scanned.volume, scanned.loop_count, scanned.is_closed
        )
        hasher.update(metric_str.encode("utf-8"))

        # Object specific detail
        if scanned.object_type == "InstanceReference":
            hasher.update(scanned.block_name.encode("utf-8"))
        elif geom is not None:
            if isinstance(geom, rg.Brep):
                fc = geom.Faces.Count if hasattr(geom, "Faces") else 0
                ec = geom.Edges.Count if hasattr(geom, "Edges") else 0
                hasher.update("Faces{}_Edges{}".format(fc, ec).encode("utf-8"))
            elif isinstance(geom, rg.Mesh):
                vc = geom.Vertices.Count if hasattr(geom, "Vertices") else 0
                fc = geom.Faces.Count if hasattr(geom, "Faces") else 0
                hasher.update("V{}_F{}".format(vc, fc).encode("utf-8"))
            elif isinstance(geom, rg.Curve):
                try:
                    crv_len = geom.GetLength() if getattr(geom, "IsValid", True) else 0.0
                except Exception:
                    crv_len = 0.0
                hasher.update("Len{:.2f}".format(crv_len).encode("utf-8"))

        return hasher.hexdigest()
