[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements-dev.txt"
$Spec = Join-Path $ProjectRoot "PythonBot.spec"
$BuildDirectory = Join-Path $ProjectRoot "build"
$DistDirectory = Join-Path $ProjectRoot "dist"
$Executable = Join-Path $DistDirectory "PythonBot\PythonBot.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Environnement virtuel introuvable : $Python. Créez-le avec python -m venv .venv."
}
if (-not (Test-Path -LiteralPath $Spec -PathType Leaf)) {
    throw "Configuration PyInstaller introuvable : $Spec"
}

Write-Host "Vérification de PyInstaller..."
& $Python -c "import importlib.metadata as m; from packaging.version import Version; v=Version(m.version('PyInstaller')); raise SystemExit(0 if Version('6.16') <= v < Version('7') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installation ou mise à jour de PyInstaller..."
    & $Python -m pip install --upgrade "PyInstaller>=6.16,<7"
    if ($LASTEXITCODE -ne 0) { throw "Installation de PyInstaller impossible." }
}

foreach ($Directory in @($BuildDirectory, $DistDirectory)) {
    $ResolvedRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    $ResolvedTarget = [IO.Path]::GetFullPath($Directory)
    if (-not $ResolvedTarget.StartsWith($ResolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Nettoyage refusé hors du projet : $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

Write-Host "Construction ONEDIR de PythonBot..."
$OriginalPath = $env:PATH
$PythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$VenvScripts = Split-Path -Parent $Python
$env:PATH = "$VenvScripts;$PythonBase;$PythonBase\DLLs;$env:SystemRoot\System32;$env:SystemRoot"
Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué avec le code $LASTEXITCODE." }
}
finally {
    Pop-Location
    $env:PATH = $OriginalPath
}

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Build incomplet : $Executable est introuvable."
}

$PackageSize = (Get-ChildItem -LiteralPath (Split-Path $Executable) -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
$SizeMiB = [Math]::Round($PackageSize / 1MB, 1)
Write-Host "Build terminé : $Executable"
Write-Host "Taille du package : $SizeMiB MiB"
