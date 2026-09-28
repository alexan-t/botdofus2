# LOT FAST-TRACK OFFLINE — cœur combat sans DOFUS

**ACTIONS RÉELLES : NONE.** Aucun clic, aucune touche, aucune souris, aucune injection, aucune lecture
mémoire ou réseau, aucune modification du client, aucune vérité corpus modifiée, aucun réglage 3B-5 sur TEST.

## Base

| | |
|---|---|
| Branche | `lot-fasttrack-core-offline` |
| Base SHA réelle | `1e90a8691697df211384ad9f8ccb07de6c90fb82` (`origin/lot-3b-7-real-observation-acceptance`) |
| HEAD réel de `lot-3b-6c-auto-map-detection` au départ | `5253cb2f59e7dfa86bad6a1fbb7d7bba50e129c4` (4 commits après `4bbdb0a`, pris comme vérité) |
| Pourquoi cette base | `lot-3b-7-real-observation-acceptance` = `5253cb2` + 9 commits sans divergence, qui contiennent déjà la recette 3B-7 (`--acceptance`) et la télémétrie de latence. Repartir de `5253cb2` aurait imposé un second harness 3B-7 parallèle (voir `AUDIT-FASTTRACK-OFFLINE.md`). |
| Environnement | conteneur Linux, sans client DOFUS ni corpus réel : tout est testé sur données synthétiques ; les bancs sont prêts pour les données du PC. |

## Commits créés

| SHA | Message |
|---|---|
| `bd8c2ee` | docs: audit offline fast-track prerequisites |
| `c67ac66` | feat: add end-to-end observation benchmark |
| `d13aa94` | feat: add gamedata combat pathfinding |
| `7cccebe` | feat: harden spell domain for real combat state |
| `ce7b6ca` | feat: add conservative targeting and los decisions |
| `a5f01a1` | feat: add dry-run combat decision planner |
| `5ac65e2` | feat: add dry-run action executor |
| `9eed914` | feat: replay recorded frames through the dry-run decision chain |
| `f6671bb` | feat: show the dry-run plan of each observed frame |
| (ce commit) | docs: report offline fast-track progress |

## Fichiers

Créés : `AUDIT-FASTTRACK-OFFLINE.md`, `LOT-FASTTRACK-OFFLINE.md`, `combatbot/combat/{__init__,pathfinding,spells,
targeting,state,planner,executor}.py`, `combatbot/corpus/{observation_e2e_metrics,observation_e2e_benchmark,
dry_run_replay}.py`, `tests/test_{observation_e2e,combat_pathfinding,combat_spells,combat_targeting,combat_planner,
combat_executor,dry_run_replay}.py`.

Modifiés : `combatbot/benchmark.py` (`--observation-e2e`, `--dry-run-plans`), `combatbot/corpus/acceptance.py`
(domaine `observation_e2e`), `combatbot/ports.py` (docstring), `combatbot/ui/main_window.py` (plan dry-run par
frame), `combatbot/ui/dofbot2/advanced.py` (accordéon « Plan dry-run »), `tests/test_dofbot2_advanced.py`, `README.md`.

## Architecture finale

```
Observation (vision, inchangée)          Corpus enregistré
        │ CombatObservation                     │ prediction + capture + vérités humaines
        ▼                                       ▼
combat/state.py  RealCombatState ◄──── corpus/dry_run_replay.py      corpus/observation_e2e_*.py
        │                                                              (frame par frame vs vérité)
        ▼                                                                       │
combat/planner.py  plan_turn ──► combat/targeting.py ──► combat/spells.py      ▼
        │                 └────► combat/pathfinding.py (GridTopology)   corpus/acceptance.py (3B-7)
        ▼
combat/executor.py  DryRunActionExecutor (ClosedLoopExecutor, ports.ActionExecutor) ──► CombatEvent
```

`combatbot/combat/` est pur : aucun import Qt, OpenCV, RapidOCR, pyautogui, ONNX ; aucune API d'entrée
Windows (vérifié par `test_combat_core_is_pure_and_sends_no_input`). Le simulateur (`engine.py`, `grid.py`,
`models.Spell`) est inchangé ; `CombatSpell.from_simulated/to_simulated` fait le pont.

## Fonctionnalités terminées

- **FAST-3B7** `python -m combatbot.benchmark --observation-e2e [--entity-splits …]` : map (mapId vérifié
  /mapid seulement), combat, joueur, ennemis (précision/rappel par cellule), occupation, PA, PM, phase, tour
  (« mon tour » affirmé à tort compté à part), taux d'UNKNOWN, latence par étape et de bout en bout
  enregistrées. Issues : CORRECT / WRONG / ABSTAINED / NOT_RECORDED / NO_TRUTH ; **une vérité absente n'est
  jamais un succès**, une abstention jamais une bonne réponse. Visibilité/alignement/source de grille :
  distribution (pas de vérité par frame dans ce corpus) ; résultat de combat : NOT_EVALUABLE. JSON + Markdown ;
  domaine supplémentaire de `--acceptance`. Corpus en lecture seule (testé octet par octet).
- **FAST-4B** `combat/pathfinding.py` : `CombatMap` (560 cellules, voisinage 4 démontré), `reachable`,
  `shortest_path`, coût en PM, statuts FOUND / ALREADY_THERE / NO_PATH / OUT_OF_MP / TARGET_BLOCKED / INVALID,
  raisons par cellule, `stop_cells` (atteignables, jamais traversées), déterminisme.
- **FAST-4A** `combat/spells.py` : `CombatSpell` (inconnu champ par champ, slot, `game_spell_id`, version
  2.64.5, provenance HUMAN_CONFIRMED / OCR_UNVERIFIED / ICON_MATCH_UNCONFIRMED / SIMULATED / UNKNOWN, statut
  CONFIRMED seulement si confirmé humainement et complet), stratégie séparée, sérialisation, validation,
  adaptateur `profile_spells` + réglages DofBot2 sans importer `Storage`.
- **FAST-4C** `combat/targeting.py` : TARGETABLE / NOT_TARGETABLE / UNKNOWN avec raisons structurées ;
  hypothèses explicites tracées dans `assumptions`.
- **FAST-4D** `combat/state.py` + `combat/planner.py` : état immuable (adaptateur `CombatObservation`), plan
  déterministe MOVE / CAST / END_TURN, chaque étape avec raison, préconditions, coûts et état attendu ;
  BLOCKED sur inconnu. Rejeu du corpus : `--dry-run-plans [--profile-id N] [--assume-logical-range]`.
  Pendant l'observation réelle, le plan de chaque frame s'affiche dans Outils avancés → Observation.
- **FAST-5A0** `combat/executor.py` : `DryRunActionExecutor` (journalise, n'agit jamais ; implémente aussi
  `ports.ActionExecutor`), contrat `ClosedLoopExecutor` (start → verify : SUCCEEDED / FAILED / TIMED_OUT,
  `correlation_id` stable par plan), événements `CombatEvent` avec `actions_sent: NONE`, plan BLOCKED refusé.

## Tests

| Fichier | Tests | Couvre |
|---|---:|---|
| `test_observation_e2e.py` | 11 | tout juste, vérité absente, UNKNOWN, annotation partielle, erreurs + tour dangereux, joueur NOT_VISIBLE, ancien pipeline, map /mapid, extraction, CLI + lecture seule, verdict acceptance |
| `test_combat_pathfinding.py` | 12 | topologie réelle (degrés 494/64/2, symétrie), chemin simple, blocage statique, inconnu statique, occupation, UNKNOWN, aucun chemin, déterminisme, budget PM, départ = arrivée, bords/coins, IDs invalides, stop_cells, performance |
| `test_combat_spells.py` | 6 | statut, validation, sérialisation, simulateur (dégâts jamais transmis), adaptateur `profile_spells` (Confirmé / À vérifier / Reconnu) |
| `test_combat_targeting.py` | 7 | portée non prouvée, règles prouvées (PA, limites, en ligne), hypothèse tracée, bonus de portée, LOS, sort non confirmé, géométrie |
| `test_combat_planner.py` | 12 | à portée, déplacement nécessaire, aucun chemin, PA insuffisants, données inconnues (7 cas), cellule UNKNOWN sur chemin, LOS/portée inconnues, plusieurs ennemis, limites par tour/cible, déterminisme, END_TURN, tacle, premier tour, adaptateur d'observation |
| `test_combat_executor.py` | 6 | plan journalisé sans exécution, plan BLOCKED refusé, succès/échec/timeout, protocole historique, id stable, **pureté + aucune API d'entrée** |
| `test_dry_run_replay.py` | 3 | parité `safe_for_decision`, état depuis document, rejeu READY/BLOCKED + lecture seule |
| `test_dofbot2_advanced.py` | +1 | plan dry-run calculé en observation et affiché ; erreur jamais bloquante |

**Suite complète** : 653 réussis, 2 ignorés, 3 échecs — les **mêmes 3 qu'avant le lot** (595 réussis au départ,
+58 tests) : `test_settings_ux`, `test_window_capture` (exigent `ctypes.WINFUNCTYPE`, Windows) et
`test_rapidocr_reads_synthetic_visible_text` (RapidOCR absent du conteneur). Aucun test supprimé, aucun xfail.
`git diff --check` propre à chaque commit.

## Performances (conteneur, Python 3.11)

| Opération | Temps |
|---|---|
| `shortest_path` sur 560 cellules (1/7 bloquées) | ≈ 150 µs |
| `reachable` 6 PM | ≈ 50 µs |
| Construction d'une `CombatMap` | ≈ 4 ms (une fois par map) |
| `evaluate_target` | ≈ 12 µs |
| `plan_turn` (déplacement + 2 sorts, 2 ennemis) | ≈ 0,9 ms |
| Banc e2e | ≈ 27 µs par frame |

Négligeable devant le p95 d'analyse mesuré en session réelle (979 ms, LOT 3B-7).

## Régressions

Aucune. Le simulateur, l'observation, la recette 3B-7 existante et l'UI gardent leurs tests verts. Seul
changement de comportement visible : un domaine `observation_e2e` de plus dans `--acceptance` (NOT_EVALUABLE
tant que le banc n'a pas tourné, donc verdict global « INCOMPLET » comme pour les autres domaines non mesurés).

## UNKNOWN / fail-closed

| Situation | Résultat |
|---|---|
| vérité absente (banc) | NO_TRUTH, jamais PASS ; « 0 erreur parce que 0 réponse » = NOT_EVALUABLE |
| `walkable`/`non_walkable_during_fight` à `None` | cellule bloquée |
| occupation UNKNOWN | cellule bloquée ; plan BLOCKED si le seul placement utile la traverse |
| `movement_cost` | ignoré (jamais rempli, sémantique non prouvée) |
| sort non confirmé / « Reconnu » par icône | UNKNOWN, jamais utilisé par le plan |
| métrique de portée, bonus de portée, algorithme de LOS | UNKNOWN par défaut ; hypothèse seulement si activée explicitement, tracée |
| phase ≠ FIGHTING, tour ≠ PLAYER, PA/PM/joueur/map inconnus, ennemi occulté, `safe_for_decision` faux | plan BLOCKED |
| PV ennemis, tacle, pièges, sorts allié/soi/case vide, « PV bas » | non modélisés : ignorés et signalés, jamais supposés |
| plan BLOCKED à l'exécuteur | REFUSED, rien ne part |

## Tâches bloquées par une intervention utilisateur

- Validation live finale 3B-6C (maps restantes) et recette live 3B-7 sur l'exécutable (latence p95).
- Lancer sur le PC : `--observation-e2e`, `--dry-run-plans --profile-id N` et `--acceptance` sur le corpus
  réel (le corpus 3B-6 est sur l'autre PC).
- Preuve de la **métrique de portée** et de l'**algorithme de LOS** 2.64.5 : captures en lecture seule de la
  zone de portée affichée par le client pour quelques sorts et positions (avec et sans obstacles).
- Lecture ou déclaration du **bonus de portée** du personnage.
- Confirmation humaine des sorts du profil (statut « Confirmé ») pour que le planificateur les utilise.

## Reste à faire avant un premier tour autonome

1. Prouver portée + LOS (ci-dessus), puis remplacer l'hypothèse par une règle vérifiée (`TargetingRules`).
2. PV/état des ennemis (ou une politique explicite sans PV), règle de tacle, pièges/glyphes si pertinents.
3. Identité des sorts DOFUS (`game_spell_id`) si une source fiable existe ; zone d'effet si représentable.
4. LOT 5A : `MouseActionExecutor` implémentant `ClosedLoopExecutor` (hors de ce lot, aucun clic ici).
5. LOT 5B : boucle fermée — `verify` alimenté par l'observation suivante, arrêt au premier FAILED/TIMED_OUT.
6. Placement, lancement de combat, fin de combat : hors périmètre.

## Tableau final

| Élément | Verdict |
|---|---|
| 3B-6C live final | **USER REQUIRED** |
| 3B-7 offline harness | **PASS** (outil complet et testé ; pas encore exécuté sur le corpus réel du PC) |
| 3B-7 live recipe | **USER REQUIRED** |
| 4A spell domain | **PASS** |
| 4B pathfinding | **PASS** |
| 4C targeting/LOS | **PARTIAL** (API et règles prouvées faites ; portée et LOS non prouvées → UNKNOWN par conception) |
| 4D dry-run decision | **PASS** (en règles prudentes, tout plan avec sort reste BLOCKED tant que 4C n'est pas prouvé) |
| 5A0 dry-run executor | **PASS** |
| REAL ACTIONS | **NONE** |
