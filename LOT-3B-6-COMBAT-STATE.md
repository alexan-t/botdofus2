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

### Mesures — TRAIN, un combat laissé de côté (le combat mesuré n'apprend jamais)
| | Justes | Fausses | Abstentions | Précision | Couverture |
|---|---|---|---|---|---|
| Phase | 110 | 1 | 6 | 0,991 | 0,949 |
| Tour | 76 | 2 | 12 | 0,974 | 0,867 |

- **« Mon tour » affirmé à tort : 0.** Les 2 tours faux sont les 1res frames de combat ambiguës
  (étiquetées « moi », bouton foncé → détecté « un autre » : erreur prudente).
- La phase fausse est la frame « placement » qui montre déjà « TERMINER LE TOUR » (étiquette douteuse).
- Abstentions du tour : bouton masqué par une infobulle (10) ; phase : résultats (2 exemples seulement
  dans tout le TRAIN), début/fin d'exploration.
- Transitions retrouvées à ± 2 frames : 24/27.
- Sans suivi temporel : mêmes erreurs de tour, 1 abstention de phase de plus.

### À faire
- **VALIDATION** : au moins 1 combat complet (jusqu'aux résultats), mesuré une seule fois avec le
  modèle appris sur tout le TRAIN. Plus de frames « résultats » en TRAIN.
- TEST aveugle ensuite, selon la même règle que 3B-5.

Commande : `python -m combatbot.benchmark --combat-state [--install-combat-state-model]`.
