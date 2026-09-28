# LOT — Validation performance (PR #8) : JobRunner, stress UI, recette Windows

> **ACTIONS RÉELLES : NONE.**
>
> - **Branche** : `lot-performance-ui-memory` (PR #8), empilée sur `lot-windows-live-readiness`. Non fusionnée, cible inchangée.
> - **Environnement de cette validation** : conteneur Linux, Qt offscreen, Python 3.11, PySide6 6.11.2. `DOFBOT_ALLOW_REAL_INPUT` absent (vérifié).
> - **Recette Windows NON EXÉCUTÉE.** Le PC de l'utilisateur, DOFUS et RapidOCR ne sont pas accessibles depuis ce conteneur. Aucun chiffre ci-dessous ne prétend venir de Windows. Tout ce qui est propre à Windows (GDI, USER, handles Windows, octets privés Windows, OCR) est marqué **NOT MEASURED** et sera produit par la recette ci-dessous.

## 1. Commande à lancer sur le PC

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1
# si plusieurs profils existent (le script le signale), relancer avec l'identifiant affiché :
powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1 -ProfileId <ID>
```

- **Ne jamais deviner `<ID>`** : utiliser celui que le script affiche.
- **La section RESOURCE HEALTH** contient :
  - `--runtime-health` : phases IDLE / OBSERVATION / onglet avancé / MINIMISÉ, 20 cycles START/STOP ;
  - la nouvelle ligne **Stress UI** : GDI, USER, handles et RAM, via `--ui-stress --cycles 20`, soit 10 blocs ;
  - le banc `--ocr-threads-benchmark` si RapidOCR est installé.
- **Aucune entrée n'est envoyée.** La fenêtre DofBot2 est pilotée dans un dossier de données temporaire.

## 2. Commits de cette étape

| SHA | Objet |
|---|---|
| `76e8987` | fix: keep JobRunner callbacks on the GUI thread and drop them after close |
| `0148145` | test: stress GDI, USER, handles and RAM across repeated UI operations |
| (ce commit) | docs: record the PR #8 performance validation |

## 3. Revue technique de PR #8 — JobRunner

### Architecture vérifiée

- **Signaux.** Le `_Signals` (QObject) de chaque job est créé dans `submit()`, donc dans le thread appelant.
- **Exécution.** `work()` s'exécute dans un thread du `QThreadPool`. Les émissions depuis ce thread sont mises en file (connexion automatique, donc *queued*) vers le thread propriétaire des signaux.
- **Pas de hack.** Aucun `processEvents` n'est utilisé comme contournement.

### Deux vrais trous trouvés et corrigés (`76e8987`)

1. **`submit()` appelé hors du thread GUI.**
   - *Avant* : les signaux naissaient dans le worker, et les rappels (qui touchent des widgets) s'exécutaient dans un thread non-GUI.
   - *Maintenant* : l'appel lève `RuntimeError`. Cela garantit que la file cible est toujours le thread GUI.
2. **`JobRunner` détruit pendant un job** (fenêtre ou dialogue fermé).
   - *Avant* : le rappel du job s'exécutait quand même, sur des widgets détruits.
   - *Maintenant* : l'état « fermé » est gardé **hors** de l'objet C++ (dict capturé), et mis à jour par `destroyed` et par `shutdown()`. Les rappels en vol sont abandonnés, et un nouveau `submit()` est refusé.

**Preuve que les tests mordent** : sur l'ancien code, exactement 2 tests échouent (runner détruit, `submit` depuis un thread).

### Tests (`tests/test_job_runner_threads.py`, 7 tests, plus `test_job_runner_release.py`)

Contrôle du thread : `QThread.currentThread() == QApplication.instance().thread()` **et** `threading.get_ident()`.

| Cas | Résultat |
|---|---|
| `work()` dans un worker ; `on_success`, `on_error`, `_done`, `all_done` dans le thread GUI | PASS |
| Succès, exception, jobs successifs | PASS |
| 8 jobs concurrents : chaque rappel exactement une fois, `all_done` une seule fois | PASS |
| Rappel capturant un widget : widget mis à jour depuis le thread GUI | PASS |
| Widget détruit / runner détruit pendant le job : aucun crash, aucun rappel | PASS (runner détruit : échouait avant) |
| `submit()` depuis un thread worker | refusé (échouait avant) |
| Tout libéré après `done` : weakrefs sur rappels, `work`, propriétaire et job à `None`, `_jobs` vide | PASS |
| Stress de 300 jobs : aucun job ni propriétaire vivant, tas plat, threads +≤ 2 | PASS |

**Note sur les tests de destruction.** `processEvents()` n'exécute pas les `deleteLater` : sans `sendPostedEvents(None, DeferredDelete)`, un test « runner détruit » passe à tort. Les tests et le harnais (`_pump`) traitent donc explicitement les suppressions différées, comme la vraie boucle d'événements Qt.

## 4. RESOURCE HEALTH (Linux, harnais)

### Stress UI (`--ui-stress`, 18 blocs)

Contenu de chaque bloc :
- 1000 `set_text`/`set_style` ;
- reconstruction volontaire des accordéons de chaque page ;
- 50 observations synthétiques ;
- 5 réductions/restaurations ;
- 20 jobs.

| Mesure | Bloc 1 | Blocs 2..18 | Verdict |
|---|---|---|---|
| RSS | 207 Mo | 202 → 222 Mo (plafond ≈ 221 Mo, bruit ± 5 Mo) | **STABLE** (+0,26 Mo par bloc, non significatif) |
| Octets privés (Linux : anonymes) | 138 Mo | 134 → 153 Mo (plafond ≈ 152 Mo) | plafond |
| Threads | 16 | 16 à chaque bloc | **STABLE** |
| Handles (descripteurs Linux) | 12 | 12 à chaque bloc | **STABLE** |
| GDI / USER | — | — | **NOT MEASURED** (Windows seulement) |
| Reconstructions d'accordéons | 9 par bloc | 9 par bloc | voulu (le stress force `refresh()`) |

**Deux artefacts de mesure éliminés avant ce verdict** (aucun n'était une fuite de DofBot2) :

1. **Harnais sans suppressions différées.** Les widgets supprimés par `deleteLater` ne l'étaient jamais : +23 Mo par bloc. Corrigé dans `_pump`, avec un test dédié.
2. **Sonde `tracemalloc`.** Les instantanés conservés par le script de mesure produisaient une croissance linéaire. Ils ont été retirés de la mesure.

**Bissection** :
- widgets isolés : plats ;
- changements d'onglet : plats ;
- page Observation : échauffement d'environ 2,7 Mo, puis plate.

### Phases (lot précédent, inchangées par cette étape)

| Phase | CPU (% d'un cœur) | RAM fin | UI lag p95 / max | Tendance |
|---|---|---|---|---|
| IDLE | 1,4 | 147 Mo | 2,5 / 4,6 ms | STABLE |
| OBSERVATION (autre onglet) | 3,2 | 186 Mo | 2,8 / 83 ms (pic isolé) | plateau |
| OBSERVATION onglet avancé | 8,9 | 205 Mo | 3,3 / 9,2 ms | plateau |
| MINIMISÉ | 2,4 | 205 Mo | 2,9 / 16 ms | STABLE |
| 20 cycles START/STOP | — | 209 → 211 Mo | — | STABLE |

## 5. Latence : UI et vision séparées

- **UI lag** : retard du timer du thread GUI. Il vaut p95 ≤ 3,3 ms dans toutes les phases. L'UI ne bloque pas.
- **Latence d'observation** : durée du job de vision.
  - *Ici* : p50 ≈ 252 ms, ce qui est la durée **simulée**, avec 0 tick sauté et aucune file.
  - *En réel* : la latence vision (capture, détection, OCR de map) n'est **pas mesurable dans ce conteneur**. La recette Windows et `--runtime-profile` la donnent.
- **Si le PC « rame »** : les deux mesures ne se confondent pas.
  - Si l'UI lag reste bas pendant que la latence d'observation monte, la cause est la vision ou l'OCR, pas l'UI.

## 6. OCR — recommandation (non appliquée)

- **Aujourd'hui.** Un seul moteur RapidOCR par processus. Les threads ONNX suivent le réglage par défaut : potentiellement tous les cœurs pendant chaque lecture de map.
- **Recommandation.** Si la recette montre une pression CPU OCR élevée, lancer `--ocr-threads-benchmark`. Choisir ensuite la plus petite valeur de `DOFBOT_OCR_THREADS` dont la latence reste acceptable ; typiquement 2 ou 4, selon le banc.
- **Non appliqué.** La variable absente conserve le comportement actuel. Aucun réglage n'est imposé sans mesure sur le PC.

## 7. Revalidation UI

| Élément | Constat |
|---|---|
| **Aperçus** | une seule réduction à la taille utile ; aucun rendu en onglet caché ou fenêtre réduite ; rendu au premier repaint après restauration (tests existants PASS) |
| **Accordéons** | 0 reconstruction en 60 s d'observation (lot précédent) ; les reconstructions forcées du stress ne font pas croître la mémoire |
| **Setters différentiels** | 1000 `set_text`/`set_style` par bloc sans croissance |
| **Journaux** | tableau de bord 100 ; Journal avancé 120 (80 affichées) ; `LogsPage` 200 |
| **`CombatPage.history`** | audité : `setPlainText` des 30 dernières entrées, historique moteur borné à 100. Borné, **non modifié** |

## 8. Tests manuels

- **UI sans DOFUS.** **Non réalisé ici** : pas d'écran ni de PC. La recette Windows ouvre la vraie fenêtre ; un contrôle visuel reste à faire sur le PC.
- **DOFUS en lecture seule.** **Non disponible** dans ce conteneur.

## 9. Interdits respectés

Aucune des pratiques interdites n'est utilisée :
- `gc.collect()` ;
- arrêt forcé de thread ;
- nettoyage global de `QPixmap` ;
- mode éco dégradant la logique ;
- délai artificiel ;
- `sleep` dans le thread UI de l'application.

Les seuls `time.sleep` sont dans le harnais de mesure et dans les tests, pendant l'attente des jobs.

Côté entrées réelles :
- aucune entrée réelle ;
- `RealInputGate` jamais activé ;
- backend souris Windows jamais appelé.

## 10. Tests

- Ciblés : `test_job_runner_threads.py` et `test_job_runner_release.py` (8 passed), `test_performance_monitor.py` (4), `test_windows_acceptance_script.py` (4).
- Suite complète : **780 passed, 2 skipped, 1 failed** (`test_rapidocr_reads_synthetic_visible_text`, module absent). Le test HUD intermittent est passé lors de cette exécution.
- Rappel : `test_rapidocr_reads_synthetic_visible_text` échoue car le module est absent du conteneur.
- Test intermittent **antérieur** : `test_hud_ground_truth::test_collection_turns_without_shared_counter_are_separate_groups` (1/15 sur la base `a174375`). Il n'est pas masqué.
- Aucun test supprimé ; aucun `xfail` ; aucune vérité de test modifiée.
- `git diff --check` : propre.

## 11. Table finale

| Critère | Verdict |
|---|---|
| JobRunner memory release | **PASS** (300 jobs : aucun job, rappel ni propriétaire vivant) |
| JobRunner GUI-thread callbacks | **PASS** (2 trous corrigés : `submit` hors GUI, runner détruit) |
| RSS stability Windows | **NOT MEASURED** (Linux : STABLE, plafond ≈ 221 Mo) — recette Windows |
| Private bytes stability | **NOT MEASURED** sous Windows (Linux : plafond ≈ 152 Mo) |
| Thread stability | **PASS** (Linux : 16 threads sur 18 blocs ; 15 → 15 sur 20 cycles) |
| Handle stability | **PARTIAL** (Linux : 12 → 12 ; handles Windows : recette) |
| GDI stability | **NOT MEASURED** (Windows seulement ; ligne « Stress UI » de la recette) |
| USER object stability | **NOT MEASURED** (Windows seulement ; ligne « Stress UI » de la recette) |
| UI responsiveness | **PASS** (UI lag p95 ≤ 3,3 ms, harnais Linux) |
| Preview rendering | **PASS** |
| Accordion rebuilds | **PASS** (0 en observation ; reconstructions forcées sans croissance) |
| Logs bounded | **PASS** |
| OCR CPU pressure | **NOT MEASURED** (RapidOCR absent ; banc prêt ; recommandation non appliquée) |
| Observation latency | **PARTIAL** (UI mesurée ; latence vision réelle : recette / `--runtime-profile`) |
| Start/stop cleanup | **PASS** (20 cycles STABLE, aucun observateur ni job résiduel) |
| REAL INPUT SENT | **NONE** |

**Est-ce que DofBot2 ralentit encore le PC ?**
- **Côté UI et mémoire (mesurable ici) : non.** Il n'y a plus de fuite démontrée : mémoire en plateau, threads et handles stables, UI lag de quelques millisecondes.
- **Ce qui reste à mesurer sur le PC :**
  - pression CPU de l'OCR ;
  - GDI / USER ;
  - latence vision réelle.

  La recette ci-dessus les donne, à renvoyer pour conclure.
