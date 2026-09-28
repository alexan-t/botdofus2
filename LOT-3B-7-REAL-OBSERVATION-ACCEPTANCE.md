# LOT 3B-7 — Recette réelle d'observation

Branche `lot-3b-7-real-observation-acceptance` (depuis `5253cb2`). Lecture seule : aucun clic, aucune
lecture réseau ou mémoire, aucune modification du client. **ACTIONS : NONE.**

But (audit § 15) : vérifier l'ensemble sans action de combat — rejouer le corpus, observer des sessions
réelles, mesurer les métriques 3B-0 et la **latence sur le package Windows**, documenter tailles de
fenêtre, DPI, mode tactique, thèmes et cas non couverts. Critère de sortie : **rapport chiffré**.

## 1. Audit de départ

- Six bancs indépendants (`grid-validation`, `hud-reader`, `entities-3b5d`, `combat-state`,
  `map-resolution`, baseline 3B-0), chacun avec ses sorties ; aucun rapport consolidé.
- La latence fait partie des métriques 3B-0 mais n'était **jamais persistée en session réelle** :
  seule `analysis_ms` était enregistrée, et uniquement pour les frames sauvées dans le corpus.
  Capture, grille et phase/tour n'étaient pas chronométrées.
- Mode tactique : jamais détecté (codé « inconnu »). Thème DOFUS : ni détecté ni enregistré.
- Ce PC : corpus runtime de 102 frames, toutes `diagnostic`, sans split déclaré et sans vérité
  phase/tour ; le corpus 3B-6 (TRAIN/VALIDATION/TEST phase-tour, benchmark map 496 frames) est resté
  sur l'autre PC. Rapports de bancs présents : ceux du 24/09, antérieurs au code actuel.

## 2. Ajouts

### 2.1 Chronométrage par étape (`RealCombatObserver.observe`)
`metadata["stage_ms"]` : `capture`, `map`, `grid`, `entities`, `hud`, `combat_state`, `overlay`,
`total` (la somme des étapes = total, testé). Aucune logique modifiée.

### 2.2 Journal de session réelle (`combatbot/vision/session_telemetry.py`)
À chaque « Démarrer l'observation » : `data/logs/sessions/<session>/frames.jsonl` (une ligne par frame
analysée : latence par étape, statut de map, source/visibilité/alignement de grille, phase, tour, PA,
PM, case joueur, nombre d'ennemis — **aucune image**). À l'arrêt (ou à la fermeture de l'application) :
`summary.json` / `summary.md` avec cadence effective, latence (moyenne, médiane, p95, max) par étape,
ticks ignorés parce que l'analyse précédente n'était pas finie, taux d'inconnus, tailles de client, DPI,
layout, exécutable ou sources, mode tactique et thème « non détecté ».

### 2.3 Recette consolidée (`python -m combatbot.benchmark --acceptance`)
`combatbot/corpus/acceptance.py` ne relance aucun banc (certains durent des heures ou installent des
gabarits) : il lit le dernier rapport de chaque banc et les journaux de session, puis rend un verdict.

| Verdict | Sens |
|---|---|
| PASS / PARTIAL / FAIL | selon le critère du lot d'origine |
| **STALE** | rapport plus ancien que le dernier changement (git) du code qu'il mesure ; jamais compté PASS |
| NOT_EVALUABLE | pas de rapport ou pas de vérité ; la commande qui le produit est indiquée |
| INCOMPLET (global) | au moins un domaine STALE ou NOT_EVALUABLE |

Critères :

| Domaine | Critère | Origine |
|---|---|---|
| Grille | 0 faux combat en exploration, rappel ≥ 0,95, ≥ 95 % des frames visibles ALIGNED | 3B-3 |
| PA/PM | 0 erreur acceptée en TEST, 0 confusion 1↔7, statut 3B-4R2 PASS | 3B-4R2 |
| Entités | verdicts 3B-5D (joueur 1,0/0,90, ennemis 0,98/0,90, suivi ≤ 5 %, FREE 0,98) ; hors TEST → PARTIAL | 3B-5D |
| Phase/tour | 0 « mon tour » affirmé à tort ; précision phase ≥ 0,97, tour ≥ 0,98 sur TEST | 3B-6B + **proposé 3B-7** |
| Map | 0 mauvaise map acceptée (couverture informative) | 3B-6C |
| Latence | p95 de l'analyse ≤ 400 ms **sur l'exécutable** (une analyse par tick du minuteur) | **proposé 3B-7** |

Sortie : `data/benchmarks/acceptance-3b7.json/.md`. Tests : `tests/test_acceptance.py`,
`tests/test_session_telemetry.py`.

## 3. Mesures sur ce PC (28/09)

Recette `python -m combatbot.benchmark --acceptance` : verdict global **FAIL** depuis la première
session réelle (avant elle : INCOMPLET, rien n'était mesurable).

| Domaine | Verdict | Mesure |
|---|---|---|
| Grille (3B-3) | **PASS** | re-mesuré avec le code actuel : 26 frames, 0 faux combat en exploration (0,86 avant 0.4.0), rappel combat 1,0, 12/12 frames visibles ALIGNED, résidu médian 1,17 px |
| PA/PM (3B-4R2) | STALE | rapport du 24/09 (brut PASS : TEST 26 ex., 0 erreur acceptée, couverture 0,92, 1↔7 validé) antérieur au lecteur actuel |
| Entités (3B-5D) | NOT_EVALUABLE | aucun split déclaré dans le corpus de ce PC |
| Phase/tour (3B-6B) | NOT_EVALUABLE | aucune vérité phase/tour sur ce PC |
| Map (3B-6C) | NOT_EVALUABLE | voir ci-dessous |
| Latence | **FAIL** | session réelle du 28/09 : p95 979 ms pour un critère ≤ 400 ms (voir § 3.1) |

**Map — mesure vide détectée.** Le banc a rejoué les 102 frames du corpus local (57 avec mapId
vérifié, 10 maps) : 0 mauvaise, mais aussi **0 correcte** (75 UNKNOWN, 16 TRANSITION, 11 AMBIGUOUS).
Cause : ces frames (24/09) ne contiennent que le crop combat ; avec la calibration de ce PC il commence
à 4,5 % du haut du client et coupe la ligne « Zone (Sous-zone) » → `AREA_NAME_INCOMPLETE`, lecture
refusée (comportement voulu, rien n'est inventé). Le client entier n'est enregistré que depuis 3B-6B.
Corrections : (1) le banc lit `client_frame` avec la ROI runtime quand il existe
(`ocr_inputs` dans le rapport) ; (2) la recette classe « 0 erreur parce que 0 réponse » en
NOT_EVALUABLE au lieu de PASS. Lecteur OCR : médiane ≈ 0,97 s par lecture sur ce PC.

## 3.1 Première session réelle (28/09, donjon Ougah de bout en bout)

`session_544de7379326` : exécutable 0.4.3, client 2560x1377, DPI 96, layout `9a0751d48681994e`,
216,4 s, **218 frames analysées**, 5 maps distinctes, 3 fins de combat détectées. Les rapports vivent
sous `data/` (non versionné) : les chiffres sont donc recopiés ici.

### Latence — **FAIL**

| Étape | médiane | p95 | max |
|---|---|---|---|
| capture | 235,72 | **532,21** | 758,67 |
| entities | 124,22 | 457,35 | 838,73 |
| grid | 120,47 | 224,12 | 297,72 |
| hud | 17,99 | 57,02 | 1580,10 |
| map | 29,21 | 35,61 | 43,64 |
| combat_state | 2,41 | 7,15 | 25,27 |
| overlay | 13,32 | 35,06 | 88,80 |
| **total** | **733,90** | **979,30** | 2261,11 |

p95 à 979 ms pour un critère de 400 ms : **2,4× trop lent**. Conséquence mesurée : minuteur à 400 ms mais
**288 ticks sautés sur 506** (57 %), cadence effective 1,03 img/s au lieu de 2,5. Premier coupable : la
**capture** (236 ms de médiane), pas la vision.

### Ce qui a tenu

PA/PM : 4,1 % et 3,2 % d'inconnus en donjon réel. Map : 163/218 RESOLVED (75 %), 5 maps, calibration
réutilisée de salle en salle. Grille : 148 frames ALIGNED, projection GameData sur 213/218 frames.

### Phase et tour — faux « mon tour », cause racine identifiée

« mon tour » affirmé sur **160 frames / 218 (73 %)**, dont **92 s en continu à travers 3 maps
différentes** (t=38,8 → t=130,9) ; `turn_owner` = `None` sur les 218 frames ; « tour adverse » jamais
détecté une seule fois sur 3 combats. 84 de ces 160 frames affichent PA=15/PM=6, signature de l'attente
pendant le tour d'un autre. Le critère **« 0 "mon tour" affirmé à tort » est violé**.

Chaîne de causes :

1. le modèle 3B-6B **n'est pas installé sur ce PC** (`%LOCALAPPDATA%\PythonBot\data\combat_state_model`
   absent ; `summary.json` porte `"combat_state_model": false`) ;
2. `CombatStateModel.load()` renvoie `None` **en silence** (`combat_state_detector.py:146`) ;
3. `RealCombatObserver` retombe alors sur l'ancienne heuristique (`combat_observer.py:261`) :
   `turn_score = 0.6 * end_signal + 0.4 * max(conf_PA, conf_PM)` ;
4. or `end_signal` vient de `_visual_activity()` (`combat_observer.py:82`) : **texture et contours, pas la
   couleur**. Le bouton « TERMINER LE TOUR » reste dessiné pendant le tour adverse, seulement plus sombre
   → même signal → « mon tour » permanent. Sans notion de début/fin de combat, l'affirmation traverse les
   changements de salle.

Le `turn_from_button()` de 3B-6B décide sur la couleur (jaune vif ≥ 0,15 → joueur ; vif ≤ 0,05 et jaune
foncé ≥ 0,30 → un autre) : ce code est correct, il n'a simplement **jamais été appelé**.

**Défaut de conception à corriger : fail-open.** Sans modèle, le système n'affiche pas « inconnu », il
affirme « mon tour ». Cela contredit les garde-fous du projet (« `UNKNOWN` bloque l'action », fail-closed).
Branché au lot 5, le bot cliquerait pendant le tour des monstres. Correction proposée : supprimer le repli
heuristique sur le tour (phase/tour inconnus sans modèle) et avertir dans le log et l'UI. **Non appliquée
à ce stade.**

Modèle non reconstructible ici : le corpus runtime de ce PC a **0/102 vérité phase/tour**
(`combat_state_confirmed`, `combat_phase_truth`, `turn_owner_truth` tous absents). Et la session du 28/09
**n'est pas annotable** : le journal 3B-7 n'écrit volontairement aucune image.

### Autre point

**Case du joueur : 90,8 % d'inconnus** (`player_cell_id` quasi jamais résolu) — indispensable au
pathfinding du lot 4B.

Mode tactique et thème DOFUS : toujours « non détecté ». Une seule taille de client observée.

## 3.2 Pré-recette sur le PC du corpus 3B-6 (28/09 soir, `DESKTOP-LCKK4IJ`)

Corpus runtime : 12 combats phase/tour (7 TRAIN, 3 VALIDATION, 2 TEST déjà consommés), benchmark map
496 frames (472 avec mapId historique), modèle phase/tour installé (7 combats TRAIN).

| Domaine | Verdict | Mesure |
|---|---|---|
| Phase/tour (3B-6B) | **PASS** | TEST figé `1eef84a` (43 frames) : phase 0,974, tour 1,000, « mon tour » à tort 0. Re-mesure TRAIN (un combat laissé de côté) 0,994 / 0,982 et VALIDATION 1,000 / 1,000 : identiques aux chiffres du 28/09 matin |
| Map (3B-6C) | **PASS** | re-mesuré avec le code actuel : 377/472 correctes, **0 mauvaise**, couverture 0,80 (0,75 sans la forme à l'écran ni les hypothèses gardées) ; lecteur ≈ 0,80 s |
| Grille (3B-3) | NOT_EVALUABLE ici | les 26 frames `lot3b2r` sont sur l'autre PC (PASS là-bas) |
| PA/PM, entités | NOT_EVALUABLE | bancs non relancés : ils installent gabarits/profils → décision utilisateur |
| Latence | NOT_EVALUABLE ici | aucune session réelle journalisée sur ce PC |

Corrections faites à cette occasion :
- **Tour fail-closed** (§ 3.1, point 4) : l'heuristique de secours est supprimée. Sans modèle phase/tour,
  le tour reste inconnu, avec un avertissement dans le log et « modèle phase/tour absent » dans
  Vision réelle. Test : `test_turn_is_fail_closed_without_combat_state_model`. Le test historique qui
  attendait « mon tour » sans modèle attend désormais « inconnu ».
- **Recette** : le rapport TEST figé (`combat-state-TEST-<sha>.json`, mesure sous la clé `result`)
  était classé « aucune vérité phase/tour » ; il est maintenant lu comme mesure TEST.

## 4. Reste à faire

1. ~~Session réelle avec `DofBot2.exe` reconstruit → latence mesurée sur le package.~~ **Fait le
   28/09** (§ 3.1) : verdict FAIL, à traiter en commençant par la capture.
2. Re-mesurer PA/PM et entités avec le code actuel (bancs qui écrivent gabarits/profils : à lancer
   explicitement, décision utilisateur).
3. Phase/tour : TEST sur ce PC impossible sans vérité ; soit copier le corpus 3B-6 de l'autre PC,
   soit annoter 2 combats neufs (TEST aveugle, second PC = validation d'une 2e configuration).
4. ~~Rendre le tour fail-closed (§ 3.1).~~ **Fait** (§ 3.2).
5. Réduire la latence de capture (236 ms de médiane pour 2560x1377).
6. Sur ce PC : 3 à 5 combats neufs avec l'exécutable reconstruit (journal de session : latence,
   cadence, map, phase/tour) — mesure de la latence sur une 2e configuration.
