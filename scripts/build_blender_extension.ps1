param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$OutputDir = "",
    [switch]$CleanDist
)

$ErrorActionPreference = "Stop"

function Get-ManifestValue {
    param(
        [string]$ManifestText,
        [string]$Key
    )
    $pattern = "(?m)^\s*$Key\s*=\s*`"([^`"]+)`""
    $match = [regex]::Match($ManifestText, $pattern)
    if (-not $match.Success) {
        throw "Missing '$Key' in blender_manifest.toml"
    }
    return $match.Groups[1].Value
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$ManifestPath = Join-Path $ProjectRoot "blender_manifest.toml"
if (-not (Test-Path $ManifestPath)) {
    throw "Cannot find manifest: $ManifestPath"
}

$ManifestText = Get-Content -Path $ManifestPath -Raw -Encoding UTF8
$ExtensionId = Get-ManifestValue -ManifestText $ManifestText -Key "id"
$ExtensionVersion = Get-ManifestValue -ManifestText $ManifestText -Key "version"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "dist"
}
$OutputDir = (Resolve-Path (New-Item -ItemType Directory -Force -Path $OutputDir)).Path

if ($CleanDist) {
    Get-ChildItem -Path $OutputDir -Filter "$ExtensionId-*.zip" -File -ErrorAction SilentlyContinue | Remove-Item -Force
}

$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("blender_ext_" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

try {
    $required = @("__init__.py", "addon.py", "blender_manifest.toml")
    foreach ($item in $required) {
        $itemPath = Join-Path $ProjectRoot $item
        if (-not (Test-Path $itemPath)) {
            throw "Missing required extension file: $item"
        }
    }

    Get-ChildItem -Path $ProjectRoot -Filter "*.py" -File |
        Where-Object { $_.Name -notin @("main.py") } |
        ForEach-Object {
            Copy-Item -Path $_.FullName -Destination (Join-Path $TempDir $_.Name) -Force
        }

    $extraFiles = @("blender_manifest.toml", "LICENSE", "README.md", "TERMS_AND_CONDITIONS.md")
    foreach ($file in $extraFiles) {
        $src = Join-Path $ProjectRoot $file
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $TempDir $file) -Force
        }
    }

    $assetsDir = Join-Path $ProjectRoot "assets"
    if (Test-Path $assetsDir) {
        Copy-Item -Path $assetsDir -Destination (Join-Path $TempDir "assets") -Recurse -Force
    }

    $zipName = "$ExtensionId-$ExtensionVersion.zip"
    $zipPath = Join-Path $OutputDir $zipName
    if (Test-Path $zipPath) {
        Remove-Item -Path $zipPath -Force
    }

    Compress-Archive -Path (Join-Path $TempDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

    Write-Host "Extension package created:" -ForegroundColor Green
    Write-Host $zipPath -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Install in Blender 4.2+:" -ForegroundColor Yellow
    Write-Host "Edit > Preferences > Extensions > Install from Disk..."
}
finally {
    if (Test-Path $TempDir) {
        Remove-Item -Path $TempDir -Recurse -Force
    }
}
