"""
One-Click Rhino-to-Revit Transfer (RIR): Module 8
reporting.py — Preflight, Exception, BUA/GFA Area Check & Post-Transfer QA Exporter

Aggregates all pipeline metrics and outputs production-grade QA reports in:
  - Interactive Responsive HTML Report (with executive summary KPI cards, Dual-Status badges, tables)
  - Comma-Separated Values (CSV) audit table
  - Machine-readable JSON log
"""

import os
import json
import csv
import datetime


class TransferQAReport(object):
    """Encapsulates full session metrics, preflight warnings, BUA totals, and element audits."""

    def __init__(self):
        self.project_name = "Architectural Project"
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.execution_mode = "Hybrid"
        self.unit_system = "Meters"

        # Preflight Metrics
        self.preflight_data = {
            "total_scanned": 0,
            "duplicates_found": 0,
            "far_origin_count": 0,
            "open_loops_count": 0,
            "warnings": []
        }

        # Execution Metrics
        self.transfer_stats = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "missing_in_rhino": 0
        }

        # BUA / GFA Metrics
        self.bua_stats = {
            "rhino_slab_area_sqm": 0.0,
            "revit_floor_area_sqm": 0.0,
            "discrepancy_sqm": 0.0,
            "discrepancy_pct": 0.0
        }

        # Element-level Dual-Status records
        self.element_records = []  # list of dicts

    def compute_bua_summary(self):
        """Calculates area totals and percentage variance between Rhino and Revit."""
        total_rhino = sum(r.get("rhino_area", 0.0) for r in self.element_records if r.get("category") == "Floors")
        total_revit = sum(r.get("bua_area", 0.0) for r in self.element_records if r.get("category") == "Floors")

        self.bua_stats["rhino_slab_area_sqm"] = round(total_rhino, 2)
        self.bua_stats["revit_floor_area_sqm"] = round(total_revit, 2)
        diff = abs(total_rhino - total_revit)
        self.bua_stats["discrepancy_sqm"] = round(diff, 2)
        self.bua_stats["discrepancy_pct"] = round((diff / max(1.0, total_rhino)) * 100.0, 2)


class ReportExporter(object):
    """Serializes TransferQAReport into HTML, CSV, and JSON."""

    @staticmethod
    def export_json(report, file_path):
        """Writes report to JSON file."""
        report.compute_bua_summary()
        payload = {
            "project_name": report.project_name,
            "timestamp": report.timestamp,
            "execution_mode": report.execution_mode,
            "unit_system": report.unit_system,
            "preflight": report.preflight_data,
            "transfer_stats": report.transfer_stats,
            "bua_stats": report.bua_stats,
            "elements": report.element_records
        }
        with open(file_path, "w") as f:
            json.dump(payload, f, indent=2)
        return file_path

    @staticmethod
    def export_csv(report, file_path):
        """Writes element records and validation status to CSV."""
        report.compute_bua_summary()
        fieldnames = [
            "rhino_guid", "revit_element_id", "layer_name", "category",
            "strategy", "sync_state", "confidence_pct", "bim_status",
            "graphics_status", "overall_status", "plan_view", "section_view",
            "elevation_view", "3d_view", "rhino_area_sqm", "revit_bua_sqm", "issues"
        ]

        with open(file_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in report.element_records:
                vm = r.get("view_matrix", {})
                writer.writerow({
                    "rhino_guid": r.get("rhino_guid", ""),
                    "revit_element_id": r.get("revit_element_id", -1),
                    "layer_name": r.get("layer_name", ""),
                    "category": r.get("category", ""),
                    "strategy": r.get("strategy", ""),
                    "sync_state": r.get("sync_state", ""),
                    "confidence_pct": r.get("confidence_pct", 0),
                    "bim_status": r.get("bim_status", ""),
                    "graphics_status": r.get("graphics_status", ""),
                    "overall_status": r.get("overall_status", ""),
                    "plan_view": vm.get("Plan", "N/A"),
                    "section_view": vm.get("Section", "N/A"),
                    "elevation_view": vm.get("Elevation", "N/A"),
                    "3d_view": vm.get("3D", "N/A"),
                    "rhino_area_sqm": r.get("rhino_area", 0.0),
                    "revit_bua_sqm": r.get("bua_area", 0.0),
                    "issues": "; ".join(r.get("issues", []))
                })
        return file_path

    @staticmethod
    def export_html(report, file_path):
        """Generates a responsive, publication-quality executive HTML report."""
        report.compute_bua_summary()

        # Build table rows
        rows_html = []
        for r in report.element_records:
            vm = r.get("view_matrix", {})
            overall = r.get("overall_status", "REVIEW REQUIRED")
            badge_class = "badge-pass" if overall == "COMPLETE" else "badge-warn"

            plan_badge = "badge-pass" if vm.get("Plan") == "PASS" else ("badge-warn" if vm.get("Plan") == "WARNING" else "badge-fail")
            sec_badge = "badge-pass" if vm.get("Section") == "PASS" else ("badge-warn" if vm.get("Section") == "WARNING" else "badge-fail")

            row = """
            <tr>
              <td><code>{guid}</code></td>
              <td><strong>{revit_id}</strong></td>
              <td>{layer}</td>
              <td><span class="category-pill">{cat}</span></td>
              <td>{strat}</td>
              <td>{sync}</td>
              <td><strong>{conf}%</strong></td>
              <td><span class="{overall_cls}">{overall}</span></td>
              <td><span class="{p_cls}">{plan}</span></td>
              <td><span class="{s_cls}">{sec}</span></td>
              <td>{issues}</td>
            </tr>
            """.format(
                guid=r.get("rhino_guid", "")[:8] + "...",
                revit_id=r.get("revit_element_id", "—"),
                layer=r.get("layer_name", ""),
                cat=r.get("category", ""),
                strat=r.get("strategy", ""),
                sync=r.get("sync_state", "CREATE"),
                conf=r.get("confidence_pct", 0),
                overall_cls=badge_class,
                overall=overall,
                p_cls=plan_badge,
                plan=vm.get("Plan", "PASS"),
                s_cls=sec_badge,
                sec=vm.get("Section", "PASS"),
                issues="; ".join(r.get("issues", [])) or "None"
            )
            rows_html.append(row)

        html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>R2R Transfer QA & Validation Report</title>
  <style>
    :root {
      --bg: #0f141c;
      --card-bg: #182230;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --border: #334155;
      --pass: #22c55e;
      --warn: #f59e0b;
      --fail: #ef4444;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 30px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2px solid var(--border);
      padding-bottom: 20px;
      margin-bottom: 30px;
    }
    h1 { margin: 0; font-size: 24px; color: #fff; font-weight: 700; letter-spacing: -0.5px; }
    .meta { color: var(--text-muted); font-size: 14px; }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 20px;
      margin-bottom: 30px;
    }
    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }
    .kpi-title { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-bottom: 8px; }
    .kpi-value { font-size: 28px; font-weight: 700; color: #fff; }
    .kpi-value.green { color: var(--pass); }
    .kpi-value.amber { color: var(--warn); }
    .kpi-value.blue { color: var(--accent); }
    .table-container {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      margin-top: 25px;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th { background: #1e293b; padding: 12px 16px; font-weight: 600; color: #cbd5e1; border-bottom: 1px solid var(--border); }
    td { padding: 12px 16px; border-bottom: 1px solid #1e293b; }
    tr:hover { background: #1a2638; }
    .badge-pass { background: rgba(34,197,94,0.15); color: var(--pass); padding: 4px 8px; border-radius: 4px; font-weight: 600; }
    .badge-warn { background: rgba(245,158,11,0.15); color: var(--warn); padding: 4px 8px; border-radius: 4px; font-weight: 600; }
    .badge-fail { background: rgba(239,68,68,0.15); color: var(--fail); padding: 4px 8px; border-radius: 4px; font-weight: 600; }
    .category-pill { background: #334155; padding: 3px 8px; border-radius: 12px; font-size: 11px; }
    code { font-family: monospace; background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>R2R ONE-CLICK TRANSFER — QA & VALIDATION REPORT</h1>
      <div class="meta">Project: __PROJECT__ | Mode: __MODE__ | Generated: __TIMESTAMP__</div>
    </div>
    <div>
      <span class="badge-pass" style="font-size:14px; padding: 6px 12px;">SYSTEM READY</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Total Scanned</div>
      <div class="kpi-value">__TOTAL_SCANNED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Created Elements</div>
      <div class="kpi-value green">__CREATED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Updated / Modified</div>
      <div class="kpi-value blue">__UPDATED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Skipped (Unchanged)</div>
      <div class="kpi-value">__SKIPPED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Review / Warnings</div>
      <div class="kpi-value amber">__FAILED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">BUA Area Variance</div>
      <div class="kpi-value">__BUA_VAR__%</div>
    </div>
  </div>

  <h3>Dual-Status Verification & View Graphics Audit</h3>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Rhino GUID</th>
          <th>Revit ID</th>
          <th>Layer</th>
          <th>Category</th>
          <th>Strategy</th>
          <th>Sync State</th>
          <th>Confidence</th>
          <th>Overall Status</th>
          <th>Plan View</th>
          <th>Section View</th>
          <th>Issues / Notes</th>
        </tr>
      </thead>
      <tbody>
__ROWS__
      </tbody>
    </table>
  </div>
</body>
</html>
"""
        html_content = html_template \
            .replace("__PROJECT__", str(report.project_name)) \
            .replace("__MODE__", str(report.execution_mode)) \
            .replace("__TIMESTAMP__", str(report.timestamp)) \
            .replace("__TOTAL_SCANNED__", str(report.preflight_data.get("total_scanned", len(report.element_records)))) \
            .replace("__CREATED__", str(report.transfer_stats.get("created", 0))) \
            .replace("__UPDATED__", str(report.transfer_stats.get("updated", 0))) \
            .replace("__SKIPPED__", str(report.transfer_stats.get("skipped", 0))) \
            .replace("__FAILED__", str(report.transfer_stats.get("failed", 0))) \
            .replace("__BUA_VAR__", str(report.bua_stats.get("discrepancy_pct", 0.0))) \
            .replace("__ROWS__", "\n".join(rows_html))

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return file_path
