# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR): Module 8
reporting.py - Preflight, Exception, BUA/GFA Area Check & Post-Transfer QA Exporter

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

        # BUA / GFA, Volumetric & Material Takeoff Metrics
        self.bua_stats = {
            "rhino_slab_area_sqm": 0.0,
            "revit_floor_area_sqm": 0.0,
            "discrepancy_sqm": 0.0,
            "discrepancy_pct": 0.0,
            "rhino_slab_volume_cum": 0.0,
            "revit_floor_volume_cum": 0.0,
            "floor_volume_discrepancy_pct": 0.0,
            "rhino_wall_area_sqm": 0.0,
            "revit_wall_area_sqm": 0.0,
            "wall_area_discrepancy_pct": 0.0,
            "rhino_wall_volume_cum": 0.0,
            "revit_wall_volume_cum": 0.0,
            "wall_volume_discrepancy_pct": 0.0,
            "total_material_volume_cum": 0.0,
            "material_takeoff": {}  # mat_name -> {"total_volume_cum": ..., "total_area_sqm": ..., "element_count": ...}
        }

        # Element-level Dual-Status records
        self.element_records = []  # list of dicts

    def compute_bua_summary(self):
        """Calculates area totals, solid volumes, and material takeoff variance between Rhino and Revit."""
        # 1. Floors Quantities
        total_rhino_floor_area = sum(r.get("rhino_area", 0.0) for r in self.element_records if r.get("category") in ("Floors", "Floor", "Slab"))
        total_revit_floor_area = sum(r.get("area_sqm", r.get("bua_area", 0.0)) for r in self.element_records if r.get("category") in ("Floors", "Floor", "Slab"))
        diff_area = abs(total_rhino_floor_area - total_revit_floor_area)

        total_rhino_floor_vol = sum(r.get("rhino_volume", 0.0) for r in self.element_records if r.get("category") in ("Floors", "Floor", "Slab"))
        total_revit_floor_vol = sum(r.get("volume_cum", 0.0) for r in self.element_records if r.get("category") in ("Floors", "Floor", "Slab"))
        diff_floor_vol = abs(total_rhino_floor_vol - total_revit_floor_vol)

        self.bua_stats["rhino_slab_area_sqm"] = round(total_rhino_floor_area, 2)
        self.bua_stats["revit_floor_area_sqm"] = round(total_revit_floor_area, 2)
        self.bua_stats["discrepancy_sqm"] = round(diff_area, 2)
        self.bua_stats["discrepancy_pct"] = round((diff_area / max(1.0, total_rhino_floor_area)) * 100.0, 2)

        self.bua_stats["rhino_slab_volume_cum"] = round(total_rhino_floor_vol, 2)
        self.bua_stats["revit_floor_volume_cum"] = round(total_revit_floor_vol, 2)
        self.bua_stats["floor_volume_discrepancy_pct"] = round((diff_floor_vol / max(1.0, total_rhino_floor_vol)) * 100.0, 2)

        # 2. Walls Quantities
        total_rhino_wall_area = sum(r.get("rhino_area", 0.0) for r in self.element_records if r.get("category") in ("Walls", "Wall"))
        total_revit_wall_area = sum(r.get("area_sqm", 0.0) for r in self.element_records if r.get("category") in ("Walls", "Wall"))
        diff_wall_area = abs(total_rhino_wall_area - total_revit_wall_area)

        total_rhino_wall_vol = sum(r.get("rhino_volume", 0.0) for r in self.element_records if r.get("category") in ("Walls", "Wall"))
        total_revit_wall_vol = sum(r.get("volume_cum", 0.0) for r in self.element_records if r.get("category") in ("Walls", "Wall"))
        diff_wall_vol = abs(total_rhino_wall_vol - total_revit_wall_vol)

        self.bua_stats["rhino_wall_area_sqm"] = round(total_rhino_wall_area, 2)
        self.bua_stats["revit_wall_area_sqm"] = round(total_revit_wall_area, 2)
        self.bua_stats["wall_area_discrepancy_pct"] = round((diff_wall_area / max(1.0, total_rhino_wall_area)) * 100.0, 2)

        self.bua_stats["rhino_wall_volume_cum"] = round(total_rhino_wall_vol, 2)
        self.bua_stats["revit_wall_volume_cum"] = round(total_revit_wall_vol, 2)
        self.bua_stats["wall_volume_discrepancy_pct"] = round((diff_wall_vol / max(1.0, total_rhino_wall_vol)) * 100.0, 2)

        # 3. Aggregated Compound Material Takeoff
        mat_summary = {}
        total_mat_vol = 0.0
        for r in self.element_records:
            elem_mto = r.get("material_takeoff", {})
            if elem_mto and isinstance(elem_mto, dict):
                for m_name, vals in elem_mto.items():
                    if m_name not in mat_summary:
                        mat_summary[m_name] = {"total_volume_cum": 0.0, "total_area_sqm": 0.0, "element_count": 0}
                    v = float(vals.get("volume_cum", 0.0) or 0.0)
                    a = float(vals.get("area_sqm", 0.0) or 0.0)
                    mat_summary[m_name]["total_volume_cum"] += v
                    mat_summary[m_name]["total_area_sqm"] += a
                    mat_summary[m_name]["element_count"] += 1
                    total_mat_vol += v
            else:
                m_name = r.get("material_name") or r.get("primary_material") or "Default"
                if m_name not in mat_summary:
                    mat_summary[m_name] = {"total_volume_cum": 0.0, "total_area_sqm": 0.0, "element_count": 0}
                v = float(r.get("material_volume_cum", r.get("volume_cum", 0.0)) or 0.0)
                a = float(r.get("area_sqm", r.get("bua_area", 0.0)) or 0.0)
                mat_summary[m_name]["total_volume_cum"] += v
                mat_summary[m_name]["total_area_sqm"] += a
                mat_summary[m_name]["element_count"] += 1
                total_mat_vol += v

        # Round totals
        for m_name in mat_summary:
            mat_summary[m_name]["total_volume_cum"] = round(mat_summary[m_name]["total_volume_cum"], 2)
            mat_summary[m_name]["total_area_sqm"] = round(mat_summary[m_name]["total_area_sqm"], 2)

        self.bua_stats["material_takeoff"] = mat_summary
        self.bua_stats["total_material_volume_cum"] = round(total_mat_vol, 2)


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
        """Writes element records, volumetric quantities, and validation status to CSV."""
        report.compute_bua_summary()
        fieldnames = [
            "rhino_guid", "revit_element_id", "layer_name", "category",
            "strategy", "sync_state", "confidence_pct", "bim_status",
            "graphics_status", "overall_status", "plan_view", "section_view",
            "elevation_view", "3d_view", "area_sqm", "volume_cum",
            "primary_material", "material_volume_cum", "rhino_area_sqm", "revit_bua_sqm", "issues"
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
                    "area_sqm": r.get("area_sqm", r.get("bua_area", 0.0)),
                    "volume_cum": r.get("volume_cum", 0.0),
                    "primary_material": r.get("material_name", r.get("primary_material", "")),
                    "material_volume_cum": r.get("material_volume_cum", 0.0),
                    "rhino_area_sqm": r.get("rhino_area", 0.0),
                    "revit_bua_sqm": r.get("bua_area", 0.0),
                    "issues": "; ".join(r.get("issues", []))
                })
        return file_path

    @staticmethod
    def export_html(report, file_path):
        """Generates a responsive, publication-quality executive HTML report with volumetric and material takeoff schedules."""
        report.compute_bua_summary()

        # Build Material Takeoff rows
        mat_rows_html = []
        tot_m_vol = max(0.001, report.bua_stats.get("total_material_volume_cum", 0.0))
        for m_name, m_info in sorted(report.bua_stats.get("material_takeoff", {}).items(), key=lambda x: x[1].get("total_volume_cum", 0.0), reverse=True):
            m_vol = m_info.get("total_volume_cum", 0.0)
            m_area = m_info.get("total_area_sqm", 0.0)
            m_cnt = m_info.get("element_count", 0)
            pct_share = round((m_vol / tot_m_vol) * 100.0, 1)
            mat_rows_html.append("""
            <tr>
              <td><strong>{name}</strong></td>
              <td>{cnt}</td>
              <td>{area:.2f} m²</td>
              <td><strong style="color: var(--accent);">{vol:.2f} m³</strong></td>
              <td><span class="category-pill">{pct}%</span></td>
            </tr>
            """.format(name=m_name, cnt=m_cnt, area=m_area, vol=m_vol, pct=pct_share))

        if not mat_rows_html:
            mat_rows_html.append('<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No compound material data recorded</td></tr>')

        # Build element table rows
        rows_html = []
        for r in report.element_records:
            vm = r.get("view_matrix", {})
            overall = r.get("overall_status", "REVIEW REQUIRED")
            badge_class = "badge-pass" if overall == "COMPLETE" else "badge-warn"

            plan_badge = "badge-pass" if vm.get("Plan") == "PASS" else ("badge-warn" if vm.get("Plan") == "WARNING" else "badge-fail")
            sec_badge = "badge-pass" if vm.get("Section") == "PASS" else ("badge-warn" if vm.get("Section") == "WARNING" else "badge-fail")

            e_area = float(r.get("area_sqm", r.get("bua_area", 0.0)) or 0.0)
            e_vol = float(r.get("volume_cum", 0.0) or 0.0)
            e_mat = r.get("material_name") or r.get("primary_material") or "-"
            e_mvol = float(r.get("material_volume_cum", 0.0) or 0.0)

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
              <td>{area:.2f} m²</td>
              <td><strong style="color: var(--accent);">{vol:.2f} m³</strong></td>
              <td>{mat}</td>
              <td>{mvol:.2f} m³</td>
              <td><span class="{p_cls}">{plan}</span></td>
              <td><span class="{s_cls}">{sec}</span></td>
              <td>{issues}</td>
            </tr>
            """.format(
                guid=r.get("rhino_guid", "")[:8] + "...",
                revit_id=r.get("revit_element_id", "-"),
                layer=r.get("layer_name", ""),
                cat=r.get("category", ""),
                strat=r.get("strategy", ""),
                sync=r.get("sync_state", "CREATE"),
                conf=r.get("confidence_pct", 0),
                overall_cls=badge_class,
                overall=overall,
                area=e_area,
                vol=e_vol,
                mat=e_mat,
                mvol=e_mvol,
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
  <title>R2R Transfer QA & Material Takeoff Report</title>
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
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 18px;
      margin-bottom: 30px;
    }
    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }
    .kpi-title { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-bottom: 8px; }
    .kpi-value { font-size: 24px; font-weight: 700; color: #fff; }
    .kpi-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
    .kpi-value.green { color: var(--pass); }
    .kpi-value.amber { color: var(--warn); }
    .kpi-value.blue { color: var(--accent); }
    .section-title { font-size: 16px; font-weight: 600; color: #fff; margin-top: 30px; margin-bottom: 12px; }
    .table-container {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow-x: auto;
      margin-bottom: 25px;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th { background: #1e293b; padding: 12px 14px; font-weight: 600; color: #cbd5e1; border-bottom: 1px solid var(--border); white-space: nowrap; }
    td { padding: 10px 14px; border-bottom: 1px solid #1e293b; }
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
      <h1>R2R ONE-CLICK TRANSFER - QUANTITY TAKEOFF & QA AUDIT</h1>
      <div class="meta">Project: __PROJECT__ | Mode: __MODE__ | Unit System: __UNIT__ | Generated: __TIMESTAMP__</div>
    </div>
    <div>
      <span class="badge-pass" style="font-size:14px; padding: 6px 12px;">SYSTEM READY</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Total Elements</div>
      <div class="kpi-value green">__CREATED__</div>
      <div class="kpi-sub">Scanned: __TOTAL_SCANNED__ | Skipped: __SKIPPED__</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Total Material Volume</div>
      <div class="kpi-value blue">__TOTAL_MAT_VOL__ m³</div>
      <div class="kpi-sub">Native Revit Solid Volume</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Floor Slab Quantities</div>
      <div class="kpi-value">__FLOOR_AREA__ m²</div>
      <div class="kpi-sub">Volume: __FLOOR_VOL__ m³ (Var: __BUA_VAR__%)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Wall Quantities</div>
      <div class="kpi-value">__WALL_AREA__ m²</div>
      <div class="kpi-sub">Volume: __WALL_VOL__ m³</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Review / Warnings</div>
      <div class="kpi-value amber">__FAILED__</div>
      <div class="kpi-sub">Exceptions requiring audit</div>
    </div>
  </div>

  <div class="section-title">Compound Material Takeoff Schedule</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Material Name</th>
          <th>Element Count</th>
          <th>Total Surface Area (m²)</th>
          <th>Total Material Volume (m³)</th>
          <th>Share of Total Volume</th>
        </tr>
      </thead>
      <tbody>
__MAT_ROWS__
      </tbody>
    </table>
  </div>

  <div class="section-title">Element-by-Element Volumetric & Dual-Status Audit</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Rhino GUID</th>
          <th>Revit ID</th>
          <th>Layer</th>
          <th>Category</th>
          <th>Strategy</th>
          <th>Sync</th>
          <th>Conf</th>
          <th>Overall</th>
          <th>Area (m²)</th>
          <th>Volume (m³)</th>
          <th>Primary Material</th>
          <th>Material Vol</th>
          <th>Plan</th>
          <th>Section</th>
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
            .replace("__UNIT__", str(report.unit_system)) \
            .replace("__TIMESTAMP__", str(report.timestamp)) \
            .replace("__TOTAL_SCANNED__", str(report.preflight_data.get("total_scanned", len(report.element_records)))) \
            .replace("__CREATED__", str(report.transfer_stats.get("created", 0))) \
            .replace("__UPDATED__", str(report.transfer_stats.get("updated", 0))) \
            .replace("__SKIPPED__", str(report.transfer_stats.get("skipped", 0))) \
            .replace("__FAILED__", str(report.transfer_stats.get("failed", 0))) \
            .replace("__TOTAL_MAT_VOL__", str(report.bua_stats.get("total_material_volume_cum", 0.0))) \
            .replace("__FLOOR_AREA__", str(report.bua_stats.get("revit_floor_area_sqm", 0.0))) \
            .replace("__FLOOR_VOL__", str(report.bua_stats.get("revit_floor_volume_cum", 0.0))) \
            .replace("__WALL_AREA__", str(report.bua_stats.get("revit_wall_area_sqm", 0.0))) \
            .replace("__WALL_VOL__", str(report.bua_stats.get("revit_wall_volume_cum", 0.0))) \
            .replace("__BUA_VAR__", str(report.bua_stats.get("discrepancy_pct", 0.0))) \
            .replace("__MAT_ROWS__", "\n".join(mat_rows_html)) \
            .replace("__ROWS__", "\n".join(rows_html))

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return file_path
