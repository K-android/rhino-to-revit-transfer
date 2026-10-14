using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Rhino.Geometry;
using RhinoInside.Revit.Convert.Geometry;

namespace RhinoToRevitTransfer
{
    public class TransferFailuresPreprocessor : IFailuresPreprocessor
    {
        public List<string> SuppressedWarnings { get; } = new List<string>();

        public FailureProcessingResult PreprocessFailures(FailuresAccessor failuresAccessor)
        {
            var failures = failuresAccessor.GetFailureMessages();
            foreach (var f in failures)
            {
                if (f.GetSeverity() == FailureSeverity.Warning)
                {
                    SuppressedWarnings.Add(f.GetDescriptionText());
                    failuresAccessor.DeleteWarning(f);
                }
                else if (f.GetSeverity() == FailureSeverity.Error && f.HasResolutions())
                {
                    failuresAccessor.ResolveFailure(f);
                    return FailureProcessingResult.ProceedWithCommit;
                }
            }
            return FailureProcessingResult.Continue;
        }
    }

    public class ConversionRouter
    {
        private readonly Document _doc;
        private readonly ElementId _defaultFloorType;
        private readonly ElementId _defaultWallType;
        private readonly ElementId _defaultCeilingType;

        public ConversionRouter(Document doc)
        {
            _doc = doc;
            _defaultFloorType = _doc.GetDefaultElementTypeId(ElementTypeGroup.FloorType);
            _defaultWallType = _doc.GetDefaultElementTypeId(ElementTypeGroup.WallType);
            _defaultCeilingType = _doc.GetDefaultElementTypeId(ElementTypeGroup.CeilingType);
        }

        public Element? Convert(ScannedObject scanned, ClassificationResult classification, ElementId levelId)
        {
            if (scanned.RawGeometry == null) return null;

            // Scale geometry from Rhino doc units to Revit internal feet
            double scale = Rhino.RhinoMath.UnitScale(RhinoDoc.ActiveDoc.ModelUnitSystem, Rhino.UnitSystem.Feet);
            var geomFeet = scanned.RawGeometry.Duplicate();
            geomFeet.Scale(scale);

            return classification.ProposedStrategy switch
            {
                "FloorNative" => CreateNativeFloor(scanned, geomFeet, levelId),
                "WallByCurve" => CreateWallByCurve(scanned, geomFeet, levelId),
                _ => CreateDirectShape(scanned, geomFeet, classification.RevitCategoryName)
            };
        }

        private Element? CreateNativeFloor(ScannedObject scanned, GeometryBase geomFeet, ElementId levelId)
        {
            if (geomFeet is not Brep brep) return CreateDirectShape(scanned, geomFeet, "OST_Floors");

            // Find top planar face
            BrepFace? topFace = null;
            double maxZ = double.MinValue;
            foreach (var face in brep.Faces)
            {
                var norm = face.NormalAt(0.5 * (face.Domain(0).Min + face.Domain(0).Max), 0.5 * (face.Domain(1).Min + face.Domain(1).Max));
                norm.Unitize();
                if (norm.Z > 0.90 && face.GetBoundingBox(true).Center.Z > maxZ)
                {
                    maxZ = face.GetBoundingBox(true).Center.Z;
                    topFace = face;
                }
            }

            if (topFace == null) return CreateDirectShape(scanned, geomFeet, "OST_Floors");

            try
            {
                var curveLoops = new List<Autodesk.Revit.DB.CurveLoop>();
                var outerLoop = topFace.OuterLoop.To3dCurve();
                var cl = new Autodesk.Revit.DB.CurveLoop();

                var segments = outerLoop.DuplicateSegments();
                if (segments == null || segments.Length == 0) segments = new[] { outerLoop };

                foreach (var seg in segments)
                {
                    var p0 = new XYZ(seg.PointAtStart.X, seg.PointAtStart.Y, seg.PointAtStart.Z);
                    var p1 = new XYZ(seg.PointAtEnd.X, seg.PointAtEnd.Y, seg.PointAtEnd.Z);
                    if (p0.DistanceTo(p1) > 0.001)
                    {
                        cl.Append(Autodesk.Revit.DB.Line.CreateBound(p0, p1));
                    }
                }
                curveLoops.Add(cl);

                var floor = Floor.Create(_doc, curveLoops, _defaultFloorType, levelId);
                floor.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)?.Set(1);
                return floor;
            }
            catch
            {
                return CreateDirectShape(scanned, geomFeet, "OST_Floors");
            }
        }

        private Element? CreateWallByCurve(ScannedObject scanned, GeometryBase geomFeet, ElementId levelId)
        {
            var bbox = geomFeet.GetBoundingBox(true);
            double height = Math.Max(8.0, bbox.Max.Z - bbox.Min.Z);
            var p0 = new XYZ(bbox.Min.X, bbox.Center.Y, bbox.Min.Z);
            var p1 = new XYZ(bbox.Max.X, bbox.Center.Y, bbox.Min.Z);

            if (bbox.Max.Y - bbox.Min.Y > bbox.Max.X - bbox.Min.X)
            {
                p0 = new XYZ(bbox.Center.X, bbox.Min.Y, bbox.Min.Z);
                p1 = new XYZ(bbox.Center.X, bbox.Max.Y, bbox.Min.Z);
            }

            if (p0.DistanceTo(p1) < 0.1) return CreateDirectShape(scanned, geomFeet, "OST_Walls");

            try
            {
                var line = Autodesk.Revit.DB.Line.CreateBound(p0, p1);
                return Wall.Create(_doc, line, _defaultWallType, levelId, height, 0.0, false, false);
            }
            catch
            {
                return CreateDirectShape(scanned, geomFeet, "OST_Walls");
            }
        }

        private Element? CreateDirectShape(ScannedObject scanned, GeometryBase geomFeet, string categoryName)
        {
            var bic = categoryName switch
            {
                "OST_Floors" => BuiltInCategory.OST_Floors,
                "OST_Walls" => BuiltInCategory.OST_Walls,
                "OST_Roofs" => BuiltInCategory.OST_Roofs,
                "OST_Ceilings" => BuiltInCategory.OST_Ceilings,
                _ => BuiltInCategory.OST_GenericModel
            };

            var catId = new ElementId(bic);
            var ds = DirectShape.CreateElement(_doc, catId);

            var revitGeom = GeometryEncoder.ToGeometryObject(geomFeet);
            if (revitGeom != null)
            {
                ds.SetShape(new List<GeometryObject> { revitGeom });
            }

            return ds;
        }
    }
}
