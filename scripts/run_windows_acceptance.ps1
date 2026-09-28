<#
.SYNOPSIS
    Recette Windows DofBot2 en UNE commande — lecture seule, AUCUNE action dans DOFUS.

.DESCRIPTION
    Vérifie Windows, l'environnement .venv et les dépendances, détecte la racine runtime
    (%LOCALAPPDATA%\PythonBot si elle contient des données, sinon le dépôt), puis lance :
      1. les 3 tests propres à Windows (échecs attendus sous Linux) ;
      2. la suite pytest complète ;
      3. l'auto-test offline de la chaîne d'exécution (--execution-selftest) ;
      4. le benchmark e2e d'observation (--observation-e2e) ;
      5. le rejeu dry-run des décisions (--dry-run-plans) ;
      6. la recette consolidée 3B-7 (--acceptance).
    Le corpus est photographié avant/après : toute modification fait échouer la recette.
    Tous les résultats sont copiés dans reports\windows-acceptance-<date>\ avec un récapitulatif
    SUMMARY.md à transmettre tel quel.

    Ce script ne lance ni l'exécutable DofBot2 ni DOFUS, n'envoie aucun clic ni aucune touche, et retire
    DOFBOT_ALLOW_REAL_INPUT de son environnement : la porte d'entrée réelle reste fermée.

.PARAMETER ProfileId
    Profil dont les sorts confirmés servent au rejeu dry-run (défaut : dernier profil utilisé).

.PARAMETER AssumeLogicalRange
    Active EXPLICITEMENT l'hypothèse de portée logique (non prouvée) pour le rejeu dry-run.

.PARAMETER DataRoot
    Racine runtime à utiliser au lieu de la détection automatique.

.PARAMETER Client
    Dossier du client DOFUS (GameData) si le réglage de l'application est absent.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1 -ProfileId 1
#>
[CmdletBinding()]
param(
    [int]$ProfileId = 0,
    [switch]$AssumeLogicalRange,
    [string]$DataRoot = "",
    [string]$Client = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path $ProjectRoot "reports\windows-acceptance-$Stamp"
$Steps = New-Object System.Collections.Generic.List[object]

function Add-Step([string]$Name, [string]$Verdict, [int]$ExitCode, [string]$Detail) {
    $Steps.Add([pscustomobject]@{ Name = $Name; Verdict = $Verdict; ExitCode = $ExitCode; Detail = $Detail })
    Write-Host ("[{0}] {1} (code {2}) {3}" -f $Verdict, $Name, $ExitCode, $Detail)
}

# --- 1. Windows, interpréteur, dépendances -----------------------------------------------------
if ($env:OS -ne "Windows_NT") { throw "Cette recette ne s'exécute que sous Windows." }
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

function Test-PythonInterpreter([string]$Candidate) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    try { & $Candidate -c "import sys" 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
$Python = $null
foreach ($Candidate in @((Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
                         (Join-Path $ProjectRoot ".venv\validation\Scripts\python.exe"))) {
    if (Test-PythonInterpreter $Candidate) { $Python = $Candidate; break }
}
if ($null -eq $Python) {
    throw "Aucun .venv valide. Créez-le (python -m venv .venv) puis installez-y requirements-dev.txt avec pip."
}
Add-Step "Interpréteur" "OK" 0 $Python

$Modules = "PySide6 cv2 numpy rapidocr onnxruntime pytest"
$ErrorActionPreference = "Continue"
& $Python -c "import importlib.util, sys; missing=[m for m in '$Modules'.split() if importlib.util.find_spec(m) is None]; print(','.join(missing)); sys.exit(1 if missing else 0)" 2>&1 |
    Out-File (Join-Path $ReportDir "dependencies.txt") -Encoding utf8
$DependencyCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($DependencyCode -ne 0) {
    Add-Step "Dépendances" "FAIL" $DependencyCode ("manquantes : " + (Get-Content (Join-Path $ReportDir "dependencies.txt") -Raw).Trim())
    throw "Dépendances manquantes : installez requirements-dev.txt dans .venv (aucune installation automatique)."
}
Add-Step "Dépendances" "OK" 0 $Modules

# --- 2. Racine runtime ---------------------------------------------------------------------------
if ($DataRoot -eq "") {
    $Local = Join-Path $env:LOCALAPPDATA "PythonBot"
    if (Test-Path -LiteralPath (Join-Path $Local "data")) { $DataRoot = $Local } else { $DataRoot = $ProjectRoot }
}
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$env:PYTHONBOT_DATA_DIR = $DataRoot
$env:PYTHONUTF8 = "1"
Remove-Item Env:DOFBOT_ALLOW_REAL_INPUT -ErrorAction SilentlyContinue      # porte d'entrée réelle fermée
$Corpus = Join-Path $DataRoot "data\corpus"
Add-Step "Racine runtime" "OK" 0 $DataRoot

function Get-CorpusSnapshot {
    if (-not (Test-Path -LiteralPath $Corpus)) { return @() }
    Get-ChildItem -LiteralPath $Corpus -Recurse -File | Sort-Object FullName | ForEach-Object {
        "{0}|{1}|{2}" -f $_.FullName.Substring($Corpus.Length), $_.Length, $_.LastWriteTimeUtc.Ticks
    }
}
$Before = @(Get-CorpusSnapshot)
Add-Step "Corpus (avant)" "OK" 0 "$($Before.Count) fichier(s) sous $Corpus"

function Invoke-Logged([string]$Name, [string]$Log, [string[]]$Arguments) {
    Push-Location $ProjectRoot
    # Python écrit aussi sur stderr : sous Windows PowerShell 5.1, « Stop » en ferait une erreur
    # fatale. Seul le code de sortie fait foi (même règle que build_exe.ps1).
    $ErrorActionPreference = "Continue"
    try {
        & $Python @Arguments 2>&1 | Out-File (Join-Path $ReportDir $Log) -Encoding utf8
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = "Stop"
        Pop-Location
    }
    $Tail = (Get-Content (Join-Path $ReportDir $Log) -Tail 1 -ErrorAction SilentlyContinue)
    $Verdict = if ($Code -eq 0) { "PASS" } else { "FAIL" }
    Add-Step $Name $Verdict $Code ("$Tail").Trim()
    return $Code
}

# --- 3. Tests -----------------------------------------------------------------------------------
$WindowsTests = @(
    "tests/test_settings_ux.py::test_browser_titled_dofus_is_not_a_game_window",
    "tests/test_vision.py::test_rapidocr_reads_synthetic_visible_text",
    "tests/test_window_capture.py::test_window_selection_by_handle_and_client_rect"
)
Invoke-Logged "Tests propres à Windows (3 échecs Linux connus)" "pytest-windows.txt" (@("-m", "pytest", "-q", "-p", "no:cacheprovider") + $WindowsTests) | Out-Null
Invoke-Logged "Suite pytest complète" "pytest-full.txt" @("-m", "pytest", "-q", "-p", "no:cacheprovider") | Out-Null

# --- 4. Chaîne d'exécution et benchmarks (lecture seule) -----------------------------------------
Invoke-Logged "Auto-test d'exécution (aucune entrée réelle)" "execution-selftest.txt" `
    @("-m", "combatbot.benchmark", "--execution-selftest", "--output-dir", $ReportDir) | Out-Null
Invoke-Logged "Benchmark e2e d'observation" "observation-e2e.txt" @("-m", "combatbot.benchmark", "--observation-e2e") | Out-Null
$DryRun = @("-m", "combatbot.benchmark", "--dry-run-plans")
if ($ProfileId -gt 0) { $DryRun += @("--profile-id", "$ProfileId") }
if ($AssumeLogicalRange) { $DryRun += "--assume-logical-range" }
if ($Client -ne "") { $DryRun += @("--client", $Client) }
Invoke-Logged ("Rejeu dry-run des décisions" + ($(if ($AssumeLogicalRange) { " (HYPOTHÈSE de portée logique)" } else { " (règles prudentes)" }))) `
    "dry-run-plans.txt" $DryRun | Out-Null
Invoke-Logged "Recette consolidée 3B-7" "acceptance.txt" @("-m", "combatbot.benchmark", "--acceptance", "--output-dir", $ReportDir) | Out-Null

# --- 5. Corpus inchangé, collecte ---------------------------------------------------------------
$After = @(Get-CorpusSnapshot)
$Changed = @(Compare-Object -ReferenceObject $Before -DifferenceObject $After)
if ($Changed.Count -eq 0) {
    Add-Step "Corpus inchangé" "PASS" 0 "$($After.Count) fichier(s), 0 différence"
} else {
    $Changed | Out-File (Join-Path $ReportDir "corpus-diff.txt") -Encoding utf8
    Add-Step "Corpus inchangé" "FAIL" 1 "$($Changed.Count) différence(s) : voir corpus-diff.txt"
}
$Benchmarks = Join-Path $DataRoot "data\benchmarks"
foreach ($Name in @("observation-e2e.json", "observation-e2e.md", "dry-run-plans.json", "dry-run-plans.md")) {
    $Source = Join-Path $Benchmarks $Name
    if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination $ReportDir -Force }
}

function Read-Report([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json) } catch { return $null }
}
$SelfTest = Read-Report (Join-Path $ReportDir "execution-selftest.json")
$E2E = Read-Report (Join-Path $ReportDir "observation-e2e.json")
$Plans = Read-Report (Join-Path $ReportDir "dry-run-plans.json")
$Acceptance = Read-Report (Join-Path $ReportDir "acceptance-3b7.json")
$Verdicts = @(
    "| Rapport | Verdict |", "|---|---|",
    "| Auto-test d'exécution | $(if ($SelfTest) { $SelfTest.verdict } else { 'absent' }) |",
    "| Observation e2e (3B-7 offline) | $(if ($E2E) { $E2E.overall } else { 'absent' }) |",
    "| Rejeu dry-run (plans) | $(if ($Plans) { ($Plans.statuses | ConvertTo-Json -Compress) } else { 'absent' }) |",
    "| Recette consolidée 3B-7 | $(if ($Acceptance) { $Acceptance.overall } else { 'absent' }) |"
)

# --- 6. Récapitulatif -----------------------------------------------------------------------------
$Commit = (& git -C $ProjectRoot rev-parse --short HEAD 2>$null)
$Branch = (& git -C $ProjectRoot rev-parse --abbrev-ref HEAD 2>$null)
$Summary = @(
    "# Recette Windows DofBot2 — $Stamp", "",
    "- Machine : $env:COMPUTERNAME · commit ``$Commit`` ($Branch)",
    "- Racine runtime : ``$DataRoot``",
    "- Profil : $(if ($ProfileId -gt 0) { $ProfileId } else { 'dernier utilisé' }) · hypothèse de portée logique : $(if ($AssumeLogicalRange) { 'OUI (explicite)' } else { 'non' })",
    "- **ACTIONS DANS DOFUS : AUCUNE** (aucun clic, aucune touche ; DofBot2 et DOFUS non lancés)", "",
    "| Étape | Verdict | Code | Détail |", "|---|---|---|---|"
)
foreach ($Step in $Steps) {
    $Summary += "| $($Step.Name) | **$($Step.Verdict)** | $($Step.ExitCode) | $($Step.Detail -replace '\|', '/') |"
}
$Summary += @("", "## Verdicts des rapports", "") + $Verdicts
$Summary += @("", "Fichiers joints : journaux *.txt, rapports JSON/Markdown des benchmarks, acceptance JSON.",
              "Transmettre le dossier entier : $ReportDir")
$Summary | Out-File (Join-Path $ReportDir "SUMMARY.md") -Encoding utf8
Write-Host ""
Write-Host "Récapitulatif : $(Join-Path $ReportDir 'SUMMARY.md')"
$Failed = @($Steps | Where-Object { $_.Verdict -eq "FAIL" })
if ($Failed.Count -gt 0) { exit 1 } else { exit 0 }
