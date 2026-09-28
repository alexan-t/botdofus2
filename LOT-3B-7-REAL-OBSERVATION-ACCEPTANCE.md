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

Recette `python -m combatbot.benchmark --acceptance` : verdict global **INCOMPLET**.

| Domaine | Verdict | Mesure |
|---|---|---|
| Grille (3B-3) | **PASS** | re-mesuré avec le code actuel : 26 frames, 0 faux combat en exploration (0,86 avant 0.4.0), rappel combat 1,0, 12/12 frames visibles ALIGNED, résidu médian 1,17 px |
| PA/PM (3B-4R2) | STALE | rapport du 24/09 (brut PASS : TEST 26 ex., 0 erreur acceptée, couverture 0,92, 1↔7 validé) antérieur au lecteur actuel |
| Entités (3B-5D) | NOT_EVALUABLE | aucun split déclaré dans le corpus de ce PC |
| Phase/tour (3B-6B) | NOT_EVALUABLE | aucune vérité phase/tour sur ce PC |
| Map (3B-6C) | NOT_EVALUABLE | voir ci-dessous |
| Latence | NOT_EVALUABLE | aucune session réelle journalisée pour l'instant |

**Map — mesure vide détectée.** Le banc a rejoué les 102 frames du corpus local (57 avec mapId
vérifié, 10 maps) : 0 mauvaise, mais aussi **0 correcte** (75 UNKNOWN, 16 TRANSITION, 11 AMBIGUOUS).
Cause : ces frames (24/09) ne contiennent que le crop combat ; avec la calibration de ce PC il commence
à 4,5 % du haut du client et coupe la ligne « Zone (Sous-zone) » → `AREA_NAME_INCOMPLETE`, lecture
refusée (comportement voulu, rien n'est inventé). Le client entier n'est enregistré que depuis 3B-6B.
Corrections : (1) le banc lit `client_frame` avec la ROI runtime quand il existe
(`ocr_inputs` dans le rapport) ; (2) la recette classe « 0 erreur parce que 0 réponse » en
NOT_EVALUABLE au lieu de PASS. Lecteur OCR : médiane ≈ 0,97 s par lecture sur ce PC.

## 4. Reste à faire

1. Session réelle avec `DofBot2.exe` reconstruit (exploration + au moins un combat), arrêt de
   l'observation → latence mesurée sur le package.
2. Re-mesurer PA/PM et entités avec le code actuel (bancs qui écrivent gabarits/profils : à lancer
   explicitement, décision utilisateur).
3. Phase/tour : TEST sur ce PC impossible sans vérité ; soit copier le corpus 3B-6 de l'autre PC,
   soit annoter 2 combats neufs (TEST aveugle, second PC = validation d'une 2e configuration).
