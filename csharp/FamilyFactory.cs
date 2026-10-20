using System;
using System.IO;
using System.Collections.Generic;
using Autodesk.Revit.ApplicationServices;
using Autodesk.Revit.DB;
using Rhino;
using Rhino.Geometry;
using RhinoInside.Revit.Convert.Geometry;

namespace RhinoToRevitTransfer
{
    public class FamilyLoadOptions : IFamilyLoadOptions
    {
        public bool OnFamilyFound(bool familyInUse, out bool overwriteParameterValues)
        {
            overwriteParameterValues = true;
            return true;
        }

        public bool OnSharedFamilyFound(Family sharedFamily, bool familyInUse, out FamilySource source, out bool overwriteParameterValues)
        {
            source = FamilySource.Family;
            overwriteParameterValues = true;
            return true;
        }
    }

    public static class FamilyTemplateLocator
    {
        private static readonly Dictionary<string, string> CachedTemplates = new(StringComparer.OrdinalIgnoreCase);

        public static string? FindTemplate(Application app, string categoryHint = "Generic Model")
        {
            string hintKey = categoryHint.Trim();
            if (CachedTemplates.TryGetValue(hintKey, out var cached) && File.Exists(cached))
            {
                return cached;
            }

            var targetFilenames = new List<string>();
            string lowerHint = hintKey.ToLowerInvariant();
            if (lowerHint.Contains("column"))
            {
                targetFilenames.AddRange(new[] { "Metric Structural Column.rft", "Structural Column.rft", "Metric Column.rft" });
            }
            else if (lowerHint.Contains("curtain") || lowerHint.Contains("panel"))
            {
                targetFilenames.AddRange(new[] { "Metric Curtain Wall Panel.rft", "Curtain Wall Panel.rft" });
            }
            else if (lowerHint.Contains("mass"))
            {
                targetFilenames.AddRange(new[] { "Metric Mass.rft", "Mass.rft" });
            }

            targetFilenames.AddRange(new[] { "Metric Generic Model.rft", "Generic Model.rft", "Metric Model.rft" });

            var searchDirs = new List<string>();

            // 1. Revit Application configured path
            if (!string.IsNullOrEmpty(app.FamilyTemplatePath) && Directory.Exists(app.FamilyTemplatePath))
            {
                searchDirs.Add(app.FamilyTemplatePath);
                foreach (var sub in new[] { "English", "English-Imperial", "Metric", "Templates" })
                {
                    string subP = Path.Combine(app.FamilyTemplatePath, sub);
                    if (Directory.Exists(subP)) searchDirs.Add(subP);
                }
            }

            // 2. ProgramData across versions
            string progData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            foreach (var yr in new[] { "2027", "2026", "2025", "2024", "2023", "2022" })
            {
                string rvtRoot = Path.Combine(progData, "Autodesk", $"RVT {yr}", "Family Templates");
                if (Directory.Exists(rvtRoot))
                {
                    searchDirs.Add(rvtRoot);
                    foreach (var sub in new[] { "English", "English-Imperial", "Metric" })
                    {
                        string subP = Path.Combine(rvtRoot, sub);
                        if (Directory.Exists(subP)) searchDirs.Add(subP);
                    }
                }
            }

            // Direct check
            foreach (var d in searchDirs)
            {
                foreach (var fname in targetFilenames)
                {
                    string p = Path.Combine(d, fname);
                    if (File.Exists(p))
                    {
                        CachedTemplates[hintKey] = p;
                        return p;
                    }
                }
            }

            // Shallow directory walk
            foreach (var d in searchDirs)
            {
                try
                {
                    foreach (var p in Directory.EnumerateFiles(d, "*.rft", SearchOption.AllDirectories))
                    {
                        string fname = Path.GetFileName(p);
                        foreach (var target in targetFilenames)
                        {
                            if (string.Equals(fname, target, StringComparison.OrdinalIgnoreCase))
                            {
                                CachedTemplates[hintKey] = p;
                                return p;
                            }
                        }
                    }
                }
                catch { }
            }

            return null;
        }
    }

    public static class FamilyFactory
    {
        public static FamilySymbol? CreateParametricBoxFamily(
            Application app,
            Document projectDoc,
            string familyName,
            double width,
            double depth,
            double height,
            string categoryHint = "Generic Model",
            BuiltInCategory? targetBic = null)
        {
            string? tmplPath = FamilyTemplateLocator.FindTemplate(app, categoryHint);
            if (string.IsNullOrEmpty(tmplPath)) return null;

            Document famDoc = app.NewFamilyDocument(tmplPath);
            if (famDoc == null) return null;

            try
            {
                var famMgr = famDoc.FamilyManager;
                using (var t = new Transaction(famDoc, "Build Parametric Geometry"))
                {
                    t.Start();

                    if (targetBic.HasValue && famDoc.OwnerFamily != null)
                    {
                        var targetCat = famDoc.Settings.Categories.get_Item(targetBic.Value);
                        if (targetCat != null) famDoc.OwnerFamily.FamilyCategory = targetCat;
                    }

                    // Add Parameters
                    FamilyParameter? pWidth = AddOrGetParameter(famMgr, "Width", isLength: true);
                    FamilyParameter? pDepth = AddOrGetParameter(famMgr, "Depth", isLength: true);
                    FamilyParameter? pHeight = AddOrGetParameter(famMgr, "Height", isLength: true);
                    FamilyParameter? pMat = AddOrGetParameter(famMgr, "Element_Material", isLength: false, isMaterial: true);

                    if (pWidth != null && width > 0.001) famMgr.Set(pWidth, width);
                    if (pDepth != null && depth > 0.001) famMgr.Set(pDepth, depth);
                    if (pHeight != null && height > 0.001) famMgr.Set(pHeight, height);

                    // Build 2D Extrusion Profile centered at (0, 0, 0)
                    var plane = Autodesk.Revit.DB.Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero);
                    var sketchPlane = SketchPlane.Create(famDoc, plane);

                    double hw = Math.Max(0.05, width / 2.0);
                    double hd = Math.Max(0.05, depth / 2.0);
                    var pt0 = new XYZ(-hw, -hd, 0.0);
                    var pt1 = new XYZ(hw, -hd, 0.0);
                    var pt2 = new XYZ(hw, hd, 0.0);
                    var pt3 = new XYZ(-hw, hd, 0.0);

                    var ca = new CurveArray();
                    ca.Append(Autodesk.Revit.DB.Line.CreateBound(pt0, pt1));
                    ca.Append(Autodesk.Revit.DB.Line.CreateBound(pt1, pt2));
                    ca.Append(Autodesk.Revit.DB.Line.CreateBound(pt2, pt3));
                    ca.Append(Autodesk.Revit.DB.Line.CreateBound(pt3, pt0));

                    var caa = new CurveArrArray();
                    caa.Append(ca);

                    var extrusion = famDoc.FamilyCreate.NewExtrusion(true, caa, sketchPlane, Math.Max(0.1, height));
                    if (extrusion != null && pHeight != null)
                    {
                        var endParam = extrusion.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM);
                        if (endParam != null) famMgr.AssociateElementParameterToFamilyParameter(endParam, pHeight);
                    }
                    if (extrusion != null && pMat != null)
                    {
                        var matParam = extrusion.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM);
                        if (matParam != null) famMgr.AssociateElementParameterToFamilyParameter(matParam, pMat);
                    }

                    t.Commit();
                }

                // Load Family into Project
                var loadOpts = new FamilyLoadOptions();
                Family? loadedFam = null;
                try
                {
                    loadedFam = famDoc.LoadFamily(projectDoc, loadOpts);
                }
                catch
                {
                    string tempDir = Path.Combine(Path.GetTempPath(), "R2R_Families");
                    Directory.CreateDirectory(tempDir);
                    string tempFile = Path.Combine(tempDir, $"{familyName}.rfa");
                    var saveOpts = new SaveAsOptions { OverwriteExistingFile = true };
                    famDoc.SaveAs(tempFile, saveOpts);
                    projectDoc.LoadFamily(tempFile, loadOpts, out loadedFam);
                }

                famDoc.Close(false);

                if (loadedFam != null)
                {
                    foreach (ElementId symId in loadedFam.GetFamilySymbolIds())
                    {
                        if (projectDoc.GetElement(symId) is FamilySymbol sym) return sym;
                    }
                }

                // Fallback search
                var col = new FilteredElementCollector(projectDoc).OfClass(typeof(FamilySymbol));
                foreach (FamilySymbol s in col)
                {
                    if (s.FamilyName.Equals(familyName, StringComparison.OrdinalIgnoreCase)) return s;
                }

                return null;
            }
            catch (Exception ex)
            {
                famDoc?.Close(false);
                System.Diagnostics.Debug.WriteLine($"[FamilyFactory C#] {ex.Message}");
                return null;
            }
        }

        public static FamilySymbol? CreateNativeFreeFormFamily(
            Application app,
            Document projectDoc,
            string familyName,
            Solid revitSolid,
            string subcategoryName = "R2R_Component",
            string categoryHint = "Generic Model",
            BuiltInCategory? targetBic = null)
        {
            string? tmplPath = FamilyTemplateLocator.FindTemplate(app, categoryHint);
            if (string.IsNullOrEmpty(tmplPath) || revitSolid == null || revitSolid.Volume <= 0.00001) return null;

            Document famDoc = app.NewFamilyDocument(tmplPath);
            if (famDoc == null) return null;

            try
            {
                var famMgr = famDoc.FamilyManager;
                using (var t = new Transaction(famDoc, "Create Native FreeForm Solid"))
                {
                    t.Start();

                    Category? famCat = famDoc.OwnerFamily?.FamilyCategory;
                    if (targetBic.HasValue && famDoc.OwnerFamily != null)
                    {
                        var targetCat = famDoc.Settings.Categories.get_Item(targetBic.Value);
                        if (targetCat != null)
                        {
                            famDoc.OwnerFamily.FamilyCategory = targetCat;
                            famCat = targetCat;
                        }
                    }

                    FamilyParameter? pMat = AddOrGetParameter(famMgr, "Element_Material", isLength: false, isMaterial: true);

                    var freeForm = FreeFormElement.Create(famDoc, revitSolid);
                    if (freeForm != null && famCat != null && !string.IsNullOrWhiteSpace(subcategoryName))
                    {
                        try
                        {
                            Category? subcat = null;
                            if (famCat.SubCategories.Contains(subcategoryName))
                            {
                                subcat = famCat.SubCategories.get_Item(subcategoryName);
                            }
                            else
                            {
                                subcat = famDoc.Settings.Categories.NewSubcategory(famCat, subcategoryName);
                            }
                            if (subcat != null) freeForm.Subcategory = subcat;
                        }
                        catch { }
                    }

                    if (freeForm != null && pMat != null)
                    {
                        var matParam = freeForm.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM);
                        if (matParam != null) famMgr.AssociateElementParameterToFamilyParameter(matParam, pMat);
                    }

                    t.Commit();
                }

                var loadOpts = new FamilyLoadOptions();
                Family? loadedFam = null;
                try
                {
                    loadedFam = famDoc.LoadFamily(projectDoc, loadOpts);
                }
                catch
                {
                    string tempDir = Path.Combine(Path.GetTempPath(), "R2R_Families");
                    Directory.CreateDirectory(tempDir);
                    string tempFile = Path.Combine(tempDir, $"{familyName}.rfa");
                    var saveOpts = new SaveAsOptions { OverwriteExistingFile = true };
                    famDoc.SaveAs(tempFile, saveOpts);
                    projectDoc.LoadFamily(tempFile, loadOpts, out loadedFam);
                }

                famDoc.Close(false);

                if (loadedFam != null)
                {
                    foreach (ElementId symId in loadedFam.GetFamilySymbolIds())
                    {
                        if (projectDoc.GetElement(symId) is FamilySymbol sym) return sym;
                    }
                }

                return null;
            }
            catch (Exception ex)
            {
                famDoc?.Close(false);
                System.Diagnostics.Debug.WriteLine($"[FamilyFactory FreeForm C#] {ex.Message}");
                return null;
            }
        }

        private static FamilyParameter? AddOrGetParameter(FamilyManager famMgr, string name, bool isLength, bool isMaterial = false)
        {
            foreach (FamilyParameter p in famMgr.Parameters)
            {
                if (p.Definition.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) return p;
            }

            try
            {
                ForgeTypeId spec = isLength ? SpecTypeId.Length : (isMaterial ? SpecTypeId.Reference.Material : SpecTypeId.String.Text);
                ForgeTypeId group = isMaterial ? GroupTypeId.Materials : GroupTypeId.Geometry;
                return famMgr.AddParameter(name, group, spec, true);
            }
            catch
            {
                return null;
            }
        }
    }
}
