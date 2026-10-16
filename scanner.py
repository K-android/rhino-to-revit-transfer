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

        # Topological flags
        self.is_closed = False
        self.is_planar = False
        self.is_horizontal = False
        self.is_vertical = False
        self.thickness = 0.0
        self.loop_count = 0
        self.repetition_signature = ""
        self.geo_hash = ""

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
            "area": self.area,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "is_planar": self.is_planar,
            "is_horizontal": self.is_horizontal,
            "is_vertical": self.is_vertical,
            "thickness": self.thickness,
            "loop_count": self.loop_count,
            "repetition_signature": self.repetition_signature,
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

        rhino_objects = list(self.doc.Objects.GetObjectList(settings))
        scanned_list = []
        hash_registry = {}  # geo_hash -> ScannedObject for duplicate detection

        # Determine document bounds for building zone vertical normalization
        min_doc_z = float("inf")
        max_doc_z = float("-inf")

        # Initial pass: gather raw objects and determine project height envelope
        valid_objs = []
        for obj in rhino_objects:
            if not obj or not obj.Geometry:
                continue

            # Layer visibility / locked filter check
            layer_idx = obj.Attributes.LayerIndex
            layer = self.doc.Layers[layer_idx]
            if not include_hidden and (not layer.IsVisible or obj.IsHidden):
                continue
            if not include_locked and (layer.IsLocked or obj.IsLocked):
                continue

            geom = obj.Geometry
            bbox = geom.GetBoundingBox(True)
            if not bbox.IsValid:
                continue

            if bbox.Min.Z < min_doc_z:
                min_doc_z = bbox.Min.Z
            if bbox.Max.Z > max_doc_z:
                max_doc_z = bbox.Max.Z

            valid_objs.append(obj)

        total_height = max(1.0, max_doc_z - min_doc_z) if valid_objs else 1.0

        for obj in valid_objs:
            scanned = self._process_rhino_object(obj, scale_to_m, revit_levels, min_doc_z, total_height)
            if not scanned:
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
            scanned.material_name = "ByLayer"

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
            elif isinstance(geom, rg.InstanceReferenceGeometry):
                idef = self.doc.InstanceDefinitions.FindId(geom.ParentIdefId)
                scanned.block_name = idef.Name if idef else ""
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
        self._compute_topology_fingerprint(scanned, geom)

        # Spatial Level Mapping
        self._compute_level_relationship(scanned, revit_levels)

        # Building Zone Classification
        self._classify_building_zone(scanned, min_doc_z, total_height)

        # Compute Deterministic SHA-256 Geo Hash
        scanned.geo_hash = self._compute_geo_hash(scanned, geom)

        return scanned

    def _compute_topology_fingerprint(self, scanned, geom):
        """Analyzes topological orientation, thickness, planarity, and loop count."""
        # Brep / Extrusion
        working_brep = None
        if isinstance(geom, rg.Brep):
            working_brep = geom
        elif isinstance(geom, rg.Extrusion):
            working_brep = geom.ToBrep()

        if working_brep:
            scanned.is_closed = working_brep.IsSolid
            # Approximate volume and area
            mp = rg.MassProperties.Compute(working_brep)
            if mp:
                scanned.area = round(mp.Area, 3)
                scanned.volume = round(mp.Volume, 3) if working_brep.IsSolid else 0.0

            # Orientation analysis: inspect face normals
            horiz_face_count = 0
            vert_face_count = 0
            face_count = working_brep.Faces.Count

            for face in working_brep.Faces:
                # Sample center uv normal
                u_mid = 0.5 * (face.Domain(0).Min + face.Domain(0).Max)
                v_mid = 0.5 * (face.Domain(1).Min + face.Domain(1).Max)
                normal = face.NormalAt(u_mid, v_mid)
                normal.Unitize()

                # Parallel to World Z (Horizontal plane)
                if abs(normal.Z) > 0.95:
                    horiz_face_count += 1
                # Perpendicular to World Z (Vertical wall)
                elif abs(normal.Z) < 0.05:
                    vert_face_count += 1

            if face_count > 0:
                scanned.is_horizontal = (horiz_face_count / float(face_count)) > 0.3 or (scanned.dz < min(scanned.dx, scanned.dy) * 0.4 and horiz_face_count >= 1)
                scanned.is_vertical = (vert_face_count / float(face_count)) > 0.3 or (scanned.dz >= 1.5 and vert_face_count >= 1)

            # Planarity check
            scanned.is_planar = working_brep.Faces.Count == 1 and working_brep.Faces[0].IsPlanar(0.01)

            # Loop count (outer + inner holes)
            total_loops = 0
            for face in working_brep.Faces:
                total_loops += face.Loops.Count
            scanned.loop_count = total_loops

            # Thickness estimate
            sorted_dims = sorted([scanned.dx, scanned.dy, scanned.dz])
            scanned.thickness = sorted_dims[0]

            if not scanned.is_closed and (scanned.volume > 0 or working_brep.Faces.Count > 1):
                scanned.has_open_loops = True
                scanned.preflight_warnings.append("Non-manifold or open Brep edges detected.")

        elif isinstance(geom, rg.Mesh):
            scanned.is_closed = geom.IsClosed
            scanned.is_planar = False
            scanned.area = round(rg.AreaMassProperties.Compute(geom).Area if rg.AreaMassProperties.Compute(geom) else 0.0, 3)
            scanned.volume = round(rg.VolumeMassProperties.Compute(geom).Volume if (geom.IsClosed and rg.VolumeMassProperties.Compute(geom)) else 0.0, 3)
            scanned.thickness = min(scanned.dx, scanned.dy, scanned.dz)
            scanned.repetition_signature = "Mesh_V{}_F{}".format(geom.Vertices.Count, geom.Faces.Count)

        elif isinstance(geom, rg.Curve):
            scanned.is_closed = geom.IsClosed
            scanned.is_planar = geom.IsPlanar(0.001)
            scanned.loop_count = 1 if geom.IsClosed else 0
            scanned.area = 0.0
            scanned.volume = 0.0
            if not geom.IsClosed:
                scanned.has_open_loops = True
                scanned.preflight_warnings.append("Open curve detected (cannot form closed boundary).")

        elif scanned.object_type == "InstanceReference":
            scanned.is_closed = True
            scanned.repetition_signature = "Block_{}".format(scanned.block_name)

        if not scanned.repetition_signature:
            # Normalized shape signature: dims rounded + volume
            scanned.repetition_signature = "{:.2f}x{:.2f}x{:.2f}_V{:.2f}".format(
                scanned.dx, scanned.dy, scanned.dz, scanned.volume
            )

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
                hasher.update("Faces{}_Edges{}".format(geom.Faces.Count, geom.Edges.Count).encode("utf-8"))
            elif isinstance(geom, rg.Mesh):
                hasher.update("V{}_F{}".format(geom.Vertices.Count, geom.Faces.Count).encode("utf-8"))
            elif isinstance(geom, rg.Curve):
                hasher.update("Len{:.2f}".format(geom.GetLength()).encode("utf-8"))

        return hasher.hexdigest()
