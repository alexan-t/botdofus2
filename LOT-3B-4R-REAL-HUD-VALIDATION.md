# LOT 3B-4R — Recette réelle PA/PM : annotation et validation 1/7

Date : 24 septembre 2026
Version : **0.4.3** (correction réelle du lecteur, voir §6)
Branche : `lot-3b-4r-hud-validation`, depuis `c53f14ed0feb20c8b3bab03bd00668c03bd088ec`
Client : `C:\Users\Alpha5\AppData\Local\Alea\Client`

Le lecteur spécialisé n'a pas été réécrit. Aucun module de détection joueur/ennemis, d'EntityTracker,
de stratégie, d'action, de `GridProjector` ou de `GridAlignmentValidator` n'a été modifié.

## Statuts

| Élément | Statut |
| --- | --- |
| REAL HUD CORPUS | **PARTIAL** |
| PA READER | **PARTIAL** |
| PM READER | **PARTIAL** |
| 1 VS 7 | **NOT VALIDATED** |
| RAPIDOCR FALLBACK | **PARTIAL** |
| GRID 3B-3 REGRESSION | **NONE** |

3B-4R n'est donc **pas PASS** : 1/7 ne peut pas être testé honnêtement avec le corpus actuel (§9).

## 1. Corpus et vérités

| Mesure | Valeur |
| --- | ---: |
| Observations | 46 (5 sessions) |
| Crops | 92 (46 PA, 46 PM) |
| Vérités PA/PM | 92/92 |
| Crops classés `VALID` | 92/92 (crops actifs, voir §4) |
| Groupes temporels indépendants | **13** |

Les 56 crops d'origine (28 observations `grid-real` + `lot3b2-real`) ont été complétés par 18
observations issues de trois sessions de collecte en lecture seule (`session_0abb7ab8c13e`,
`session_62a2182c0d93`, `session_771240ea1dc7`, 11 h 26 – 11 h 53).

**Provenance des vérités.** Les annotations portent le commentaire « Vérité PA/PM confirmée par
l'utilisateur le 2026-09-24 ». Elles ont été écrites par lots scriptés (11 h 35, 11 h 47, 11 h 56)
avant cette session. Je n'ai pas pu retracer la confirmation elle-même. Un contrôle visuel des 92 crops
(original + zoom nearest-neighbor) n'a trouvé **aucun désaccord** entre pixels et vérité. Ce contrôle ne
remplace pas une vérité humaine : les valeurs doivent être reconfirmées par l'utilisateur si un doute existe.
L'écran d'annotation n'initialise jamais la vérité avec une prédiction (compteur à « Non annoté »).

### Distribution réelle

| Chiffre | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Occurrences | 9 | 39 | 2 | 10 | **0** | 34 | 29 | **2** | **0** | 3 |

Nombres : PA `0`×1, `3`×1, `5`×3, `7`×2, `9`×3, `10`×2, `11`×3, `12`×1, `13`×2, `15`×28 ;
PM `0`×6, `2`×1, `3`×7, `5`×3, `6`×29.

Jamais observés : chiffres **4** et **8**, **PA = 1** seul, **PM = 1**, **PM = 7**. Aucune classe n'a
été fabriquée. Le seul PA = 7 provient d'un unique burst (session `0abb`, frames 48 et 52).

## 2. Groupes et split

Règle de groupe (nouvelle, `_merge_continuous_counters`) : burst annoté, puis fusion des captures
consécutives d'une même session qui partagent un compteur **inchangé**. Motif mesuré : en 3B-4, les
frames 47 (PA 11 / PM 3, TRAIN) et 48 (PA 7 / PM 3, TEST) étaient séparées de 1 s et montraient le
même PM immobile de part et d'autre de TRAIN/TEST. De même, les 25 captures `grid-real` à 15/6 sont un
seul compteur au repos, pas 25 preuves.

Les usages explicites du manifeste local avaient été posés par script et contredisaient ces groupes ;
ils ont été remis à `diagnostic` (sauvegarde : `data/corpus/manifests/corpus_manifest.pre-3b4r-usage.json`).
Le split vient désormais uniquement du hash des groupes (70/15/15, VALIDATION et TEST non vides).

| Split | Groupes | Crops | Contenu |
| --- | ---: | ---: | --- |
| TRAIN | 9 | 32 | PA 3,5,9,10,11,12,13,15 ; PM 0,2,3,5,6 |
| VALIDATION | 2 | 52 | PA 15 ; PM 6 uniquement |
| TEST | 2 | 8 | PA 0,7,11 ; PM 0,3 |

Templates : TRAIN seul, glyphes identiques dédupliqués. TEST n'a servi ni aux templates ni aux seuils.
VALIDATION ne contient que 15/6 : c'est une validation faible, signalée comme telle.

## 3. Problème PM (14 crops rejetés en 3B-4)

Examen individuel des 14 crops PM rejetés : ce sont **les 14 frames d'exploration** de `grid-real`.

| Cause candidate | Verdict |
| --- | --- |
| ROI PM trop étroite / décalée | **Oui — cause unique** |
| Chiffre touchant le bord | Oui, conséquence de la ROI |
| Icône PM | Non |
| Threshold | Non (Otsu et luminosité échouent aussi, le glyphe est tronqué) |
| Composante connexe | Non (le « 6 » coupé se scinde en 2 composantes `TOUCH_LEFT`) |
| Absence réelle de chiffre | Non (le « 6 » est visible sur la frame d'origine) |

En exploration, le bloc PA/PM est dessiné plus à gauche qu'en combat. L'ancienne ROI (41 × 48 px PM,
49 × 55 px PA) était calibrée en combat : les crops d'exploration sont **CUT_LEFT**. Les 14 crops PA
correspondants l'étaient aussi : le « 1 » de « 15 » était réduit à 2 px de large.

**Erreur silencieuse trouvée** : 0.4.2 écartait ce reste de « 1 » comme composante de bord et lisait
**15 → 5** avec ACCEPTED (14 erreurs, accepted accuracy PA 0,674). Le « PA 28/28 segmentables » de
3B-4 masquait ces lectures partielles.

## 4. Correction ROI

La ROI est une zone **normalisée** du profil de calibration utilisateur ; aucun code ne contient de
coordonnée PA/PM. Elle a été recalibrée (PA 72 × 63 px, PM 66 × 65 px à 2560 × 1377), ce qui reste
relatif au client et compatible avec l'ancien layout (chaque observation conserve sa calibration).

Les 26 crops `grid-real` ont été **recalculés depuis `frame.png`** avec la nouvelle zone
(`hud_corrected/`), sans agrandir un crop déjà coupé. Les anciens crops restent dans `hud/`.

### Segmentation avant / après (mêmes 46 observations)

| Lecteur / crops | PA segmentés correctement | PM segmentés | Erreurs acceptées |
| --- | ---: | ---: | ---: |
| 0.4.2, anciens crops | 32/46 | 32/46 | **14** (15 → 5) |
| 0.4.2, crops recalculés | 45/46 | 46/46 | 0 |
| 0.4.3, anciens crops | 32/46 (+14 `CLIPPED_GLYPH`) | 32/46 (+14 `CLIPPED_GLYPH`) | **0** |
| 0.4.3, crops recalculés | **46/46** | **46/46** | 0 |

Le cas 45/46 de 0.4.2 : la sélection « meilleure qualité moyenne » choisissait le masque de luminosité,
qui fusionnait « 15 » en un glyphe. 0.4.3 priorise le masque blanc HUD, puis Otsu et luminosité en repli.

## 5. Benchmark 0.4.3 (crops actifs, `python -m combatbot.benchmark --hud-reader --hud-rapidocr`)

`coverage = acceptées / lisibles`, `accepted_accuracy = correctes / acceptées`.

| Mode | Portée | Exact acc. | Accepted acc. | Coverage | UNKNOWN |
| --- | --- | ---: | ---: | ---: | ---: |
| Spécialisé | PA | 0,935 | **1,000** | 0,935 | 3 |
| Spécialisé | PM | 1,000 | **1,000** | 1,000 | 0 |
| Spécialisé | GLOBAL | 0,967 | **1,000** | 0,967 | 3 |
| Spécialisé | VALIDATION | 1,000 | 1,000 | 1,000 | 0 |
| Spécialisé | TEST | 0,625 | 1,000 | 0,625 | 3 |
| RapidOCR seul | PA | 0,891 | 1,000 | 0,891 | 5 |
| RapidOCR seul | PM | 0,457 | **0,600** | 0,761 | 11 |
| RapidOCR seul | GLOBAL | 0,674 | 0,816 | 0,826 | 16 |
| Combiné | GLOBAL | 0,967 | 1,000 | 0,967 | 3 |

Les 3 UNKNOWN : les deux PA = 7 (TEST, aucun template 7 en TRAIN) et un PA = 0 de l'ancien layout
`lot3b2` (0 contre 9, marge 0,03).

### Matrice de confusion par chiffre (spécialisé)

PA :

| Vérité → | 0 | 1 | 2 | 3 | 5 | 7 | 9 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Prédit | 0×2, UNK×1 | 1×39 | 2×1 | 3×3 | 5×31 | **UNK×2** | 9×3 |

PM :

| Vérité → | 0 | 2 | 3 | 5 | 6 |
| --- | --- | --- | --- | --- | --- |
| Prédit | 0×6 | 2×1 | 3×7 | 5×3 | 6×29 |

GLOBAL : aucune erreur hors diagonale. Explicitement : **1 → 7 : 0**, **7 → 1 : 0**,
**1 → UNKNOWN : 0**, **7 → UNKNOWN : 2**. Chiffres 4 et 8 : aucun exemple.

RapidOCR seul, PM : **6 → 0 ×14**, 6 → UNKNOWN ×11 ; PA : 1 → UNKNOWN ×6.

## 6. Corrections du lecteur (baseline → après)

Toutes sont justifiées par une mesure réelle ; aucune n'a été calibrée sur TEST.

1. **Glyphe tronqué → `UNKNOWN` (`CLIPPED_GLYPH`)** : un trait blanc de hauteur chiffre (≥ 30 % du crop)
   qui touche le bord gauche ou droit bloque la lecture, sans fallback RapidOCR. Anciens crops : 14 erreurs
   silencieuses → 0 ; crops recalculés : aucun changement (46/46).
2. **Priorité au masque blanc HUD** (déjà dans l'arbre de travail) : 45/46 → 46/46 PA.
3. **`distinguish_one_seven` retiré de la décision** : sur les 39 « 1 » réels, la largeur du sommet vaut
   1,0 et l'heuristique **préfère 7 pour 39/39**. Sur un 7 de forme réelle et de marge 0,31, elle rendait
   `None` et forçait `AMBIGUOUS_1_7`. Ses mesures restent exportées ; seule la marge des templates décide.
   Aucun changement mesuré sur le corpus (elle ne rendait jamais `None` sur des données réelles).
4. **`TEMPORAL_HOLD` refusé quand la frame illisible montre un changement** (nombre de glyphes ou
   proposition différente de la valeur stable). Voir §10.

## 7. Seuils

| Seuil | Valeur | Justification (VALIDATION) |
| --- | ---: | --- |
| `min_template_score` | 0,70 (inchangé) | Min. observé sur les lectures acceptées : 1,000. Aucun cas limite → aucune raison de bouger. |
| `min_margin` | 0,055 (inchangé) | Min. observé : 0,097. Idem. |
| `one_seven_margin` | = `min_margin` (0,055) | Non calibrable : aucun 7 en TRAIN ni en VALIDATION. |
| `skip_rapidocr_confidence` | **0,74** (0.4.2 : 0,88) | Min. des lectures correctes : 0,823. À 0,88, RapidOCR est appelé 42 fois, lit 6 → 0 et fait passer 14 lectures correctes en `OCR_DISAGREEMENT` (coverage VALIDATION PM 1,000 → 0,462). À 0,74 : 3 appels, coverage 1,000, 0 erreur. |
| `fallback_confidence` | 0,94 (inchangé) | Le fallback n'a jamais été sollicité utilement sur VALIDATION. |
| `NumberTemporalTracker.high_confidence` | 0,88 (inchangé) | Choix prudent : 6 transitions correctes lues entre 0,82 et 0,88 attendent une frame de confirmation (UNKNOWN, jamais faux). |

## 8. RapidOCR

- **Spécialisé seul** : accepted accuracy 1,000, coverage 0,967.
- **RapidOCR seul** : accepted accuracy 0,816, coverage 0,826 ; erreur systématique 6 → 0 sur les PM
  recalculés.
- **Combiné** : identique au spécialisé. Le fallback n'ajoute **aucune** lecture correcte sur ce corpus ;
  le désaccord → UNKNOWN empêche ses erreurs de passer.

Conclusion : le fallback est sûr mais n'améliore pas le système mesuré (**PARTIAL**). Il ne doit rester
qu'un dernier recours.

## 9. Analyse 1 / 7

Export : `data/benchmarks/one-seven.json` et `one-seven.png` (masque normalisé, projection horizontale,
occupation de la bande haute, largeur du sommet, meilleur/second score, marge, `distinguish_one_seven`,
prédiction finale).

| | « 1 » annotés | « 7 » annotés |
| --- | ---: | ---: |
| Glyphes | 39 | 2 |
| Groupes indépendants | 9 (TRAIN 6, VALIDATION 2, TEST 1) | **1** (TEST) |
| Lus correctement | 39/39 (marge min. 0,371 face à 3) | 0/2 (UNKNOWN) |
| Confusions 1 ↔ 7 | 0 | 0 |

Les deux 7 : meilleur candidat « 3 » à 0,695, marge 0,015 → `LOW_MARGIN`. Aucun 7 n'est lu comme 1,
faute de template 7. La projection horizontale est très nette (7 : 5 lignes à 15 px puis hampe à 5 px ;
1 : hampe constante avec petit drapeau), mais **un seul burst de 7** ne permet ni template TRAIN ni test
indépendant.

Minimum §13 non atteint : moins de deux groupes indépendants contenant 7 ; aucun 7 en TRAIN.
→ **1 VS 7 : NOT VALIDATED**. Le banc applique désormais ce critère automatiquement (`one_seven`).

## 10. Transitions temporelles réelles

Rejeu de `NumberTemporalTracker` sur les séquences réelles ordonnées par frame (36 lectures) :

- `0abb` PA : 15 → 11 → 7 → 7 → 15 → 9 → 13 ; PM : 6 → 3 → 3 → 3 → 5 → 5 → 2.
- `62a2` PA : 5 → 9 → 13 → 11 → 11 → 9 → 5 → 3 → 10 → 10 ; PM : 6 → 5 → 3 → 3 → 0 ×4 → 3 → 3.

| Tracker | OK | UNKNOWN | Valeur périmée / fausse |
| --- | ---: | ---: | ---: |
| 0.4.2 | 29 | 6 | **1** |
| 0.4.3 | 29 | 7 | **0** |

Le cas corrigé : 11 → 7 illisible. 0.4.2 exposait `ap = 11` (`TEMPORAL_HOLD`, confiance 0,70) alors que
l'écran montrait 7. Vérifié : un changement très confiant (13 → 11 à 0,97) est accepté immédiatement ; une
lecture faible (6 → 3 à 0,82) ne remplace pas la valeur stable avant confirmation ; l'ancienne valeur
expire ; UNKNOWN reste possible. Aucun coût de sort ou de mouvement n'est supposé ; aucune règle métier
ne corrige l'image.

## 11. HUD en exploration

Les 14 frames d'exploration montrent les mêmes compteurs (15/6), plus à gauche. Avec l'ancienne ROI,
0.4.3 renvoie `UNKNOWN / CLIPPED_GLYPH` (28/28) ; avec la ROI recalibrée, il lit 15 et 6 (28/28 corrects).
La détection de combat 3B-3 n'utilise pas cette différence et n'est pas modifiée.

## 12. Performance (ce PC)

| Mesure | PA | PM |
| --- | ---: | ---: |
| Spécialisé, moyenne | 6,6 ms | 3,8 ms |
| Combiné (skip 0,74), moyenne | 7,1 ms | 4,9 ms |
| RapidOCR, moyenne | 980 ms | 873 ms |

L'écart avec « ~1 ms » de 3B-4 vient des templates : la bibliothèque était vide en 3B-4 ; elle contient
maintenant 8 templates PA et 7 PM, comparés avec une tolérance de ±1 px. Le spécialisé reste le chemin
normal et RapidOCR n'est appelé que 3 fois sur 92.

## 13. Non-régression 3B-3

`python -m combatbot.benchmark --grid-validation` (26 frames `lot3b2r`) :

- exploration faux combat : **0/14** ;
- placement / combat : **12/12** ;
- visibilité de grille : **26/26** ;
- alignement : **12/12 ALIGNED** (résidu médian 1,17 px) ;
- cohérence de map : correcte 10/10 CONSISTENT ; simulée 7 SUSPECT, 1 STALE_LIKELY, 2 CONSISTENT — identique à 3B-3.

## 14. Tests, build, commit

Tests ajoutés : `test_real_pm_crop_not_clipped`, `test_pm_roi_keeps_full_glyph`,
`test_rapidocr_not_called_on_clipped_crop`, `test_one_real_like_glyph`, `test_seven_real_like_glyph`,
`test_one_seven_heuristic_is_not_trusted_on_narrow_real_one`, `test_real_transition_high_confidence`,
`test_low_confidence_transition_stays_unknown`, `test_unreadable_changed_counter_is_not_held`,
`test_unreadable_glitch_without_glyph_is_still_held_once`, `test_unchanged_counter_merges_consecutive_bursts`,
`test_unchanged_counter_cannot_cross_train_and_test`, `test_test_split_never_used_for_templates`,
`test_unknown_truth_excluded_from_accuracy`, `test_coverage_and_accepted_accuracy`,
`test_one_seven_not_validated_without_independent_train_and_test_sevens` (+ tests de l'arbre de travail
initial : qualité de crop, burst, split non vide, déduplication, seuil de validation).

- `git diff --check` : PASS.
- pytest ciblé (HUD + corpus) : **59 passés**.
- pytest complet : **283 passés**.
- `compileall` : PASS.
- PyInstaller ONEDIR : PASS, `dist/PythonBot/PythonBot.exe`, **338,9 Mio**.
- Smoke packagé (`PYTHONBOT_SMOKE_SKIP_DOFUS=1`) : PASS — lecteur HUD initialisé, fixture 7 lue par
  `GLYPH_TEMPLATE`, RapidOCR chargé, grille 560 cellules, `action_executed: false`.
- Commit : `fix: validate and harden ap mp reader` (hash dans le bilan de tâche).

Les crops, captures et manifestes réels restent dans `data/` et ne sont pas commités.

## 15. Pour passer à PASS

Une session de collecte en lecture seule, l'utilisateur jouant normalement, doit apporter au minimum :

- **PA = 7** dans au moins deux combats différents supplémentaires (pour avoir des 7 en TRAIN **et** en TEST) ;
- **PA = 1** seul, **PM = 1** et, si rencontré, **PM = 7** (sinon le signaler, ne pas le fabriquer) ;
- des valeurs contenant **4** et **8** ;
- des valeurs PA/PM variées en VALIDATION, qui ne contient aujourd'hui que 15/6.

Puis : annotation humaine, `python -m combatbot.benchmark --hud-reader --hud-rapidocr`, et lecture du bloc
`one_seven`. Arrêt après ce rapport ; LOT 3B-5 non commencé.
