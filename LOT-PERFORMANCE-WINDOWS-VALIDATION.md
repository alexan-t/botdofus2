# LOT — Validation Windows performance et readiness (PR #8)

> **ENTRÉE RÉELLE ENVOYÉE : AUCUNE.**
>
> Branche `lot-performance-ui-memory`, PR #8, non fusionnée. Validation exécutée le 29 septembre 2026 sur le commit `19153f9`. La variable `DOFBOT_ALLOW_REAL_INPUT` était absente puis explicitement supprimée avant la capture réelle.

## Environnement et périmètre

- Windows 10 Professionnel 22H2, build 19045.
- AMD Ryzen 5 3600, 6 cœurs / 12 processeurs logiques, 32 Go de RAM.
- Python 3.12.14.
- Profil résolu sans ambiguïté : profil 1.
- Client détecté : HWND `177343598`, titre `Barbare - Dofus 2.64.5.0`, non minimisé.
- Capture réelle en lecture seule : **réussie**, 2560 × 1377, source `window`, contenu non uniforme. Le résultat `CONTENT_UNCERTAIN` signifie seulement qu'aucune icône connue n'a confirmé visuellement le client ; la capture elle-même est valide.
- Observation réelle longue : **NOT_EVALUABLE**. Le profil 1 de cette base ne possède aucune calibration et aucun dossier GameData configuré. Le démarrage normal refuse donc l'observation. Ce garde-fou n'a pas été contourné et aucune calibration n'a été inventée.
- Corpus : 3840 fichiers avant et après la recette, aucune différence.

Artefacts détaillés : `reports/windows-acceptance-20260929-010036/`.

## Correctifs issus de la recette

| Commit | Correction |
|---|---|
| `1a9c79f` | Déclare les signatures Win32 complètes dans le collecteur de ressources. Sans `argtypes`, le pseudo-handle 64 bits de `GetCurrentProcess` était tronqué et provoquait `OverflowError: int too long to convert`. Le test de chemin de sécurité accepte aussi les séparateurs Windows. |
| `19153f9` | Classe les cycles start/stop après la moitié d'échauffement et publie séparément RSS, Private Bytes, threads, handles, GDI et USER. L'ancien calcul incluait la création initiale des caches Qt et signalait à tort une fuite. |

Les corrections existantes de PR #8 sur `JobRunner` restent validées : callbacks sur le thread GUI, abandon des callbacks après destruction, refus d'un `submit()` hors thread GUI et libération des références après les jobs.

## Résultats automatisés

- Tests Windows ciblés de la recette : **3 passed**.
- Suite complète de la recette : **782 passed, 1 skipped**.
- Suite complète après les deux correctifs : **783 passed, 1 skipped en 91,03 s**.
- Stress `JobRunner`, 300 jobs : aucun job ni propriétaire restant, rappels libérés, threads dans la tolérance.
- Auto-test d'exécution : **PASS**, entrée réelle envoyée : **NONE**.
- `git diff --check` : propre.

## Ressources Windows

Chaque phase dure 60 secondes. L'observation du harnais emploie des frames synthétiques afin de mesurer l'UI et le cycle de vie sans agir sur DOFUS.

| Phase | CPU moy./p95 (% d'un cœur) | RSS début → fin, max (Mo) | Private Bytes fin (Mo) | Threads | Handles | GDI / USER fin | UI lag p95 / max (ms) | Observation p50 / p95 (ms) | Verdict mémoire |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| IDLE | 0,9 / 1,9 | 139,27 → 139,38, 139,48 | 445,78 | 25 → 21 | 1218 → 1214 | 23 / 41 | 26,27 / 45,41 | — | STABLE |
| OBSERVATION | 2,3 / 2,5 | 159,68 → 160,04, 160,05 | 464,05 | 22 → 21 | 1228 → 1230 | 26 / 45 | 19,16 / 52,48 | 252,79 / 253,43 | STABLE |
| ADVANCED_OBSERVATION | 2,7 / 1,9 | 160,47 → 160,40, 160,50 | 465,00 | 21 → 13 | 1230 → 1207 | 26 / 31 | 26,07 / 51,36 | 252,91 / 253,65 | STABLE |
| MINIMIZED | 1,9 / 1,9 | 162,15 → 162,08, 162,18 | 466,45 | 19 → 13 | 1226 → 1208 | 26 / 33 | 17,58 / 42,34 | 252,79 / 253,44 | STABLE |

Le mode minimisé ne reconstruit pas l'aperçu. Son CPU reste bas, mais n'est pas significativement inférieur à l'observation synthétique, elle-même très légère.

### 50 cycles start/stop

La première moitié est l'échauffement. Sur les 25 derniers cycles :

| Ressource | Mesure | Verdict |
|---|---|---|
| RSS | plateau autour de 147,5 Mo, dernier point 145,95 Mo | PLATEAU_AFTER_WARMUP |
| Private Bytes | plateau autour de 468,8 Mo, dernier point 466,82 Mo | PLATEAU_AFTER_WARMUP |
| Threads | montée initiale 28 → 32, puis retour et plateau à 29 | PLATEAU_AFTER_WARMUP |
| Handles | 439 → 444 puis 444 stable | PLATEAU_AFTER_WARMUP |
| GDI / USER | stable ; le stress UI confirme GDI 26 → 26 et USER 46 → 46 | STABLE |
| QTimers / widgets / paquets | 10 / 1249 / 1, stables | STABLE |
| Observateur ou job après arrêt | aucun | PASS |

Il n'y a pas de croissance continue après échauffement : **aucune fuite mémoire ou de ressource n'est démontrée**.

### Stress UI, 10 blocs

Chaque bloc force 1000 changements texte/style, les reconstructions d'accordéons, 50 aperçus, 5 réductions/restaurations et 20 jobs.

- RSS : 193,02 → 192,96 Mo, **STABLE**.
- Handles : 1261 → 1257, **STABLE**.
- GDI : 26 → 26, **STABLE**.
- USER : 46 → 46, **STABLE**.
- Threads : échauffement 25 → 35, puis 31 en fin.
- 140 reconstructions d'accordéons au total, toutes provoquées volontairement par le stress. En observation normale, le compteur reste à zéro.

## OCR et latence

| Threads ONNX | Chargement (ms) | Lecture médiane / p95 (ms) | CPU (% d'un cœur) |
|---:|---:|---:|---:|
| défaut | 955,9 | 1334,72 / 1409,41 | 538,1 |
| 1 | 340,4 | 1920,18 / 2098,93 | 99,7 |
| 2 | 344,5 | 1484,71 / 1514,87 | 190,7 |
| 4 | 439,1 | 1350,36 / 1499,36 | 365,0 |

**Recommandation : 2 threads** via `DOFBOT_OCR_THREADS=2`. Cela réduit la pression CPU d'environ 65 % par rapport au défaut, avec environ 11 % de latence médiane supplémentaire. Le réglage n'est pas appliqué automatiquement : la variable existante constitue déjà un mécanisme réversible.

Le profil détaillé capture / map / grille / entités / HUD / état de combat reste **NOT_EVALUABLE** faute de session réelle calibrée. Les 472 frames historiques donnent seulement une durée totale médiane de 772,79 ms et p95 de 1675,35 ms, sans ventilation fiable. Le lecteur de coordonnées de map est le coût spécialisé mesuré le plus élevé, médiane 800,526 ms ; l'OCR/map est donc le suspect principal, mais un classement complet des trois postes les plus chers demanderait une session calibrée.

## Readiness vision et combat

- Observation e2e hors ligne, 496 frames : **FAIL**.
- État de combat : phase précision 97,44 %, couverture 90,70 % ; tour précision 100 %, couverture 90 %, aucun faux « mon tour » dangereux.
- Map : précision 100 % sur les valeurs produites, couverture 79,87 %.
- Cellule joueur en combat : 116 connues sur 149 ; 33 inconnues, soit **22,1 %**.
- Entités ennemies : précision 45,76 %, donc **FAIL**.
- Occupation : précision 40 %, taux inconnu global 94,35 %, donc **FAIL**.
- PA/PM : taux inconnu 75,51 %, non évaluable.
- Dry-run : 496/496 plans **BLOCKED**. `safe_for_decision` bloque toutes les frames ; les autres causes majeures sont phase 353, ennemi 330, PM 249, PA 214, map/topologie 201 et cellule joueur 191.
- Sorts : 0/13 prêts. Les 13 scans sont `Inconnu` et les règles AP, portée, ligne, LOS, par tour et par cible restent non renseignées. Aucune valeur n'a été modifiée sans preuve.
- Portée/LOS 4C : **UNVERIFIED**, zéro échantillon.
- Aucun seuil vision, vérité TEST, moteur de combat ou calibration automatique n'a été modifié.

Le blocage de décision le plus directement actionnable est la préparation des sorts, suivie par la couverture des observations réelles annotées. Aucun résultat de cette recette ne justifie d'assouplir les garde-fous.

## Verdict final

| Critère | Verdict |
|---|---|
| JobRunner memory release | **PASS** |
| JobRunner GUI-thread callbacks | **PASS** |
| RSS stability Windows | **PASS** |
| Private Bytes stability | **PASS** |
| Thread stability | **PASS** |
| Handle stability | **PASS** |
| GDI stability | **PASS** |
| USER object stability | **PASS** |
| UI responsiveness | **PASS** |
| Preview rendering | **PASS** |
| Accordion rebuilds | **PASS** |
| Logs bounded | **PASS** |
| OCR CPU pressure | **PARTIAL** — réglage par défaut lourd ; 2 threads recommandés |
| Observation latency | **PARTIAL** — total historique mesuré, ventilation live non évaluable |
| Start/stop cleanup | **PASS** |
| Fenêtre et capture DOFUS | **PASS** |
| Observation réelle calibrée | **NOT_EVALUABLE** — calibration absente |
| Readiness décision combat | **FAIL** — 0/13 sorts prêts, 496/496 plans bloqués |
| REAL INPUT SENT | **NONE** |

**Est-ce que DofBot2 ralentit encore le PC ?** L'UI et la gestion mémoire sont désormais acceptables : pas de fuite, pas de croissance de handles/GDI/USER et une boucle UI réactive. RapidOCR reste susceptible de charger fortement le processeur pendant la lecture de map avec le réglage par défaut. Le compromis mesuré est 2 threads. La latence vision réelle complète devra être rejouée après calibration du profil, sans contourner les validations.
