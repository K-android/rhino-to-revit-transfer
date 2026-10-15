<#
.SYNOPSIS
    Uninstaller for Rhino-to-Revit Transfer (R2R) Revit Add-in.
#>

$TargetRoots = @(
    "$env:APPDATA\Autodesk\Revit\Addins",
    "$env:ProgramData\Autodesk\Revit\Addins"
)

Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host "   Rhino-to-Revit Transfer (R2R) - Add-in Uninstaller    " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host ""

foreach ($root in $TargetRoots) {
    if (Test-Path $root) {
        $DetectedVersions = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "^\d{4}$" } | Select-Object -ExpandProperty Name
        foreach ($ver in $DetectedVersions) {
            $ManifestPath = "$root\$ver\RhinoToRevitTransfer.addin"
            $PluginFolder = "$root\$ver\RhinoToRevitTransfer"

            if (Test-Path $ManifestPath) {
                Remove-Item -Path $ManifestPath -Force -ErrorAction SilentlyContinue
                Write-Host "  -> Removed manifest from: $ManifestPath" -ForegroundColor Green
            }
            if (Test-Path $PluginFolder) {
                Remove-Item -Path $PluginFolder -Recurse -Force -ErrorAction SilentlyContinue
                Write-Host "  -> Removed plugin files from: $PluginFolder" -ForegroundColor Green
            }
        }
    }
}

Write-Host "`nUninstallation complete. RhinoToRevitTransfer has been cleanly removed from all Revit locations." -ForegroundColor Green
Write-Host ""
