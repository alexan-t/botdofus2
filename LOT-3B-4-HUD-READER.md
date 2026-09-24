# LOT 3B-4 — Lecteur PA/PM spécialisé

Date : 24 septembre 2026
Version : **0.4.2**
Branche : `lot-3b-4-hud-reader`
Source : `f9c577b66842c152c5382bde8d9effeb1513ce1a`
Client utilisé pour la non-régression GameData : `C:\Users\Alpha5\AppData\Local\Alea\Client`

## 1. Audit du lecteur précédent

- Les ROI `ap` et `mp` étaient des rectangles normalisés du client, convertis par `LayoutTransform`,
  puis copiés depuis la capture cliente.
- Les 28 crops AP réels mesurent **49 × 55 px** et les 28 crops PM **41 × 48 px**.
- `combat_ocr.py` agrandissait le crop, puis produisait quatre variantes : contraste normalisé,
  Otsu, Otsu inversé et seuil adaptatif.
- Chaque variante appelait le même moteur RapidOCR mis en cache. Le vote privilégiait le nombre de
  variantes concordantes puis leur confiance moyenne. Ces votes n'étaient donc pas indépendants.
- L'observateur conservait le dernier couple PA/PM pendant 550 ms. Il n'existait aucun consensus
  temporel spécialisé pour les changements de compteurs.

## 2. Corpus disponible

L'inventaire `data/corpus/manifests/hud_manifest.json` contient **56 crops** : 28 AP et 28 PM,
issus de 28 observations et de deux sessions. Les métadonnées conservées sont l'observation, la
session, la map déclarée, le type de compteur, le chemin relatif, la taille client, la signature de
layout et le split.

Les 26 annotations présentes dans la session réelle ne contiennent actuellement **aucune vérité
humaine PA ou PM**. Les anciennes prédictions ne sont jamais converties en vérité. En conséquence :

| Chiffre | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Exemples humains | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

La distribution des nombres est vide. TRAIN, VALIDATION et TEST ont chacun 0 exemple labellisé.
Aucune matrice complète 0–9 ni mesure statistique n'est annoncée.

Sans attribuer de vérité, le segmentateur trouve un glyphe sur 16 crops AP et deux glyphes sur 12,
soit 28/28 segmentables. Il trouve un glyphe sur 14 crops PM ; les 14 autres sont rejetés, notamment
quand le chiffre touche le bord gauche du crop. Ces rejets restent `UNKNOWN` au lieu d'être forcés.

L'écran **Corpus / Annotation** affiche désormais chaque crop PA/PM original, son agrandissement
nearest-neighbor, le champ de vérité et les commandes **Valider**, **Inconnu** et **Suivant**.

## 3. Dataset et prévention des fuites

- Les vérités viennent seulement de `Annotation.ap_truth` et `Annotation.mp_truth`.
- Une session entière appartient à un seul split.
- Un usage explicite `train`, `validation` ou `test` s'applique à toute la session.
- Sans usage explicite, un hash SHA-256 stable de la session applique une répartition 70/15/15.
- Une session répartie explicitement entre plusieurs splits est rejetée.
- Seul TRAIN construit les templates. VALIDATION et TEST ne les modifient jamais.

## 4. Architecture

`combatbot/vision/hud_reader.py` contient :

- `NumberReadResult`, `GlyphRead` et leurs sérialisations explicites ;
- `segment_glyphs()` et la normalisation canonique 24 × 36 ;
- `GlyphTemplateLibrary`, stockée localement sous `data/hud_templates/` quand des exemples TRAIN
  humains sont disponibles ;
- `HUDReader`, commun aux PA et PM ;
- `NumberTemporalTracker` ;
- `distinguish_one_seven()`.

`combatbot/corpus/hud_dataset.py` construit l'inventaire, les templates TRAIN et le benchmark.
`RealCombatObserver` expose toujours `ap`, `mp`, `confidence_ap` et `confidence_mp`, avec en plus
`ap_read` et `mp_read` : source, scores, marge, raison, glyphes, candidats et temps séparés.

## 5. Segmentation et templates

Trois variantes limitées sont évaluées : masque blanc à faible saturation, Otsu et seuil de
luminosité. Les bordures sont supprimées, puis les composantes trop petites, trop courtes, trop
larges ou touchant le bord sont rejetées. Un ou deux glyphes sont acceptés et triés horizontalement.

Chaque glyphe est recadré, redimensionné en conservant son ratio, puis centré avec padding. La
similarité combine Dice binaire (72 %) et accord pixel à pixel (28 %), avec une tolérance de
translation d'un pixel. Les templates enregistrent leur observation source. Aucun asset du client
n'est utilisé comme vérité.

## 6. Décision, 1/7 et UNKNOWN

Seuils initiaux configurables, validés seulement par tests synthétiques :

- score minimal : **0,70** ;
- marge minimale : **0,055** ;
- RapidOCR évité à partir d'une confiance spécialisée de **0,88** ;
- fallback RapidOCR seul accepté à partir de **0,94**.

La confiance spécialisée combine score absolu (45 %), marge ramenée à 0,18 (30 %) et qualité de
segmentation (25 %). La décision exige simultanément score et marge.

Pour 1/7, `distinguish_one_seven()` expose la largeur occupée dans la bande haute et la position de
la hampe basse. Si 1 et 7 sont les deux meilleurs candidats et que la marge ou la forme reste
ambiguë, le résultat est `AMBIGUOUS_1_7`. Aucun remplacement « 1 devient 7 » n'existe.

Une valeur hors domaine est rejetée sans correction. Les crops vides, masqués, coupés, les échecs
de segmentation, faibles marges, désaccords OCR et transitions faibles produisent `UNKNOWN`.

## 7. RapidOCR secondaire et cache

- Une preuve spécialisée ≥ 0,88 n'appelle pas RapidOCR.
- Un accord peut ajouter au plus un faible bonus et produire `CONSENSUS`.
- Un désaccord produit `OCR_DISAGREEMENT` et `UNKNOWN`.
- Une ambiguïté 1/7 n'est jamais levée par RapidOCR seul.
- Sans segmentation ou template, RapidOCR reste un fallback au seuil strict de 0,94.
- L'instance ONNX reste celle du cache existant. Une ROI identique réutilise son résultat OCR ; une
  ROI modifiée est limitée dans le temps.

`read_small_number()` reste disponible pour compatibilité et comme fallback général.

## 8. Consensus temporel

- Une lecture forte (≥ 0,88) peut remplacer immédiatement l'ancienne valeur, afin de suivre une
  dépense réelle de PA ou PM.
- Une transition moins sûre doit être observée deux fois.
- Une frame inconnue peut conserver une fois la valeur stable avec confiance réduite et raison
  `TEMPORAL_HOLD`.
- Au deuxième échec, l'ancienne valeur expire et le résultat devient `UNKNOWN`.

`safe_for_decision` reste faux dès que PA ou PM est inconnu. Aucune décision ni action n'a été ajoutée.

## 9. Benchmark HUD réel

Commande :

```powershell
python -m combatbot.benchmark --hud-reader --hud-rapidocr
```

Résultat : **INSUFFICIENT**, 0 vérité humaine sur 56 crops.

| Mesure | PA | PM |
| --- | ---: | ---: |
| Lecteur spécialisé moyen | 1,055 ms | 0,639 ms |
| RapidOCR moyen | 1 156,3 ms | 1 206,2 ms |

Accuracy, accepted accuracy, coverage, UNKNOWN rate, précision/rappel 1 et 7, confusion 1→7 et
7→1 restent `N/A` ou sans échantillon. Les seuils n'ont pas été ajustés sur TEST.

## 10. Tests synthétiques

Les tests couvrent notamment : crop vide, segmentation simple et double, 1, 7, ambiguïté 1/7,
faible marge, hors domaine, accord/désaccord RapidOCR, absence d'appel OCR sur preuve forte,
stabilité temporelle, changement fort immédiat, expiration, lecteur commun PA/PM, sérialisation et
compatibilité de `read_small_number()`.

- Tests HUD ciblés et intégration observateur : **36 passés**.
- Suite complète : **260 passés**.
- `compileall` : **PASS**.

## 11. Non-régression LOT 3B-3

Sur les 26 frames réelles `lot3b2r` avec le client situé dans
`C:\Users\Alpha5\AppData\Local\Alea\Client` :

- exploration : **0 faux combat / 14** ;
- placement et combat : **12/12 détectés** ;
- visibilité de grille : **26/26 correcte** ;
- alignement : **12/12 ALIGNED** ;
- résultats de dérive et cohérence de map inchangés.

## 12. Build et smoke test

- PyInstaller ONEDIR : **PASS**.
- Exécutable : `dist/PythonBot/PythonBot.exe`.
- Package : **333,2 Mio** (349 409 799 octets).
- Smoke packagé : **PASS**.
- Interface et 8 pages : chargées.
- Lecteur spécialisé : initialisé, fixture `7` lue par `GLYPH_TEMPLATE`.
- Fallback RapidOCR : disponible.
- Grille GameData et validation 3B-3 : PASS.
- Action exécutée : **false**.

Warnings de build : Tkinter absent et exclu ; deux `SyntaxWarning` dans les dépendances RapidOCR et
PyAutoGUI. Ils n'ont pas empêché le smoke test.

Le smoke a volontairement utilisé `PYTHONBOT_SMOKE_SKIP_DOFUS=1`. Aucune capture live ni lecture PA/PM
dans le client ouvert n'est annoncée.

## 13. Commit et limites

Commit demandé : `feat: add specialized ap mp reader`. Le hash final est fourni dans le bilan de la
tâche, car un fichier ne peut pas contenir le hash du commit qui le contient sans modifier ce hash.

Limites principales : aucune vérité PA/PM humaine dans le corpus actuel, un seul layout réel, aucune
validation réelle de 1 contre 7, templates runtime encore vides tant que l'annotation n'est pas faite,
et aucune lecture live validée. Le lecteur retombe donc sur RapidOCR strict quand aucun template n'est
disponible.

## Statuts finaux

- **IMPLEMENTATION: PASS**
- **SYNTHETIC: PASS**
- **REAL HUD CORPUS: INSUFFICIENT**
- **1 VS 7: NOT VALIDATED**
- **RAPIDOCR FALLBACK: PASS**
- **GRID 3B-3 REGRESSION: NONE**

Arrêt après LOT 3B-4. LOT 3B-5 non commencé.
