# Audit court — fast-track offline (avant codage)

Branche : `lot-fasttrack-core-offline`, créée depuis `origin/lot-3b-7-real-observation-acceptance`
(`1e90a8691697df211384ad9f8ccb07de6c90fb82`).

**Pourquoi pas directement `lot-3b-6c-auto-map-detection` ?** Son HEAD réel est `5253cb2` (4 commits après
`4bbdb0a`, pris comme vérité). La branche `lot-3b-7-real-observation-acceptance` contient exactement ce HEAD
plus 9 commits (aucune divergence) qui ajoutent déjà la recette 3B-7 consolidée (`--acceptance`) et la
télémétrie de latence par étape. Partir de `5253cb2` aurait obligé à recréer un second harness 3B-7
parallèle, ce que les règles interdisent. La draft PR vise donc `lot-3b-7-real-observation-acceptance`.

Environnement : conteneur Linux (pas le PC Windows). Aucun client DOFUS, aucun corpus réel local
(`tests/fixtures/combat_real/` est vide) : tout est testé sur données synthétiques ; les bancs sont faits
pour tourner ensuite sur les données du PC.

## Déjà implémenté

| Domaine | Où | Remarque |
|---|---|---|
| Recette 3B-7 consolidée | `corpus/acceptance.py`, `--acceptance` | agrège les **rapports** des 6 bancs + sessions ; ne compare aucune frame à une vérité |
| Latence par étape | `vision/session_telemetry.py`, `capture.stage_ms` | capture, map, grid, entities, hud, combat_state, overlay, total |
| Topologie logique 560 cellules | `gamedata/topology.py` | voisinage **4-connexe démontré** (12 153 maps, GAME-DATA-REAL-VALIDATION §12-14) ; coins optionnels |
| Drapeaux statiques | `gamedata/formats/maps.py` → `GameMapCell` | `walkable` (bit 0), `non_walkable_during_fight` (bit 1), `line_of_sight` (bit 3) ; noms « historiques », statistiquement cohérents |
| Phase/tour | `vision/combat_state*.py`, `corpus/combat_state_metrics.py` | abstention = UNKNOWN, erreur dangereuse = « mon tour » affirmé à tort |
| Simulateur | `engine.py`, `grid.py`, `models.py` | grille rectangulaire synthétique, BFS, Bresenham, `Spell` avec `damage` **simulé** |
| Port d'exécution | `ports.ActionExecutor` | protocole seul, jamais branché |

## Réutilisable

- `CorpusRepository.list_entries/read_observation/read_annotation` : prédiction enregistrée
  (`prediction`, `capture`, `grid_snapshot`) et vérités humaines (`Annotation`).
- `combat_state_metrics.evaluate` : sémantique abstention/erreur dangereuse, à réutiliser telle quelle.
- `GridTopology.adjacency`, `topology.cell_to_grid/grid_to_cell/neighbors`.
- `models.Spell`, `models.Action`, `ports.ActionExecutor`, `CombatEvent`.
- `acceptance.Domain/Verdict` : le nouveau banc s'y branche comme un domaine de plus.

## Manquant (réalisable offline)

1. **Mesure image par image** de l'observation complète contre les vérités (map, grille, joueur, ennemis,
   occupation, PA, PM, phase, tour) avec taux d'UNKNOWN et latences : `--observation-e2e`.
2. **Pathfinding sur la vraie topologie** (DofusCellId + flags statiques + occupation dynamique),
   fail-closed.
3. **Domaine de sort réel** : UNKNOWN champ par champ, provenance, version 2.64.5, adaptateur
   `profile_spells` confirmé → sort ; le `Spell` simulé reste compatible.
4. **Ciblage/portée/LOS** avec résultat TARGETABLE / NOT_TARGETABLE / UNKNOWN et raisons.
5. **Planificateur dry-run** sur un état réel immuable (MOVE / CAST / END_TURN, BLOCKED sur inconnu).
6. **Exécuteur dry-run** + contrat corrélé (début, effet attendu, succès/échec/timeout).

## Points NON démontrés dans le dépôt (donc jamais devinés)

| Point | Conséquence dans ce lot |
|---|---|
| `movement_cost` | jamais rempli par le parseur (toujours `None`) : **ignoré**, 1 PM par pas d'arête |
| Coût « 1 PM par cellule » en combat | règle déjà supposée par le simulateur ; gardée, documentée comme hypothèse de règle |
| Métrique de portée des sorts (distance en cases) | **non prouvée** : désactivée par défaut → UNKNOWN ; activable explicitement (`RangeRule.LOGICAL_MANHATTAN`, marquée non vérifiée) pour le dry-run |
| Algorithme de LOS DOFUS 2.64.5 | **non prouvé** : sort avec LOS → UNKNOWN sauf oracle vérifié injecté ; sort sans LOS → LOS non requise |
| Sémantique exacte des bits de drapeaux | noms historiques ; `walkable is None` ou `non_walkable_during_fight is None` → bloqué |
| Zone d'effet des sorts | non représentable sans supposition → non modélisée |
| Résultat de combat (victoire/défaite) | aucune vérité dans le corpus → NOT_EVALUABLE |

## Dépendant d'un test humain / du jeu

- Validation live finale 3B-6C (maps restantes), recette live 3B-7 (sessions sur l'exécutable).
- Vérités phase/tour, entités, PA/PM supplémentaires ; corpus 3B-6 resté sur l'autre PC.
- Preuve de la métrique de portée et de l'algorithme de LOS (captures de portée en jeu).
- Toute action réelle (souris/clavier) : hors périmètre, **REAL ACTIONS : NONE**.

## Réalisable immédiatement offline (ce lot)

FAST-3B7 (banc e2e), FAST-4B (pathfinding), FAST-4A (domaine de sort), FAST-4C (ciblage conservateur),
FAST-4D (planificateur dry-run), FAST-5A0 (exécuteur dry-run). Logique pure : ni Qt, ni OpenCV, ni
RapidOCR, ni fenêtre Windows dans `combatbot/combat/`.
