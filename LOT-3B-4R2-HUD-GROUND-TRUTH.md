# LOT 3B-4R2 — Vérité humaine du HUD et collecte des classes manquantes

Date : 24 septembre 2026
Version : **0.4.3** (lecteur PA/PM inchangé : seul l'outillage corpus/revue/collecte a évolué)
Branche : `lot-3b-4r-hud-validation`, depuis `ecd891f`
Client : `C:\Users\Alpha5\AppData\Local\Alea\Client`
Référence : `LOT-3B-4R-REAL-HUD-VALIDATION.md`

## Statuts

| Élément | Statut |
| --- | --- |
| REAL HUD GROUND TRUTH | **PASS** |
| PA READER | **PASS** (chiffres observés ; limites §9) |
| PM READER | **PASS** |
| 1 VS 7 | **VALIDATED** (critère §16, support encore modeste) |
| DIGIT 4 | **VALIDATED** (support minimal : 1 crop TRAIN, 1 crop TEST) |
| DIGIT 8 | **NOT OBSERVED** |
| RAPIDOCR | **PARTIAL** |
| GRID REGRESSION | **NONE** |

Le lecteur n'a pas été modifié : segmentation, templates, `min_template_score` (0,70), `min_margin`
(0,055), `one_seven_margin` (= `min_margin`), seuils RapidOCR (skip 0,74, fallback 0,94),
`NumberTemporalTracker` et ROI sont ceux de 0.4.3. LOT 3B-5 non commencé.

## 1. Annotations initiales

- 3B-4R : 46 observations / 92 crops portant la mention « Vérité PA/PM confirmée par l'utilisateur »,
  écrites par script et **non traçables** → `truth_source = "unverified_import"`, exclues du banc.
- Corpus de l'exécutable : 1 observation du 23/09 (PA 11 / PM 1) avec crops, 3 sans crops.

### Deux corpus distincts sur le nouveau PC

`python main.py` lit `data\corpus` du projet ; `PythonBot.exe` lit `%LOCALAPPDATA%\PythonBot\data\corpus`.
La revue affichait donc « 1/1 ». Le corpus 3B-4R a été **fusionné dans celui de l'exécutable** par
`scripts/merge_corpus.py` : simulation préalable, refus si l'exe est ouvert, aucune collision
(identifiant, frame, dossier), copie vérifiée par SHA-256, sauvegarde
`manifests.backup-20260924-131222`, reprise du registre de split (TEST gelé). Aucune vérité créée.
`data\corpus` du projet reste intact comme copie de secours.

**Corpus de référence désormais : `%LOCALAPPDATA%\PythonBot\data\corpus`.**

## 2. Provenance et revue humaine

Chaque vérité porte `truth_source`, `confirmed_at`, `confirmed_by = "user"`, `session_id`,
`hud_review` (confirmed / corrected / entered / unreadable) et `truth_history` (ancienne valeur
conservée). Seul `human_confirmed` compte ; une valeur modifiée dans l'annotation générale redevient
`unverified_import`. L'écran « Revue HUD PA/PM » montre la frame d'origine, les crops PA/PM (taille
réelle + zoom ×5 nearest-neighbor) et la valeur inscrite ; **la prédiction du lecteur n'est jamais
affichée**. Une valeur existante n'est modifiable qu'après « Corriger ».

### Rapport de confirmation

| Observations 3B-4R + exe (47 obs, 94 crops) | PA | PM |
| --- | ---: | ---: |
| Confirmées sans changement | 47 | 47 |
| Corrigées | 0 | 0 |
| Illisibles | 0 | 0 |
| Non traitées | 0 | 0 |

Nouvelles captures (14 obs, 28 crops) : 14/14 saisies par l'utilisateur, dont **1 correction** (§4).
Total : **61 observations, 122 vérités humaines, 0 non vérifiée, 0 conflit.**

## 3. Nouvelle collecte (lecture seule)

« Collecte HUD réelle » : capture de la fenêtre et découpe PA/PM par zones normalisées relatives au
client ; bouton « Capturer PA/PM maintenant » et capture continue (2 s) ; aucun lecteur exécuté,
aucune prédiction enregistrée, `actions_sent = false`, aucun clic/sort/déplacement/fin de tour.

| Session | Captures | Heure | Mode |
| --- | ---: | --- | --- |
| hud-collect-20260924-132041 | 1 | 13:20:46 | manuelle |
| hud-collect-20260924-132105 | 1 | 13:21:13 | manuelle |
| hud-collect-20260924-132117 | 12 | 13:21:20–13:21:53 | continue |

Contexte déclaré : combat (toutes). Crops PA 74×62, PM 62×63 (ROI relative, client du nouveau PC) ;
aucun glyphe tronqué (`CLIPPED_GLYPH` = 0). Les ROI exploration/placement restent couvertes par le
corpus 3B-4R (14 exploration, 5 placement) ; **aucune nouvelle capture en exploration/placement**.

## 4. Correction et contrôle de cohérence

Contrôle ajouté au banc : des crops identiques (écart moyen ≤ 0,5 niveau de gris) annotés avec des
valeurs différentes sont un **conflit de vérité** (le HUD est un rendu déterministe). Il a trouvé un
cas réel : `hud-collect-20260924-132117` frame 5 saisie PM 6, pixels identiques à trois PM 1.
L'utilisateur a corrigé (PM 6 → 1, historique conservé). Cette erreur avait créé en TRAIN deux
templates identiques « 1 » et « 6 » : le lecteur rendait UNKNOWN (score 1,000 contre 1,000), jamais
une valeur fausse.

Seuil justifié par mesure : même compteur ≤ 0,003 ; valeurs différentes ≥ 1,942 (PM 6/0, icône
identique). La déduplication de la collecte utilisait 2,0 et **aurait pu écarter un vrai changement**
PM 6 → 0 : seuil ramené à 0,5.

## 5. Groupes indépendants et split

Groupe = session + continuité temporelle + valeur + absence de changement :

- capture de collecte : horodatée, elle part seule puis est reliée à ses voisines (≤ 90 s, **y compris
  entre sessions** — rouvrir la fenêtre crée une session) qui partagent un compteur inchangé ;
- sessions anciennes sans horodatage fiable : session ou burst annoté, comme en 3B-4R.

Split reconstruit explicitement (`--hud-split`), registre `hud_split_registry.json` :
TEST gelé (3B-4R conservé, dont le burst PA = 7), le gel suit les **membres** d'un groupe (la
correction PM 6 → 1 a fusionné deux groupes ; la capture 4/1 gelée est restée en TEST), chiffres rares
stratifiés TRAIN puis TEST puis VALIDATION, reste hash 70/15/15. Le 7 TEST existant n'a pas été déplacé.

| Split | Groupes | Crops | PA (valeur:crops/groupes) | PM |
| --- | ---: | ---: | --- | --- |
| TRAIN | 8 | 74 | 4:1/1, 5:1/1, 7:1/1, 9:1/1, 10:2/1, 11:2/2, 13:1/1, 15:28/3 | 0:4/1, 1:1/1, 2:1/1, 3:2/1, 5:1/1, 6:28/4 |
| VALIDATION | 3 | 22 | 3:1/1, 5:1/1, 7:4/1, 9:1/1, 11:2/1, 13:1/1, 15:1/1 | 0:4/1, 1:2/1, 3:4/2, 6:1/1 |
| TEST | 6 | 26 | 0:1/1, 4:1/1, 5:1/1, 7:4/2, 9:1/1, 11:2/2, 12:1/1, 15:2/2 | 0:2/2, 1:2/1, 3:3/1, 5:2/1, 6:4/2 |

VALIDATION n'est plus limitée à 15/6 (contient 3, 5, 7, 9, 11, 13 et PM 0, 1, 3).

### Distribution globale

| Chiffre | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Crops | 13 | 47 | 2 | 12 | 2 | 37 | 33 | 9 | 0 | 3 |
| Groupes indépendants | 5 | 13 | 2 | 5 | 2 | 10 | 7 | 4 | 0 | 3 |

Nombres complets : PA = 7 : 9 crops / 4 groupes ; **PM = 1 : 5 crops / 3 groupes** ;
**PA = 1 seul : NOT OBSERVED** (le chiffre 1 n'apparaît que dans 10, 11, 12, 13, 15) ;
**PM = 7 : NOT OBSERVED**.

Crops pixel-identiques présents dans plusieurs splits : 9 images sur 54 (AP 7/9/11/13/15, MP 1/6).
C'est attendu : deux tours indépendants au même compteur donnent le même rendu. L'indépendance est
temporelle.

## 6. Benchmark 0.4.3 (vérités humaines uniquement)

`python -m combatbot.benchmark --hud-reader --hud-rapidocr --corpus-root "%LOCALAPPDATA%\PythonBot\data\corpus"`

`coverage = acceptées / lisibles` ; `accepted_accuracy = correctes / acceptées`.

| Mode | Portée | n | Exact | Accepted | Coverage | UNKNOWN |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Spécialisé | PA | 61 | 0,967 | **1,000** | 0,967 | 2 (3,3 %) |
| Spécialisé | PM | 61 | 1,000 | **1,000** | 1,000 | 0 |
| Spécialisé | GLOBAL | 122 | 0,984 | **1,000** | 0,984 | 2 (1,6 %) |
| Spécialisé | TRAIN | 74 | 1,000 | 1,000 | 1,000 | 0 |
| Spécialisé | VALIDATION | 22 | 1,000 | 1,000 | 1,000 | 0 |
| Spécialisé | TEST | 26 | 0,923 | **1,000** | 0,923 | 2 |
| RapidOCR seul | PA | 61 | 0,754 | 1,000 | 0,754 | 15 |
| RapidOCR seul | PM | 61 | 0,508 | **0,689** | 0,738 | 16 |
| RapidOCR seul | GLOBAL | 122 | 0,631 | 0,846 | 0,746 | 31 |
| RapidOCR seul | TEST | 26 | 0,692 | 1,000 | 0,692 | 8 |
| Combiné | GLOBAL | 122 | 0,984 | 1,000 | 0,984 | 2 |
| Combiné | TEST | 26 | 0,923 | 1,000 | 0,923 | 2 |

Les 2 UNKNOWN (TEST) : PA 12 (aucun template « 2 » en PA dans TRAIN : 3 contre 9, marge 0,031) et
PA 0 sur l'ancien crop 49 × 55 de `lot3b2` (0 contre 9, marge 0,03). Aucune lecture acceptée fausse.

### Matrices de confusion par chiffre (spécialisé)

GLOBAL : 0→0 ×12, 0→UNK ×1 ; 1→1 ×52, 1→UNK ×1 ; 2→2 ×1, 2→UNK ×1 ; 3→3 ×12 ; 4→4 ×2 ; 5→5 ×37 ;
6→6 ×33 ; 7→7 ×9 ; 9→9 ×3. Aucune case hors diagonale.

TEST : 0→0 ×2, 0→UNK ×1 ; 1→1 ×8, 1→UNK ×1 ; 2→UNK ×1 ; 3→3 ×3 ; 4→4 ×1 ; 5→5 ×5 ; 6→6 ×4 ; 7→7 ×4 ;
9→9 ×1.

PA et PM séparés : PA porte les deux UNKNOWN ; PM 61/61 sur la diagonale.

Explicitement (spécialisé, GLOBAL) : **1 → 7 : 0**, **7 → 1 : 0**, **1 → UNKNOWN : 1** (le « 1 » de
« 12 », à cause du « 2 »), **7 → UNKNOWN : 0**.

## 7. 1 / 7

| | Groupes TRAIN | VALIDATION | TEST | Lecture TEST |
| --- | ---: | ---: | ---: | --- |
| 1 | 6 | 3 | 4 | 8/9 (le 9e est le 1 de « 12 ») |
| 7 | 1 | 1 | 2 | **4/4**, marge 0,305 face à 3 |

Critère §16 : TEST contient 1 et 7 de groupes indépendants ✔ ; le template 7 vient de TRAIN ✔ ;
0 confusion 1 ↔ 7 en TEST ✔ ; 0 ambiguïté `AMBIGUOUS_1_7` ✔ ; couverture TEST 1 = 0,889, 7 = 1,000 ✔
→ **VALIDATED**. Aucun seuil n'a été réglé sur TEST ; `distinguish_one_seven` reste diagnostic.

Sur les 4 crops 7 de TEST, 3 diffèrent au pixel du 7 de TRAIN : les 2 du burst gelé 3B-4R viennent de
l'ancien PC (72 × 63) alors que le template vient du nouveau (74 × 62). La lecture se généralise donc
entre deux layouts. Limite : TRAIN ne contient qu'**un** crop de 7. Le banc applique désormais §16 :
seule une vraie ambiguïté 1/7 donne IMPROVED (une UNKNOWN due à un autre chiffre ne déclasse pas).

## 8. Chiffres 4 et 8, PA = 1, PM = 1, PM = 7

- **4** : 2 groupes (TRAIN 1, TEST 1), lu 4→4 en TEST → VALIDATED, support minimal.
- **8** : jamais observé → NOT OBSERVED, aucun template artificiel.
- **2** : OBSERVED_INSUFFICIENT (PM 2 en TRAIN seulement ; PA 12 en TEST reste UNKNOWN).
- **PA = 1 seul** : NOT OBSERVED. **PM = 1** : 3 groupes, 100 % en TEST. **PM = 7** : NOT OBSERVED.

## 9. RapidOCR

- Spécialisé seul = combiné sur tout le corpus : RapidOCR n'apporte aucune lecture correcte de plus.
- Seul, il lit **PM 6 comme 0 (14 fois)** et ne lit **aucun 7** (9/9 UNKNOWN).
- VALIDATION enrichie : couverture 1,000 en combiné, aucun désaccord → aucun problème démontré, seuils
  inchangés (§20). RapidOCR reste un dernier recours sûr mais sans gain → PARTIAL.

## 10. Performance (nouveau PC)

| Mesure | PA | PM |
| --- | ---: | ---: |
| Spécialisé | 6,6 ms | 3,9 ms |
| Combiné (skip 0,74) | 6,9 ms | 4,1 ms |
| RapidOCR | 1 002 ms | 884 ms |

## 11. Transitions temporelles réelles

Rejeu de `NumberTemporalTracker` sur 114 lectures ordonnées (comparaison à l'image annotée uniquement,
aucun coût d'action supposé) : **105 OK, 9 UNKNOWN, 0 valeur fausse ou périmée**.

Transitions observées : PA 15→11 (×3), 11→7 (×3), 7→4 (×2), 4→15, 7→15 (×2), 15→12, 15→9, 9→13 (×2),
13→11, 11→9, 9→5, 5→3 (×2), 3→10 ; PM 6→0 (×3), 6→1, 3→1, 1→6 (×2), 6→3, 6→5, 5→0, 5→2, 3→0, 0→3, 0→6.
UNKNOWN : 7 `TEMPORAL_CONFLICT` (lecture brute juste, confiance < 0,88, confirmée à la frame suivante)
et 2 `LOW_MARGIN` (PA 12, PA 0 ancien layout).

## 12. Non-régression 3B-3

`python -m combatbot.benchmark --grid-validation` : exploration faux combat **0/14**, placement/combat
**12/12**, visibilité **26/26**, alignement **12/12 ALIGNED** (résidu médian 1,17 px), cohérence de map
identique (10/10 CONSISTENT ; simulée 7 SUSPECT, 1 STALE_LIKELY, 2 CONSISTENT).

## 13. Code, tests, build

Outillage uniquement (le lecteur est inchangé → version 0.4.3) :

- provenance des vérités (`models.py`, `repository.confirm_hud_truth`, `hud_review_summary`) ;
- écran « Revue HUD PA/PM » et « Collecte HUD réelle » (`ui/hud_review_dialog.py`, `corpus/hud_collection.py`) ;
- split explicite gelé, stratifié, projeté par membres (`hud_dataset.py`, `--hud-split`) ;
- continuité temporelle entre sessions de collecte, conflits de vérité, déduplication à 0,5 ;
- verdict 1/7 aligné sur §16, distribution par split, statut par chiffre, rejeu des transitions ;
- `scripts/merge_corpus.py`.

- pytest complet : **301 passés** (283 + 18 nouveaux, dont provenance, revue sans prédiction, collecte
  et doublons, TEST gelé après fusion, continuité entre sessions, conflit de vérité, 1/7 §16).
- `compileall` : PASS ; `git diff --check` : PASS.
- Build ONEDIR : PASS, 338,9 Mio, construit dans un dossier séparé (l'exe de `dist\` était ouvert).
- Smoke packagé (données isolées, `PYTHONBOT_SMOKE_SKIP_DOFUS=1`) : PASS, 8 pages, lecteur HUD
  `GLYPH_TEMPLATE`, grille ALIGNED, `action_executed: false`, modules revue/collecte inclus.

**À faire côté utilisateur** : fermer `PythonBot.exe` puis relancer `build_exe.ps1` ; le `dist\`
actuel date d'avant les correctifs de déduplication et de conflit.

Le benchmark a écrit les templates TRAIN humains dans `%LOCALAPPDATA%\PythonBot\data\hud_templates` :
l'observateur de l'exe les utilisera pour la lecture en direct.

## 14. Condition pour 3B-5 (§30)

Vérités réellement confirmées ✔ ; aucune erreur silencieuse connue ✔ ; 1/7 testé avec plusieurs
groupes indépendants ✔ ; UNKNOWN sur classe inconnue ✔ (PA 12, PA 0) ; benchmark reproductible ✔.
Limites documentées : 8, PA = 1 seul et PM = 7 non observés ; 4 et 7 en TRAIN reposent sur un seul
crop chacun ; nouvelles captures uniquement en combat.

Arrêt après ce rapport. LOT 3B-5 non commencé.
