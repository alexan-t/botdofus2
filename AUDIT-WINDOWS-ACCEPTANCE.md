# Audit de la recette Windows — état au 28/09/2026

## 0. Statut : recette NON EXÉCUTÉE dans cette session

Cette session tourne dans un **conteneur Linux cloud**, pas sur le PC de l'utilisateur :

- `C:\Users\Thoma\Documents\pythonbot_test` est inaccessible ;
- `%LOCALAPPDATA%` est absent ;
- le corpus local ne contient qu'un manifeste vide ;
- ni DOFUS, ni PowerShell, ni sessions réelles journalisées.

La recette `scripts\run_windows_acceptance.ps1` n'a donc **pas pu être lancée**. Aucun résultat Windows n'est inventé : toutes les sections qui en dépendent restent **NON MESURÉES**.

Tout ce qui pouvait être préparé sans le PC l'a été, pour qu'**une seule commande** sur le PC produise les mesures manquantes (voir § 6).

| | |
|---|---|
| Commit préparé | tête de `lot-windows-live-readiness` (base `3407de7`) |
| OS / Python de cette session | Linux 6.18 (conteneur) / Python 3.11.15 |
| Profil | non déterminable ici. Le script le choisit seulement s'il est **unique** ; sinon il s'arrête avec la commande à relancer. Une base neuve contient déjà « Profil 1 » : dès qu'un autre profil existe, `-ProfileId` sera demandé |

## 1. Les 3 échecs Linux historiques

| Test | Cause exacte | Sous Linux maintenant | Attendu sous Windows |
|---|---|---|---|
| `test_settings_ux.py::test_browser_titled_dofus_is_not_a_game_window` | `ctypes.WINFUNCTYPE` n'existe que sous Windows ; le test simule `user32` mais la fonction utilisait directement `WINFUNCTYPE` | **PASS** : repli `CFUNCTYPE`, type inchangé sous Windows (`069dd5a`) | PASS (même code qu'avant sous Windows) — **à confirmer par la recette** |
| `test_window_capture.py::test_window_selection_by_handle_and_client_rect` | même cause | **PASS** (`069dd5a`) | PASS — **à confirmer** |
| `test_vision.py::test_rapidocr_reads_synthetic_visible_text` | module `rapidocr` absent du conteneur ; son installation échoue ici (compilation de `antlr4-python3-runtime`) | FAIL (environnement, pas le code) | dépend du `.venv` du PC : `rapidocr` fait partie de `requirements.txt`. La recette lance ce test **en premier** et vérifie la présence du module |

Aucun de ces tests n'est masqué, marqué xfail ou supprimé.

## 2. Suite complète (cette session)

- **757 passed, 2 skipped, 1 failed.** L'échec restant est RapidOCR absent de l'environnement.
- Base `3407de7` : 730 passed, 3 failed.
- `git diff --check` propre sur chaque commit.

## 3. Rapports de la recette — NON MESURÉS ici

| Rapport | Produit par | Statut |
|---|---|---|
| observation-e2e | `--observation-e2e` | non mesuré (corpus vide ici) |
| dry-run-plans | `--dry-run-plans --profile-id N` | non mesuré |
| acceptance 3B-7 | `--acceptance` | non mesuré ; dernier état connu : FAIL (latence p95 979 ms, PC n° 1) |
| corpus avant/après | instantané du script | non mesuré ; le script échoue si un fichier change |
| sorts du profil | `--spell-readiness` | non mesuré (nouveau) |
| latence par étape | `--runtime-profile` | non mesuré (nouveau) |
| cellule joueur | `--player-cell-diagnostics` | non mesuré (nouveau) |
| preuve 4C | `--targeting-proof-report` | aucun échantillon collecté |

### Blocages attendus du dry-run (analyse du code, pas une mesure)

Avec les règles prudentes par défaut, dès qu'un état est complet, le premier refus sera « **sort possible mais non prouvé** », parce que la portée et la LOS sont UNVERIFIED. En amont, les refus attendus sont :
- « observation non sûre (`safe_for_decision`) » ;
- « cellule du joueur inconnue » ;
- « tour … : pas mon tour ».

Le top 10 réel sort de `dry-run-plans.json`, dans `blocked_reasons`.

## 4. Warnings connus

- `combatbot/ui/main_window.py` importe `cv2` sans l'utiliser (pyflakes). Ce warning est antérieur et n'a pas été corrigé.
- Le taux « cellule joueur inconnue » du résumé de session 3B-7 compte **toutes** les frames, exploration comprise. Les ~90,8 % publiés mélangent donc des frames hors combat. `--player-cell-diagnostics` sépare désormais les deux.

## 5. Performances mesurées dans cette session (synthétique)

| Mesure | Avant | Après |
|---|---|---|
| Traitement après capture, client 2560×1377 (2 conversions + 2 écarts types plein cadre → 1 conversion + écart type sur 1/64, complet seulement si doute) | ≈ 185 ms | ≈ 27 ms |
| Projection GameData (`projected_observation`, 560 cellules, zone combat ≈ 2100×1000) | ≈ 11 ms | inchangé |

Les « ≈ 120 ms » de l'étape grid ne viennent donc pas de la projection. Elles viennent probablement de la validation, désormais journalisée à part (`grid_ms.validate`). La mesure réelle viendra de `--runtime-profile`, après une session sur le PC.

## 6. Ce qu'il faut lancer sur le PC

```
powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1
```

Si le profil est ambigu, relancer avec `-ProfileId N`. Puis, après une session live guidée (`RECETTE-LIVE-FASTTRACK.md`) :

```
.venv\Scripts\python -m combatbot.benchmark --live-fasttrack-report --profile-id N
```
