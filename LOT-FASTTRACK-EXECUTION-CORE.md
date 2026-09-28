# LOT FAST-TRACK — Cœur d'exécution offline (5A1 → 6C, garde-fous)

> Serveur privé/test uniquement. **ENTRÉE RÉELLE ENVOYÉE : NONE.** Dans cette session : aucun clic, aucune souris réelle, aucune touche, aucune injection, aucune écriture mémoire, aucune interception réseau.

## Base, branche et pile de revue

| | |
|---|---|
| Base | `lot-fasttrack-core-offline` @ `75c624e` (PR #4) |
| Branche | `lot-fasttrack-execution-core` |
| PR | draft vers `lot-fasttrack-core-offline` (empilée) |

Pile de revue (rien n'est mergé ; `main` n'est pas touché) :

```
lot-3b-6c-auto-map-detection       5253cb2
    ↑ PR #5 (draft)
lot-3b-7-real-observation-acceptance 1e90a86
    ↑ PR #4 (draft)
lot-fasttrack-core-offline         75c624e
    ↑ cette PR (draft)
lot-fasttrack-execution-core
```

Hygiène GitHub faite dans cette session :
- création de la PR draft 3B-7 → 3B-6C ([#5](https://github.com/alexan-t/botdofus2/pull/5)) ;
- mise à jour de la roadmap [issue #1](https://github.com/alexan-t/botdofus2/issues/1). L'issue suit l'état réel : aucun PASS n'y est déclaré sans validation réelle.

## Commits

| SHA | Objet |
|---|---|
| `2c5c3ff` | feat: translate plan steps into screen actions without executing them (fast-5a1) |
| `a33e6d7` | refactor: judge step effects with per-kind invariants (fast-5b) |
| `1a08b43` | feat: add safety core and a disabled mouse executor (fast-safety, fast-5a2) |
| `cadd397` | feat: add the closed-loop action coordinator (fast-5b) |
| `6794ae0` | feat: run one autonomous turn against simulated observations (fast-5c) |
| `8aa0ee3` | feat: add the offline multi-turn fight loop (fast-6a) |
| `a7ba7b9` | feat: add the offline placement domain and decision (fast-6b) |
| `ee6e079` | feat: add the fight launch state machine (fast-6c) |
| `cb8d67d` | feat: add an offline self-test of the execution chain |
| `6c0ae19` | style: drop an unused import in the execution self-test |
| `d0bedff` | feat: add a one-command read-only Windows acceptance script |
| `af067db` | docs: record the 4C offline proof search (range and LOS stay UNVERIFIED) |
| `6c9d7ff` | feat: prepare the adapter from real observation packets to the action loop |
| (ce commit) | docs: report the offline execution core |

## Architecture

```
RealCombatObserver ──(live_adapter, préparé, non branché)──► Observation(state, at, frame_id, hwnd, layout_digest)
                                                                   │
 FightLoop (6A) ── WAIT_PLAYER_TURN ─► SingleTurnRunner (5C) ──────┤
   RESULT / ABORT        │               OBSERVE → PLAN (planner 4D, re-planifié après chaque preuve)
                         │               → ClosedLoopCoordinator (5B) pour UNE étape :
                         │                  PRECHECK → translate_step (5A1) → SafetyGuard → ActionSender
                         │                  → WAITING_EFFECT → check_effect (MOVE/CAST/END_TURN)
                         │                  → SUCCEEDED | FAILED | TIMED_OUT | ABORTED
                         ▼
 ActionSender = FakeSender (tests, auto-test) | MouseActionExecutor (5A2, désactivé par défaut)
                                                 └─ backend : RecordingBackend (défaut)
                                                             | Win32MouseBackend (combatbot/input, jamais appelé)
 Garde-fous : EmergencyStop (F9) · RealInputGate (kill switch global) · SafetyGuard · ActionJournal
 Hors boucle : placement (6B), LaunchStateMachine (6C)
```

- **Cœur pur.** Tout `combatbot/combat/` s'importe sans cv2, PySide6, rapidocr, pyautogui, onnxruntime, numpy ni le backend Win32. Un sous-processus le vérifie.
- **Code d'entrée Windows isolé.** `SendInput` et `SetCursorPos` n'existent que dans `combatbot/input/win32_mouse.py`. Un test le vérifie sur tout `combatbot/`.
- **Aucune abstraction parallèle :**
  - 5A1 réutilise `LayoutTransform`, `cell_to_client` (projection GameData), `Calibration.layout_compatibility`, `CombatGridProfileV2.status_for` et les zones normalisées ;
  - le découpage de la barre de sorts reprend celui de `scan_spell_bar` ;
  - 5B étend le contrat `start` / `verify` de 5A0 : `verify` applique désormais les invariants de chaque type d'étape ;
  - F9 réutilise le raccourci existant de DofBot2.

## Implémenté (testé offline)

- **5A1 — Plan → actions écran** (`screen_actions.py`).
  - `ScreenAction` porte : kind CLICK, coordonnées client et écran, `semantic_target`, `source`, `confidence`, `prerequisites`. C'est toujours un clic gauche simple.
  - MOVE : centre de la cellule par la projection GameData.
  - CAST : clic sur la case calibrée du sort, puis sur la cellule cible.
  - END_TURN : centre de la zone `end_turn`.
  - REFUSED si une donnée manque : projection absente ou non confirmée, orientation, cellule hors zone, zone absente ou non confirmée, sort sans case, page de sorts affichée inconnue ou différente, case hors barre, calibration incompatible ou à revalider.
- **Invariants d'effet** (`effects.py`).
  - MOVE : la cellule du joueur doit correspondre ; les PM ne sont vérifiés que s'ils sont lisibles.
  - CAST : baisse exacte des PA ; le joueur ne doit pas avoir bougé ; ni la mort ni les PV de la cible ne sont exigés.
  - END_TURN : le tour quitte PLAYER ; PA et PM ne sont pas exigés.
  - L'exécuteur dry-run 5A0 utilise ces mêmes règles.
- **Garde-fous** (`safety.py`) :
  - verrou `EmergencyStop`, déclenché par F9 et réarmé seulement par un « Démarrer » explicite ;
  - `RealInputGate` : fermée par défaut, fermée sous pytest, ne s'ouvre qu'avec une variable d'environnement explicite plus un armement, `kill` définitif ;
  - limites : actions par tour, clics par seconde (on espace les clics, on n'abandonne pas), durée du tour, expiration du plan, fraîcheur de l'observation ;
  - invariants : HWND, layout, map, `safe_for_decision` ;
  - journal AVANT et APRÈS chaque action.
- **5B — Coordinateur en boucle fermée** (`closed_loop.py`).
  - États IDLE, PRECHECK, READY_TO_SEND, WAITING_EFFECT, SUCCEEDED, FAILED, TIMED_OUT, ABORTED, FINISHED.
  - Identifiants `correlation_id`, `plan_id` et `step_index` portés partout.
  - Jamais deux étapes sans preuve de la précédente.
- **5C — Un tour** (`turn_runner.py`).
  - Re-planification après chaque étape prouvée, avec report des lancers déjà faits.
  - Un ennemi qui disparaît interrompt le tour : sa mort n'est pas prouvée.
- **6A — Multi-tours** (`fight_loop.py`).
  - Ne joue jamais pendant OTHER ; UNKNOWN toléré pendant un temps borné.
  - RESULTS détecté.
  - ABORT sur perte de fenêtre, de layout ou de map ; arrêt d'urgence permanent ; plafond de tours.
- **6B — Placement** (`placement.py`).
  - Types `PlacementState`, `PlacementPolicy`, `PlacementDecision` ; distance de marche par BFS GameData ; choix déterministe.
  - Les indices rouge/bleu GameData sont toujours BLOCKED.
- **6C — Lancement** (`launch.py`) : machine EXPLORATION → … → FIGHTING / FAILED, pilotée par des événements synthétiques.
- **Auto-test** : `python -m combatbot.benchmark --execution-selftest`.
  - 12 scénarios à travers le vrai code, avec vérification que la porte d'entrée réelle est fermée sur la machine.
  - Écrit `execution-selftest.json/.md` ; code de sortie 1 si un scénario diverge.

## Seulement préparé (non exercé en réel)

- **`MouseActionExecutor`** : désactivé par défaut, backend enregistreur par défaut.
  - Avant le déplacement ET avant le clic, il revérifie F9 et la fenêtre : même HWND, au premier plan, même position, même taille.
  - `Win32MouseBackend` (souris physique) est présent. Chaque méthode appelle d'abord `RealInputGate.require()`. Il n'a **jamais** été appelé : ni dans les tests, ni dans le CI, ni dans cette session.
- **`live_adapter.observation_from_packet`** : fait le pont avec `RealCombatObserver`, mais n'est branché nulle part.
- **`scripts/run_windows_acceptance.ps1`** : écrit et vérifié statiquement. Il n'a pas pu être exécuté ici (Linux, pas de PowerShell).

## Nécessite encore l'utilisateur ou le jeu

1. **Recette Windows en une commande** :
   ```
   powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1 -ProfileId <N>
   ```
   Ajouter `-AssumeLogicalRange` seulement pour mesurer l'hypothèse. Transmettre ensuite le dossier `reports\windows-acceptance-<date>\` avec son `SUMMARY.md`.
2. **Recette live 3B-6C et 3B-7.** La latence est FAIL (p95 979 ms) ; la re-mesure PA/PM et entités est à décider.
3. **Preuve 4C** : portée et LOS (voir `LOT-4C-OFFLINE-PROOF.md`). Protocole : l'oracle visuel du client, sur une collecte aidée.
4. **Sorts du profil** : confirmation humaine (Confirmé + `decision_ready`), et **page de sorts affichée**. Cette page n'est pas observée aujourd'hui, donc tout CAST réel est REFUSED.
5. **Zones `spell_bar` et `end_turn`** au statut « confirmée », et profil de projection confirmé sur le layout réel.
6. **Placement** : un détecteur des cases de départ actives (nouvelles données) avant tout 6B réel.
7. **Lancement** : un détecteur de groupe (`TargetConfirmed`) avant tout 6C réel.
8. **Première action réelle.** Décision explicite, variable d'environnement, armement et F9 testés à vide sur le PC. Rien de tout cela n'est fait ici.

## Tests

| Fichier | Tests | Couverture |
|---|---|---|
| `test_combat_screen_actions.py` | 11 | cellule connue, projection absente ou non confirmée, sort sans case, case hors page ou hors barre, page inconnue, calibration incompatible, zone `end_turn` absente ou non confirmée, redimensionnement, client → écran (écran secondaire négatif), UNKNOWN → REFUSED, plan réel complet |
| `test_combat_effects.py` | 3 | invariants MOVE / CAST / END_TURN |
| `test_combat_safety.py` | 16 | verrou F9 (concurrence), porte d'entrée réelle, backend Win32 refusé avant tout appel Windows, invariants et limites du garde-fou, cadence, exécuteur désactivé, backend réel refusé, fenêtre changée (4 cas), F9 entre déplacement et clic, point hors client et double clic, confinement des appels d'entrée |
| `test_combat_closed_loop.py` | 13 | plan complet prouvé, attente d'une frame nouvelle, contradiction, timeout, UNKNOWN (immédiat ou après délai de grâce), fenêtre / layout / map, tour perdu, fin de combat, préconditions, échec ou exception de l'exécuteur, F9 entre deux clics, MOVE / END_TURN, plan expiré, limite d'actions, plan BLOCKED |
| `test_combat_turn_runner.py` | 10 | les 13 scénarios 5C demandés + journal |
| `test_combat_fight_loop.py` | 7 | multi-tours jusqu'aux résultats, jamais pendant OTHER, UNKNOWN borné, perte de map ou de fenêtre, F9 permanent, échec de tour, plafond de tours, géométrie absente, journal |
| `test_combat_placement_launch.py` | 10 | placement déterministe, départage par identifiant, rester sur place, indices refusés, entrées inconnues, ennemi inaccessible ; lancement : parcours nominal, événements hors ordre, cible perdue ou expirée, délais, phases incohérentes, F9, combat sans placement observé |
| `test_execution_selftest.py` | 2 | auto-test PASS, CLI + rapport |
| `test_windows_acceptance_script.py` | 3 | BOM UTF-8, étapes requises, aucune action dans le jeu, hypothèse seulement explicite |
| `test_combat_live_adapter.py` | 2 | paquet → Observation, layout incompatible |

Tests existants modifiés, sans suppression :
- `test_combat_executor.py` :
  - le cas « PA inchangés » est désormais « en attente » (nouvelle sémantique CAST). La contradiction est testée avec une vraie contradiction ;
  - le test de pureté couvre les nouveaux modules.
- `test_dofbot2_app.py` : F9 verrouille, « Démarrer » réarme.

**Suite complète : 730 passed, 2 skipped, 3 failed** (+77 tests par rapport au lot précédent, qui en comptait 653).
- Les 3 échecs sont ceux connus sous Linux, non masqués : `test_browser_titled_dofus_is_not_a_game_window`, `test_rapidocr_reads_synthetic_visible_text`, `test_window_selection_by_handle_and_client_rect`.
- Ils sont lancés explicitement en premier par la recette Windows.
- `git diff --check` est propre sur chaque commit.

## Performances (ce conteneur, Python 3.11)

| Opération | Temps |
|---|---|
| `translate_step` (CAST, 2 actions) | ≈ 23 µs |
| `check_effect` | ≈ 4 µs |
| Tour simulé complet (3 étapes, 5 clics, planification comprise) | ≈ 1,2 ms |
| `decide_placement` (3 cases, BFS) | ≈ 1,7 ms |
| Auto-test complet (12 scénarios) | ≈ 5,4 ms |

Le coût du cœur est négligeable devant la latence d'observation (p95 979 ms mesuré en 3B-7). La boucle attend toujours une frame **postérieure** à l'envoi : la cadence réelle est fixée par l'observateur.

## Invariants de sécurité

1. Aucune action si `safe_for_decision` n'est pas True, si le tour n'est pas PLAYER ou si la phase n'est pas FIGHTING.
2. Aucune action si la fenêtre (HWND, premier plan, position, taille), le layout ou la map diffère de la géométrie utilisée pour traduire.
3. Jamais deux étapes sans preuve de la précédente. CONTRADICTED donne FAILED, l'absence de frame donne TIMED_OUT, UNKNOWN donne ABORTED.
4. F9 est vérifié avant chaque étape, avant chaque clic et entre le déplacement et le clic. Le verrou ne se réarme que par un démarrage humain.
5. Porte d'entrée réelle : fermée par défaut, sous pytest et après `kill` ; le backend Win32 la vérifie à chaque appel.
6. Uniquement des clics gauches simples, jamais de double clic ; point toujours dans le client.
7. Plafonds : 12 actions par tour, 2 clics par seconde, 45 s par tour, plan valable 5 s, observation valable 1 s, 40 tours.
8. Le journal est complet : AVANT et APRÈS pour chaque action, plus les transitions d'étape, de tour et de combat. `unmatched()` doit rester vide.
9. Aucune coordonnée en dur : tout vient de la projection GameData, de `LayoutTransform` et des zones calibrées confirmées.

## UNKNOWN et fail-closed nouveaux

| Inconnu | Effet |
|---|---|
| Page de sorts affichée | CAST REFUSED |
| Zone non confirmée, calibration à revalider | REFUSED |
| PA illisibles après un CAST, cellule illisible après un MOVE | ABORTED |
| Tour illisible après END_TURN | attente, puis TIMED_OUT |
| Ennemi disparu | tour ABORTED (mort non prouvée) |
| Cases de placement actives non observées | BLOCKED |
| Cible de lancement non prouvée | refusée |
| Portée et LOS 2.64.5 | toujours UNVERIFIED |

## Table finale

| Lot | Verdict |
|---|---|
| 5A1 plan→screen translation | **PASS** (offline) |
| 5A2 mouse backend disabled | **PASS** (désactivé, jamais exécuté) |
| 5B closed-loop coordinator | **PASS** (offline) |
| 5C single-turn offline | **PASS** |
| 6A multi-turn offline | **PASS** |
| 6B placement domain | **PASS** (domaine ; en réel toujours BLOCKED faute de détecteur de cases actives) |
| 6C launch state machine | **PASS** (machine seule, sans détecteur) |
| Safety core | **PASS** |
| Windows one-command acceptance | **PARTIAL** (écrit et vérifié statiquement ; à exécuter sous Windows) |
| 4C offline proof | **UNVERIFIED** |
| REAL INPUT SENT | **NONE** |
