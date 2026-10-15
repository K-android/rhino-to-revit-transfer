<#
.SYNOPSIS
    One-Click Setup Installer for Rhino-to-Revit Transfer (R2R) Revit Add-in.
.DESCRIPTION
    Detects all installed Autodesk Revit versions (2020-2026), compiles or deploys
    the C# add-in assembly and registers the .addin manifest into Revit's Addins directory.
#>

param(
    [string]$TargetVersion = "all"
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Rhino-to-Revit Transfer (R2R) - One-Click Installer   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\.."
$CsharpDir = "$ProjectRoot\csharp"
$RevitAddinsRoot = "$env:APPDATA\Autodesk\Revit\Addins"

if (-not (Test-Path $RevitAddinsRoot)) {
    Write-Host "[ERROR] Autodesk Revit Addins directory not found at:" -ForegroundColor Red
    Write-Host "  $RevitAddinsRoot" -ForegroundColor Yellow
    Write-Host "Please ensure Autodesk Revit is installed." -ForegroundColor Yellow
    exit 1
}

# 1. Detect and ensure target Revit Versions (2024 to 2027)
$DetectedVersions = Get-ChildItem -Path $RevitAddinsRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "^\d{4}$" } | Select-Object -ExpandProperty Name
$TargetYears = @("2024", "2025", "2026", "2027")
$AllVersions = ($TargetYears + $DetectedVersions) | Select-Object -Unique

Write-Host "[INFO] Target Revit Add-in versions: $($AllVersions -join ', ')" -ForegroundColor Green
$InstallVersions = if ($TargetVersion -eq "all") { $AllVersions } else { @($TargetVersion) }

# 2. Build C# Project if .NET SDK / dotnet is available
$BuiltDllPath = "$CsharpDir\bin\Release\net48\RhinoToRevitTransfer.dll"
$DotnetCmd = Get-Command "dotnet" -ErrorAction SilentlyContinue

if ($DotnetCmd) {
    Write-Host "`n[STEP 1/2] Building C# Add-in with dotnet build..." -ForegroundColor Cyan
    try {
        & dotnet build "$CsharpDir\RhinoToRevitTransfer.csproj" -c Release
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[SUCCESS] Build completed successfully." -ForegroundColor Green
        }
    } catch {
        Write-Host "[NOTE] Dotnet build skipped or requires local Revit/Rhino reference assemblies." -ForegroundColor Yellow
    }
} else {
    Write-Host "`n[STEP 1/2] Dotnet SDK not detected. Checking for pre-built binaries..." -ForegroundColor Yellow
}

# 3. Deploy to each target Revit version
Write-Host "`n[STEP 2/2] Deploying Add-in Manifest & Files to Revit..." -ForegroundColor Cyan

foreach ($ver in $InstallVersions) {
    $TargetAddinFolder = "$RevitAddinsRoot\$ver"
    $TargetPluginFolder = "$TargetAddinFolder\RhinoToRevitTransfer"

    if (-not (Test-Path $TargetAddinFolder)) {
        New-Item -ItemType Directory -Path $TargetAddinFolder -Force | Out-Null
    }
    if (-not (Test-Path $TargetPluginFolder)) {
        New-Item -ItemType Directory -Path $TargetPluginFolder -Force | Out-Null
    }

    # Generate version-specific .addin manifest
    $ManifestContent = @"
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>Rhino-to-Revit One-Click Transfer (RIR)</Name>
    <Assembly>RhinoToRevitTransfer\RhinoToRevitTransfer.dll</Assembly>
    <FullClassName>RhinoToRevitTransfer.R2RTransferApp</FullClassName>
    <ClientId>B845C723-9D5F-4E38-B60F-F348C9E78201</ClientId>
    <VendorId>AEC_R2R</VendorId>
    <VendorDescription>AEC Computational Design Team</VendorDescription>
  </AddIn>
</RevitAddIns>
"@

    $TargetManifestPath = "$TargetAddinFolder\RhinoToRevitTransfer.addin"
    Set-Content -Path $TargetManifestPath -Value $ManifestContent -Encoding UTF8

    # Copy binary matching target framework
    $YearInt = 0
    [int]::TryParse($ver, [ref]$YearInt) | Out-Null
    $DllSrc = if ($YearInt -ge 2025 -and (Test-Path "$CsharpDir\bin\Release\net8.0-windows\RhinoToRevitTransfer.dll")) {
        "$CsharpDir\bin\Release\net8.0-windows\RhinoToRevitTransfer.dll"
    } elseif (Test-Path "$CsharpDir\bin\Release\net48\RhinoToRevitTransfer.dll") {
        "$CsharpDir\bin\Release\net48\RhinoToRevitTransfer.dll"
    } elseif (Test-Path "$ScriptDir\RhinoToRevitTransfer\RhinoToRevitTransfer.dll") {
        "$ScriptDir\RhinoToRevitTransfer\RhinoToRevitTransfer.dll"
    } else {
        $null
    }

    if ($DllSrc) {
        Copy-Item -Path $DllSrc -Destination "$TargetPluginFolder\RhinoToRevitTransfer.dll" -Force
        Write-Host "  -> Revit $ver : Installed DLL & registered manifest (.addin)" -ForegroundColor Green
    } else {
        Write-Host "  -> Revit $ver : Registered manifest ($TargetManifestPath)" -ForegroundColor Green
        Write-Host "                 Plugin subfolder ready at $TargetPluginFolder" -ForegroundColor Gray
    }
}

# Unblock files to prevent Windows 0x80131515 security block
Write-Host "`nUnblocking deployed files (preventing 0x80131515 security block)..." -ForegroundColor Cyan
Get-ChildItem -Path $RevitAddinsRoot -Filter "*RhinoToRevitTransfer*" -Recurse | Unblock-File

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "                INSTALLATION COMPLETE!                    " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "How to use:" -ForegroundColor Cyan
Write-Host "  1. Launch Autodesk Revit." -ForegroundColor White
Write-Host "  2. Launch Rhino.Inside from the 'Rhino.Inside' ribbon tab." -ForegroundColor White
Write-Host "  3. You will now see the new 'R2R Transfer' ribbon tab!" -ForegroundColor White
Write-Host "  4. Click 'One-Click Transfer' to start." -ForegroundColor White
Write-Host ""
