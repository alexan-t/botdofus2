# LOT 3B-2R — Recette réelle multi-map de la grille GameData

Date : 24 septembre 2026. Code fonctionnel mesuré : **0.4.0 tel quel**. Les seuils, le scoring,
`GridScreenTransform`, `GridProjector`, `topology_consistency` et le détecteur de contours n'ont
pas été modifiés. La version n'a pas changé.

## 1. Commit du LOT 3B-2

- Diff examiné par rapport à la référence `ec34e20a109245c0ae5c554cfb7950553d82bb20`.
- `git diff --check` était propre, après retrait d'une ligne vide en fin de `gamedata_grid.py`.
- 203 tests passaient avant le commit.
- **`1aa8f18cc0ab9eece1c8847dde8ee3f1b01f44f8`** (`1aa8f18`) :
  `feat: project combat grid from gamedata topology`, sur `lot-3b-2-gamedata-grid`,
  23 fichiers.
- Présents dans le commit : `GridScreenTransform`, `GridProjector`, `CombatGridProfileV2`,
  l'intégration GameData, l'UI de calibration, le corpus/benchmark, `build_exe.ps1` et
  `PythonBot.spec` corrigés, les tests 3B-2, la version 0.4.0 et le rapport 3B-2.
- Aucun fichier de `data/`, `dist/`, `build/`, `.venv/`, du client, de cache ni de log.

L'outillage de recette ajouté ensuite (§ 3) n'est **pas commité** : seul le commit 3B-2 était
demandé.

## 2. Environnement

- Client privé Alea DOFUS 2.64.5, fenêtre « Barbare - Dofus 2.64.5.0 ».
- Client 2560 × 1377 px, DPI 96, signature de layout `31995d0b75c9c8a4`.
- Zone combat 2555 × 1151 px (profil 1).
- Captures faites avec `capture_client(hwnd, activate=False)` : lecture seule, sans activation ni
  clic ni touche.
- **Aucune action dans le jeu** : l'utilisateur s'est déplacé, a tapé `/mapid`, a lancé les combats
  et joué lui-même.
- Session : `data/validation/grid-real/grid-real-20260924/` (non versionnée) :
  `session.json`, `baseline.json`, `analysis.json`, `corpus-promotion.json`, et pour chaque capture
  `frame.png`, `combat.png`, `overlay.png`, `review.png` et éventuellement `redblue_check.png`.
- Déroulé : recette guidée. Je capturais, calculais les mesures et envoyais l'overlay et la planche
  des bords ; l'utilisateur répondait. Deux légendes que j'avais écrites étaient fausses
  (maps 4 et 7) ; je les ai corrigées avant de recueillir la réponse.

## 3. Outillage ajouté (sans changer les algorithmes)

- **`map_id_source`** : `user_verified_mapid` ou `manual_guess`.
  - Enregistré dans `DeclaredMapId`, l'observation, les métadonnées et le snapshot du corpus.
  - Case « vérifié par /mapid » dans Vision réelle.
  - Les anciens corpus sont comptés `UNSPECIFIED`.
- **`combatbot/vision/grid_recipe.py`** : `RealGridValidationSession`, persistée en JSON.
  - Mesures automatiques par capture, calculées de façon identique quel que soit le jugement (test
    dédié) : candidats, inliers, statut, scores, marge, orientation, décalage entier,
    résidus (moyenne, médiane, p95, max), taille de cellule, `projection_confidence`,
    `topology_consistency`, scores ±U/±V et dérive du réseau.
  - Jugement utilisateur : alignement, direction de décalage, `edge_top_ok`, `edge_bottom_ok`,
    `edge_left_ok`, `edge_right_ok`, `center_ok`, anomalie et notes.
  - Réutilisation du transform, tests de map périmée, rouge/bleu, baseline.
  - `score_transform` reproduit exactement la formule de l'ajustement ; un test vérifie l'égalité
    au bit près.
- **Statut d'une map** : PASS seulement si toutes ces conditions sont réunies :
  - `/mapid` vérifié ;
  - alignement jugé correct, 4 bords et centre corrects, pas d'anomalie ;
  - sur les frames de placement ou de combat : orientation NORMAL, résidu médian ≤ 12 % de la
    largeur de cellule et `topology_consistency` ≥ 0,65. Ce sont les seuils 0.4.0.
  - En exploration, DOFUS ne dessine pas la grille ; une map sans frame de combat est donc
    **VISUAL_ONLY**, jamais PASS.
- **UI** : bouton **Vision réelle → Recette de grille…** et dialogue `GridRecipeDialog`
  (overlay, survol cell ID, candidats, mesures, jugement, rouge/bleu).
- **CLI** : `scripts/grid_recipe.py` (init, capture, verdict, reuse, stale, redblue,
  redblue-check, fit-transform, rerender, promote, report) et `scripts/grid_recipe_analysis.py`.
- **Tests** : 8 nouveaux dans `tests/test_grid_recipe.py`. Suite complète : **211 tests** passés
  (3 passages).

## 4. Maps testées (toutes vérifiées par `/mapid`)

| # | Map ID | Lieu | Captures (hors map périmée) | Combat | Statut |
| ---: | --- | --- | --- | --- | --- |
| 1 | 182327297 | Temple du Grand Ougah, 1re salle (donjon, intérieur) | A, B, D (placement) | oui | **PASS** |
| 2 | 182326273 | Grand Ougah, 2e salle (donjon) | A, 2 × combat | oui | **PASS** |
| 3 | 182325249 | Grand Ougah, 3e salle (donjon) | A, D, E, F | oui | **PASS** |
| 4 | 182192129 | Grand Ougah, 9e salle (donjon) | A, D | oui | **PASS** |
| 5 | 181146624 | Landes de Sidimote, devant la Caverne des Fungus (extérieur) | A | non | VISUAL_ONLY |
| 6 | 191105024 | Astrub (ville, extérieur) | A, D, E, C | oui | **PASS** |
| 7 | 191106050 | Environs d'Astrub (extérieur) | A, D, E | oui | **FAIL** (automatique) |

- Les 7 maps ont été **jugées correctes par l'utilisateur**, avec les 4 bords et le centre
  corrects, et sans anomalie majeure.
- La map 2 (182326273) est celle que le LOT 3B-2 avait retenue sans vérification :
  `/mapid` la confirme a posteriori.
- Le FAIL de la map 7 vient uniquement de `topology_consistency` : 0,601 et 0,620, sous le seuil
  0.4.0 de 0,65. Pourtant l'alignement est jugé correct, le décalage vaut [0, 0] et le résidu médian
  est de 4,5 et 3,5 px. C'est un **faux positif de l'indicateur**, pas une erreur de géométrie.
- Au total : 26 captures, dont 20 sur la map déclarée (12 avec grille dessinée, en placement ou
  combat, et 8 en exploration) et 6 « map périmée ».

### Mesures par capture

| Capture | Map | Type | Mode | Périmée | Candidats | Ajustement | Décalage | Résidu méd. px | Résidu max px | Cohérence | Marge ±1 |
| --- | --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| C001 | 182327297 | A | exploration | non | 4 | INSUFFICIENT | — | 17,56 | 31,3 | −0,054 | −0,010 |
| C002 | 182327297 | B | exploration | non | 4 | INSUFFICIENT | — | 17,56 | 31,3 | −0,054 | −0,010 |
| C003 | 182327297 | D | placement | non | 168 | AMBIGUOUS | [0,0] | 1,38 | 57,9 | 0,716 | 0,006 |
| C004 | 182327297 | A | exploration | oui | 1 | INSUFFICIENT | — | 50,10 | 50,1 | −0,136 | 0,000 |
| C005 | 182326273 | A | exploration | non | 2 | INSUFFICIENT | — | 38,11 | 50,1 | −0,182 | −0,008 |
| C006 | 182326273 | — | combat | non | 215 | AMBIGUOUS | [0,0] | 1,36 | 48,7 | 0,660 | 0,019 |
| C007 | 182326273 | — | combat | non | 203 | AMBIGUOUS | [0,0] | 1,06 | 48,7 | 0,799 | 0,015 |
| C008 | 182326273 | A | exploration | oui | 1 | INSUFFICIENT | — | 29,03 | 29,0 | −0,063 | 0,000 |
| C009 | 182325249 | A | exploration | non | 1 | INSUFFICIENT | — | 29,03 | 29,0 | −0,061 | 0,000 |
| C010 | 182325249 | D | placement | non | 214 | AMBIGUOUS | [0,0] | 1,07 | 43,6 | 0,682 | 0,008 |
| C011 | 182325249 | E | combat | non | 216 | AMBIGUOUS | [0,0] | 1,29 | 43,6 | 0,655 | 0,008 |
| C012 | 182325249 | F | combat | non | 217 | AMBIGUOUS | [0,0] | 1,29 | 43,6 | 0,654 | 0,008 |
| C013 | 182325249 | A | exploration | oui | 9 | INSUFFICIENT | — | 21,61 | 39,4 | 0,052 | 0,000 |
| C014 | 182192129 | A | exploration | non | 9 | INSUFFICIENT | — | 21,61 | 39,4 | −0,163 | 0,000 |
| C015 | 182192129 | D | placement | non | 131 | WEAK | [0,0] | 1,92 | 32,8 | 0,687 | 0,000 |
| C016 | 182192129 | A | exploration | oui | 3 | INSUFFICIENT | — | 26,24 | 47,7 | −0,279 | 0,000 |
| C017 | 181146624 | A | exploration | non | 3 | INSUFFICIENT | — | 26,24 | 47,7 | −0,333 | 0,000 |
| C018 | 181146624 | A | exploration | oui | 79 | ORIENT._CONFLICT | [22,0] | 24,87 | 52,2 | −0,036 | −0,001 |
| C019 | 191105024 | A | exploration | non | 79 | ORIENT._CONFLICT | [22,0] | 24,87 | 52,2 | −0,115 | −0,006 |
| C020 | 191105024 | D | placement | non | 187 | AMBIGUOUS | [0,0] | 2,14 | 34,7 | 0,809 | 0,039 |
| C021 | 191105024 | E | combat | non | 191 | AMBIGUOUS | [0,0] | 2,28 | 34,7 | 0,800 | 0,046 |
| C022 | 191105024 | C | combat | non | 191 | AMBIGUOUS | [0,0] | 2,28 | 34,7 | 0,800 | 0,046 |
| C023 | 191105024 | A | exploration | oui | 59 | WEAK | [−4,−4] | 23,22 | 38,0 | 0,127 | −0,029 |
| C024 | 191106050 | A | exploration | non | 59 | WEAK | [−4,−4] | 23,22 | 38,0 | 0,107 | −0,026 |
| C025 | 191106050 | D | placement | non | 289 | AMBIGUOUS | [0,0] | 4,51 | 35,0 | 0,601 | 0,040 |
| C026 | 191106050 | E | combat | non | 314 | AMBIGUOUS | [0,0] | 3,53 | 35,0 | 0,620 | 0,043 |

Les frames C006 et C007 ont été capturées en croyant être en placement. Le combat avait déjà
commencé : elles ont été requalifiées en « combat » et leur analyse rouge/bleu retirée. Les
mesures d'exploration (sans grille dessinée) sont **non applicables** : l'ajustement y accroche
le décor (C018, C019, C023, C024).

## 5. Réutilisation du transform

Le transform T1 a été confirmé au LOT 3B-2 sur la 2e salle, sans `/mapid` à l'époque. Pour chaque
nouvelle map, il a été appliqué **tel quel avant tout jugement** :

- maps réutilisant exactement T1 : **7 / 7** ; recalibrations nécessaires : **0** ;
- écart entre le réseau réellement ajusté et T1, sur les 12 frames avec grille (décalage entier
  toujours [0, 0]) : centre max de 0,72 à 2,84 px (moyenne 1,84, p95 2,80), soit ≤ 2,4 % de la
  largeur de cellule ;
- taille de cellule ajustée : 119,17 × 59,62 px (p95 119,25 × 59,75).

**Un unique transform par layout est viable** sur ce client et ce layout : 4 salles de donjon, une
ville, deux maps extérieures.

## 6. Stabilité entre frames

Il y a 8 paires de frames avec grille dans 4 maps. Les réseaux ajustés sont alignés sur T1 :

| Mesure | Moyenne | Maximum |
| --- | ---: | ---: |
| Dérive de l'origine | 1,55 px | 3,12 px |
| `basis_x` / `basis_y` | ≤ 0,06 px | ≤ 0,13 px |
| Centres : moyenne | 0,67 px | 1,38 px |
| Centres : maximum | 1,64 px | 3,34 px |

Les 560 IDs sont identiques sur toutes les frames : la projection ne dépend pas des contours.
Dans le corpus, `cell_identity_stability` vaut 1,0 et `projection_center_drift` 0 px.

## 7. Résidus (12 frames avec grille)

| Par frame | Min | Moyenne | Médiane | p95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Résidu moyen | 4,41 | 8,55 | 7,70 | 12,99 | 13,26 |
| Résidu médian | 1,06 | 2,01 | 1,65 | 3,97 | 4,51 |
| Résidu max | 32,8 | 41,1 | 39,3 | 52,8 | 57,9 |

Le résidu médian reste loin du seuil 0.4.0, fixé à 12 % de 119 px (≈ 14,3 px). Les maxima de
33 à 58 px viennent de candidats parasites : entités, effets, décor. Le résidu moyen y est donc
sensible, alors que la médiane ne l'est pas.

## 8. topology_consistency : bonne map contre map périmée

**Changements de map réels** : 6 transitions. Elles ont toutes été capturées **en exploration**,
avant d'entrer en combat. Hors combat, la grille n'est pas dessinée : la cohérence vaut de −0,33 à
0,13, avec la bonne comme avec la mauvaise map. Elle n'est donc **pas discriminante hors combat**.

**Map périmée simulée sur les 12 vraies frames avec grille** : chaque frame est réévaluée avec la
topologie de la map précédente de la session. C'est exactement ce que PythonBot aurait calculé si
l'utilisateur n'avait pas redéclaré la map.

| Map déclarée | n | Min | Moyenne | Médiane | p95 | Max | Sous le seuil de 0,65 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bonne map | 12 | 0,601 | 0,707 | 0,684 | 0,804 | 0,809 | 2 / 12 (map 191106050) |
| Map précédente | 12 | 0,226 | 0,429 | 0,439 | 0,562 | 0,602 | 12 / 12 |
| N'importe quelle autre map | 72 | — | 0,422 | 0,440 | 0,592 | 0,635 | 72 / 72 |

- La baisse entre la bonne map et la map précédente est **présente sur 12 frames sur 12**, de
  0,19 à 0,39.
- En valeur absolue, les distributions se chevauchent légèrement : bonne map au minimum à 0,601,
  mauvaise map au maximum à 0,635.
- Un seuil absolu seul ne peut donc pas être une preuve, alors que la baisse relative sur une
  même frame est nette.

## 9. Ambiguïté d'un décalage d'une cellule

| Constat (12 frames avec grille) | Valeur |
| --- | --- |
| Transform confirmé meilleur ou à égalité face à ±U et ±V | 12 / 12 |
| Écart avec le meilleur décalage d'une cellule | min 0,000, médiane 0,017, p95 0,046, max 0,046 |
| Égalité parfaite | C015 : `+U` = 0,675 = score du transform confirmé |
| Ajustement automatique accepté | **0 / 12** (11 AMBIGUOUS, 1 WEAK) |

Le problème observé au LOT 3B-2 est confirmé sur toutes les maps. **La calibration automatique
initiale ne suffit jamais seule.** La confirmation humaine (hypothèses, ajustement manuel) reste
nécessaire. Elle n'a cependant été requise **qu'une fois** : le transform a ensuite été réutilisé
tel quel sur 7 maps.

## 10. Rouge / bleu

Cinq phases de placement ont été capturées et une sixième confirmée de mémoire :

| Map | Indices GameData (rouge/bleu) | Cases réelles de DOFUS | Correspondance |
| --- | --- | --- | --- |
| 182327297 | 10 / 10 | 10 / 10 | **exacte** (précision = rappel = 1). L'analyse couleur voyait un bleu en trop, 219, que l'utilisateur a rejeté. |
| 182192129 | 10 / 10 | 10 / 10 | **exacte** (précision = rappel = 1) |
| 182326273 | 10 / 10 | non capturées | exacte **de mémoire** ; alliés observés sur 4 indices bleus |
| 182325249 | **0 / 0** | 8 / 8 | aucun indice GameData |
| 191105024 | **0 / 0** | 8 / 3 | aucun indice GameData |
| 191106050 | **0 / 0** | 9 rouges (bleu non confirmé) | aucun indice GameData |

- Sur les placements capturés, les indices GameData ont une **précision de 1,0** : aucune case
  indiquée à tort.
- Leur **rappel n'est que de 40 / 76 = 0,53** : 3 maps sur 5 capturées affichent des cases de
  placement **sans aucun indice DLM**, dont une salle de donjon et deux combats de monstres en
  extérieur.
- Les cases de placement ne viennent donc pas uniquement des drapeaux DLM ; le serveur les définit
  au moins sur ces maps.
- Le critère « CONFIRMED » demande au moins 5 maps en correspondance. Il est **non atteint** :
  2 maps capturées plus 1 de mémoire.
- `fight_start_allowed` reste **UNKNOWN**. `red_hint` et `blue_hint` restent des indices,
  fiables quand ils existent, mais incomplets.
- L'analyse couleur automatique (`observed_placement_cells`) a retrouvé les cases réelles sur les
  5 placements, avec 1 faux positif (219).

## 11. Corpus et benchmark

- **26 captures promues** dans `data/corpus`, via le vrai pipeline observateur, avec une annotation
  (`comments` = verdict et statut, `combat_truth` selon le mode).
- Tags : `lot3b2r` 26, `mapid_verified` 26, `grid_real_verified` 20, `projection_good` 20,
  `projection_stale_map` 6, `placement_phase` 5, `red_blue_validation` 6, plus `mode_*` et
  `map_status_*`. Aucune capture n'est `projection_ambiguous`, car aucune map n'a été jugée ambiguë.
- **Benchmark du corpus** (`python -m combatbot.benchmark`) : 28 observations, dont 26 annotées.
  - `grid_source` : `GAMEDATA_PROJECTED` = 28.
  - `map_id_source` : `user_verified_mapid` = 26, `UNSPECIFIED` = 2 (anciennes, LOT 3B-2).
  - 7 `verified_map_ids`.
  - `projected_cell_count` = 560 (min et max) ; `cell_identity_stability` = 1,0.
- **Baseline de recette** (`baseline.json`) :
  - 7 maps vérifiées : PASS 5, FAIL 1, VISUAL_ONLY 1 ;
  - 26 captures : 12 avec grille, 8 en exploration non applicables, 6 périmées ;
  - distributions ci-dessus ; statuts d'ajustement : AMBIGUOUS 11, WEAK 1.

### Constat hors périmètre : détection de combat

Sur les frames promues, `combat_detected` vaut vrai pour **12 des 14 frames d'exploration**.

Depuis 0.4.0, la grille GameData a toujours 560 cellules, donc le garde-fou historique
`len(grid.cells) >= 4` ne filtre plus rien. Les signaux d'interface (fin de tour, barre de sorts,
compteurs) suffisent à franchir 0,45, même avec une confiance de grille de 0,14. C'est une
**régression de la détection de combat introduite par 0.4.0**, non corrigée ici, conformément au § 2.

Donnée utile pour la corriger : `topology_consistency` vaut de 0,60 à 0,81 en placement ou en
combat, contre −0,33 à 0,13 en exploration. Elle sépare nettement les deux sur ces 26 frames.

## 12. Propositions de seuils pour le LOT 3B-3 (non appliquées)

1. **Map périmée** : remplacer le seuil absolu 0,65 par un critère relatif sur la même frame, par
   exemple la cohérence de la map déclarée comparée à sa propre valeur récente. Données : baisse de
   0,19 à 0,39 sur 12 frames sur 12, alors qu'en absolu la bonne map descend à 0,601.
2. **Combat affiché** : utiliser l'évidence de grille dessinée (`topology_consistency` ≳ 0,4, ou le
   nombre de candidats ≳ 120), et non le nombre de cellules. Les frames avec grille avaient
   131 à 314 candidats ; celles d'exploration 1 à 79.
3. **Ambiguïté ±1 cellule** : les marges réelles atteignent au plus 0,046. Garder la confirmation
   humaine pour la calibration initiale, puis la réutilisation du transform par layout.
4. **Résidu** : suivre la médiane plutôt que la moyenne. Médiane réelle ≤ 4,5 px, contre 14,3 px
   pour le seuil actuel.

Ces propositions reposent sur une seule session, un seul layout et 7 maps ; elles restent à
confirmer.

## 13. Limites

- Un seul layout et une seule résolution (2560 × 1377), sur une seule session.
- Les changements de map réels n'ont été capturés qu'en exploration. Le comportement « map
  périmée » avec grille dessinée est **simulé** sur de vraies frames, pas observé en direct.
- Map 2 : placement non capturé (rouge/bleu confirmé de mémoire). Map 5 : pas de combat.
  Map 7 : bleu non confirmé.
- Jugements recueillis sur mes overlays et planches envoyés en images. Le dialogue de recette de
  PythonBot a été construit après la session et n'est testé qu'en synthétique.
- Deux légendes erronées de ma part ont été corrigées avant les réponses. Une frame (C020) marquée
  « périmée » par erreur a été corrigée après la réponse `/mapid` (même map).

## 14. Condition pour le LOT 3B-3

| Condition | Statut |
| --- | --- |
| Au moins 5 map IDs vérifiés par `/mapid` | **Oui, 7** |
| Projection validée sur plusieurs maps | **Oui** : 5 PASS, 7 sur 7 jugées correctes, T1 réutilisé sur 7 sur 7 |
| Distribution de `topology_consistency` | **Oui** : 12 frames réelles avec grille, plus 72 évaluations avec une autre map déclarée |
| Distribution des résidus | **Oui** : 12 frames avec grille |
| Comportement « map périmée » mesuré | **Oui** : 6 transitions réelles (exploration) et 12 frames simulées avec grille |
| Ambiguïtés ±1 cellule documentées | **Oui** : 12 frames |

Les conditions sont réunies. Deux points sont à traiter en priorité au LOT 3B-3 : la
régression de détection de combat (§ 11) et le critère relatif de map périmée (§ 12).

## Verdict

```
REAL GRID PROJECTION: PASS
  7 maps /mapid vérifiées : 5 PASS, 1 FAIL (topology_consistency 0,601–0,620 < 0,65 ;
  alignement jugé correct, décalage [0,0]), 1 VISUAL_ONLY (pas de combat).
  0 désalignement jugé par l'utilisateur sur 7 maps ; T1 réutilisé 7/7, 0 recalibration ;
  décalage entier [0,0] sur 12/12 frames avec grille ; résidu médian 1,06–4,51 px ;
  dérive du réseau ≤ 2,84 px ; dérive entre frames ≤ 3,34 px ; 560 IDs stables.

RED/BLUE PLACEMENT: PARTIAL
  Indices exacts quand présents (2 maps capturées, 40/40 cases, précision = rappel = 1 ;
  1 map de plus de mémoire), mais absents sur 3/5 placements capturés
  (rappel global 40/76 = 0,53). fight_start_allowed reste UNKNOWN.
```

**Arrêt ici. LOT 3B-3 non commencé.**
