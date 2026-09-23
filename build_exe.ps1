[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
# Interpréteur : .venv s'il est valide, sinon .venv\validation, sinon arrêt explicite.
# « Valide » = le fichier existe ET démarre (un venv copié depuis un autre compte
# pointe souvent vers un interpréteur de base absent).
function Test-PythonInterpreter([string]$Candidate) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    try {
        & $Candidate -c "import sys" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}
$Candidates = @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv\validation\Scripts\python.exe")
)
$Python = $null
foreach ($Candidate in $Candidates) {
    if (Test-PythonInterpreter $Candidate) { $Python = $Candidate; break }
    Write-Host "Interpréteur ignoré (absent ou invalide) : $Candidate"
}
$Requirements = Join-Path $ProjectRoot "requirements-dev.txt"
$Spec = Join-Path $ProjectRoot "PythonBot.spec"
$BuildDirectory = Join-Path $ProjectRoot "build"
$DistDirectory = Join-Path $ProjectRoot "dist"
$Executable = Join-Path $DistDirectory "PythonBot\PythonBot.exe"

if ($null -eq $Python) {
    throw "Aucun interpréteur valide : ni .venv\Scripts\python.exe ni .venv\validation\Scripts\python.exe. Créez un environnement avec python -m venv .venv puis installez requirements-dev.txt."
}
Write-Host "Interpréteur utilisé : $Python"
if (-not (Test-Path -LiteralPath $Spec -PathType Leaf)) {
    throw "Configuration PyInstaller introuvable : $Spec"
}

Write-Host "Vérification de PyInstaller..."
& $Python -c "import importlib.metadata as m; from packaging.version import Version; v=Version(m.version('PyInstaller')); raise SystemExit(0 if Version('6.16') <= v < Version('7') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    # Aucun téléchargement automatique : l'installation reste une décision explicite.
    throw "PyInstaller >=6.16,<7 absent de $Python. Installez-le explicitement : & '$Python' -m pip install -r requirements-dev.txt"
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
