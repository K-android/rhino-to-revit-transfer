using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using Rhino;
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
        private readonly Dictionary<string, FamilySymbol> _familySymbolCache = new(StringComparer.OrdinalIgnoreCase);

        public ConversionRouter(Document doc)
        {
            _doc = doc;
            _defaultFloorType = _doc.GetDefaultElementTypeId(ElementTypeGroup.FloorType);
            _defaultWallType = _doc.GetDefaultElementTypeId(ElementTypeGroup.WallType);
            _defaultCeilingType = _doc.GetDefaultElementTypeId(ElementTypeGroup.CeilingType);
        }

        /// <summary>
        /// Phase 1: Pre-Transaction .rfa Family Generation & Loading.
        /// Runs BEFORE the main project Transaction/TransactionGroup is started.
        /// </summary>
        public void PreGenerateFamilies(IEnumerable<ScannedObject> scannedObjects, Dictionary<string, ClassificationResult> classifications)
        {
            var app = _doc.Application;

            foreach (var scanned in scannedObjects)
            {
                if (!classifications.TryGetValue(scanned.RhinoGuid, out var cResult)) continue;
                string strategy = cResult.ProposedStrategy;

                if (strategy == "ParametricFamily")
                {
                    string famKey = $"R2R_Parametric_{scanned.LayerName}";
                    if (!_familySymbolCache.ContainsKey(famKey))
                    {
                        double scale = Rhino.RhinoMath.UnitScale(RhinoDoc.ActiveDoc?.ModelUnitSystem ?? Rhino.UnitSystem.Meters, Rhino.UnitSystem.Feet);
                        double w = Math.Max(0.5, scanned.Dx * scale);
                        double d = Math.Max(0.5, scanned.Dy * scale);
                        double h = Math.Max(0.5, scanned.Dz * scale);

                        var sym = FamilyFactory.CreateParametricBoxFamily(app, _doc, famKey, w, d, h, categoryHint: cResult.InferredCategory);
                        if (sym != null)
                        {
                            _familySymbolCache[famKey] = sym;
                        }
                    }
                }
                else if (strategy == "RepetitiveFamily")
                {
                    string famKey = !string.IsNullOrEmpty(scanned.BlockName) 
                        ? $"R2R_Block_{scanned.BlockName}" 
                        : $"R2R_Repetitive_{scanned.RepetitionSignature}";

                    if (!_familySymbolCache.ContainsKey(famKey) && scanned.RawGeometry != null)
                    {
                        double scale = Rhino.RhinoMath.UnitScale(RhinoDoc.ActiveDoc?.ModelUnitSystem ?? Rhino.UnitSystem.Meters, Rhino.UnitSystem.Feet);
                        var localGeom = scanned.RawGeometry.Duplicate();
                        localGeom.Translate(-scanned.Centroid.X, -scanned.Centroid.Y, -scanned.Centroid.Z);
                        localGeom.Scale(scale);

                        Solid? revitSolid = GeometryEncoder.ToSolid(localGeom);
                        if (revitSolid != null && revitSolid.Volume > 0.0001)
                        {
                            var sym = FamilyFactory.CreateNativeFreeFormFamily(app, _doc, famKey, revitSolid, subcategoryName: scanned.LayerName, categoryHint: cResult.InferredCategory);
                            if (sym != null)
                            {
                                _familySymbolCache[famKey] = sym;
                            }
                        }
                    }
                }
            }
        }

        /// <summary>
        /// Phase 2: Project Placement & Parameter Driving.
        /// Runs INSIDE project Transaction.
        /// </summary>
        public Element? Convert(ScannedObject scanned, ClassificationResult classification, ElementId levelId)
        {
            if (scanned.RawGeometry == null) return null;

            double scale = Rhino.RhinoMath.UnitScale(RhinoDoc.ActiveDoc?.ModelUnitSystem ?? Rhino.UnitSystem.Meters, Rhino.UnitSystem.Feet);
            var geomFeet = scanned.RawGeometry.Duplicate();
            geomFeet.Scale(scale);

            return classification.ProposedStrategy switch
            {
                "ParametricFamily" => CreateParametricFamilyInstance(scanned, classification, levelId, scale),
                "RepetitiveFamily" => CreateRepetitiveFamilyInstance(scanned, classification, levelId, scale),
                "FloorNative" => CreateNativeFloor(scanned, geomFeet, levelId),
                "WallByCurve" => CreateWallByCurve(scanned, geomFeet, levelId),
                "Toposolid" => CreateToposolid(scanned, geomFeet, levelId),
                _ => CreateDirectShape(scanned, geomFeet, classification.RevitCategoryName)
            };
        }

        private Element? CreateParametricFamilyInstance(ScannedObject scanned, ClassificationResult classification, ElementId levelId, double scale)
        {
            string famKey = $"R2R_Parametric_{scanned.LayerName}";
            if (_familySymbolCache.TryGetValue(famKey, out var sym) && sym != null)
            {
                try
                {
                    if (!sym.IsActive) sym.Activate();

                    var level = _doc.GetElement(levelId) as Level;
                    var pt = new XYZ(scanned.Centroid.X * scale, scanned.Centroid.Y * scale, scanned.Centroid.Z * scale);
                    var inst = _doc.Create.NewFamilyInstance(pt, sym, level, StructuralType.NonStructural);

                    if (scanned.RotationAngle != 0 && inst != null)
                    {
                        var axis = Line.CreateBound(pt, new XYZ(pt.X, pt.Y, pt.Z + 1.0));
                        ElementTransformUtils.RotateElement(_doc, inst.Id, axis, scanned.RotationAngle);
                    }

                    // Set instance parameters: Width, Depth, Height
                    inst?.LookupParameter("Width")?.Set(scanned.Dx * scale);
                    inst?.LookupParameter("Depth")?.Set(scanned.Dy * scale);
                    inst?.LookupParameter("Height")?.Set(scanned.Dz * scale);

                    return inst;
                }
                catch { }
            }

            // Fallback to DirectShape if family instance placement failed
            var geomFeet = scanned.RawGeometry.Duplicate();
            geomFeet.Scale(scale);
            return CreateDirectShape(scanned, geomFeet, classification.RevitCategoryName);
        }

        private Element? CreateRepetitiveFamilyInstance(ScannedObject scanned, ClassificationResult classification, ElementId levelId, double scale)
        {
            string famKey = !string.IsNullOrEmpty(scanned.BlockName) 
                ? $"R2R_Block_{scanned.BlockName}" 
                : $"R2R_Repetitive_{scanned.RepetitionSignature}";

            if (_familySymbolCache.TryGetValue(famKey, out var sym) && sym != null)
            {
                try
                {
                    if (!sym.IsActive) sym.Activate();

                    var level = _doc.GetElement(levelId) as Level;
                    var pt = new XYZ(scanned.Centroid.X * scale, scanned.Centroid.Y * scale, scanned.Centroid.Z * scale);
                    var inst = _doc.Create.NewFamilyInstance(pt, sym, level, StructuralType.NonStructural);

                    if (scanned.RotationAngle != 0 && inst != null)
                    {
                        var axis = Line.CreateBound(pt, new XYZ(pt.X, pt.Y, pt.Z + 1.0));
                        ElementTransformUtils.RotateElement(_doc, inst.Id, axis, scanned.RotationAngle);
                    }

                    return inst;
                }
                catch { }
            }

            var geomFeet = scanned.RawGeometry.Duplicate();
            geomFeet.Scale(scale);
            return CreateDirectShape(scanned, geomFeet, classification.RevitCategoryName);
        }

        private Element? CreateToposolid(ScannedObject scanned, GeometryBase geomFeet, ElementId levelId)
        {
            // DirectShape with OST_Toposolid or native Toposolid
            return CreateDirectShape(scanned, geomFeet, "OST_Toposolid");
        }

        private Element? CreateNativeFloor(ScannedObject scanned, GeometryBase geomFeet, ElementId levelId)
        {
            if (geomFeet is not Brep brep) return CreateDirectShape(scanned, geomFeet, "OST_Floors");

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
            BuiltInCategory bic = BuiltInCategory.OST_GenericModel;
            string cleanCat = categoryName.Trim();

            if (cleanCat.Equals("OST_Floors", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Floors", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_Floors;
            else if (cleanCat.Equals("OST_Walls", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Walls", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_Walls;
            else if (cleanCat.Equals("OST_Roofs", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Roofs", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_Roofs;
            else if (cleanCat.Equals("OST_Ceilings", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Ceilings", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_Ceilings;
            else if (cleanCat.Equals("OST_Toposolid", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Toposolid", StringComparison.OrdinalIgnoreCase))
            {
#if NET8_0_OR_GREATER || REVIT2024_OR_GREATER
                bic = BuiltInCategory.OST_Toposolid;
#else
                bic = BuiltInCategory.OST_Topography;
#endif
            }
            else if (cleanCat.Equals("OST_Columns", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Columns", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_Columns;
            else if (cleanCat.Equals("OST_StructuralFraming", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Structural Framing", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_StructuralFraming;
            else if (cleanCat.Equals("OST_CurtainWallPanels", StringComparison.OrdinalIgnoreCase) || cleanCat.Equals("Curtain Panels", StringComparison.OrdinalIgnoreCase))
                bic = BuiltInCategory.OST_CurtainWallPanels;

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
