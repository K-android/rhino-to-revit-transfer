<#
.SYNOPSIS
    Uninstaller for Rhino-to-Revit Transfer (R2R) Revit Add-in.
#>

$RevitAddinsRoot = "$env:APPDATA\Autodesk\Revit\Addins"

Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host "   Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller    " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host ""

if (Test-Path $RevitAddinsRoot) {
    $DetectedVersions = Get-ChildItem -Path $RevitAddinsRoot -Directory | Where-Object { $_.Name -match "^\d{4}$" } | Select-Object -ExpandProperty Name
    foreach ($ver in $DetectedVersions) {
        $ManifestPath = "$RevitAddinsRoot\$ver\RhinoToRevitTransfer.addin"
        $PluginFolder = "$RevitAddinsRoot\$ver\RhinoToRevitTransfer"

        if (Test-Path $ManifestPath) {
            Remove-Item -Path $ManifestPath -Force
            Write-Host "  -> Removed manifest from Revit $ver" -ForegroundColor Green
        }
        if (Test-Path $PluginFolder) {
            Remove-Item -Path $PluginFolder -Recurse -Force
            Write-Host "  -> Removed plugin files from Revit $ver" -ForegroundColor Green
        }
    }
}

Write-Host "`nUninstallation complete. RhinoToRevitTransfer has been removed from Revit." -ForegroundColor Green
Write-Host ""
