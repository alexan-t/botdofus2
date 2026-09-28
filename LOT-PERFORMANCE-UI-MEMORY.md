# LOT — Performance UI et mémoire

> **ACTIONS RÉELLES : NONE.**
>
> - **Branche** : `lot-performance-ui-memory`, créée depuis `a174375` (`lot-windows-live-readiness`, PR #7).
> - **Environnement de mesure** : conteneur Linux, Qt offscreen, Python 3.11. Le PC de l'utilisateur n'était pas accessible.
> - **Méthode** : `python -m combatbot.benchmark --runtime-health --duration 60 --sample-every 5 --cycles 20`. La vraie fenêtre DofBot2 est pilotée avec des paquets de taille réelle ; la vision est simulée par une attente sans CPU, pour isoler le coût de l'UI.
> - **Où lire les chiffres Windows** : GDI/USER, handles Windows et OCR seront mesurés par la recette Windows (section RESOURCE HEALTH).
> - **Détail avant modification** : `AUDIT-UI-PERFORMANCE-MEMORY.md`.

## Commits

| SHA | Objet |
|---|---|
| `b7ddab6` | perf: add runtime resource telemetry and a long-run health harness |
| `4d27730` | fix: release finished background jobs and everything their callbacks hold |
| `c800f60` | perf: build UI previews once at display size instead of at full resolution |
| `d10e62c` | perf: update the advanced UI in place and only when values change |
| `a8d7235` | perf: do not render previews for hidden tabs or a minimized window |
| `787aec0` | fix: bound qt log history and cheapen the journal change check |
| `f7f8893` | perf: make RapidOCR thread usage measurable and optionally bounded |
| `4f5d345` | feat: add a RESOURCE HEALTH section to the Windows acceptance |
| (ce commit) | docs: report ui performance and memory findings |

## Causes identifiées

1. **Fuite mémoire réelle — `JobRunner` (démontrée, corrigée).**
   - **Mécanisme.** Les `QRunnable` Python en `setAutoDelete(False)` étaient gardés par le pool. La connexion `done → lambda(job)` formait en plus un cycle via Qt, invisible pour le GC.
   - **Conséquence.** Chaque job terminé restait vivant, avec tout ce que ses rappels capturaient :
     - par tick d'observation : le job et ses closures ;
     - par cycle démarrer/arrêter : l'observateur complet.
   - **Correctif.** Le pool exécute une fonction Python simple, libérée par Qt, et les rappels sont déconnectés à la fin du job.
2. **Reconstruction complète des accordéons de la page Observation à chaque changement du texte de map.** Corrigé : mise à jour en place.
3. **Réécriture de ≈ 30 labels (texte et style) toutes les 800 ms**, même sans changement. Corrigé : mises à jour différentielles.
4. **Aperçus pleine résolution** : 3 copies, puis réduction lissée ; `QImage` pleine résolution gardées et réduites à chaque repaint. Corrigé : une réduction unique à la taille utile.
5. **Aperçus rendus même invisibles** (onglet caché, fenêtre réduite). Corrigé : rendu à l'affichage.
6. **`LogsPage` sans borne.** Corrigé : 200 lignes affichées, l'historique complet reste dans SQLite.

## Avant / après (harnais, 60 s par phase)

| Phase | CPU moyen (% d'un cœur) | RAM fin (Mo) | Privé fin (Mo) | UI lag p95 / max (ms) | Tendance mémoire |
|---|---|---|---|---|---|
| IDLE | 1,7 → **1,4** | 147 → 147 | 78 → 78 | 3,2 / 54 → **2,5 / 4,6** | STABLE → STABLE |
| OBSERVATION (autre onglet) | 8,8 → **3,2** | 198 → **186** | 126 → **115** | 3,3 / 30 → 2,8 / 83 (un pic isolé) | plateau → plateau |
| OBSERVATION onglet avancé | 15,0 → **8,9** | 216 → **205** | 144 → **132** | 20,0 / 49 → **3,3 / 9,2** | plateau → plateau |
| MINIMISÉ | 7,9 → **2,4** | 225 → **205** | 152 → **132** | 3,4 / 31 → 2,9 / 16 | plateau → **STABLE** |
| 20 cycles START/STOP | — | 231 → 346 (**GROWING**) → **209 → 211 (STABLE)** | — | — | fuite → stable |

Autres constats :

- **Threads.** 15 pendant l'observation, stables d'un cycle à l'autre (15 → 15). Après arrêt, les threads inactifs du pool se retirent.
- **Timers Qt actifs après arrêt** : 1 → 1.
- **Paquets vivants après arrêt** : 1 (le dernier, voulu).
- **Observateur ou job vivant après un arrêt** : aucun.
- **Reconstructions d'accordéons** : 67 → **0** en 60 s sur l'onglet Observation.
- **Observation** : p50 ≈ 252 ms, qui est la durée simulée, avec 0 tick sauté et aucune file d'attente.

## Micro-mesures

| Opération (thread UI) | Avant | Après |
|---|---|---|
| Aperçu d'observation par frame (2100×1000 → 900×500) | 17,4 ms | **6,0 ms** |
| Aperçu + vignette Connexion par nouvelle frame (2560×1377) | 14,8 ms | **8,3 ms** |
| Tick 800 ms, valeurs identiques | 6,05 ms | **0,51 ms** |
| Tick 800 ms, map qui change | 42,3 ms (46 reconstructions / 60) | **9,8 ms (0)** |
| Test « rien n'a changé » du Journal | 0,153 ms | 0,006 ms |
| `LogsPage` après 20 000 lignes | 20 200 blocs, +9,2 Mo | ≤ 200 blocs |
| 40 cycles démarrer/arrêter (tas Python) | 45 → 267 Mo | 14,5 → 14,5 Mo |

Le coût restant par frame sur l'onglet avancé est d'environ 12,5 ms. Il se répartit ainsi :
- réduction de l'aperçu (`cv2.resize`) : ≈ 5 ms ;
- rendu Qt : ≈ 6 ms.

Cela représente environ 1,3 % d'un cœur pour l'aperçu à 2,5 Hz. Un « mode éco » qui espacerait l'aperçu ne gagnerait que ce 1,3 % : il n'a pas été ajouté.

## Par domaine

- **Capture.** Corrigée au lot précédent : 185 → 27 ms de traitement après capture (synthétique). La frame de vision reste unique par tick.
- **Aperçus.** La vision garde sa frame en pleine résolution. L'UI n'utilise qu'une copie réduite, jamais réutilisée par la vision.
- **Observation.**
  - Architecture « timer → si occupé : sauter, sinon une seule frame » confirmée, sans file cachée.
  - Fréquence adaptative non modifiée : la lecture OCR de la map a déjà son propre thread, à une lecture toutes les 2 s. Aucun changement sans mesure réelle.
- **OCR / ONNX.**
  - Un seul moteur par processus.
  - Threads ONNX par défaut, donc potentiellement tous les cœurs pendant chaque lecture de map. **Non mesurable ici.** `DOFBOT_OCR_THREADS` permet de les borner (absent = comportement inchangé), et `--ocr-threads-benchmark` mesure chaque réglage sur le PC.
- **Journaux.** Tous bornés : tableau de bord 100, Journal avancé 120 (80 lignes affichées), `LogsPage` 200.
- **SQLite.**
  - ≈ 0,8 ms par événement, avec un commit par événement.
  - L'observation n'écrit aucun événement par frame. **Non modifié**, car négligeable.
- **Caches.** Tous bornés ou de taille finie (voir l'audit). Aucun `gc.collect()` périodique n'a été ajouté.

## Risques et régressions

- **Suite complète : 771 passed, 2 skipped.**
  - 1 échec : `test_rapidocr_reads_synthetic_visible_text`, module absent de ce conteneur.
  - Test intermittent **antérieur** : `test_hud_ground_truth::test_collection_turns_without_shared_counter_are_separate_groups` échoue parfois, aussi sur la base `a174375` (1/15 exécutions). Ce n'est pas une régression de ce lot. Il n'est pas masqué et reste à examiner.
- Tests ajoutés :
  - jobs libérés ;
  - aperçus réduits, source intacte, clics remappés, pas de rendu invisible ;
  - aucune reconstruction ni réécriture sans changement ;
  - journaux bornés ;
  - option threads OCR ;
  - moniteur de ressources.
- **Tests existants ajustés, aucun supprimé.** Un test lisait l'aperçu d'une page non affichée : il vérifie désormais que la frame est transmise. Le rendu à l'affichage est couvert par un test dédié.
- **Risque : Windows.** Le harnais ouvre une vraie fenêtre DofBot2 pendant environ 5 minutes sur Windows. Les données sont isolées dans un dossier temporaire.
- **Risque : l'aperçu minimisé.** Il est rendu au premier repaint après restauration : un seul calcul, dernière frame.

## Table finale

| Critère | Verdict |
|---|---|
| Memory leak | **YES** (JobRunner, démontré et corrigé) — autres fuites : NOT_PROVEN |
| RSS stability | **PASS** (cycles STABLE, plateaux après échauffement) — sur Linux ; Windows : recette |
| Thread stability | **PASS** (15 → 15 sur 20 cycles) |
| Handle/GDI stability | **PARTIAL** (handles stables sous Linux ; GDI/USER mesurés seulement sous Windows) |
| UI responsiveness | **PASS** (UI lag p95 ≤ 3,3 ms dans toutes les phases) |
| Preview cost | **PASS** (17,4 → 6,0 ms ; 14,8 → 8,3 ms ; aucun rendu invisible) |
| Observation CPU | **PARTIAL** (coût UI mesuré ; coût vision réel : `--runtime-profile` sur le PC) |
| Capture performance | **PARTIAL** (−158 ms synthétique au lot précédent ; à mesurer sur le PC) |
| OCR CPU pressure | **PARTIAL** (moteur unique ; threads non mesurables ici, banc prêt) |
| Logs bounded | **PASS** |
| SQLite UI blocking | **PASS** (≈ 0,8 ms par événement, aucun événement par frame) |
| Start/stop cleanup | **PASS** (aucun observateur ni job résiduel ; RSS stable sur 20 cycles) |
| REAL INPUT SENT | **NONE** |
