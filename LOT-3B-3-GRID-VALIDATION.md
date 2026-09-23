# LOT 3B-3 — Validation d'alignement, dérive et détection fiable de grille/combat

Date : 24 septembre 2026. Version : **0.4.1** (depuis 0.4.0). Branche : `lot-3b-3-grid-validation`,
créée depuis le commit de recette `e562765` (outillage 3B-2R, commité en ouverture de ce lot).

## Statuts

```
IMPLEMENTATION:                   PASS
SYNTHETIC:                        PASS
REAL CORPUS:                      PASS (26 frames réelles 3B-2R, une session, un layout)
COMBAT FALSE-POSITIVE REGRESSION: FIXED (12/14 → 0/14 en exploration, 12/12 combats reconnus)
MAP STALE DETECTION:              ADVISORY (simulée : 8/11 frames signalées, 0 faux positif sur 10 frames correctes)
```

## 1. Ce qui ne change pas

- GameData reste la source canonique de `DofusCellId`, `GridCoordinate`, l'adjacence, la
  walkability et la LOS statiques. Les 560 cellules sont toujours projetées, même sans rien de
  visible.
- `GridProjector` reste indépendant d'OpenCV. Le nouveau module `grid_validation.py` n'utilise
  que numpy : les candidats lui sont fournis par la couche OpenCV existante.
- Les contours ne créent, ne suppriment ni ne renumérotent aucune cellule. Ils sont seulement
  comparés aux centres déjà projetés. Un test de bout en bout vérifie les IDs 0..559 après une
  dérive compensée.
- Aucun état FREE n'est déduit : l'occupation reste UNKNOWN tant qu'elle n'est pas observée
  (test dédié).
- Rouge/bleu : aucune modification. Ce sont toujours des indices ; `fight_start_allowed` reste
  inconnu et aucun placement manquant n'est complété.
- Aucune action dans DOFUS. Le smoke test vérifie que le pipeline d'observation ne référence
  aucun `ActionExecutor`.

## 2. Architecture

```
frame ─▶ LayoutTransform ─▶ crop combat
          map déclarée ─▶ GameDataGridResolver ─▶ ProjectedGrid (base_transform + runtime_adjustment)
                                                     │
            candidate_union (OpenCV, existant) ──────┤
                                                     ▼
                        GridVisibilityDetector ─▶ GridVisibilityObservation   (la grille est-elle dessinée ?)
                        GridAlignmentValidator ─▶ GridAlignmentObservation    (est-elle bien alignée ?)
                        DriftTracker (temporel) ─▶ runtime_adjustment borné   (frame suivante)
                        MapConsistencyTracker   ─▶ MapDeclarationState        (avertissement)
                        CombatStateDetector     ─▶ COMBAT / EXPLORATION / UNKNOWN
```

Fichiers :
- **Nouveau** : `combatbot/vision/grid_validation.py`,
  `combatbot/corpus/grid_validation_benchmark.py`, `tests/test_grid_validation.py` (24 tests).
- **Modifiés** :
  - `combat_observer.py` : `_validate_grid`, nouvel état de combat, overlay `alignment_debug`,
    métadonnées ;
  - `combat_models.py` : champs `grid_visibility`, `alignment`, `drift`, `map_declaration`,
    `combat_state` ;
  - `gamedata_grid.py` : `runtime_adjustment` appliqué au transform de base ;
  - `combat_tracker.py` : UNKNOWN ne vote ni pour le combat ni pour l'exploration ;
  - `pages.py` : affichage Vision réelle ;
  - `benchmark.py` : `--grid-validation` ;
  - `packaging_smoke.py`, `grid_fit.py` (documentation du seuil historique), README, version.

## 3. Métriques et méthode

Toutes les limites sont exprimées en fraction de la **hauteur de cellule projetée** (`cell_h`) et
regroupées dans des configurations immuables et testables : `MatchConfig`, `VisibilityConfig`,
`AlignmentConfig`, `DriftConfig`, `MapConsistencyConfig`. Elles viennent d'une seule session, d'un
seul layout et de 7 maps. Ce ne sont pas des vérités universelles, et UNKNOWN reste la réponse
quand la preuve manque.

**Appariement.** Chaque candidat retenu :
- a une taille comprise entre 0,55 et 1,35 fois celle de la cellule projetée ;
- tombe dans un losange projeté (lookup O(1) existant).

L'appariement se fait en **deux passes**, parce qu'une grille qui a légèrement dérivé reste une
grille dessinée :
1. on estime la translation globale comme la médiane des vecteurs résiduels ;
2. un candidat est « compatible » s'il est à moins de 0,3 × `cell_h` de cette translation.

**Rejet des aberrants : médiane + MAD.**
- Sur les résidus des compatibles, après retrait de la translation globale, on garde
  `r ≤ médiane + 3 × 1,4826 × MAD`, avec un plancher de 0,03 × `cell_h`.
- Seuls les candidats posés sur des cellules traversables en combat comptent comme preuve de
  grille, puisque DOFUS ne dessine que celles-là.
- Le résidu **médian** (des inliers) décide. Le p95 et le max sont calculés sur tous les
  compatibles, aberrants inclus, et restent **diagnostiques**.
- La moyenne n'est jamais utilisée.

**Couverture spatiale.**
- On découpe en 3 × 3 régions la boîte englobante des centres des cellules traversables visibles.
- Une région est « attendue » si elle contient au moins 3 cellules traversables.
- Elle est « soutenue » si elle contient au moins 2 inliers.
- La couverture est le rapport régions soutenues / régions attendues.

### GridVisibility : « la grille de combat DOFUS est-elle dessinée ? »

| État | Règle (défauts) |
| --- | --- |
| VISIBLE | inliers ≥ max(10 ; 8 % des cellules traversables visibles), **et** ≥ 3 régions soutenues avec couverture ≥ 0,5, **et** résidu médian (sans la translation) ≤ 0,1 × `cell_h`, **et** cohérence ≥ 0,1 si la walkability est connue |
| NOT_VISIBLE | inliers ≤ max(3 ; 3 % des cellules attendues), ou couverture ≤ 0,25 avec cohérence < 0,1 |
| UNKNOWN | tous les autres cas (preuves contradictoires ou partielles) |

Chaque observation expose l'état, la confiance, `candidate_count`, `compatible_count`,
`inlier_count`, `expected_cells`, `coverage`, les régions, `topology_consistency`,
`residual_median`, les preuves et les raisons.

La topologie existe toujours : la visibilité ne répond qu'à la question de l'affichage.

### GridAlignmentValidator : micro-ajustement seulement

- La **translation** est la médiane, axe par axe, des vecteurs résiduels des inliers.
- L'**échelle uniforme** est estimée par moindres carrés des résidus restants autour du centre des
  inliers. Elle est bornée à ± 1 % et ramenée à 0 sous 0,2 %.
- Le **consensus spatial** exige qu'au moins 3 régions aient chacune une translation médiane à
  moins de 0,05 × `cell_h` de la translation globale. Sinon : INSUFFICIENT_EVIDENCE.
- Statuts :

| Statut | Règle |
| --- | --- |
| ALIGNED | translation ≤ 0,05 × `cell_h` et échelle nulle |
| DEGRADED | translation ≤ 0,15 × `cell_h` (fenêtre locale) ; correction **proposée**, jamais appliquée directement |
| MISALIGNED | translation hors de la fenêtre, ou résidu médian après correction > 0,15 × `cell_h` ; raison `RECALIBRATION_REQUIRED`, aucune correction proposée |
| INSUFFICIENT_EVIDENCE | grille non VISIBLE, ou consensus spatial insuffisant |

- Il n'y a **aucune** recherche de ±1 cellule, de permutation d'axes, de miroir ou de nouvelle
  origine.
- Dimensionnement de la fenêtre : la recette a mesuré une dérive inter-frames ≤ 3,34 px, soit
  0,056 × `cell_h`. La fenêtre de 0,15 × `cell_h` (≈ 9 px à 59,6 px) laisse une marge tout en
  restant très inférieure à une cellule (vecteur de base ≈ 67 px).

### DriftTracker : consensus temporel, base immuable

- `base_transform` est celui du profil confirmé : **jamais modifié**.
  `runtime_adjustment` (dx, dy, scale) n'existe qu'en mémoire.
  `effective_transform = runtime_adjustment.apply(base_transform)`.
- Chaque observation est mesurée contre le transform effectif ; une correction confirmée
  s'ajoute à l'ajustement courant.
- États :
  - `ALIGNED` ;
  - `DRIFT_SUSPECTED` : 1 ou 2 frames DEGRADED, ou corrections en désaccord ;
  - `DRIFT_CONFIRMED` : 3 corrections consécutives d'accord à 0,03 × `cell_h` près ;
    l'ajustement est alors mis à jour ;
  - `RECOVERED` : de nouveau aligné après une dérive ou une alerte, ou retour à zéro après
    4 frames sans preuve ;
  - `RECALIBRATION_REQUIRED` : 3 frames MISALIGNED consécutives, ou ajustement cumulé qui
    dépasserait la fenêtre ; l'ajustement est remis à zéro et n'est jamais appliqué ;
  - `INSUFFICIENT_EVIDENCE`.
- Le nouvel ajustement ne s'applique qu'à la frame suivante, sans `sleep()`.

### MapConsistencyTracker : relatif, avertissement seulement

- Le statut « map périmée » ne repose plus sur le seuil absolu `topology_consistency < 0,65`.
  La constante reste dans `grid_fit.py` pour les seuls critères historiques de la recette 3B-2R.
- Chaque map déclarée a sa baseline : la médiane de ses 5 dernières valeurs cohérentes, relevées
  grille VISIBLE.
- Le tracker expose `absolute_consistency`, `baseline_consistency` et `delta_from_baseline`.
- États :

| État | Condition |
| --- | --- |
| UNKNOWN | grille non VISIBLE (exploration : jamais de conclusion), ou aucun map ID |
| CONSISTENT | delta < 0,08 (la baseline est mise à jour), ou baisse modérée < 0,12 (baseline gelée) |
| SUSPECT | delta ≥ 0,12 |
| STALE_LIKELY | delta ≥ 0,15 sur 2 frames consécutives (`manual_guess`) ou 3 frames (`user_verified_mapid`). Une seule frame basse ne dépasse jamais SUSPECT pour une map vérifiée. |

- Sans baseline, seule une cohérence absolue < 0,35 donne SUSPECT.
- Message affiché : « Le map ID déclaré semble ne plus correspondre à la grille observée. » ;
  jamais « vous êtes sur la map X ».

### CombatStateDetector : topologie ≠ grille visible ≠ combat actif

- **GameData projetée** : COMBAT si la grille est VISIBLE, EXPLORATION si NOT_VISIBLE, UNKNOWN
  sinon.
  - Les signaux d'interface (compteurs, fin de tour, barre de sorts) ne modulent que la
    confiance : sur le client réel, ils sont aussi actifs en exploration.
  - Les 560 cellules ne sont **jamais** une preuve, et `len(grid.cells)` n'est plus utilisé nulle
    part.
- **Sources historiques** (`LEGACY_CALIBRATION` / `VISION_DETECTED`) : la décision repose sur la
  confiance visuelle de la grille (≥ 0,4 et score combiné ≥ 0,45 pour COMBAT ; < 0,2 pour
  EXPLORATION), jamais sur le nombre de cellules.
- **Tri-état** : `combat_state` vaut COMBAT, EXPLORATION ou UNKNOWN. `combat_detected` reste
  booléen et n'est vrai que pour COMBAT. Dans le suivi temporel, UNKNOWN ne vote pas, ce qui
  conserve l'état stable précédent au lieu de forcer True.

## 4. Interface et overlay

**Vision réelle** affiche désormais :
- **Grille** : Visible / Non visible / Incertaine ;
- **Alignement** : OK / Dégradé / Recalibration nécessaire / Preuves insuffisantes ;
- **Map déclarée** : ID et « vérifiée par /mapid » ou « non vérifiée » ;
- **Cohérence map** : OK / Suspecte / Inconnue, avec le message d'avertissement ;
- **Correction runtime** : dx, dy, échelle (« profil inchangé »).

Les raisons détaillées vont dans « Signaux visuels ». L'overlay « diagnostic alignement » montre :
- les centres du transform de base (croix grises, s'ils diffèrent de l'effectif) ;
- les inliers (points verts) et les aberrants (croix rouges) ;
- les régions soutenues ;
- la correction proposée (flèche amplifiée ×8) et celle appliquée.

## 5. Tests synthétiques

`tests/test_grid_validation.py` compte **24 tests**. Ils couvrent les 19 noms demandés :
`test_projected_topology_does_not_imply_grid_visible`, `test_exploration_grid_not_visible`,
`test_combat_grid_visible`, `test_grid_visibility_unknown_on_weak_evidence`,
`test_alignment_uses_spatial_coverage`, `test_alignment_uses_median_residual`,
`test_outlier_does_not_move_transform`, `test_small_runtime_translation_can_be_suggested`,
`test_full_cell_shift_is_never_auto_applied`, `test_runtime_adjustment_does_not_mutate_profile`,
`test_temporal_consensus_required_for_drift`, `test_runtime_adjustment_recovers_to_zero`,
`test_stale_map_not_decided_when_grid_not_visible`,
`test_single_low_consistency_does_not_mark_verified_map_stale`,
`test_relative_consistency_drop_marks_suspect`,
`test_correct_map_low_absolute_consistency_can_remain_valid` (valeurs réelles 0,601 et 0,620),
`test_combat_detection_does_not_use_cell_count`, `test_exploration_not_detected_as_combat`,
`test_existing_combat_still_detected`.

S'y ajoutent :
- une translation hors fenêtre donnant MISALIGNED alors que la grille reste VISIBLE ;
- une dérive observée de bout en bout, confirmée après consensus puis réalignée, sans modifier le
  profil ;
- l'absence d'état FREE ;
- le benchmark optionnel et le benchmark sur un mini-corpus.

L'ancien test qui imposait l'alerte absolue < 0,65 a été adapté : cette alerte est supprimée.

**Suite complète : 235 tests passés** (2 passages), `compileall` OK.

## 6. Mesures sur le corpus réel

Commande : `python -m combatbot.benchmark --grid-validation`. Résultats dans
`data/benchmarks/grid-validation.json` et `.md`, rejoués sur les 26 frames `lot3b2r` :
14 en exploration, 5 en placement, 7 en combat.

La vérité terrain vient uniquement des annotations humaines de la recette : `combat_truth` et le
mode déclaré par l'utilisateur. Les anciennes prédictions ne servent que de comparaison « avant ».

### GRID VISIBILITY

| Vérité \ Prédit | VISIBLE | NOT_VISIBLE | UNKNOWN |
| --- | ---: | ---: | ---: |
| Grille dessinée (placement/combat) | **12** | 0 | 0 |
| Pas de grille (exploration) | 0 | **14** | 0 |

- Frames avec grille : 75 à 170 inliers, 6 à 9 régions soutenues.
- Frames d'exploration : 0 à 6 inliers ; le décor de ville d'Astrub ne dépasse pas 6.

### COMBAT DETECTION : avant / après

| | Exploration → COMBAT (faux positifs) | Combat → COMBAT (rappel) |
| --- | ---: | ---: |
| Avant (0.4.0, prédictions enregistrées) | **12 / 14 = 0,857** | 12 / 12 |
| Après (0.4.1) | **0 / 14 = 0,0** | **12 / 12** |

Matrice après correction : exploration → EXPLORATION 14 ; combat → COMBAT 12 ; aucun UNKNOWN.

### ALIGNMENT (12 frames avec grille)

- ALIGNED : **12 / 12** ; aucune correction runtime proposée.
- Résidu médian des inliers par frame : 0,90 à 1,57 px (médiane 1,17). Le max diagnostique va de
  3,5 à 15,8 px ; il était de 32,8 à 57,9 px avant le rejet des aberrants.
- Couverture : moyenne 0,98, minimum 0,86.

**Dérives injectées** sur une frame réelle (C011, 3e salle, `cell_h` = 59,6 px) :

| Transform appliqué | Visibilité | Statut | Correction proposée |
| --- | --- | --- | --- |
| confirmé | VISIBLE | ALIGNED | — |
| +3 px | VISIBLE | DEGRADED | (−3,43 ; 0,08) |
| +5 / −3 px | VISIBLE | DEGRADED | (−5,43 ; 3,08) |
| +8 px | VISIBLE | DEGRADED | (−8,43 ; 0,08) |
| +12 px | VISIBLE | MISALIGNED | aucune (RECALIBRATION_REQUIRED) |
| échelle +0,6 % | VISIBLE | DEGRADED | échelle −0,61 % |
| +1 cellule (U) | VISIBLE | ALIGNED | aucune (voir limites) |

Le transform confirmé porte un léger écart intrinsèque de −0,43 px en x, visible dans chaque
correction.

### MAP CONSISTENCY

- **Exploration** : 14 / 14 → UNKNOWN, aucune conclusion hors combat.
- **Map correcte** : 10 / 10 frames → CONSISTENT. La map 191106050, à 0,601 et 0,620, serait
  signalée par l'ancien seuil de 0,65 ; elle ne l'est plus.
- **Map périmée simulée** : pour chaque changement de map, les frames de la nouvelle map sont
  évaluées avec la topologie de l'ancienne, après que la baseline a été établie sur l'ancienne.
  C'est ce qui se passerait si la map n'était pas redéclarée. Sur 11 frames :
  - **8 signalées** : 7 SUSPECT, 1 STALE_LIKELY ;
  - 2 restent CONSISTENT (baisses de 0,114 et 0,116, sous le seuil de 0,12) ;
  - 1 est UNKNOWN : avec la mauvaise topologie, les preuves de visibilité deviennent partielles.
- Chaque changement de map dont la grille reste évaluable (4 sur 5) est signalé au moins une fois.
- Seuils conservés volontairement : sur une map correcte, deux frames ont déjà varié de 0,14
  (0,66 puis 0,80). Abaisser le seuil de SUSPECT créerait des faux positifs.

## 7. Performance

Le pipeline observateur complet, mesuré sur des frames réelles 2560 × 1377 (zone combat
2555 × 1151), prend :
- **160 à 195 ms** par frame en combat ;
- **225 à 300 ms** en exploration, où le décor produit plus de contours ;

contre 85 à 96 ms en 0.4.0. Le surcoût vient de la détection de candidats (union de deux seuils
Canny), désormais faite à chaque frame. Cela reste sous l'intervalle d'observation de 400 ms.

## 8. Limites

- **Données limitées** : une session, un layout (2560 × 1377), 7 maps et 26 frames. Aucune
  robustesse universelle n'est annoncée ; les seuils sont documentés, relatifs à `cell_h` et
  configurables.
- **±1 cellule indétectable localement** : un transform décalé d'une cellule donne ALIGNED, car
  les centres coïncident avec d'autres cellules. Ce n'est jamais appliqué : l'ajustement est borné
  à 0,15 × `cell_h`, et un test le garantit. La seule trace est une baisse de cohérence (0,655 →
  0,559, delta 0,096), sous le seuil de SUSPECT. **La calibration initiale confirmée par un humain
  reste l'autorité.**
- **Map périmée : avertissement, pas preuve.** 2 frames sur 11 ne sont pas signalées, les
  baisses dépendent de la paire de maps, et la bonne map varie elle-même jusqu'à 0,14.
- **Dérive réelle jamais observée** : les 12 frames réelles sont ALIGNED. Le micro-ajustement
  n'est validé que sur dérives injectées et en synthétique.
- **Signaux d'interface non discriminants** sur ce client : la décision de combat repose sur la
  grille dessinée. Un mode d'affichage qui dessinerait la grille hors combat (mode tactique ?)
  n'a pas été testé.
- Surcoût CPU par frame (§ 7).

## 9. Build et smoke test

- Suite ciblée 3B-3 : 24 tests passés. Suite complète : 235 tests passés, 2 fois. `compileall` OK.
- Banc sur le corpus réel : disponible et exécuté (§ 6).
- `build_exe.ps1` (interpréteur `.venv\validation`, PyInstaller 6.22.3) : **réussi**,
  `dist\PythonBot\PythonBot.exe`, 380,4 Mio.
- Smoke test (`--package-smoke-test`, données isolées, `PYTHONBOT_SMOKE_SKIP_DOFUS=1`) :
  **success = true**.
  - `grid_validation` : 560 cellules projetées ; image vide → NOT_VISIBLE /
    INSUFFICIENT_EVIDENCE ; grille dessinée → VISIBLE / ALIGNED ;
    `action_executor_invoked = false`.
  - `gamedata_grid` : 560 cellules.

## 10. Commit

Voir la réponse finale : commit `fix: validate projected grid and combat state` sur
`lot-3b-3-grid-validation`.

**Arrêt ici. LOT 3B-4 non commencé.**
