# -*- coding: utf-8 -*-
"""
One-Click Rhino-to-Revit Transfer (RIR)
Production-grade 6-stage pipeline: SCAN -> CLASSIFY -> REVIEW -> TRANSFER -> VALIDATE -> SYNC
"""

__version__ = "1.0.0"
__author__ = "AEC Computational Design & BIM Team"

try:
    from .scanner import RhinoScanner, ScannedObject, ScannerPreflightReport
    from .classifier import AecClassifier, ClassificationResult
    from .ai_assistant import AISemanticInterpreter, SemanticInterpretation
    from .profile_manager import ProfileManager, MappingRule
    from .conversion_router import ConversionRouter, ConversionResult, TransferFailuresPreprocessor
    from .graphics_validator import GraphicsValidator, ElementValidationReport
    from .sync_engine import SyncEngine, RevitSyncRecord, SyncEvaluation
    from .reporting import TransferQAReport, ReportExporter
    from .ui_dialog import TransferDashboardDialog
except (ImportError, ValueError):
    from scanner import RhinoScanner, ScannedObject, ScannerPreflightReport
    from classifier import AecClassifier, ClassificationResult
    from ai_assistant import AISemanticInterpreter, SemanticInterpretation
    from profile_manager import ProfileManager, MappingRule
    from conversion_router import ConversionRouter, ConversionResult, TransferFailuresPreprocessor
    from graphics_validator import GraphicsValidator, ElementValidationReport
    from sync_engine import SyncEngine, RevitSyncRecord, SyncEvaluation
    from reporting import TransferQAReport, ReportExporter
    from ui_dialog import TransferDashboardDialog
