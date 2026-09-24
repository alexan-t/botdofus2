# LOT 3B-5 — reprise, audit et préparation de la validation réelle

Date : 24 septembre 2026. Version conservée : **0.4.3**.

## Conclusion

**Dernière vérification : voir section 14.** Une nouvelle collecte de 28 images distinctes,
sur deux maps et deux sessions, est annotée. Les identités sont cette fois cohérentes à
l'examen des séquences, mais aucun profil TRAIN ne couvre leur nouvelle calibration :
le pipeline complet ne produit ni joueur ni ennemi classé sur ces nouvelles frames.
Ne pas demander une nouvelle collecte avant de traiter ce blocage logiciel.

**Mise à jour après collecte : 28 annotations humaines reçues et mesurées.** Le détecteur
retrouve 54/58 positions ennemies sans faux ennemi mesuré, mais le joueur reste en échec
(1 bonne cellule, 1 mauvaise, 26 UNKNOWN). L'utilisateur a confirmé avoir renuméroté E1/E2
selon chaque image : ces numéros ne sont pas une vérité de tracking. Voir la section 12,
qui remplace les statuts initiaux. Version toujours **0.4.3**.

État initial avant collecte : le pipeline existe et ses tests synthétiques passent. Le corpus de l'application contenait
**67 observations, dont 28 avec grille GAMEDATA_PROJECTED, et zéro vérité humaine d'entités**.
Il est donc impossible de valider réellement le joueur, les ennemis ou le tracking.
La reprise s'arrête à la préparation de l'annotation. Aucun seuil de perception/tracking
n'a été réglé, aucune action n'a été exécutée dans DOFUS, aucun développement 3B-6.

## 1. État initial et Git

- Branche : `lot-3b-5-entity-tracking`.
- HEAD complet : `57d075366c080bbaf8ad52fd1ebbde1708eced99`.
- HEAD court : `57d0753` — `test: learn first real 8 and single-digit ap 1`.
- Le travail HUD 3B-4R2 est déjà commité ; la frontière est identifiée.
- Working tree initial non propre : 11 fichiers suivis modifiés, 567 ajouts / 28 suppressions,
  plus les nouveaux modules et tests 3B-5 non suivis. `.claude/` est également non suivi et
  a été laissé intact.
- Aucun reset, checkout destructif, suppression du travail existant ou réécriture d'historique.
- `git diff --check` initial : succès ; avertissements de conversion LF/CRLF.

Fichiers suivis déjà modifiés avant reprise : `combatbot/benchmark.py`,
`combatbot/corpus/{models,repository}.py`, `combatbot/packaging_smoke.py`,
`combatbot/ui/{corpus_page,main_window,pages}.py`,
`combatbot/vision/{combat_models,combat_observer,combat_tracker}.py`,
`tests/test_gamedata_grid.py`.

Modules déjà présents, non suivis : `combatbot/vision/{entity_detector,entity_geometry,
entity_models,entity_profiles,entity_tracker,background_model}.py`,
`combatbot/corpus/entity_benchmark.py`, `combatbot/ui/entity_annotation_dialog.py`,
`tests/entity_fixtures.py`, `tests/test_entity_{detection,tracking,corpus}.py`.

Documents de référence lus : LOT-3B-4R2-HUD-GROUND-TRUTH, LOT-3B-4R-REAL-HUD-VALIDATION,
LOT-3B-3-GRID-VALIDATION, LOT-3B-2-GAMEDATA-GRID et AUDIT-BOTS-DOFUS-PYTHONBOT.

## 2. Audit de l'implémentation déjà présente

| Point | Constat avant correction |
|---|---|
| Intégration | `RealCombatObserver.observe` appelle `_observe_entities` pour `GAMEDATA_PROJECTED`. |
| Chemin | ProjectedGrid → CellEntityDetector → EntityTracker → CombatObservation. |
| Fallback | `classify_cell_occupancy` demeure sur les autres sources ; metadata `LEGACY_CLASSIFY`. |
| Nouveau chemin | Metadata `entities.pipeline = CELL_ENTITY_DETECTOR`. |
| Association | Algorithme hongrois global, exact et rectangulaire ; helper greedy réservé à la comparaison. |
| États | OBSERVED, OCCLUDED et LOST existent. |
| Occultation | Dans `TrackedEntity` et `CombatObservation.entities`, `cell_id=None`, `observed_this_frame=False`, `last_known_cell_id` conservé, confiance courante nulle. |
| Occupation | Calculée depuis les preuves de la frame ; la dernière cellule d'une piste occultée n'est pas forcée OCCUPIED. |
| Compatibilité historique | La structure `EnemyObservation` conserve une position passée pour l'affichage des pistes occultées ; son flag d'observation et son état doivent être respectés. `enemy_cells` exclut ces pistes. |
| Map | Reset du tracker et du background au changement de map déclarée ; profil visuel conservé. |
| Profils | Fichiers JSON persistants joueur par profil et équipe ; provenance humaine et mesures locales de marqueur/pieds/centre. |
| Layout | Contrôle joueur permissif en cas de signature manquante ; profil équipe non filtré. Défauts corrigés pendant cette reprise. |
| Annotation | Image originale + grille projetée + survol Cell ID + zoom brut. Aucune prédiction affichée ou préremplie ; seules d'anciennes confirmations humaines sont rechargeables. |
| Benchmark | BEFORE/AFTER et split par session/map présents ; tests synthétiques fonctionnels. Défaut de scoring des cellules non annotées corrigé. |
| Overlay | Diagnostic réalisé après la perception sur une copie de l'image. Options ROIs, preuves, IDs, occultation, occupation présentes. « Fond (FREE) » colore les FREE ; ce n'est pas encore une visualisation numérique du delta de fond. |

### Conditions FREE exactes du modèle actuel

- Cellule statiquement traversable (`static_traversable is True`).
- Contexte explicitement `grid_visible=True` et `grid_aligned=True`.
- Aucun pic faible de marqueur : seuil provisoire `peak < 8`, aucune occupation courante.
- Au moins 3 échantillons de fond, historique limité à 12, dispersion Lab moyenne ≤ 4.
- Distance Lab du fond courant au modèle ≤ 6, confirmée sur 2 évaluations successives.
- Modèle réinitialisé si map/projection change ; preuve d'entité interrompt la confirmation.

Ces conditions sont des garanties algorithmiques, **pas une précision FREE mesurée dans DOFUS**.
Une présence forte non classée reste `EntityKind.UNKNOWN` et peut marquer OCCUPIED.
Les seuils et poids sont provisoires, inchangés.

## 3. Changements pendant cette reprise

Corrections limitées aux défauts constatés et aux vérifications nécessaires :

1. Annotation : plusieurs ennemis sans identité peuvent coexister ; seul E1/E2/etc. est unique.
2. Cocher « Joueur non visible » retire effectivement la cellule joueur précédente.
3. Mode tactique tri-état : oui / non / inconnu, persistance fidèle.
4. Fenêtre d'annotation maximisable, ouverte maximisée depuis Corpus ; image redimensionnée.
5. Validation : VISIBLE exige une cellule joueur ; une vérité confirmée exige une visibilité ;
   contrôle de la plage des cellules vides et joueur.
6. Profils joueur/équipe : provenance humaine et signature de layout explicite identique requises.
   Signature absente ou incompatible : profil inutilisable, classification prudente UNKNOWN.
7. Profils de benchmark construits séparément par layout, exclusivement depuis TRAIN.
   Le format persistant équipe étant unique, aucun choix arbitraire n'est exporté si plusieurs layouts existent.
8. Occupation : seules les cellules explicitement annotées sont scorées comme correctes/fausses.
   FREE sur cellule non annotée devient `free_unlabelled`, jamais une vérité vide automatique.
   Ajout de couverture FREE et compte faux FREE. Les cellules non annotées restent hors précision.
9. Faux ennemi sur le joueur compté comme faux positif ; précision joueur acceptée inclut les
   faux positifs sur frames joueur non visible.
10. Benchmark : une preuve de visibilité/alignement absente n'est plus remplacée par VISIBLE/ALIGNED.
11. Tests de ces corrections, du reset map, et smoke packagé enrichi : annotation, occultation,
    réapparition avec mêmes identités.

Ni les modules HUD, ni les seuils de détection, ni les poids/limites du tracker n'ont été modifiés.
Les fixtures existantes utilisent maintenant des signatures de layout explicites pour tester
la même contrainte que l'application.

## 4. Corpus réel et provenance — inventaire initial avant collecte

Les logs de l'application confirment l'exécution de `dist/PythonBot/PythonBot.exe` avec données
dans `C:/Users/Alpha5/AppData/Local/PythonBot` (dernier démarrage constaté : 24/09 à 14:02:54).

| Inventaire | Corpus application | Corpus développement |
|---|---:|---:|
| Frames totales | 67 | 46 |
| Frames GAMEDATA_PROJECTED | 28 | 28 |
| Sessions enregistrées, tous usages | 13 | 5 |
| Frames avec annotation entités | 0 | 0 |
| Frames entités human_confirmed | 0 | 0 |
| Groupes/combats avec vérités entités | 0 | 0 |
| Player truths | 0 | 0 |
| Enemy truths | 0 | 0 |
| Track truths E1/E2/etc. | 0 | 0 |
| Phases entités confirmées | aucune | aucune |

**13 sessions ne signifient pas 13 combats annotés.** Aucun nombre de combats effectifs
n'est déduit des prédictions. Les anciennes vérités HUD ne sont pas des vérités d'entités.

Les deux exécutions du benchmark entités (avant/après corrections de cette reprise) ont utilisé
directement `%LOCALAPPDATA%/PythonBot/data/corpus` : résultat **INSUFFICIENT, 0 frame**.
Aucun profil ni split d'entités n'a été appris/exporté sur ces données vides.

Pour GRID/HUD, une copie du corpus autoritaire a été créée dans
`data/validation/lot3b5-reprise/corpus` : 435 fichiers vérifiés SHA-256 identiques à la source.
`snapshot.json` contient l'inventaire et les empreintes. Les benchmarks pouvant produire
des registres/templates, ils ont travaillé sur cette copie, sans modifier le corpus utilisateur.

## 5. Split, profils et résultats réels

TRAIN : **0 frame / 0 groupe** ; VALIDATION : **0 / 0** ; TEST : **0 / 0**.
Le registre `entity_split_registry.json` conserve les splits déjà attribués par session/map.
Une même session/map n'est pas partagée entre splits. Pendant la collecte, arrêter et relancer
l'observation entre combats crée des sessions indépendantes, même sur une map identique.

Les profils utilisent uniquement les vérités TRAIN. Aucun profil TEST n'est appris.
Teintes, plages, distances Lab et variations réelles d'équipe : **non mesurables, 0 échantillon**.

| Mesure réelle | BEFORE historique | AFTER détecteur + tracker |
|---|---|---|
| Player recall / cell accuracy / accepted accuracy / FP / UNKNOWN | N/A | N/A |
| Enemy precision / recall / cell accuracy / count MAE / UNKNOWN | N/A | N/A |
| OCCUPIED precision | N/A | N/A |
| FREE precision / coverage / UNKNOWN | N/A | N/A |
| ID switches / fragmentation / réassociations fausses | N/A | N/A |
| Occlusions / réapparitions / lost / recovered | N/A | N/A |
| Greedy vs global sur plusieurs ennemis | N/A | N/A |
| Normal vs tactique | N/A | N/A |

Les compteurs vides du JSON (par exemple zéro switch pour zéro frame) **ne constituent pas un succès**.
Les tests synthétiques n'apportent aucune preuve que le hongrois est meilleur sur de vrais combats.
La référence BEFORE n'a actuellement pas de profil HSV joueur provenant du corpus ; sa mesure
joueur doit donc être présentée avec cette limite lorsqu'elle sera exécutée sur des vérités réelles.

### Limites du banc à traiter avant une conclusion réelle sur le tracking

Le banc existant utilise encore `frame_index * 0.4` comme temps de replay. Il faudra exploiter
les timestamps réels des captures (la collecte peut sauter des frames). Ses métriques de
fragmentation et d'occultation sont simplifiées : une absence de track annoté est actuellement
interprétée comme une absence à récupérer, sans exploiter explicitement `enemy_occluded_tracks`.
Les événements détaillés piste par piste et les comptes LOST ne sont pas encore rapportés.
Ne pas utiliser ces compteurs pour déclarer un PASS réel en l'état.

Le comparatif greedy/global utilise les mêmes détections, mais les politiques de conservation,
gating et coûts diffèrent : ce n'est pas une comparaison isolant uniquement l'algorithme
d'affectation. Ces limites sont identifiées ; aucun résultat réel n'est publié avant collecte.

## 6. Performance

Le détecteur convertit la région englobante en HSV et Lab une fois par frame, puis agrège les
ROIs projetées. Cache des géométries présent ; pas de conversion lourde indépendante ×560.

`detector_ms` et `tracker_ms` sont instrumentés. Le temps background est actuellement inclus
dans la décision du détecteur, sans `background_ms` séparé. Le `observer_ms` du benchmark
provient de la capture historique : **ce n'est pas le temps du nouveau pipeline rejoué**.
Aucune performance réelle du nouveau pipeline n'est revendiquée ni comparée à la cible de 400 ms.

## 7. Non-régressions

### GRID

Benchmark exécuté avec le client `C:/Users/Alpha5/AppData/Local/Alea/Client`, corpus application copié.
Les 26 frames de référence sont toujours présentes : 14 exploration, 5 placement, 7 combat.

- Faux combat en exploration : **0/14**.
- Placement/combat correctement reconnus : **12/12**.
- Visibilité : **26/26**.
- Alignement : **12/12 ALIGNED**, résidu médian 1,165 px.
- Verdict : **aucune régression constatée** sur le corpus de référence.

### HUD

64 observations exploitables, **128 crops avec vérité humaine**. Les autres observations du
corpus ne contribuent pas à cet inventaire HUD. TRAIN 80, VALIDATION 22, TEST 26 crops.

| Split | Lectures acceptées correctes | Précision acceptée | UNKNOWN |
|---|---:|---:|---:|
| TRAIN | 80/80 | 1,000 | 0 |
| VALIDATION | 21/21 | 1,000 | 1 |
| TEST | 24/24 | 1,000 | 2 |

1/7 : **VALIDATED**, zéro confusion 1↔7 sur TEST. Couverture 1 : 8/9, 7 : 4/4.
Le chiffre 8 reste couvert par un seul exemple TRAIN ; aucune généralisation sur TEST revendiquée.
Verdict : **aucune lecture acceptée fausse connue, aucune régression constatée**.

## 8. Tests et interface

Avant toute modification :

- `pytest tests/test_entity_detection.py -q` : **17 passed**.
- `pytest tests/test_entity_tracking.py -q` : **17 passed**.
- `pytest tests/test_entity_corpus.py -q` : **12 passed**.
- `pytest -q` : **348 passed** (30,70 s).
- `python -m compileall -q combatbot` : succès.

Après corrections :

- Détection : **20 passed**.
- Tracking : **18 passed**, dont reset map/fond sans perte du profil.
- Corpus : **16 passed**.
- Suite complète : **356 passed** (31,55 s).
- Compilation : succès.
- `git diff --check` : succès (avertissements LF/CRLF).

Un nouveau test a d'abord échoué parce que sa cellule censée non annotée était celle du joueur
de la fixture ; les cellules de test ont été corrigées, sans modifier l'algorithme pour le faire passer.

Vérification Qt hors écran sur copie réelle : fenêtre d'annotation ouverte, image originale chargée,
28 entrées projetées, zéro label prérempli ; rendu de la grille et canevas vérifiés.
Les tests synthétiques vérifient l'enregistrement humain, sans sauvegarder de vérité sur les
captures réelles. Ceci ne remplace pas votre contrôle visuel de l'alignement dans Windows.

## 9. Build et livraison

- `build_exe.ps1` : **succès**, ONEDIR, environ 86 s pour PyInstaller.
- Exécutable : `C:/Users/Alpha5/Documents/pythonbot_test/dist/PythonBot/PythonBot.exe`.
- Package : **349 526 074 octets, 333,3 Mio**.
- Version inchangée : **0.4.3**.
- Aucun processus PythonBot en cours lors du nettoyage du build ; aucun programme utilisateur tué.
- Cache PyInstaller placé dans le projet ; le script vérifie que build/dist sont internes avant nettoyage.

Smoke lancé **depuis le nouvel exécutable**, avec `--package-smoke-test`, sortie **0**, `frozen=true`,
`success=true`. Données isolées sous `data/validation/lot3b5-reprise/smoke-runtime` : corpus copié
et backup SQLite réalisé depuis une connexion source en lecture seule. Qt utilisé en mode
**offscreen** : ouverture et navigation programmatiques testées, pas de validation visuelle native Windows.

Vérifié depuis l'exécutable :

- Initialisation Qt, navigation entre les 8 pages, SQLite, 1 profil, statistiques existantes de la copie.
- 67 observations chargées, fenêtre d'annotation ouverte, 28 frames projetées,
  image chargée, zéro label humain prérempli.
- OpenCV opérationnel ; GridProjector projette 560 cellules, lookup cell 287 correct.
- Fixtures de visibilité/alignement : grille vide NOT_VISIBLE, grille dessinée VISIBLE/ALIGNED.
- CellEntityDetector : joueur et ennemi de fixture reconnus.
- EntityTracker : pistes `player` et `enemy_1`, occultation sans cellule observée,
  réapparition conservant les identités.
- HUDReader : fixture 7 lue ; fallback disponible.
- RapidOCR/ONNX réellement exécuté : `3 PA Portee 1-4`, confiance 0,999495.
- Aucune action exécutée ; `PYTHONBOT_SMOKE_SKIP_DOFUS=1` : détection des fenêtres et capture
  DOFUS **volontairement non testées** dans ce smoke. Le texte générique « aucune fenêtre détectée »
  du JSON ne doit pas être interprété comme un inventaire réel des fenêtres.
- En-tête PE vérifié : subsystem **2 (Windows GUI)**. Pas de console configurée ;
  absence de fenêtre console parasite non contrôlée visuellement sur le bureau.

Warnings PyInstaller : installation tkinter incomplète et exclue (interface PySide6),
SyntaxWarning d'échappements dans les chemins optionnels X11 de PyAutoGUI et PyTorch de RapidOCR.
Le fichier `build/PythonBot/warn-PythonBot.txt` liste aussi des imports optionnels/non Windows,
des moteurs volontairement exclus (torch/paddle/etc.) et des symboles dynamiques NumPy.
Aucun échec dans le parcours Qt/OpenCV/RapidOCR/ONNX du smoke.

Preuves locales (ignorées par Git, aucune capture DOFUS commitée) :

- `data/validation/lot3b5-reprise/{snapshot.json,pytest-final.log,build.log,packaging-smoke.json}`.
- `data/validation/lot3b5-reprise/entities-before-fixes/entities.json` et `entities/entities.json`.
- `data/validation/lot3b5-reprise/grid/grid-validation.{json,md}`.
- `data/validation/lot3b5-reprise/hud/hud-reader.{json,md}` et `one-seven.{json,png}`.

Contrôle final des empreintes des 435 fichiers du corpus source : **aucun fichier modifié**.

## 10. Collecte à effectuer dans l'application

1. Ouvrir `dist/PythonBot/PythonBot.exe` par double-clic ; aucune réinstallation nécessaire.
2. Pour annoter les captures existantes : **Corpus / Annotation → Annoter les entités…**.
   La fenêtre s'ouvre maximisée ; survoler donne le Cell ID et un zoom de l'image brute.
3. Vérifier que la grille correspond aux pieds/marqueurs des personnages. Ne pas confirmer une
   cellule si la projection est décalée ; recalibrer puis enregistrer une nouvelle séquence.
4. Cliquer la cellule du joueur dans PythonBot → **JOUEUR**. S'il est invisible/occulté,
   cocher **Joueur non visible (UNKNOWN)**, sans conserver sa dernière cellule.
5. Cliquer chaque ennemi visible → **ENNEMI → E1**, puis **E2**, etc. Garder la même identité
   dans les frames consécutives du même combat seulement lorsqu'on peut la suivre.
   Si l'identité est incertaine : **Ennemi sans identifiant**.
6. En cas d'occultation, ne pas attribuer de cellule à l'ennemi absent. Inscrire par exemple
   `E2` dans **Ennemis occultés**, cocher **Occlusion / sprite masquant un marqueur**.
   À sa réapparition certaine, réutiliser E2 sur sa cellule réellement visible.
7. Annoter quelques cellules évidemment vides via **VIDE confirmé**. **INCONNU (effacer)**
   retire une annotation ; les cellules non annotées restent inconnues.
8. Choisir la phase ; indiquer le mode tactique si connu (coché oui, décoché non, tiret inconnu).
9. Cliquer **Confirmer cette frame** : enregistrement puis passage à la suivante.
   Les boutons précédente/suivante seuls n'enregistrent pas les changements.

### Nouvelles séquences nécessaires

Cible initiale : **20 à 40 frames annotées, sur au moins 3 combats distincts**, avec des frames
consécutives par combat et au moins une séquence comportant deux ennemis.

Dans **Combat → Vision réelle**, connecter le client selon les réglages habituels, vérifier
la map déclarée et la projection, cocher **Enregistrer la séquence dans le corpus (entités,
lecture seule)** puis **Démarrer l'observation**. Jouer manuellement.
La collecte ignore les images quasi identiques et espace les enregistrements d'au moins 1 s.
Cliquer **Arrêter l'observation** entre combats, puis redémarrer pour créer une nouvelle session.

Situations manquantes : placement/début, joueur et ennemis immobiles pendant mon tour,
ennemi en déplacement, plusieurs ennemis proches, animation de sort, changement de tour.
Occultation puis réapparition uniquement si cela se présente naturellement ; ne pas forcer ces cas.
Annoter ensuite dans Corpus. Rien à cliquer dans DOFUS pour PythonBot ; le jeu reste manuel.

## 11. Statuts avant collecte (remplacés par la section 12)

```text
IMPLEMENTATION: PARTIAL
REAL ENTITY CORPUS: INSUFFICIENT
PLAYER DETECTION: PARTIAL
ENEMY DETECTION: PARTIAL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
HUD REGRESSION: NONE
GRID REGRESSION: NONE
ACTIONS: NONE
```

Aucun passage à 0.5.0, aucun commit de finalisation : critères réels non atteints.
HEAD inchangé, branche inchangée, **working tree clean : non** (travail 3B-5 conservé).
La suite attend les annotations humaines ; aucun résultat du détecteur ne sera promu en vérité.

## 12. Réception des annotations et baseline réelle — 24 septembre 2026

### Données reçues

Source confirmée : `%LOCALAPPDATA%/PythonBot/data/corpus`, toujours 67 observations.
Les **28 frames projetées portent maintenant une confirmation humaine d'entités** :

- 28 positions joueur VISIBLE ; aucune frame joueur NOT_VISIBLE.
- 58 positions ennemies, dont 56 étiquettes E1… et 2 ennemis sans identifiant.
- **0 identité de tracking confirmée** : réponse explicite de l'utilisateur :
  « J'ai renuméroté selon l'image ». Les 56 étiquettes sont donc propres à chaque image.
- 1 cellule vide confirmée ; aucune occultation ennemie annotée.
- Phases humaines : exploration 13, début de combat 5, mon tour 8, non précisée 2.
- 2 sessions d'origine ; 8 groupes session/map, qui ne constituent pas une preuve de
  8 combats indépendants ni de 8 séquences temporelles réelles.
- 23 images distinctes par SHA-256 pour 28 annotations. Cinq paires sont identiques ;
  deux paires d'exploration traversent TRAIN/VALIDATION. Le split est conservé pour cette
  baseline et cette limite est signalée ; ces images ne peuvent prouver une généralisation.

Copie des 435 fichiers dans `data/validation/lot3b5-annotations-1/corpus`, empreintes vérifiées.
Contrôle après mesures : **aucun fichier du corpus utilisateur modifié**. Ni les phases
manquantes, ni les identités renumérotées n'ont été corrigées automatiquement.

### Baseline conservée, sans réglage

Commande exécutée sur cette copie fidèle :

```powershell
.\.venv\Scripts\python.exe -m combatbot.benchmark --entities --corpus-root data/validation/lot3b5-annotations-1/corpus --output-dir data/validation/lot3b5-annotations-1/baseline
```

Split figé dans le snapshot : TRAIN **21 frames / 6 groupes**, VALIDATION **3 / 1**,
TEST **4 / 1**. Le seul groupe TEST comporte une frame de début de combat avec quatre ennemis ;
le reste ne suffit pas à démontrer une bonne généralisation sur plusieurs combats.
Conserver ce registre lors des prochaines mesures : ne pas redistribuer TEST pour améliorer un score.

Profil appris exclusivement depuis TRAIN, layout `e561118fd8115c8f` :
10 mesures de teinte joueur, 47 mesures ennemies ; teintes circulaires OpenCV
respectivement **2,19 ± 8** et **118,84 ± 8**. Ces plages décrivent ce corpus seulement.
Le profil Lab joueur reste un seul exemple (`obs_e23970ecd2fb4a33`, cellule 506), tolérance
provisoire 30 inchangée. Aucun apprentissage sur VALIDATION/TEST.

| Métrique sur les 28 annotations | BEFORE historique | AFTER actuel |
|---|---:|---:|
| Positions ennemies humaines | 58 | 58 |
| Détections ennemies | 182 | 54 |
| Appariements à distance ≤ 1 cellule | 9 | 54 |
| Appariements sur cellule exacte | 3 | 54 |
| Faux ennemis mesurés | 173 | 0 |
| Précision ennemis | 4,95 % | 100 % |
| Rappel ennemis | 15,52 % | 93,10 % |
| Erreur absolue moyenne de nombre | 4,643 | 0,143 |
| Joueur correct / mauvais / UNKNOWN | 0 / 0 / 28 | 1 / 1 / 26 |
| Précision des positions joueur acceptées | N/A | 50 % |

Le BEFORE n'a pas de référence HSV joueur disponible : sa colonne joueur ne constitue
pas une comparaison équitable avec un ancien système calibré manuellement.

AFTER sur TEST : 4/4 ennemis sur les cellules exactes, aucun faux ennemi, joueur UNKNOWN 4/4.
Sur VALIDATION : 4/4 ennemis exacts, joueur UNKNOWN 3/3.
Le résultat ennemi est encourageant mais **PARTIAL**, avec un seul groupe TEST contenant
un instant de combat. Le rappel global comprend TRAIN ; il ne vaut pas une estimation indépendante.

Les 13 frames explicitement exploration restent joueur UNKNOWN, sans ennemi détecté.
Sur les 13 frames explicitement début de combat/mon tour : joueur **1 correct, 1 mauvais,
11 UNKNOWN**. Les 2 frames sans phase restent rapportées séparément, sans phase inventée.

### Défaut joueur démontré (TRAIN/VALIDATION seulement)

Sur TRAIN `obs_b9526dc0825942e1`, vérité joueur 326, prédiction 367 avec confiance **0,854**.
Le profil local attribue 0,995 à la cellule 367, contre 0,724 au joueur réel : confusion avec
une autre entité du même camp, visible sur la capture. La couleur d'équipe seule ne suffit pas
à identifier le joueur. Sur VALIDATION `obs_fad510a418b54f89`, le marqueur joueur 490 est
candidat mais son score de profil local est nul : l'apparence unique apprise ne généralise pas.
Le diagnostic détaillé ne consulte pas les images TEST pour chercher une correction.

Aucun seuil n'a été modifié. **PLAYER DETECTION = FAIL** pour cette baseline, et non une
erreur imputée par défaut à l'annotation utilisateur. Une correction devra être évaluée sur
des données de validation suffisamment variées sans relâcher l'identification des alliés.

### Tracking : scores bruts invalides

Le benchmark historique émet global : 15 switches / 15 fragmentations / 14 réassociations,
et greedy : 19 / 19 / 11. **Ces nombres sont invalides comme mesures de tracking réel** :
les étiquettes ennemies ne sont pas persistantes, conformément à la réponse utilisateur.
Ils sont conservés uniquement dans la baseline brute pour traçabilité.

Le rapport `validated-detection-report.json` marque explicitement le tracking `NOT_EVALUABLE`,
avec zéro vérité d'identité confirmée. Il ne conclut à aucune supériorité globale/greedy.
Les limitations de replay temporel et de métriques identifiées en section 5 restent à traiter
avant une future validation du tracking. Aucune occultation/réapparition réelle validée.

### Occupation et performance

Aucun FREE émis : précision FREE **N/A**, couverture de la seule cellule vide confirmée **0 %**.
Aucun faux FREE mesuré ne prouve donc pas la sécurité du modèle. OCCUPIED a une précision
de 100 % sur les cellules scorables, mais cinq prédictions OCCUPIED sont sans vérité locale
et exclues de cette précision ; un seul négatif vide est insuffisant.
UNKNOWN AFTER : 98,77 % des 5 790 cellules analysées, contre 98,84 % des 15 680 cellules
legacy. Ces taux ont des domaines différents (filtrage statique) et ne sont pas directement comparables.

Temps de replay AFTER : détecteur moyenne **76,24 ms**, médiane 68,56, max 161,90 ; tracker
moyenne **0,069 ms**, max 0,216. Background inclus dans le détecteur, non isolé.
Le `observer_ms` brut reste une durée historique ; aucune durée complète du nouvel observer
n'est revendiquée à partir de cette valeur.

### Vérifications de cette réception

- Tests ciblés : **54 passed** (20 détection, 18 tracking, 16 corpus).
- Suite complète : **356 passed** (41,38 s) ; compilation réussie.
- GRID relancé : 0/14 faux combats, 12/12 combats, 26/26 visibilité, 12/12 alignement.
- HUD/RapidOCR relancé : 128 vérités, PASS, 1/7 VALIDATED, aucune régression.
- Aucune modification de code applicatif pendant cette réception : build et exécutable de
  la section 9 conservés, aucune réinstallation/reconstruction nécessaire.
- Rapport mis à jour ; version 0.4.3, HEAD/branche inchangés, aucun commit de finalisation.

Preuves : `data/validation/lot3b5-annotations-1/` contient le snapshot, l'inventaire humain,
la baseline brute, `validated-detection-report.json`, le diagnostic joueur TRAIN/VALIDATION,
les rapports GRID/HUD et le journal pytest. Ces données ne sont pas versionnées.

### Suite nécessaire sans refaire les 28 annotations

Conserver les positions déjà annotées. Ajouter une petite collecte de **8 à 12 nouvelles frames**
réparties sur **2 à 3 combats**, avec 3 à 5 frames consécutives par combat et au moins deux ennemis.
Utiliser les commandes de collecte et d'annotation de la section 10. Un monstre reçoit E1
une fois et conserve E1 quand il se déplace ; un autre reste E2. Si l'identité devient incertaine,
utiliser « Ennemi sans identifiant ». Une nouvelle invocation a sa propre identité.
Les numéros peuvent repartir à E1 au combat suivant, après arrêt/redémarrage de l'observation.
Quelques cellules vides supplémentaires et une occultation naturelle, si elle se présente,
compléteront les mesures. Ne pas marquer occulté un monstre simplement mort ou disparu sans certitude.

### Statuts actuels

```text
IMPLEMENTATION: PARTIAL
REAL ENTITY CORPUS: PARTIAL
PLAYER DETECTION: FAIL
ENEMY DETECTION: PARTIAL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
HUD REGRESSION: NONE
GRID REGRESSION: NONE
ACTIONS: NONE
```

Pas de version 0.5.0 ni de LOT 3B-6 : la reconnaissance joueur est en échec et les données
de tracking ne sont pas encore valides. La prochaine collecte complète les positions existantes,
elle ne les remplace pas.

## 13. Retrait des captures mal calibrées et calibration automatique à l'ouverture

Demande utilisateur : retirer les captures prises après les 28 annotations initiales.
Au moment de l'opération, le corpus contenait 100 entrées : 67 anciennes à préserver et
33 nouvelles (19 GAMEDATA_PROJECTED, 14 VISION_DETECTED), sans nouvelle vérité confirmée.

- 33 nouvelles entrées retirées du manifeste après sauvegarde vérifiée SHA-256.
- 67 entrées antérieures conservées, dont les 28 annotations d'entités et les anciennes données HUD.
- Images non effacées définitivement ; sauvegarde du manifeste et copie des fichiers dans
  `data/quarantine/20260924-new-observations/`, avec `archive.json` listant les identifiants retirés.
- Aucun changement de vérité humaine ni recalibration rétroactive des captures.

La proposition automatique existante de `GridProjectionDialog` se lance désormais au premier
affichage lorsque la topologie de la map est chargée. Le calcul des candidats et l'ajustement
tournent dans `JobRunner`, hors du thread Qt. Les signaux appliquent la proposition dans l'UI ;
confirmation et édition géométrique sont suspendues pendant le calcul. Annuler pendant le
calcul ferme le dialogue après la fin du worker, sans sauvegarde. La fenêtre s'ouvre maximisée,
avec l'image ajustée à la fenêtre. Le bouton de proposition permet de relancer le calcul.

Les seuils de calibration n'ont pas changé. Une proposition ne devient pas silencieusement
une calibration confirmée : la confirmation visuelle reste nécessaire. La map active doit
être chargée auparavant ; sa détection automatique ne fait pas partie de cette correction.

Vérification sur une capture archivée (map déclarée 191105024) : calcul en 513,7 ms,
178 candidats, 121 inliers, résidu médian 0,78 px, score 0,886 et marge 0,077.
Statut **AMBIGUOUS**, six hypothèses : aucun alignement réel PASS revendiqué.
Preuve : `data/validation/calibration-auto-real.json`.

Tests : 8 tests UI grille réussis, dont calcul automatique sur un worker et fermeture pendant
calcul ; **358 tests complets réussis** (42,90 s). Compilation et `git diff --check` réussis.
Benchmark GRID relancé dans `data/validation/calibration-auto-grid/`.
Build ONEDIR reconstruit ; journaux `data/validation/calibration-auto-build.log` et
`calibration-auto-pytest.log`. Version toujours 0.4.3 ; aucune action dans DOFUS.

Build terminé avec succès (333,3 Mio). Smoke du nouvel exécutable : sortie 0, `success=true`,
Qt hors écran et copie isolée des données ; rapport `data/validation/calibration-auto-smoke.json`.
GRID reste à 0/14 faux combats, 12/12 combats, 26/26 visibilité et 12/12 alignement.

## 14. Vérification des deux nouvelles séquences annotées

### Réception et intégrité

Corpus actif : 95 entrées, dont **56 annotations humaines d'entités** (28 anciennes + 28 nouvelles).
Les captures retirées lors des demandes précédentes ne sont pas réintroduites dans le manifeste.
Snapshot de contrôle : `data/validation/lot3b5-sequences-2/corpus`, 944 fichiers vérifiés SHA-256
(inclut les fichiers conservés sur disque mais absents du manifeste).

| Nouvelle session | Map déclarée | Frames annotées | Positions joueur visibles | Positions ennemies |
|---|---:|---:|---:|---:|
| session_1f2858ce5c14 | 88084225 | 12 | 11 | 21 |
| session_325b4d78d112 | 88083713 | 16 | 16 | 45 |
| Total | 2 maps | **28** | **27** | **66** |

Les 28 images sont distinctes par SHA-256. Une frame indique joueur NOT_VISIBLE.
Toutes portent une grille GAMEDATA_PROJECTED, avec metadata VISIBLE/ALIGNED ; ces flags
ne remplacent pas à eux seuls une vérification humaine de tous les cell IDs.
Phases : exploration 1, début de combat 2, mon tour 13, tour ennemi 7, non précisée 5.
Les phases manquantes restent manquantes. Aucune cellule vide ou occultation explicite
n'a été annotée dans ces nouvelles séquences.

Les identités E1/E2 dans la première session, E1/E2/E3 dans la seconde, restent associées
aux mêmes cellules lorsque les ennemis sont immobiles et évoluent de façon cohérente dans
les annotations lors des déplacements. Pas d'inversion systématique de numéros comme dans
l'ancien corpus. Cela ne prouve pas indépendamment chaque identité sur chaque image.
Les disparitions finales ne sont pas interprétées automatiquement comme des occultations.

### Baseline et split préservé

Le registre de split de la section 12 a été copié avant calcul : aucun ancien groupe TEST déplacé.
Les nouveaux groupes sont attribués par le répartiteur existant à VALIDATION (12 frames)
et TEST (16 frames). Résultat cumulé : TRAIN 21/6 groupes, VALIDATION 15/2, TEST 20/2.
Baseline brute : `data/validation/lot3b5-sequences-2/baseline/entities.json`.
Les scores de tracking mélangés aux anciennes étiquettes renumérotées restent invalides.

### Blocage constaté : aucun apprentissage pour la nouvelle disposition

La nouvelle signature de calibration est stockée en JSON, avec digest `9a0751d48681994e`.
L'ancienne signature TRAIN est `e561118fd8115c8f`. Ce n'est pas seulement une différence de
format : la zone combat a été déplacée verticalement et les zones PA/PM ont été modifiées.
Il serait incorrect de forcer silencieusement la compatibilité.

Le répartiteur n'assure pas de groupe TRAIN par disposition. Il a mis les deux nouveaux combats
hors apprentissage : **0 profil joueur, 0 profil équipe pour cette nouvelle calibration**.
Le rejet prudent fonctionne, mais le banc ne peut pas tester un détecteur classifié opérationnel
sur cette disposition en l'état. Aucune vérité TEST n'a été utilisée pour créer un profil.
Les splits n'ont pas été redistribués après lecture des scores pour améliorer artificiellement le résultat.

Sur une image de VALIDATION (début de combat map 88084225), le joueur est une présence UNKNOWN
sur la bonne cellule 332. Les marqueurs des ennemis aux cellules humaines 300 et 382 sont
partiellement recouverts par leurs sprites ; leurs pics de chroma mesurés sont -14,8 et 4,7,
sous le seuil provisoire de présence. Ce second défaut doit être étudié après le problème de
profil : ajouter un profil ne garantit pas à lui seul de résoudre les détections manquantes.
Aucune image TEST n'a été inspectée pour choisir un nouveau seuil.

### Replay séparé des nouvelles séquences

Un replay de diagnostic utilise les timestamps enregistrés, avec intervalles **1,09 à 4,09 s**,
au lieu de `frame_index * 0.4`. Il emploie les mêmes profils exclusivement TRAIN, détecteur,
background et tracker que l'application ; aucune identité humaine n'est injectée comme détection.
Preuve : `data/validation/lot3b5-sequences-2/new-sequences-replay.json`.

- Joueur : **0/27 localisé**, 27 UNKNOWN, aucune position fausse acceptée ; NOT_VISIBLE respecté 1/1.
- Ennemis : **0/66 classé**, rappel 0 ; précision N/A (aucune prédiction ennemie).
- 3 présences UNKNOWN tombent sur des cellules ennemies humaines ; elles ne sont pas requalifiées en ENEMY.
- Tracking : 66 observations humaines d'identité, **0 appariement avec une piste détectée**.
  ID switches, fragmentation, réassociation et comparaison greedy/global : **non mesurables**,
  pas des zéros considérés comme un succès.
- Aucune occultation explicite : récupération d'identité non évaluée.
- FREE : 4 192 sorties sur des cellules sans annotation vide, exclues de la précision.
  Aucun faux FREE sur une cellule joueur/ennemi annotée ; aucune précision/coverage FREE
  revendiquée faute de vérités vides sur ces nouvelles images.

### Vérifications et suite

54 tests ciblés réussis ; **358 tests complets réussis** (32,89 s), compilation réussie.
GRID et HUD relancés sur le snapshot ; résultats consignés dans les sous-dossiers `grid/` et `hud/`.
GRID conserve 0/14 faux combats, 12/12 combats, 26/26 visibilité et 12/12 alignement.
HUD conserve 128 vérités, précision acceptée 1,000, PASS et 1/7 VALIDATED. Les 56 nouveaux
crops PA/PM ne sont pas annotés HUD : provenance globale PARTIAL, sans régression du lecteur.
Aucune modification du code applicatif, des annotations originales ou des paramètres du détecteur
pendant cette vérification. Aucun rebuild requis, exécutable 0.4.3 conservé.

Les nouvelles captures sont utilisables pour poursuivre le diagnostic. **Ne pas demander de
les refaire ni multiplier les maps maintenant.** Avant une prochaine validation complète :
traiter la couverture TRAIN par disposition sans toucher au TEST gelé, améliorer le profil joueur
et étudier les marqueurs masqués sur TRAIN/VALIDATION, puis reprendre la mesure du tracking.
Le corpus d'identités ancien renuméroté reste exclu de cette mesure.

```text
IMPLEMENTATION: PARTIAL
REAL ENTITY CORPUS: PARTIAL
PLAYER DETECTION: FAIL
ENEMY DETECTION: FAIL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
ACTIONS: NONE
```

Ces échecs décrivent le pipeline complet sur la nouvelle disposition, pas un défaut supposé
des annotations utilisateur. Pas de 0.5.0, de commit de finalisation ni de LOT 3B-6.

HUD REGRESSION: NONE ; GRID REGRESSION: NONE sur les corpus de référence mesurés.
Contrôle final des 944 empreintes source : aucun fichier modifié.
