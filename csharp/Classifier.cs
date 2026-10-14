using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace RhinoToRevitTransfer
{
    public class ClassificationResult
    {
        public Guid RhinoGuid { get; set; }
        public string InferredCategory { get; set; } = "DirectShape";
        public string RevitCategoryName { get; set; } = "OST_GenericModel";
        public string ProposedStrategy { get; set; } = "DirectShape";
        public double ConfidenceScore { get; set; }
        public string ConfidenceTier { get; set; } = "MANUAL_OR_EXCLUDED";
        public bool IsPrechecked { get; set; }
        public string Rationale { get; set; } = string.Empty;
    }

    public class AecClassifier
    {
        public string Mode { get; set; } = "Hybrid";

        private static readonly Dictionary<string, Regex> RegexDict = new()
        {
            ["Floors"] = new Regex(@"\b(slab|floor|forjado|losa|deck|pavement|bua|nivel|paviment|solera|sostre|forjat)\b", RegexOptions.IgnoreCase),
            ["Walls"] = new Regex(@"\b(wall|muro|tabique|core|nucleo|shear|partition|fachada|paret|tancament|façana|formigo)\b", RegexOptions.IgnoreCase),
            ["Roofs"] = new Regex(@"\b(roof|cubierta|canopy|crown|top|coberta|teulada)\b", RegexOptions.IgnoreCase),
            ["Ceilings"] = new Regex(@"\b(ceiling|falso_techo|soffit|plafond|cel_ras|fals_sostre)\b", RegexOptions.IgnoreCase),
            ["Family"] = new Regex(@"\b(block|col|pilar|mullion|door|puerta|window|ventana|furniture|module|porta|finestra)\b", RegexOptions.IgnoreCase),
            ["DirectShape"] = new Regex(@"\b(veil|velo|mesh|malla|screen|celosia|freeform|subd|gelosia|inox)\b", RegexOptions.IgnoreCase)
        };

        public ClassificationResult Classify(ScannedObject obj)
        {
            var res = new ClassificationResult { RhinoGuid = obj.RhinoGuid };
            var categories = new[] { "Floors", "Walls", "Roofs", "Ceilings", "Family", "DirectShape" };
            var scores = new Dictionary<string, double>();

            // 1. Layer/Name Match (35%)
            string cleanSearch = Regex.Replace($"{obj.LayerFullPath} {obj.LayerName} {obj.ObjectName}", @"[^a-zA-Z0-9]+", " ");
            
            // 2. Geometry Characteristics (30%)
            // 3. Spatial Level (15%)
            // 4. Repetition (10%)
            // 5. Context (10%)

            foreach (var cat in categories)
            {
                double lPts = RegexDict[cat].IsMatch(cleanSearch) ? 35.0 : 2.0;

                double gPts = 2.0;
                if (cat == "Floors" && obj.IsHorizontal) gPts = 30.0;
                else if (cat == "Walls" && obj.IsVertical) gPts = 30.0;
                else if (cat == "DirectShape" && (obj.ObjectType == "Mesh" || obj.ObjectType == "SubD")) gPts = 30.0;

                double sPts = 2.0;
                if ((cat == "Floors" || cat == "Walls") && Math.Abs(obj.DzToLevel) <= 0.3) sPts = 15.0;
                else if (cat == "Ceilings" && obj.DzToLevel >= 2.2 && obj.DzToLevel <= 3.8) sPts = 15.0;

                double rPts = (cat == "Family" && obj.ObjectType == "InstanceReference") ? 10.0 : 2.0;
                double cPts = (cat == "Floors" && obj.IsHorizontal) ? 8.0 : (cat == "Walls" && obj.IsVertical ? 8.0 : 2.0);

                scores[cat] = Math.Min(100.0, lPts + gPts + sPts + rPts + cPts);
            }

            // Find best category
            string bestCat = "DirectShape";
            double bestScore = 0.0;
            foreach (var kvp in scores)
            {
                if (kvp.Value > bestScore)
                {
                    bestScore = kvp.Value;
                    bestCat = kvp.Key;
                }
            }

            res.InferredCategory = bestCat;
            res.ConfidenceScore = Math.Round(bestScore, 1);
            res.RevitCategoryName = bestCat switch
            {
                "Floors" => "OST_Floors",
                "Walls" => "OST_Walls",
                "Roofs" => "OST_Roofs",
                "Ceilings" => "OST_Ceilings",
                "Family" => "OST_Columns",
                _ => "OST_GenericModel"
            };

            // Strategy selection
            res.ProposedStrategy = Mode switch
            {
                "Geometry-First" => (bestCat == "Floors" && obj.IsHorizontal) ? "FloorNative" : "DirectShape",
                "Native BIM" => bestCat switch
                {
                    "Floors" => "FloorNative",
                    "Walls" => "WallByCurve",
                    "Roofs" => "RoofNative",
                    "Ceilings" => "CeilingNative",
                    _ => "DirectShape"
                },
                _ => bestCat switch // Hybrid (Recommended)
                {
                    "Floors" when obj.IsHorizontal => "FloorNative",
                    "Walls" when obj.IsVertical && obj.Thickness <= 0.6 => "WallByCurve",
                    "Ceilings" => "CeilingNative",
                    _ => "DirectShape"
                }
            };

            // Tiers
            if (bestScore >= 85.0)
            {
                res.ConfidenceTier = "AUTO_READY";
                res.IsPrechecked = true;
            }
            else if (bestScore >= 60.0)
            {
                res.ConfidenceTier = "SUGGESTED_NEEDS_APPROVAL";
                res.IsPrechecked = false;
            }
            else
            {
                res.ConfidenceTier = "MANUAL_OR_EXCLUDED";
                res.IsPrechecked = false;
            }

            return res;
        }
    }
}
