using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;

namespace RhinoToRevitTransfer
{
    public class RevitSyncRecord
    {
        public int ElementId { get; set; }
        public string RhinoGuid { get; set; } = string.Empty;
        public string GeoHash { get; set; } = string.Empty;
        public string LayerPath { get; set; } = string.Empty;
        public string Timestamp { get; set; } = string.Empty;
    }

    public class SyncEngine
    {
        private static readonly Guid SchemaGuid = new("7B89A3E4-4321-4F29-9EBA-D0F568C8B123");
        private readonly Document _doc;
        private readonly Schema _schema;

        public SyncEngine(Document doc)
        {
            _doc = doc;
            _schema = GetOrCreateSchema();
        }

        private Schema GetOrCreateSchema()
        {
            var existing = Schema.Lookup(SchemaGuid);
            if (existing != null) return existing;

            var builder = new SchemaBuilder(SchemaGuid);
            builder.SetReadAccessLevel(AccessLevel.Public);
            builder.SetWriteAccessLevel(AccessLevel.Public);
            builder.SetVendorId("AEC_R2R");
            builder.SetSchemaName("AecTransferTracking");

            builder.AddSimpleField("R2R_RhinoGUID", typeof(string));
            builder.AddSimpleField("R2R_GeoHash", typeof(string));
            builder.AddSimpleField("R2R_LayerPath", typeof(string));
            builder.AddSimpleField("R2R_Timestamp", typeof(string));

            return builder.Finish();
        }

        public void WriteRecord(Element elem, ScannedObject scanned)
        {
            var entity = new Entity(_schema);
            entity.Set("R2R_RhinoGUID", scanned.RhinoGuid.ToString());
            entity.Set("R2R_GeoHash", scanned.GeoHash);
            entity.Set("R2R_LayerPath", scanned.LayerFullPath);
            entity.Set("R2R_Timestamp", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));

            elem.SetEntity(entity);
        }

        public Dictionary<string, RevitSyncRecord> ScanRevitRecords()
        {
            var dict = new Dictionary<string, RevitSyncRecord>();
            var collector = new FilteredElementCollector(_doc).WhereElementIsNotElementType();

            foreach (var elem in collector)
            {
                var entity = elem.GetEntity(_schema);
                if (entity.IsValid())
                {
                    string guid = entity.Get<string>("R2R_RhinoGUID");
                    string hash = entity.Get<string>("R2R_GeoHash");
                    dict[guid] = new RevitSyncRecord
                    {
#if NET8_0_OR_GREATER
                        ElementId = (int)elem.Id.Value,
#else
                        ElementId = elem.Id.IntegerValue,
#endif
                        GeoHash = hash,
                        LayerPath = entity.Get<string>("R2R_LayerPath"),
                        Timestamp = entity.Get<string>("R2R_Timestamp")
                    };
                }
            }
            return dict;
        }

        public (Dictionary<Guid, string> SyncStates, List<RevitSyncRecord> MissingInRhino) Evaluate(List<ScannedObject> scannedObjects)
        {
            var revitRecords = ScanRevitRecords();
            var states = new Dictionary<Guid, string>();
            var scannedGuids = new HashSet<string>();

            foreach (var obj in scannedObjects)
            {
                string guidStr = obj.RhinoGuid.ToString();
                scannedGuids.Add(guidStr);

                if (!revitRecords.TryGetValue(guidStr, out var rec))
                {
                    states[obj.RhinoGuid] = "CREATE";
                }
                else if (rec.GeoHash == obj.GeoHash)
                {
                    states[obj.RhinoGuid] = "SKIP_UNCHANGED";
                }
                else
                {
                    states[obj.RhinoGuid] = "UPDATE_MODIFIED";
                }
            }

            var missing = new List<RevitSyncRecord>();
            foreach (var kvp in revitRecords)
            {
                if (!scannedGuids.Contains(kvp.Key))
                {
                    missing.Add(kvp.Value);
                }
            }

            return (states, missing);
        }
    }
}
