using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoToRevitTransfer
{
    public class ScannedObject
    {
        public Guid RhinoGuid { get; set; }
        public string LayerFullPath { get; set; } = string.Empty;
        public string LayerName { get; set; } = string.Empty;
        public string ObjectName { get; set; } = string.Empty;
        public string BlockName { get; set; } = string.Empty;
        public string ObjectType { get; set; } = "Unknown";
        public Dictionary<string, string> UserText { get; set; } = new Dictionary<string, string>();

        // Geometry Fingerprint
        public Point3d BoundingBoxMin { get; set; }
        public Point3d BoundingBoxMax { get; set; }
        public Point3d Centroid { get; set; }
        public double Dx { get; set; }
        public double Dy { get; set; }
        public double Dz { get; set; }
        public double Area { get; set; }
        public double Volume { get; set; }

        public bool IsClosed { get; set; }
        public bool IsPlanar { get; set; }
        public bool IsHorizontal { get; set; }
        public bool IsVertical { get; set; }
        public double Thickness { get; set; }
        public int LoopCount { get; set; }
        public string RepetitionSignature { get; set; } = string.Empty;
        public string GeoHash { get; set; } = string.Empty;

        // Spatial Context
        public string NearestLevelName { get; set; } = "Level 1";
        public double NearestLevelElevation { get; set; }
        public double DzToLevel { get; set; }
        public string BuildingZone { get; set; } = "General";

        // Preflight
        public double OriginDistance { get; set; }
        public bool IsFarFromOrigin { get; set; }
        public bool IsDuplicate { get; set; }
        public List<string> Warnings { get; set; } = new List<string>();

        public GeometryBase? RawGeometry { get; set; }
    }

    public class RhinoScanner
    {
        private const double OriginLimitMeters = 16000.0;
        private readonly RhinoDoc _doc;

        public RhinoScanner(RhinoDoc? doc = null)
        {
            _doc = doc ?? RhinoDoc.ActiveDoc;
        }

        public double GetUnitScaleToMeters()
        {
            if (_doc == null) return 1.0;
            return RhinoMath.UnitScale(_doc.ModelUnitSystem, UnitSystem.Meters);
        }

        public List<ScannedObject> ScanAll(bool includeHidden = false, bool includeLocked = false, List<(string Name, double Elevation)>? levels = null)
        {
            var results = new List<ScannedObject>();
            if (_doc == null) return results;

            double scaleToM = GetUnitScaleToMeters();
            var settings = new ObjectEnumeratorSettings
            {
                IncludeLights = false,
                IncludeGrips = false,
                NormalObjects = true,
                HiddenObjects = includeHidden,
                LockedObjects = includeLocked
            };

            var hashRegistry = new Dictionary<string, ScannedObject>();
            var rhinoObjects = _doc.Objects.GetObjectList(settings);

            foreach (var rhObj in rhinoObjects)
            {
                if (rhObj?.Geometry == null) continue;

                var layer = _doc.Layers[rhObj.Attributes.LayerIndex];
                if (!includeHidden && (!layer.IsVisible || rhObj.IsHidden)) continue;
                if (!includeLocked && (layer.IsLocked || rhObj.IsLocked)) continue;

                var scanned = ProcessObject(rhObj, scaleToM, levels);
                if (scanned == null) continue;

                // Duplicate Hash Check
                if (hashRegistry.TryGetValue(scanned.GeoHash, out var existing))
                {
                    scanned.IsDuplicate = true;
                    scanned.Warnings.Add($"Duplicate geometry matching GUID: {existing.RhinoGuid}");
                }
                else
                {
                    hashRegistry[scanned.GeoHash] = scanned;
                }

                results.Add(scanned);
            }

            return results;
        }

        private ScannedObject? ProcessObject(RhinoObject rhObj, double scaleToM, List<(string Name, double Elevation)>? levels)
        {
            var geom = rhObj.Geometry;
            var bbox = geom.GetBoundingBox(true);
            if (!bbox.IsValid) return null;

            var scanned = new ScannedObject
            {
                RhinoGuid = rhObj.Id,
                LayerFullPath = _doc.Layers[rhObj.Attributes.LayerIndex].FullPath,
                LayerName = _doc.Layers[rhObj.Attributes.LayerIndex].Name,
                ObjectName = rhObj.Attributes.Name ?? string.Empty,
                RawGeometry = geom,
                BoundingBoxMin = bbox.Min,
                BoundingBoxMax = bbox.Max,
                Centroid = bbox.Center,
                Dx = Math.Max(0.001, bbox.Max.X - bbox.Min.X),
                Dy = Math.Max(0.001, bbox.Max.Y - bbox.Min.Y),
                Dz = Math.Max(0.001, bbox.Max.Z - bbox.Min.Z)
            };

            // Origin Distance (>16km)
            double distOriginMeters = bbox.Center.DistanceTo(Point3d.Origin) * scaleToM;
            scanned.OriginDistance = distOriginMeters;
            if (distOriginMeters > OriginLimitMeters)
            {
                scanned.IsFarFromOrigin = true;
                scanned.Warnings.Add($"Centroid is {distOriginMeters / 1000.0:F1} km from origin (exceeds 16km Revit limit)");
            }

            // Topological orientation
            ComputeTopology(scanned, geom);

            // Spatial Levels
            ComputeLevels(scanned, levels);

            // SHA-256 GeoHash
            scanned.GeoHash = ComputeGeoHash(scanned);

            return scanned;
        }

        private void ComputeTopology(ScannedObject scanned, GeometryBase geom)
        {
            if (geom is Brep brep)
            {
                scanned.ObjectType = "Brep";
                scanned.IsClosed = brep.IsSolid;
                var amp = AreaMassProperties.Compute(brep);
                if (amp != null) scanned.Area = Math.Round(amp.Area, 3);
                if (brep.IsSolid)
                {
                    var vmp = VolumeMassProperties.Compute(brep);
                    if (vmp != null) scanned.Volume = Math.Round(vmp.Volume, 3);
                }

                int horizCount = 0, vertCount = 0;
                foreach (var face in brep.Faces)
                {
                    var uMid = 0.5 * (face.Domain(0).Min + face.Domain(0).Max);
                    var vMid = 0.5 * (face.Domain(1).Min + face.Domain(1).Max);
                    var norm = face.NormalAt(uMid, vMid);
                    norm.Unitize();

                    if (Math.Abs(norm.Z) > 0.95) horizCount++;
                    else if (Math.Abs(norm.Z) < 0.05) vertCount++;
                }

                scanned.IsHorizontal = (horizCount / (double)brep.Faces.Count) > 0.3 || (scanned.Dz < Math.Min(scanned.Dx, scanned.Dy) * 0.4);
                scanned.IsVertical = (vertCount / (double)brep.Faces.Count) > 0.3 || (scanned.Dz >= 1.5 && Math.Min(scanned.Dx, scanned.Dy) <= 0.6);
            }
            else if (geom is Mesh mesh)
            {
                scanned.ObjectType = "Mesh";
                scanned.IsClosed = mesh.IsClosed;
            }
            else if (geom is Curve crv)
            {
                scanned.ObjectType = "Curve";
                scanned.IsClosed = crv.IsClosed;
                scanned.IsPlanar = crv.IsPlanar();
            }
        }

        private void ComputeLevels(ScannedObject scanned, List<(string Name, double Elevation)>? levels)
        {
            if (levels == null || levels.Count == 0) return;
            double baseZ = scanned.BoundingBoxMin.Z;
            double minDelta = double.MaxValue;

            foreach (var lvl in levels)
            {
                double delta = Math.Abs(baseZ - lvl.Elevation);
                if (delta < minDelta)
                {
                    minDelta = delta;
                    scanned.NearestLevelName = lvl.Name;
                    scanned.NearestLevelElevation = lvl.Elevation;
                    scanned.DzToLevel = Math.Round(baseZ - lvl.Elevation, 3);
                }
            }
        }

        private string ComputeGeoHash(ScannedObject scanned)
        {
            using var sha = SHA256.Create();
            string raw = $"{scanned.ObjectType}_{scanned.BoundingBoxMin.X:F2},{scanned.BoundingBoxMin.Y:F2},{scanned.BoundingBoxMin.Z:F2}_" +
                         $"{scanned.BoundingBoxMax.X:F2},{scanned.BoundingBoxMax.Y:F2},{scanned.BoundingBoxMax.Z:F2}_" +
                         $"{scanned.Area:F2}_{scanned.Volume:F2}_{scanned.IsClosed}";
            byte[] bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(raw));
            var sb = new StringBuilder();
            foreach (byte b in bytes) sb.Append(b.ToString("x2"));
            return sb.ToString();
        }
    }
}
