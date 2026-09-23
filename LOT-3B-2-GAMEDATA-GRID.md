# LOT 3B-2 — Grille GameData : topologie réelle et projection écran

Date : 24 septembre 2026. Version : **0.4.0**, contre 0.3.3-gamedata.1 au départ.
Projet : `C:\Users\Thoma\OneDrive\Documents\pythonbot_test`. Client en lecture seule :
`C:\Users\Thoma\AppData\Local\Alea\Client`.

## Statut final

```
IMPLEMENTATION:          COMPLETE
SYNTHETIC VALIDATION:    PASS
REAL GAMEDATA:           PASS
REAL SCREEN PROJECTION:  PASS (limité : 1 map, 1 capture, map déclarée non vérifiée par /mapid)
```

La validation réelle de la projection repose sur une seule capture et une seule
map. Le map ID a été choisi par l'utilisateur parmi mes candidates, sans `/mapid`.
L'alignement a été vérifié visuellement dans quatre zones, avant que le joueur ne
change de salle. Ce n'est pas une recette utilisateur. Le détail est dans la section
« Validation réelle ».

## 1. État initial

- Le dossier n'était pas un dépôt Git (`fatal: not a git repository`).
- La suite comptait 168 tests. L'observateur réel reconstruisait la grille à chaque frame
  (`infer_combat_grid`) : l'origine, l'identité et le nombre de cellules dépendaient des contours
  (audit, § 9.2).
- GameData (LOT 3B-2A-R) : 12 153 maps v11 lues, 560 cellules chacune.
  La topologie logique était démontrée, mais n'était pas encore connectée à la vision.

## 2. Git et nettoyage

- `git init` à la racine. Le `.gitignore` exclut :
  - les environnements et caches : `.venv/`, `__pycache__/`, `.pytest_cache/`, `.pytest_tmp/` ;
  - les sorties de build : `build/`, `dist/` ;
  - les données locales : `data/` (dont `data/gamedata/real-validation/`), `logs/`, `*.log`,
    `*.sqlite3`, `*.bak` ;
  - les formats du client : `*.d2p`, `*.d2o`, `*.dlm`, `*.d2i`, `*.swf`, `*.ele` ;
  - les deux dumps concaténés du projet (`pythonbot_test.md`, 11 Mo, et `projet-pythonbot_test.md`),
    qui sont conservés sur disque mais pas versionnés.
- **Commit de référence** : `ec34e20a109245c0ae5c554cfb7950553d82bb20` sur `master`
  (`chore: baseline before gamedata grid integration`, 85 fichiers).
- Le travail du lot est sur la branche **`lot-3b-2-gamedata-grid`**. Il n'est **pas commité** :
  aucun commit n'a été demandé au-delà de la référence.
- **`scripts/inspect_client_abc.py` supprimé** avant le premier commit, pour qu'il n'entre jamais
  dans l'historique. Aucun fichier de code, de test ou de script n'y faisait référence (vérifié par grep).
  Seules deux mentions documentaires existaient ; elles ont été mises à jour.

## 3. Architecture

```
DofusCellId ──cell_to_grid()──▶ GridCoordinate          (GameData, topology.py : inchangé)
      │                              │
      │                   GridScreenTransform            (grid_projection.py : seul pont)
      │                              ▼
      └──────── GridProjector ──▶ ProjectedGrid ──▶ CombatPoint (centre + losange théorique)
                                                     │ LayoutTransform (inchangé)
                                                     ▼
                                             ClientPoint ──▶ ScreenPoint
```

Les deux domaines restent séparés :
- la **topologie GameData** (`DofusCellId`, `GridCoordinate`, voisinage, walkability et LOS statiques)
  n'est pas modifiée ;
- la **géométrie visuelle** (`CombatPoint`, `ClientPoint`, `ScreenPoint`, `LayoutTransform`)
  n'est pas modifiée non plus.

`GridScreenTransform` est l'unique pont entre les deux, et une `GridCoordinate` n'est jamais traitée
comme un pixel. Le lecteur D2P, le parseur DLM, le lecteur D2O, la formule et le voisinage n'ont
**pas** été touchés.

### Modèles ajoutés

| Module | Contenu |
| --- | --- |
| `vision/grid_projection.py` | `GridScreenTransform` (affine `p = origin + x·basis_x + y·basis_y`, immuable, sérialisé), `GridOrientation`, `GridSource`, `GridProjector`, `ProjectedGrid`, `ProjectedCell`, `cell_to_combat/client/screen`, `cell_polygon`, `lattice_pixel_to_cell_id`, adaptateur `legacy_cell`. **Sans OpenCV.** |
| `vision/grid_fit.py` | Candidats (union de seuils), ajustement global, hypothèses d'orientation et de décalage, ancres par moindres carrés, `projection_alignment`, `topology_consistency`. |
| `vision/grid_profile.py` | `CombatGridProfileV2` (schema 2, `topology_source="gamedata"`, `scope="layout"`), `ProjectionStatus`, `DeclaredMapId`, `MapIdentityProvider`, `ManualMapIdentity`. |
| `vision/gamedata_grid.py` | `GameDataTopologySource` (cache LRU, scan limité à `content/maps`), `GameDataGridResolver` (priorité des sources), `projected_observation`. |
| `ui/grid_projection_dialog.py` | Calibration de projection sur une capture figée. |

Les modèles d'observation sont étendus de façon additive, avec des valeurs par défaut
compatibles avec les anciens JSON :
- `ObservedCell` reçoit `cell_id`, `grid_coordinate`, `walkable_static`,
  `non_walkable_during_fight_static`, `los_static`, `red_hint`, `blue_hint`, ainsi que les
  propriétés `visual_state`, `visual_confidence` et `static_traversable` ;
- `CombatGridObservation` reçoit `grid_source`, `map_id_declared`, `projection_confidence`,
  `grid_profile_version`, `projection_status`, `transform`, `topology_consistency`,
  `pixel_to_cell_id()` et `cell_by_id()` ;
- `EnemyObservation.cell_id` et `CombatObservation.player_cell_id` sont ajoutés.

### Relation DofusCellId / GridCoordinate / CombatPoint

- `DofusCellId` (0..559) est l'**identité canonique** : elle existe parce que GameData la définit.
- `GridCoordinate(x, y)` est sa coordonnée logique : x − y = demi-rangée,
  x + y = 2 × colonne + parité.
- `CombatPoint` = `GridScreenTransform(GridCoordinate)`, en pixels du crop combat.
- En orientation normale, cellule 0 en haut à gauche : `basis_x = (W/2, H/2)` et
  `basis_y = (W/2, −H/2)`.
- Le losange théorique vaut `centre ± (bx+by)/2` et `centre ± (bx−by)/2`. Il vient des vecteurs de
  projection, jamais d'un contour de la frame, et reste donc stable pour une calibration donnée.

### Audit de l'ancien `Cell(x, y)`

| Usage | Décision |
| --- | --- |
| Simulateur : `engine.py`, `grid.py`, `simulation.py`, `ui/grid_widget.py`, `models.Actor/Action/CombatSnapshot` | Inchangé. Le simulateur garde son propre `Cell`. |
| `ObservedCell.logical`, `EnemyObservation.cell`, `CombatObservation.player_cell` | Conservés. Avec `GAMEDATA_PROJECTED`, ils sont remplis par l'adaptateur **explicite** `legacy_cell(GridCoordinate)`, et l'identité canonique est portée par `cell_id` et `player_cell_id`. |
| `combat_tracker.py` (distance de Manhattan ≤ 2) | Inchangé, via l'adaptateur. La distance de Manhattan en (x, y) GameData égale le nombre de pas en 4-voisinage, donc le rayon garde un sens géométrique. EntityTracker n'est pas refondu. |
| `combat_grid.py` (détection historique, classification) | Détection conservée pour le diagnostic et le secours. La classification préserve désormais les champs GameData (`dataclasses.replace`). |
| Corpus (`logical` des annotations, `_logical` du benchmark) | Inchangé ; `cell_id` et `grid_coordinate` sont ajoutés aux cellules enregistrées. |

La migration future du moteur de combat réel vers `DofusCellId` pourra s'appuyer sur `cell_id`,
sans casser le simulateur.

## 4. Stratégie de calibration

Aucun clic n'est envoyé au jeu : la calibration se fait sur une **capture figée**, dans
**Combat → Vision réelle → Calibrer projection de grille**.

1. **Proposition automatique**.
   - Candidats : détecteur historique de losanges, réutilisé seulement comme générateur de centres.
     On prend l'union des seuils Canny historiques (35/110) et de seuils sensibles (10/30). Ces
     derniers ont été nécessaires sur le client réel : 20 candidats avec les seuils historiques,
     197 avec l'union, pour un dallage en damier peu contrasté.
   - Bases estimées depuis les **vecteurs entre centres voisins**, car la taille des contours est
     biaisée : les contours intérieurs sont ~18 % plus petits.
   - Phase du réseau par moyenne circulaire.
   - Moindres carrés affines itératifs avec rejet des aberrants (tolérance 0,25 × cellule).
   - Le réseau étant périodique et symétrique, il reste à choisir **l'orientation et le décalage
     entier**. On teste 4 orientations (NORMAL, SWAPPED, MIRRORED, ROTATED_180) × tous les décalages
     compatibles avec la grille 14 × 40. Chaque hypothèse est notée par :
     - la validité (les candidats tombent-ils dans les 560 cellules ?) ;
     - avec une map déclarée, le F1 entre candidats et cellules **traversables en combat**, puisque
       DOFUS ne dessine la grille que sur ces cellules ;
     - sans map déclarée, la couverture de la zone.
   - Acceptation automatique **seulement** si toutes ces conditions sont réunies :
     - orientation NORMAL ;
     - score ≥ 0,75 avec topologie, ou ≥ 0,92 sans ;
     - écart avec la 2e hypothèse ≥ 0,08 ;
     - résidu médian ≤ 12 % de la largeur de cellule.
   - Sinon, le statut est `WEAK`, `AMBIGUOUS`, `ORIENTATION_CONFLICT` ou `INSUFFICIENT_CANDIDATES`,
     et la proposition sert seulement de point de départ.
2. **Hypothèse suivante** : fait défiler les 6 meilleurs placements NORMAL. L'humain choisit celui
   qui coïncide, sans connaître les IDs, en s'aidant de l'overlay de walkability.
3. **Ajustement manuel** : centre de la cellule 0, largeur, hauteur, inclinaison légère, nudges
   ±1 px (boutons et flèches du clavier), zoom à la molette.
4. **Ancres facultatives** (cell ID → pixel) : moindres carrés dès 3 ancres non colinéaires.
   Les ancres colinéaires, dupliquées ou qui produisent une orientation inversée sont refusées.
5. **Je confirme l'alignement** enregistre un `CombatGridProfileV2` avec
   `confirmed_by_user=True`, la signature de layout, la méthode, les métriques et les ancres.
   On persiste **la transformation, pas les 560 positions**.

**Orientation** : une symétrie ne peut pas inverser silencieusement les IDs. Une meilleure
hypothèse non NORMAL donne `ORIENTATION_CONFLICT`, un profil non NORMAL est refusé à l'application
(`ORIENTATION_REJECTED`), et l'orientation retenue est affichée dans le diagnostic.

## 5. Projection, priorité des sources et identité

`GridProjector` produit **toujours 560 cellules**, avec les mêmes IDs, quelle que soit l'image.
La géométrie est calculée une fois par transformation et partagée par toutes les maps. Un
changement de map ne remplace que les propriétés statiques.

`GameDataGridResolver` décide de la source à chaque observation :

| Ordre | Source | Condition |
| ---: | --- | --- |
| 1 | `GAMEDATA_PROJECTED` | map déclarée, topologie chargée, profil confirmé, layout compatible |
| 2 | `LEGACY_CALIBRATION` / `VISION_DETECTED` | aucun profil GameData (comportement historique inchangé), ou secours **explicitement** autorisé par la case « Autoriser la grille historique en secours » |
| 3 | `NONE` | profil GameData présent mais inapplicable et secours refusé ; la raison est exposée |

Raisons exposées : `NO_MAP_ID_DECLARED`, `MAP_UNAVAILABLE`, `GRID_CALIBRATION_INCOMPATIBLE`,
`NOT_CONFIRMED`, etc.

Avec `GAMEDATA_PROJECTED`, `infer_combat_grid()` n'est **plus appelé** : il ne crée donc plus
aucune identité. La vision n'agit plus que sur :
- la confiance d'alignement (support des contours le long de chaque losange théorique) ;
- l'occupation (classification historique appliquée aux cellules projetées) ;
- `topology_consistency`, décrit ci-dessous.

`pixel_to_cell()` / `pixel_to_cell_id()` fonctionne sans contour. L'inverse affine, avec le réseau
lui-même comme index spatial, est en O(1). Un point hors du losange ou au-delà d'une
`max_distance` optionnelle donne `None`.

**Statique ≠ dynamique** :
- `walkable_static`, `non_walkable_during_fight_static` et `los_static` viennent de GameData.
- L'état visuel reste `UNKNOWN` tant qu'aucune observation ne le renseigne, et n'est **jamais**
  `FREE` par déduction statique.
- Une entité peut occuper une cellule statiquement traversable.
- La LOS statique n'est pas une LOS dynamique.

**Rouge/bleu** : exposés seulement comme `red_hint` et `blue_hint`, avec l'option d'overlay
« Afficher indices rouge/bleu GameData ». `fight_start_allowed` n'est pas utilisé, et aucune
décision n'en dépend.

**Cohérence écran / map déclarée** (`topology_consistency`) : support moyen des cellules
traversables moins celui des cellules non traversables. En dessous de 0,65, l'observation affiche
« l'écran semble ne plus correspondre à la map déclarée ; redéclarez la map active ». C'est un
**indice**, jamais une détection ni un blocage. Le seuil vient de 2 captures réelles (0,78 contre
0,51) et doit être recalibré sur le corpus. Une map très fragmentée pourrait déclencher l'alerte
à tort.

## 6. Map ID manuel et profil par layout

- **Vision réelle** : champ « Map ID active » avec le bouton **Charger**.
  - La topologie est chargée via `provider.get_map_topology()` : 560 IDs, coordonnées, adjacence,
    walkability, LOS, indices rouge/bleu et métadonnées.
  - Le chargement se fait en tâche de fond, avec un scan limité à `content/maps` : 1,8 s à froid,
    0,19 s avec cache.
  - Une map inconnue donne une erreur claire (`MAP_NOT_FOUND`).
- L'interface affiche « **Map ID déclaré manuellement : N** » et rappelle que PythonBot ne sait pas
  quand la map change.
- La dernière valeur est mémorisée (réglage de profil `declared_map_id` et `map_id` du profil).
  Au changement de profil, elle ne fait que **pré-remplir** le champ : la map n'est déclarée
  qu'au clic sur Charger.
- `MapIdentityProvider` est l'interface qu'un futur fournisseur pourra implémenter sans toucher à
  `GridProjector`. `ManualMapIdentity` est la seule implémentation de ce lot, et
  `MapIdOrigin.DETECTED` est refusé par le profil.
- **Décision « profil par layout »** : les 12 153 maps lisibles ont toutes `zoom_scale = 100` et
  `zoom_offset = (0, 0)`. Sur l'écran réel, le réseau des 2e et 3e salles coïncide à moins de 1 px.
  Le `GridScreenTransform` est donc partagé par toutes les maps d'un même layout, et le map ID ne
  choisit que la topologie.
- **Signature de layout** : le profil ne s'applique jamais silencieusement sur un layout
  incompatible.
  - Rapport largeur/hauteur, zones ou zone combat modifiés : `GRID_CALIBRATION_INCOMPATIBLE`,
    avec `requires_recalibration=True`.
  - Redimensionnement proportionnel (`CLIENT_SIZE_SCALED`) : la transformation est mise à
    l'échelle et le statut `SCALED` est affiché.

## 7. Compatibilité legacy

- `infer_combat_grid()` est conservé, pour les suggestions, le diagnostic et le secours explicite.
  Son comportement par défaut est inchangé : la détection a seulement été extraite dans
  `detect_diamond_candidates()`, avec les seuils historiques par défaut.
- Sans profil GameData, l'observateur fonctionne comme avant, avec `LEGACY_CALIBRATION` ou
  `VISION_DETECTED`.
- La référence HSV du joueur reste dans l'ancien `GridCalibration`. Avec `GAMEDATA_PROJECTED`,
  elle y est stockée sans cellules logiques, pour ne jamais recréer une grille historique à
  partir des cellules GameData.
- Les anciens corpus se chargent toujours (test dédié). Une observation sans `grid_source` est
  comptée comme `LEGACY_UNSPECIFIED`.

## 8. Corpus et métriques

- `save_debug_observation` et `grid_snapshot` enregistrent `grid_source`, `map_id_declared`,
  `map_id_origin`, `grid_profile_version` et `projection_confidence`.
- Chaque cellule projetée porte `cell_id` et `grid_coordinate`.
- Le benchmark ajoute :
  - `grid_source` (comptes), `projected_cell_count_mean/min/max` et `projection_confidence_mean` ;
  - par session : `cell_identity_stability` (Jaccard des cell IDs) et
    `projection_center_drift_px_mean/max`.
- `projected_cell_count` ne dépend pas du nombre de contours détectés.

Corpus local après ce lot : 2 observations réelles (voir § 10), avec :
- `projected_cell_count` = 560 / 560 ;
- `cell_identity_stability` = 1,0 ;
- dérive des centres = 0 px.

### Performances (`scripts/benchmark_gamedata_grid.py`, échelle réelle 2555 × 1151, cellules 119 × 60 px)

| Mesure | Temps |
| --- | ---: |
| Scan topologie `content/maps` : froid / cache | 1 787 ms / 189 ms |
| Chargement d'une topologie réelle : froid / cache LRU | 8,8 ms / < 0,001 ms |
| Initialisation de `GridProjector` (560 géométries) | 2,8 ms |
| Projection des 560 cellules pour une map | 0,86 ms |
| Recherche de cellule (lookup O(1)) | 5,3 µs |
| Alignement visuel (Canny + échantillonnage des losanges) | 18,8 ms |
| Classification d'occupation sur 560 cellules | 24,7 ms |
| `GameDataGridResolver.resolve` par frame | 22,8 ms |
| Candidats (union) / ajustement automatique | 62 ms / 338 ms, une seule fois |
| Pipeline observateur complet, capture réelle 2560 × 1377 | 84–96 ms |

## 9. Tests

- Suite complète : **203 tests**, qui passent (168 au départ, 35 nouveaux).
- `tests/test_gamedata_grid.py` (29 tests) couvre tous les tests demandés :
  `test_project_560_gamedata_cells`, `test_cell_id_identity_is_stable`,
  `test_projection_without_any_contours`, `test_projection_with_missing_contours`,
  `test_projection_with_false_contours`, `test_projection_with_sprite_occlusion`,
  `test_cell_to_combat_roundtrip_nearest`, `test_map_switch_preserves_geometry`,
  `test_map_switch_changes_static_properties`, `test_unknown_map_rejected`,
  `test_manual_map_id_is_marked_declared`, `test_incompatible_layout_invalidates_projection`,
  `test_red_blue_remain_hints`, `test_walkable_does_not_mean_free`,
  `test_static_los_separate_from_dynamic`, `test_legacy_grid_fallback`,
  `test_gamedata_grid_has_priority`, `test_old_corpus_still_loads`.
  Il ajoute la composition client/écran, la mise à l'échelle explicite, les profils non confirmés
  ou miroir, l'aller-retour du profil, les contours qui ne définissent jamais l'identité,
  l'ajustement automatique (accepté, jamais faible ou ambigu, scène miroir), les ancres,
  l'observateur de bout en bout avec corpus et benchmark, et la map périmée signalée.
- `tests/test_gamedata_grid_ui.py` (6 tests, hors écran) couvre :
  - dialogue : proposition automatique puis confirmation, ajustement manuel sans IDs, ancres,
    défilement des hypothèses ;
  - page Vision réelle : map déclarée et survol ;
  - câblage de `MainWindow`.
- Répétitions :
  - tests ciblés 3B-2 et observation : 5 × 57 tests, tous passés ;
  - suite complète : 8 × 202 tests passés, puis 3 × 203 tests passés sur le code final.
- `compileall` passe.

### Test instable de la simulation

`tests/test_simulation.py::test_worker_pause_resume_stop` reste instable. Le crash a été reproduit
avant le travail GameData (1 sur 12) et pendant le LOT 3B-2A-R (5 sur 23). **Aucun crash** sur
les 11 passages complets de ce lot. `simulation.py` n'a pas été modifié, et une tâche séparée
avait été proposée. Aucun nouvel échec n'a été masqué.

## 10. Validation réelle (lecture seule)

La fenêtre « Barbare - Dofus 2.64.5.0 » était ouverte, en combat. Toutes les captures ont été
faites avec `capture_client(hwnd, activate=False)`, qui se limite à `ImageGrab` : aucune
activation, aucun clic, aucune touche.

1. **Capture 1** : « Temple du Grand Ougah – Deuxième salle », coordonnées (-9,29), client
   2560 × 1377, zone combat 2555 × 1151.
   - Candidats : 20 avec les seuils historiques, 197 avec l'union.
   - **Géométrie réelle mesurée sans topologie** : bases (59,59 ; 29,84) et (59,60 ; −29,80),
     soit des cellules de 119,2 × 59,6 px, isométriques 2:1, orientation NORMAL.
   - Résidu médian 0,97 px sur 157 inliers.
   - Identité AMBIGUOUS sans map, comme prévu.
2. **Map ID** : `MapPositions` place **15 maps** en (-9,29), donc les coordonnées affichées ne
   suffisent pas. L'utilisateur a choisi de déclarer **182326273**, ma candidate au meilleur accord
   de walkability, **non vérifiée par `/mapid`**.
3. **Ajustement avec cette topologie** : `AMBIGUOUS`.
   - Score 0,78, précision 1,0, rappel 0,64.
   - Écart de 0,02 avec le placement décalé d'une cellule.
   - Le refus automatique est donc correct.
4. **Choix humain et vérification visuelle** des 4 meilleurs placements, en vues zoomées sur
   le bloc central, le bord haut, les piliers de gauche et le mur de droite.
   - Le placement n°1, origine (473,1 ; −21,5), coïncide partout : sol = cellules traversables ;
     vide central, piliers et fond noir = non traversables ; entités sur des cellules traversables.
   - Le placement décalé d'une cellule laisse des dalles de sol non marquées.
5. **Capture 2**, quelques minutes plus tard : le joueur est passé en **Troisième salle**, aux mêmes
   coordonnées (-9,29).
   - Avec la map 182326273 toujours déclarée, l'ajustement refuse (`ORIENTATION_CONFLICT`,
     score ≤ 0,67).
   - `topology_consistency` tombe de 0,78 à **0,51**, et l'avertissement « map déclarée
     périmée » s'affiche.
   - Le réseau géométrique est identique à < 1 px.
   - C'est un contrôle négatif réel : PythonBot n'a pas prétendu connaître la nouvelle map.
6. **Deux observations enregistrées dans le corpus local**, session `lot3b2-real-20260924` :
   `obs_b9526dc0825942e1` (2e salle) et `obs_cfd1452d934c4df4` (3e salle, map périmée).
   Aucun profil n'a été écrit dans la base de l'utilisateur, qui confirmera sa propre
   calibration dans l'interface.

**Conclusion** :
- la projection des 560 cellules est cohérente avec l'écran réel sur 1 capture, pour la map
  déclarée ;
- la géométrie du réseau est confirmée sur 2 salles ;
- il reste à valider par l'utilisateur, avec un map ID vérifié par `/mapid`, sur plusieurs maps
  et plusieurs combats.

## 11. Build et smoke test

- **`build_exe.ps1`** :
  - interpréteur choisi dans l'ordre `.venv\Scripts\python.exe` s'il **démarre**, sinon
    `.venv\validation\Scripts\python.exe`, sinon arrêt avec une erreur claire ;
  - plus d'installation automatique : sans PyInstaller, le script s'arrête avant tout nettoyage
    et donne la commande.
- **PyInstaller 6.22.3** a été installé dans `.venv\validation` **avec l'accord explicite de
  l'utilisateur** (`pip install -r requirements-dev.txt`).
- **`PythonBot.spec`**, correctif nécessaire au build avec l'interpréteur de repli : le filtre
  historique retirait tous les binaires dont le chemin contient `codex-runtimes`. Or l'interpréteur
  de base de `.venv\validation` s'y trouve, et `python312.dll`, `python3.dll` et `DLLs\*.pyd`
  étaient exclus. Le premier exécutable échouait avec « Failed to load Python DLL ». Le filtre
  n'exclut plus que les binaires étrangers à `sys.base_prefix`.
- **Smoke test** (`PythonBot.exe --package-smoke-test`, données isolées via `PYTHONBOT_DATA_DIR`) :
  - nouveau contrôle `gamedata_grid` : modules GameData et grille embarqués, 560 cellules
    projetées, lookup de la cellule 287 ;
  - `PYTHONBOT_SMOKE_SKIP_DOFUS=1` (nouveau, facultatif) pour ne pas activer la fenêtre d'une
    partie en cours.
  - Résultat : voir le bloc ci-dessous.

| Étape | Résultat |
| --- | --- |
| Build 1 (avant le correctif du `.spec`) | Réussi (286,7 Mio), mais l'exécutable ne démarre pas : « Failed to load Python DLL python312.dll » |
| Build final (`build_exe.ps1`, interpréteur `.venv\validation`) | **Réussi**, `dist\PythonBot\PythonBot.exe`, 380,3 Mio |
| Smoke test (`--package-smoke-test`) | **PASS**, code 0, `success: true` |
| Contrôles du smoke test | interface visible ; 8 pages ; SQLite (1 profil) ; OpenCV ; RapidOCR (« 3 PA Portee 1-4 », 99,9 %) ; **gamedata_grid** : 8 modules GameData/grille embarqués, 560 cellules projetées, lookup de la cellule 287 = 287 |
| Fenêtre DOFUS | Non testée, volontairement (`PYTHONBOT_SMOKE_SKIP_DOFUS=1`), pour ne pas activer la partie en cours. Le rapport JSON indique alors « Aucune fenêtre DOFUS détectée » : ce libellé historique décrit ici un saut volontaire. |

## 12. Limites

- **Map active non détectée** : saisie manuelle obligatoire, et PythonBot ignore les changements
  de map. `topology_consistency` n'en est qu'un indice.
- **Ajustement automatique rarement décisif sur une vraie map** : un décalage d'une cellule reste
  proche, car la région marchable forme un grand bloc. L'humain tranche avec « Hypothèse suivante »
  et l'overlay de walkability. Une seule map réelle a été testée.
- **Confiance d'alignement saturée** : la médiane vaut 1,0 sur le réel dans les deux captures.
  C'est `topology_consistency` qui discrimine.
- **Seuils calibrés sur peu de données** : `MIN_TOPOLOGY_CONSISTENCY` repose sur 2 captures, et les
  seuils de score et d'écart sur des cas synthétiques et 2 captures réelles.
- **Cellules de placement** non validées : le rouge/bleu reste un indice.
- **Occupation** : l'algorithme historique (marqueurs colorés) est inchangé, et le joueur exige
  toujours la signature cliquée.
- **Cellules hors de la zone combat** : elles sont projetées mais non évaluées visuellement.
- **Pas de modèle en perspective** : l'affine suffit sur la capture réelle (résidu < 1 px).
- **`safe_for_decision`** reste faux avec 560 cellules dont l'état est `UNKNOWN` ; aucune décision
  n'est prise, conformément au lot.
- **Travail non commité**, sur la branche `lot-3b-2-gamedata-grid`.

**Arrêt ici. LOT 3B-3 non commencé : aucun mouvement, sort, clic, fin de tour ni lancement de combat.**
