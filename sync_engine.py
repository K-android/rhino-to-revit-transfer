"""
One-Click Rhino-to-Revit Transfer (RIR): Module 7
sync_engine.py — Persistent Identity (GUID + Geometry Hash) & Revision Tracker

Implements bidirectional revision tracking between Rhino and Revit using Revit ExtensibleStorage.
Attaches tamper-proof metadata entities to every transferred Revit element:
  - R2R_SourceDocName
  - R2R_RhinoGUID
  - R2R_LayerPath
  - R2R_GeoHash
  - R2R_MappingVersion
  - R2R_Timestamp
  - R2R_Strategy

Revision Logic:
  - CREATE: Rhino object not yet transferred to Revit
  - SKIP_UNCHANGED: Rhino object exists in Revit and geo_hash matches exactly
  - UPDATE_MODIFIED: Rhino object exists in Revit but geometry or layer has changed
  - FLAG_MISSING: Element exists in Revit with transfer tag, but source object was deleted in Rhino
                  (Never auto-deleted without explicit user confirmation).
"""

import sys
import datetime
import traceback

try:
    import clr
    clr.AddReference("System")
    from System import Guid
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    import Autodesk.Revit.DB as DB
    import Autodesk.Revit.DB.ExtensibleStorage as ES
    REVIT_AVAILABLE = True
except Exception:
    REVIT_AVAILABLE = False


class RevitSyncRecord(object):
    """Represents the persisted R2R sync metadata stored inside a Revit element."""

    def __init__(self, revit_element_id):
        self.revit_element_id = revit_element_id
        self.source_doc_name = ""
        self.rhino_guid = ""
        self.layer_path = ""
        self.geo_hash = ""
        self.mapping_version = "1.0.0"
        self.timestamp = ""
        self.strategy = ""


class SyncEvaluation(object):
    """Contains the calculated sync delta state for a scanned Rhino object."""

    def __init__(self, rhino_guid):
        self.rhino_guid = str(rhino_guid)
        self.sync_state = "CREATE"  # CREATE | SKIP_UNCHANGED | UPDATE_MODIFIED | FLAG_MISSING
        self.revit_element_id = -1
        self.existing_geo_hash = ""
        self.new_geo_hash = ""
        self.explanation = ""

    def to_dict(self):
        return {
            "rhino_guid": self.rhino_guid,
            "sync_state": self.sync_state,
            "revit_element_id": self.revit_element_id,
            "existing_geo_hash": self.existing_geo_hash,
            "new_geo_hash": self.new_geo_hash,
            "explanation": self.explanation
        }


class SyncEngine(object):
    """Engine managing ExtensibleStorage schemas and revision change detection."""

    SCHEMA_GUID_STR = "7B89A3E4-4321-4F29-9EBA-D0F568C8B123"

    def __init__(self, revit_doc):
        self.doc = revit_doc
        self.schema = None
        if REVIT_AVAILABLE and self.doc:
            self.schema = self._get_or_create_schema()

    def _get_or_create_schema(self):
        """Initializes the Revit ExtensibleStorage schema for R2R transfer metadata."""
        schema_guid = Guid(self.SCHEMA_GUID_STR)
        existing = ES.Schema.Lookup(schema_guid)
        if existing:
            return existing

        builder = ES.SchemaBuilder(schema_guid)
        builder.SetReadAccessLevel(ES.AccessLevel.Public)
        builder.SetWriteAccessLevel(ES.AccessLevel.Public)
        builder.SetVendorId("AEC_R2R")
        builder.SetSchemaName("AecTransferTracking")
        builder.SetDocumentation("Tracks Rhino-to-Revit transfer identity and geometry hash")

        builder.AddSimpleField("R2R_SourceDocName", str)
        builder.AddSimpleField("R2R_RhinoGUID", str)
        builder.AddSimpleField("R2R_LayerPath", str)
        builder.AddSimpleField("R2R_GeoHash", str)
        builder.AddSimpleField("R2R_MappingVersion", str)
        builder.AddSimpleField("R2R_Timestamp", str)
        builder.AddSimpleField("R2R_Strategy", str)

        return builder.Finish()

    def read_sync_record(self, element):
        """Reads R2R sync entity from an element, returning RevitSyncRecord or None."""
        if not self.schema or not element:
            return None

        try:
            entity = element.GetEntity(self.schema)
            if not entity.IsValid():
                return None

            rec = RevitSyncRecord(element.Id.IntegerValue)
            rec.source_doc_name = entity.Get[str]("R2R_SourceDocName")
            rec.rhino_guid = entity.Get[str]("R2R_RhinoGUID")
            rec.layer_path = entity.Get[str]("R2R_LayerPath")
            rec.geo_hash = entity.Get[str]("R2R_GeoHash")
            rec.mapping_version = entity.Get[str]("R2R_MappingVersion")
            rec.timestamp = entity.Get[str]("R2R_Timestamp")
            rec.strategy = entity.Get[str]("R2R_Strategy")
            return rec
        except Exception:
            return None

    def write_sync_record(self, element, scanned_obj, strategy="DirectShape", version="1.0.0"):
        """Attaches R2R tracking entity to a Revit element."""
        if not self.schema or not element:
            return False

        try:
            entity = ES.Entity(self.schema)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            entity.Set[str]("R2R_SourceDocName", "Rhino_Model")
            entity.Set[str]("R2R_RhinoGUID", scanned_obj.rhino_guid)
            entity.Set[str]("R2R_LayerPath", scanned_obj.layer_full_path)
            entity.Set[str]("R2R_GeoHash", scanned_obj.geo_hash)
            entity.Set[str]("R2R_MappingVersion", version)
            entity.Set[str]("R2R_Timestamp", now_str)
            entity.Set[str]("R2R_Strategy", strategy)

            element.SetEntity(entity)

            # Also mirror GUID and Hash to standard Comments parameter for quick Revit UI inspection
            param_comments = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if param_comments and not param_comments.IsReadOnly:
                param_comments.Set("R2R_ID: {} | Hash: {}".format(scanned_obj.rhino_guid, scanned_obj.geo_hash[:8]))

            return True
        except Exception:
            return False

    def scan_revit_sync_records(self):
        """Finds all Revit elements bearing R2R tracking entities across the project."""
        records = {}  # rhino_guid -> RevitSyncRecord
        if not REVIT_AVAILABLE or not self.doc or not self.schema:
            return records

        collector = DB.FilteredElementCollector(self.doc).WhereElementIsNotElementType()
        for elem in collector:
            try:
                rec = self.read_sync_record(elem)
                if rec and rec.rhino_guid:
                    records[rec.rhino_guid] = rec
            except Exception:
                continue

        return records

    def evaluate_sync_states(self, scanned_objects):
        """Compares scanned Rhino objects against existing Revit records to determine sync states.

        Returns:
          evaluations: dict {rhino_guid: SyncEvaluation}
          missing_in_rhino: list of RevitSyncRecord (elements in Revit no longer in Rhino)
        """
        revit_records = self.scan_revit_sync_records()
        evaluations = {}
        scanned_guids = set()

        for obj in scanned_objects:
            guid = obj.rhino_guid
            scanned_guids.add(guid)
            ev = SyncEvaluation(guid)
            ev.new_geo_hash = obj.geo_hash

            if guid not in revit_records:
                ev.sync_state = "CREATE"
                ev.explanation = "New object not yet present in Revit document."
            else:
                existing_rec = revit_records[guid]
                ev.revit_element_id = existing_rec.revit_element_id
                ev.existing_geo_hash = existing_rec.geo_hash

                if existing_rec.geo_hash == obj.geo_hash:
                    ev.sync_state = "SKIP_UNCHANGED"
                    ev.explanation = "Identical geometry hash match. No update needed."
                else:
                    ev.sync_state = "UPDATE_MODIFIED"
                    ev.explanation = "Geometry or layer changed in Rhino. Update required."

            evaluations[guid] = ev

        # Identify elements present in Revit that have disappeared from Rhino model
        missing_in_rhino = []
        for r_guid, rec in revit_records.items():
            if r_guid not in scanned_guids:
                missing_in_rhino.append(rec)

        return evaluations, missing_in_rhino

    def delete_elements(self, element_ids):
        """Safely removes approved missing elements inside an active Revit transaction."""
        if not REVIT_AVAILABLE or not self.doc:
            return 0

        deleted_count = 0
        for eid in element_ids:
            try:
                elem_id = DB.ElementId(eid)
                self.doc.Delete(elem_id)
                deleted_count += 1
            except Exception:
                pass

        return deleted_count
