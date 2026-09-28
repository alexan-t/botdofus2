# LOT 3B-6 — HUD local et états sémantiques du combat (OBSERVATION SEULE)

**ACTIONS : NONE.** Aucun clic, aucun « Prêt », aucune fin de tour, aucun sort.

## 3B-6A — Revue HUD PA/PM et gabarits locaux (validé par l'utilisateur)
- Revue assistée : suggestions sur TRAIN/VALIDATION, TEST aveugle, « ✓ Tout est correct ».
- Gabarits appris sur les seules vérités humaines TRAIN ; seuil RapidOCR 0,94 inchangé.
- Corrections faites en cours de route : splits de collecte HUD figés avant apprentissage
  (dérive TRAIN → TEST), sauvegarde en double, PM 1 mal étiqueté 0 (suggestion acceptée trop vite).
- Lecture gabarits seuls : TRAIN 124/124, VALIDATION 36/36, 0 erreur.

## 3B-6B — Phase de combat et tour

### Outil d'annotation (« Annoter phase et tour… »)
Chaque frame capturée garde le bouton fin de tour, la barre de sorts et le client entier. Phases :
hors combat, placement, combat, résultats, inconnu ; tour (en combat) : moi, un autre, inconnu.
Un clic enregistre et avance ; Entrée reprend la réponse HUMAINE précédente ; aucune prédiction
affichée (TEST aveugle).

### Corpus TRAIN annoté (27–28/09)
5 combats, 117 frames : hors combat 13, placement 12, mon tour 63, tour d'un autre 27, résultats 2.
Contrôle visuel : les étiquettes suivent la couleur du bouton (jaune vif = à moi, foncé = un autre).
Cas particuliers relevés : la 1re frame après « Prêt » (bouton encore foncé, fumée de début de
combat) est étiquetée « mon tour » dans 2 combats ; une frame étiquetée « placement »
(`session_cdeafdbd608b` frame 14) montre déjà « TERMINER LE TOUR » → probable erreur d'étiquette.

### Détecteur (`combatbot/vision/combat_state_detector.py`)
- Phase : k plus proches voisins (k = 3) sur des exemples TRAIN humains — couleurs du bouton,
  profil vertical du texte (« PRÊT » une ligne / « TERMINER LE TOUR » deux lignes), vignette du bouton
  et du client. Vote par phase ; abstention si scène trop éloignée (> 1,2) ou marge < 0,34.
- Tour : **uniquement** la couleur du bouton (jaune vif ≥ 0,15 → moi ; jaune vif ≤ 0,05 et foncé
  ≥ 0,30 → un autre ; sinon inconnu). Jamais déduit d'une scène voisine.
- Suivi temporel : la phase incertaine est maintenue au plus 2,5 s ; un changement de phase peu sûr
  doit être vu 2 fois ; **le tour n'est jamais mémorisé**.
- Modèle runtime : `data/combat_state_model` (provenance vérifiée au chargement : humain + TRAIN).
- Vision réelle : « Mon tour » et la phase viennent de ce détecteur quand le modèle est installé.

### Corpus complété (28/09)
- TRAIN : 7 combats, 167 frames (2 combats ajoutés, joués jusqu'aux résultats) ; 4 frames « résultats ».
- VALIDATION : 3 combats, 85 frames (`a4cad421d66e` 34, `7f42661f926b` 30, `a5be3001c39d` 21).
- Correction d'étiquette VALIDATION, à la demande de l'utilisateur : `7f42661f926b` frame 33
  (`obs_ff15e43092834682`) « tour d'un autre » → « mon tour ». L'étiquette d'origine était reprise de la
  frame précédente (`carried_previous`) ; l'image montre la flèche de la timeline sur le joueur, ses cases
  de déplacement et le bouton jaune vif.

### Mesures — TRAIN, un combat laissé de côté (le combat mesuré n'apprend jamais)
| | Justes | Fausses | Abstentions | Précision | Couverture |
|---|---|---|---|---|---|
| Phase | 162 | 1 | 4 | 0,994 | 0,976 |
| Tour | 112 | 2 | 14 | 0,982 | 0,891 |

- **« Mon tour » affirmé à tort : 0.** Les 2 tours faux sont les 1res frames de combat ambiguës
  (étiquetées « moi », bouton foncé → détecté « un autre » : erreur prudente).
- La phase fausse est la frame « placement » qui montre déjà « TERMINER LE TOUR » (étiquette douteuse,
  non corrigée).
- Résultats : 4/4 reconnus (0/2 avec 5 combats).
- Abstentions du tour : bouton masqué par une infobulle.
- Transitions retrouvées à ± 2 frames : 37/39.

### Mesures — VALIDATION (modèle appris sur tout le TRAIN, jamais sur la VALIDATION)
| | Justes | Fausses | Abstentions | Précision | Couverture |
|---|---|---|---|---|---|
| Phase | 85 | 0 | 0 | 1,000 | 1,000 |
| Tour | 68 | 0 | 6 | 1,000 | 0,919 |

- « Mon tour » affirmé à tort : 0 ; manqué : 0. Transitions 18/18. Résultats 3/3.
- Les 6 abstentions du tour : bouton masqué (aucun jaune visible).
- Historique honnête : la VALIDATION a été mesurée 3 fois. (1) Modèle 5 combats : phase 83/1 faux
  (résultats pris pour du combat), tour 67/1 faux = la frame 33 mal étiquetée. (2) Après correction de
  cette étiquette : tour 0 faux. (3) Après ajout de 2 combats TRAIN : chiffres ci-dessus. Aucun réglage du
  détecteur n'a été fait d'après la VALIDATION ; seules les données TRAIN ont changé.
- Modèle runtime réinstallé : 7 combats TRAIN, 167 frames.

### Mesure — TEST aveugle (UNE seule fois, 28/09, référence figée `1eef84a`)
2 combats jamais vus, annotés sans suggestion (`session_476dfc09fee9` 17 frames,
`session_a9f208f1f1bc` 26 frames ; 43 frames). Modèle appris sur les 7 combats TRAIN.
Rapport brut : `data/benchmarks/combat-state-TEST-1eef84a.json`. Ces combats sont désormais historiques :
ne plus jamais les mesurer comme TEST.

| | Justes | Fausses | Abstentions | Précision | Couverture |
|---|---|---|---|---|---|
| Phase | 38 | 1 | 4 | 0,974 | 0,907 |
| Tour | 27 | 0 | 3 | 1,000 | 0,900 |

- **« Mon tour » affirmé à tort : 0 ; manqué : 0.** Transitions 9/10. Résultats 1/2 (l'autre : abstention).
- La phase fausse (`476dfc09fee9` frame 45) est l'instant juste après « Prêt » : fumée de début de combat,
  bouton déjà « TERMINER LE TOUR » mais foncé. Étiquetée « placement », détectée « combat, tour d'un autre ».
  C'est le même instant ambigu que dans le TRAIN. La vérité humaine est gardée telle quelle, et l'erreur est comptée.
- Abstentions : 3 frames hors combat (phase), 1 résultat (phase), 3 boutons masqués (tour).
- Aucun réglage après ce TEST.

### Bilan 3B-6B
Le détecteur n'a jamais annoncé « mon tour » à tort, ni en TRAIN, ni en VALIDATION, ni en TEST.
Point à surveiller : l'instant de début de combat, entre la fin du placement et le 1er tour.

Commande : `python -m combatbot.benchmark --combat-state [--install-combat-state-model]`.
