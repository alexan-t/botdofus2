# LOT 3B-5 — reprise, audit et préparation de la validation réelle

Date : 24 septembre 2026. Version conservée : **0.4.3**.

## Conclusion

**Réparation 3B-5B : voir section 15.** La couverture TRAIN tient compte du layout (TEST
inchangé). Le profil joueur utilise plusieurs exemples et l'anneau partiel est appris sur TRAIN.
TEST du nouveau layout, exécuté une seule fois : joueur 13/16 sans erreur, ennemis 35/45 sans
faux ; ancien layout inchangé (54/58). Le suivi reste non mesurable faute d'identités vérifiées.
LOT 3B-5 non finalisé, version 0.4.3.

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

## 15. Réparation layout-aware et détection partielle

LOT 3B-5B, 24 septembre 2026. Aucune nouvelle collecte ni nouvelle map. Aucun groupe TEST
déplacé. Aucun profil ni seuil appris sur TEST. Version conservée : **0.4.3**.

### 15.1 Git

- Branche `lot-3b-5-entity-tracking`, partie de `57d0753`. Le code 3B-5 n'avait jamais été commité.
- `8c8926c fix: make entity learning layout aware` : point de gel. Il contient le code 3B-5 et
  la réparation 3B-5B (le message inclut « fix: improve player and partial marker detection »).
  Les seuils étaient fixés avant tout passage sur VALIDATION ou TEST.
- `526bea0 perf: restrict partial ring evidence to band pixels` : optimisation seulement.
  Les sorties TRAIN sont identiques frame par frame (33/33).
- Aucun fichier `data/`, `dist/`, `build/`, `.venv/`, capture, template runtime ni log n'est commité.

### 15.2 Reproduction du blocage (snapshot isolé)

Snapshot `data/validation/lot3b5b-run2/corpus` : 944 fichiers, empreintes SHA-256 vérifiées.
Registre v1 recopié de `lot3b5-sequences-2`. Le corpus utilisateur ne contient aucun registre
entités. Contrôle final : ses 944 fichiers sont identiques au snapshot (0 différence).

| Layout | Digest | TRAIN (avant) | Baseline joueur | Baseline ennemis |
|---|---|---|---|---|
| nouveau | `9a0751d48681994e` | **0 groupe** | 0/27, 27 UNKNOWN | 0/66 |
| ancien | `e561118fd8115c8f` | 6 groupes | 1 bon, 1 mauvais / 21 TRAIN | 54/58, 0 faux |

La baseline attendue est reproduite exactement.

### 15.3 Couverture TRAIN par layout (§4–8)

- `cover_layouts` : un layout sans groupe TRAIN en reçoit un. L'ordre est déterministe :
  VALIDATION d'abord, puis non attribué, puis `sha256(layout|groupe)`. Un groupe TEST n'est
  jamais promu, même absent du corpus courant.
- Migration appliquée : `session_1f2858ce5c14|map88084225` (12 frames) passe de VALIDATION à TRAIN.
  `session_325b4d78d112|map88083713` (16 frames) **reste TEST**.
- Registre `schema_version: 2`. Chaque migration est tracée (`reason: layout_train_coverage`,
  `previous_split`, `new_split`, `layout_signature`) et l'historique est conservé. Fichier produit
  à part : `data/validation/lot3b5b-run2/entity_split_registry.v2.json`. Le corpus utilisateur
  n'est pas modifié.
- **Ce layout n'a temporairement plus de VALIDATION indépendante.** Les résultats TRAIN et TEST
  du nouveau layout sont rapportés séparément ci-dessous.

### 15.4 Profils d'équipe (TRAIN seulement, §21)

La classe d'équipe vient des anneaux complets s'il y en a au moins 3. Sinon, la teinte est
estimée par enrichissement : densité de pixels saturés dans l'anneau moins le maximum des densités
intérieure et extérieure, aux cellules annotées. Un corps de sprite coloré déborde vers
l'intérieur ; un trait d'anneau reste dans sa bande. La tolérance pixel est le p95 des écarts + 1.

| Layout | Équipe | Méthode | Échantillons | Teinte | Dispersion | Tol. anneau | Tol. pixel | Partiel TRAIN (retrouvés / contraires) |
|---|---|---|---|---|---|---|---|---|
| nouveau | joueur | anneaux complets | 9 | 3,78 | 0,76 | 8,0 | 4,78 | 3 / **15** → refusé |
| nouveau | ennemi | enrichissement | 21 cellules (0 anneau complet) | 120,52 | — | 8,0 | 3,52 | 18 / 0 → **autorisé** |
| ancien | joueur | anneaux complets | 10 | 2,20 | 1,11 | 8,0 | 11,80 | 0 / 0 → refusé (grille non enregistrée) |
| ancien | ennemi | anneaux complets | 47 | 118,84 | 0,91 | 8,0 | 3,84 | 0 / 0 → refusé (grille non enregistrée) |

L'anneau partiel de l'équipe joueur est refusé dans le nouveau layout. Il se déclenche sur 15
vérités ennemies : le corps des monstres étoiles porte du rouge. Les anciennes frames sont
antérieures à 3B-3 et n'ont pas d'état de grille enregistré. Le chemin partiel, qui exige une
grille fiable, n'y est donc jamais utilisé : l'ancien layout reste entièrement en anneau complet.

### 15.5 PlayerVisualProfileV2 (§9–14, §22)

- Prototypes : vecteurs (Lab pieds, Lab centre) des vérités PLAYER `human_confirmed` de TRAIN.
- Rejets motivés : `player_not_visible`, `grid_untrusted` (état refusé explicitement),
  `roi_abnormal`, `marker_unreadable`, `weak_evidence`, `marker_team_mismatch`,
  `duplicate_prototype`. Un état de grille non enregistré (ancien corpus) est accepté et compté.
- Décision : distance = min sur les prototypes de la moyenne ΔE (pieds, centre). PLAYER si
  distance ≤ 20 **et** écart ≥ 5 avec le candidat suivant de la même équipe. Sinon UNKNOWN :
  un allié proche du profil rend le cas ambigu. Le temporel ne sert jamais de vérité.
- Choix de 20 / 5 en leave-one-frame-out TRAIN (frames identiques exclues) : 0 mauvais joueur
  de 15 à 30 / marge 3 à 8. 20 / 5 donne nouveau 8 bons + 3 UNKNOWN, ancien 8 bons + 13 UNKNOWN.

| Layout | Acceptés | Rejets | Prototypes | Dispersion pieds / centre | Paires médiane / max |
|---|---|---|---|---|---|
| nouveau | 9 | marker_unreadable 2, duplicate_prototype 2 | 7 | 24,9 / 16,6 | 23,4 / 57,5 |
| ancien | 10 (état de grille non enregistré) | marker_unreadable 11, duplicate_prototype 2 | 8 | 37,4 / 20,9 | 50,4 / 65,6 |

Profils persistés par layout : `team_markers_<digest>.json` et `player_train_<digest>.json`.
L'observateur charge ceux du layout courant ; l'ancien `team_markers.json` reste lu en secours.

### 15.6 PARTIAL_RING (§15–19)

États : FULL_RING (chemin 3B-5 inchangé), PARTIAL_RING, sinon rien. Tous les seuils viennent de
TRAIN : distributions sur les vérités ENEMY/PLAYER/EMPTY_CONFIRMED ; les cellules non annotées ne
servent qu'au diagnostic.

- Pixels d'équipe : teinte à la tolérance pixel, S ≥ 135, V ≥ 105 (p5 des traits ennemis).
- Secteur cohérent (8 secteurs, cercle complet) : fraction anneau ≥ 0,05, dépasse
  max(intérieur, extérieur) de 0,03 **et** en vaut au moins le triple. Le p5 TRAIN du rapport est 7,1.
- Au moins 2 secteurs cohérents, dont deux séparés d'au moins 2 pas.
- Contradictions : un secteur rempli à plus de 0,77 (aplat), ou teinte d'équipe hors de l'anneau
  ≥ 0,05 (TRAIN max 0,023) ; décor ou sprite coloré.
- Conditions requises : grille fiable, profil d'équipe du layout, autorisation TRAIN de l'équipe,
  visibilité > 0,5, centre hétérogène (sprite, écart-type ≥ 8), un seul camp compatible.
  `peak_presence` n'est pas modifié.
- La couleur seule ne suffit jamais. Les fixtures couvrent l'aplat, l'arc sans sprite, le sprite
  coloré sans anneau, un fond parsemé à 3/10/35 % et des rayures.
- Coût : 21 ms par frame 2555×1151 après optimisation (77 ms avant), vectorisé sur les pixels des bandes.

### 15.7 Résultats

**TRAIN (profils appris sur ces frames : optimiste pour le joueur)**

| Layout | Joueur bon / mauvais / UNKNOWN | Ennemis exacts | Faux ennemis |
|---|---|---|---|
| nouveau (11 visibles, 21 ennemis) | 9 / **0** / 2 | 18/21 | 0 |
| ancien (21 visibles, 50 ennemis) | 9 / **0** / 12 | 46/50 | 0 |

Aucun joueur faux avec haute confiance. Leave-one-frame-out TRAIN : nouveau 8/0/3, ancien 8/0/13.

**Non-régression ancien layout (TRAIN + VALIDATION + TEST, §24)**

- Ennemis **54/58**, 0 faux : identique à la référence (46 + 4 + 4).
- Joueur 11/28 bons, **0 mauvais**, 17 UNKNOWN. Baseline : 1 bon, 1 mauvais.
- 0 faux FREE, précision OCCUPIED 1,000.

**TEST nouveau layout — exécuté une seule fois, code gelé `8c8926c` (§25–26)**

- Joueur **13/16**, **0 mauvais**, 3 UNKNOWN. Deux UNKNOWN sans candidat de l'équipe joueur ;
  un vrai joueur trouvé mais au-delà de la tolérance. Baseline : 0/16.
- Ennemis **35/45** exacts, **0 faux**, 0 UNKNOWN sur une vérité ennemie. Baseline : 0/45.
  Les 10 manqués ne donnent aucune preuve (ni ENEMY ni UNKNOWN).
- Cellules : 0 faux FREE sur PLAYER/ENEMY, précision OCCUPIED 1,000. 3 791 FREE sur des cellules
  sans annotation vide : exclus (`free_unlabelled`). Aucune vérité EMPTY_CONFIRMED sur ce layout,
  donc aucune précision FREE revendiquée.
- Aucun réglage n'a suivi ce passage. Les images TEST n'ont pas été ouvertes.

### 15.8 Tracking, horodatages, occlusion (§27–31)

- Horodatages réels : les 16 frames TEST utilisent `prediction.timestamp`. Replis explicites
  (interpolation entre ancres réelles, 0,4 s ancré, 0,4 s sans ancre), ordre non croissant ou
  NaN refusés. Tests : écart réel 1 s (saut de 8 cellules accepté, refusé au pas fictif 0,4 s),
  écart 4 s (piste perdue), ordre et valeurs manquantes.
- Provenance `tracking_identity` : aucune frame du corpus n'a de source d'identité explicite.
  Les anciennes identités renumérotées et les nouvelles séquences sont donc exclues des métriques
  (28 frames anciennes et 16 TEST exclues), et le suivi est `NOT_EVALUABLE`. Aucune ancienne
  identité n'est mélangée aux nouvelles.
- L'interface d'annotation ne permet pas encore de confirmer E1/E2/E3 comme identités suivies.
  Suite proposée, sans recollecte : ajouter cette revue à l'interface, puis la faire sur les
  deux séquences existantes. La détection étant désormais réparée, la mesure devient possible.
- Occlusion : aucune occultation annotée, NOT OBSERVED.

### 15.9 Performance (§33)

| Mesure (moyenne / max) | Nouveau layout | Ancien layout |
|---|---|---|
| detector_ms TRAIN, modèle de fond actif | 109 / 217 | 92 / 198 |
| tracker_ms | 0,1 / 0,2 | 0,1 / 0,3 |

Sur le TEST, avant l'optimisation `526bea0`, le détecteur mesurait 269 ms en moyenne. Les sorties TRAIN
sont identiques après optimisation ; le TEST n'est pas relancé pour mesurer le temps.

### 15.10 Non-régressions (§34) et tests (§35)

- GRID (snapshot, `nonreg/`) : 0/14 faux combats, 12/12 combats, 26/26 états de visibilité corrects, 12/12 alignés.
- HUD (snapshot) : précision acceptée 1,000 sur TRAIN/VALIDATION/TEST, 1/7 VALIDATED, PASS.
- `tests/test_entity_repair.py` : les 11 tests exigés, plus l'ordre et les manques d'horodatage,
  la barrière partielle, la persistance par layout et le profil multi-exemples sur corpus.
- **373 tests réussis**, compilation réussie.

### 15.11 Statuts

```text
IMPLEMENTATION: PASS
REAL ENTITY CORPUS: PARTIAL
PLAYER DETECTION: PARTIAL
ENEMY DETECTION: PARTIAL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
HUD REGRESSION: NONE
GRID REGRESSION: NONE
ACTIONS: NONE
```

- PLAYER : 0 mauvais partout, mais 17/28 UNKNOWN sur l'ancien layout (marqueurs illisibles).
- ENEMY : 0 faux, rappel TEST nouveau layout 35/45.
- TRACKING : implémenté, non mesurable faute d'identités vérifiées.
- CORPUS : pas de VALIDATION indépendante sur le nouveau layout, aucune vérité vide.

Le LOT 3B-5 **n'est pas finalisé**. Version 0.4.3, pas de 0.5.0, pas de LOT 3B-6.


### 15.12 Reprise et vérification du 25 septembre 2026

Au retour, HEAD était `3184517` (rapport), après `526bea0` et `8c8926c`.
Le working tree suivi était propre ; `.claude/` non suivi est conservé intact.
Les corrections de détection et leur unique TEST étaient déjà enregistrés. Aucune modification
supplémentaire du détecteur, du tracker, des profils ni de leurs seuils dans cette reprise.
Version conservée : **0.4.3**. Aucun nouveau commit de finalisation.

**Réception indépendante des artefacts.** Nouveau snapshot isolé
`data/validation/lot3b5b-reprise/corpus` ; les 944 empreintes du corpus utilisateur correspondent
au snapshot de référence. Les TEST ancien et nouveau gardent exactement leur groupe et split.
Le nouveau TEST n'a été ni rejoué ni inspecté visuellement. Son rapport précédent est conservé :
`data/validation/lot3b5b-run2/new-layout-test-once/entities.json`.

Complément chiffré du TEST déjà réalisé (aucune nouvelle mesure) :

| Mesure | Valeur |
|---|---:|
| Joueur correct / mauvais / UNKNOWN | 13 / 0 / 3 |
| Précision des positions joueur acceptées | 1,000 |
| Couverture joueur | 81,25 % |
| Ennemis exacts / vérités | 35 / 45 |
| Précision ennemis / rappel | 1,000 / 77,78 % |
| Erreur absolue moyenne du nombre d'ennemis par frame | 0,625 |
| Présences UNKNOWN sur ennemis annotés | 0 (10 ennemis sans présence détectée) |
| Faux FREE sur joueur ou ennemi annoté | 0 |
| Précision OCCUPIED sur vérités scorables | 1,000 |
| Cellules UNKNOWN dans le domaine analysé | 47,60 % |
| FREE sans annotation, exclus de la précision | 3 791 |

Ces scores de précision ne prouvent pas l'absence de faux positifs sur des cellules sans
vérité humaine. Aucun gain de couverture FREE n'est validé : zéro vérité vide sur ce layout.
La VALIDATION indépendante ancienne reste distincte : 1 joueur correct / 3 visibles,
0 mauvais, 2 UNKNOWN et 4 ennemis exacts / 4 ; elle n'a pas été utilisée comme TRAIN.

**Provenance de suivi corrigée.** Contrairement à la conclusion initiale de §15.8, la section 0
de la demande LOT 3B-5B fournit une confirmation explicite : « Identités E1/E2/E3 : cohérentes
dans ces nouvelles séquences. » Cette source, son SHA-256 et les 28 identifiants concernés sont
consignés dans `identity-provenance.json`. Seule la copie des annotations reçoit
`tracking_identity_confirmed=true` et la référence de cette demande. Les positions ne changent
pas ; les 28 anciennes annotations renumérotées restent non vérifiées pour le suivi.

Le replay **TRAIN uniquement** des 12 frames nouvelles retrouve les mêmes sorties, frame par
frame, que le rapport gelé : joueur 9 corrects / 0 mauvais / 2 UNKNOWN ; ennemis 18/21, 0 faux.
Suivi global : 18/21 observations d'identité appariées, 0 switch, 0 fragmentation selon la
métrique actuelle (identifiants prédits distincts moins un), 0 réassociation fausse. Ce résultat
sur une seule séquence TRAIN ne valide pas la généralisation du suivi.

Le suivi TEST demeure non mesurable depuis l'artefact existant : les cellules prédites sont
présentes, mais les identifiants de pistes n'y ont pas été conservés. On ne les reconstruit pas
à partir des vérités et on ne relance pas TEST. Le benchmark sauvegarde désormais les pistes
complètes et la référence gloutonne par frame, permettant une future réévaluation des métriques
sans nouvelle détection. La comparaison glouton/global n'isole pas l'affectation : les règles de
coût et de persistance diffèrent ; aucun gain causal n'est revendiqué.

**Interface de revue.** « Annoter les entités » propose maintenant une case confirmant que les
mêmes identifiants désignent les mêmes ennemis dans le combat. Elle recharge la provenance
existante, conserve les positions, et se décoche si un identifiant est modifié ou effacé.
Cette case n'est jamais déduite de la présence du texte E1. Aucune nouvelle collecte demandée.

**Vérifications de reprise :** 70 tests ciblés réussis ; **374 tests complets réussis** (49,74 s) ;
`compileall` et `git diff --check` réussis (avertissements CRLF uniquement).
GRID relancé : 0/14 faux combats, 12/12 combats, 26/26 visibilité, 12/12 alignement.
HUD avec RapidOCR relancé : 128 vérités humaines, PASS, précision acceptée 1,000,
1/7 VALIDATED. Les 56 crops sans vérité HUD restent exclus des mesures supervisées.

Performance mesurée sur le nouveau TRAIN : détecteur moyenne 94,78 ms, médiane 85,32 ms,
max 175,98 ms ; tracker moyenne 0,070 ms, max 0,100 ms. Les durées `observer_ms` restent
historiques, elles ne mesurent pas un lancement complet du nouvel observateur.

Profils appris exportés par layout dans `data/validation/lot3b5b-reprise/entity_profiles/`.
Ils sont disponibles pour le banc et le smoke isolé ; aucune installation silencieuse dans
les données AppData du logiciel en cours d'utilisation. Les annotations et profils de
l'utilisateur sont conservés. Les résultats décrivent un replay de captures, pas une
validation supplémentaire dans une partie DOFUS en direct.

**Limite logicielle héritée du point de gel :** les exemples de l'ancien layout sans état de
grille enregistré sont encore acceptés dans l'apprentissage du profil joueur, avec diagnostic
`grid_unrecorded`. Ils ne satisfont pas une exigence stricte de grille explicitement fiable.
Ce comportement de compatibilité n'est pas modifié après le passage TEST ; il empêche de
qualifier la totalité des exigences logicielles de PASS sans réserve. L'anneau partiel exige,
lui, une grille explicitement fiable.


**Build et lancement packagé de reprise.** Construction ONEDIR explicite via `PythonBot.spec`,
réussie (105 s). Taille totale : **333.35 Mio** ; exécutable : 9,319,970 octets.
PythonBot était encore ouvert dans `dist/PythonBot` : ce dossier et son processus sont conservés.
La version reconstruite est disponible par double-clic ici :

`C:\Users\Alpha5\Documents\pythonbot_test\dist\PythonBot-3B5B\PythonBot.exe`

Conserver tout le dossier ONEDIR autour de l'exécutable. Aucun déplacement du dossier de données
utilisateur n'est nécessaire. Fermer l'ancienne application avant d'ouvrir la nouvelle.
Le script `build_exe.ps1` habituel demeure disponible pour une reconstruction ultérieure dans
`dist/PythonBot` lorsque ce dossier n'est plus utilisé. Dans cette reprise, PyInstaller a été
invoqué avec le même spec, `--distpath` et `--workpath` isolés, pour préserver le programme ouvert.

Smoke réel depuis le `.exe`, puis après son déplacement au chemin livré : sortie 0 et
`success=true`. Qt **hors écran**, huit pages parcourues, SQLite (copie, un profil), dialogue
d'annotation et sa capture TRAIN chargés, OpenCV, grille 560 cellules, HUD 7, détecteur et suivi
sur fixture synthétique. RapidOCR lit « 3 PA Portee 1-4 » (confiance 0,9995).
PE subsystem 2 confirmé (application graphique). Aucune fenêtre console affichée par le lancement
masqué du smoke ; une vérification visuelle du double-clic sur le bureau n'est pas revendiquée.
Détection et capture de fenêtre DOFUS volontairement désactivées dans ce smoke : **non testées**
pendant cette reprise (le message générique « aucune fenêtre détectée » ne décrit pas une recherche
effectuée). Aucune action dans le jeu.

Warnings PyInstaller : tkinter absent/exclu (l'UI utilise PySide6), échappements invalides dans
le backend X11 optionnel de PyAutoGUI et un module PyTorch optionnel de RapidOCR ; liste des imports
conditionnels non résolus dans `build/PythonBot/warn-PythonBot.txt` sous le dossier de validation.
L'OCR ONNX et Qt ont réellement fonctionné depuis le binaire livré.

Fichiers modifiés dans cette reprise : `combatbot/corpus/entity_benchmark.py`,
`combatbot/ui/entity_annotation_dialog.py`, `tests/test_entity_corpus.py`,
`tests/test_entity_repair.py`, et ce rapport. Les preuves, profils, copies de données et build sont
sous `data/validation/lot3b5b-reprise/` (hors Git). Aucun fichier client modifié.

### 15.13 Statuts à la fin de la reprise

```text
IMPLEMENTATION: PARTIAL
REAL ENTITY CORPUS: PARTIAL
PLAYER DETECTION: PARTIAL
ENEMY DETECTION: PARTIAL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
HUD REGRESSION: NONE
GRID REGRESSION: NONE
ACTIONS: NONE
```

La réparation améliore la détection mesurée, mais la couverture reste incomplète. La compatibilité
avec les anciennes grilles non renseignées reste une limite d'apprentissage, et le tracking TEST
n'est pas validé. **LOT 3B-5 non finalisé**, version 0.4.3 conservée. Aucun LOT 3B-6,
aucune nouvelle collecte et aucun combat automatique.


### 15.14 Mesure du suivi après revue humaine des identités — 25 septembre 2026

**Revue dans l'application (`dist/PythonBot`, commit `0c72484`).** L'utilisateur a confirmé
les deux séquences du nouveau layout via « Enregistrer la confirmation de la séquence » :
`session_1f2858ce5c14|map88084225` (12 frames, TRAIN) et `session_325b4d78d112|map88083713`
(16 frames, TEST). Source `human_ui_review`, horodatée. Les 28 anciennes frames renumérotées
restent non confirmées et exclues. Comparaison au snapshot `lot3b5b-run2` : sur les 28 annotations
réécrites, seuls les champs d'identité de suivi et `entity_confirmed_at` changent, aucune position.

Défaut d'interface constaté : la case de confirmation se recharge à chaque changement de frame.
Cochée puis suivie d'une navigation, elle est donc décochée au clic sur le bouton, sans
avertissement. Contournement utilisé : cocher puis enregistrer sans changer de frame. Pas encore
corrigé.

**Mesure.** Snapshot `data/validation/lot3b5c-tracking/corpus` (copie du corpus utilisateur +
registre v2 gelé de `lot3b5b-reprise`). Pipeline gelé, profils TRAIN, aucun réglage.
La détection TEST est **identique frame par frame (16/16)** au passage unique gelé
`new-layout-test-once` : aucune nouvelle décision de détection. Horodatages réels
(`prediction.timestamp`) : écart médian 1,2 s en TRAIN et 1,7 s en TEST, maximum 4,1 s.

| Portée | Observations d'identité | Associations exactes | ID switches | Fragmentations | Réassociations fausses | Couverture |
|---|---:|---:|---:|---:|---:|---:|
| TRAIN (12 frames), global | 21 | 18 | 0 | 2 | 0 | 85,7 % |
| **TEST (16 frames), global** | 45 | 35 | **3** | 5 | **2** | 77,8 % |
| TEST, référence gloutonne | 45 | 35 | 9 | 5 | 2 | 77,8 % |

- Les associations manquantes viennent des ennemis non détectés (35/45) : le tracker ne crée
  pas de piste sans détection.
- Le suivi global fait moins de switches que la référence gloutonne sur les mêmes détections
  (3 contre 9). Les règles de coût et de persistance diffèrent : aucun gain causal n'est revendiqué.
- 3 switches et 2 réassociations fausses sur 35 associations TEST : le suivi n'est pas sans erreur.
  Ces cas ne sont ni inspectés ni corrigés à partir de TEST.
- Occlusion : aucune occultation annotée, NOT OBSERVED.
- Performance de ce passage : détecteur 182 ms en moyenne (max 498 ms), tracker 0,10 ms.

```text
GLOBAL TRACKING: PARTIAL (mesuré : TEST 3 switches, 2 réassociations fausses / 35 associations)
OCCLUSION HANDLING: NOT OBSERVED
ACTIONS: NONE
```

Les autres statuts de 15.13 sont inchangés. **LOT 3B-5 non finalisé**, version 0.4.3.


### 15.15 Correctif UI et activation runtime réelle

LOT 3B-5C-FIX, 25 septembre 2026. Détecteur et tracker non modifiés, aucun seuil changé, TEST
non réutilisé, aucune nouvelle collecte. Version **0.4.3**.

**Git initial.** Branche `lot-3b-5-entity-tracking`, HEAD
`b8d86fc080983ff9cb61fe221a69d23c75762367` (`b8d86fc`). Working tree propre, aucun fichier
modifié ni non suivi. `git diff --check` propre.

**Bug UI reproduit et cause.** Dans « Annoter les entités », la case de confirmation de suivi
était relue depuis le corpus à chaque changement de frame. Cochée puis suivie d'une navigation,
elle était décochée au clic sur « Enregistrer la confirmation de la séquence ». Le premier essai
réel de l'utilisateur a été perdu ainsi, sans message. Un premier correctif (`df43418`)
conservait la coche pendant la navigation. Ce sous-lot le complète :

- état local **par séquence** (`tracking_sequence_id`) : la valeur non enregistrée survit à la
  navigation entre les frames de la séquence ;
- état « dirty » explicite (`tracking_confirmation_dirty()`) : valeur locale différente de la
  valeur enregistrée. Une ligne d'état l'affiche sous la case (enregistrée, cochée non
  enregistrée, retrait non enregistré, invalidée) ;
- en quittant une séquence avec une modification non enregistrée (sélecteur, frame suivante qui
  change de séquence, fermeture) : « Vous avez une modification de confirmation non
  enregistrée. », avec [Enregistrer] [Ignorer] [Annuler]. Annuler garde la séquence et la coche ;
  rien n'est perdu en silence ;
- modification, effacement ou déplacement d'une identité E1…E8 : case décochée et message
  « Confirmation de suivi invalidée après modification des identités. » ;
- bouton sans case cochée : avertissement, aucune écriture ;
- écriture de séquence inchangée : toutes les annotations de la séquence, rollback complet en cas
  d'échec.

**Tests UI** (`tests/test_tracking_review_ui.py`, 7/7) :
`test_tracking_checkbox_survives_frame_navigation_before_save`,
`test_tracking_confirmation_is_sequence_scoped`,
`test_switch_sequence_warns_when_tracking_confirmation_dirty`,
`test_save_tracking_confirmation_clears_dirty_state`,
`test_editing_enemy_id_invalidates_sequence_confirmation`,
`test_failed_sequence_confirmation_rolls_back_all_annotations` (échec simulé à la 3ᵉ écriture :
octets identiques à l'origine, séquence non confirmée),
`test_old_unverified_sequence_remains_unverified`.

**État runtime avant.** `%LOCALAPPDATA%\PythonBot\data\entity_profiles` : `active.json` présent,
génération active `20260925-133324-6172ee19`, seule génération. Aucun ancien fichier racine
(`team_markers.json`, `player_<id>.json`). Six fichiers, tous valides (`validate_payload`) :

| Fichier | SHA-256 (16) | Contenu |
|---|---|---|
| `player_train_9a0751d48681994e.json` | `1b232415d7687fa4` | V2, human_confirmed, 9 exemples, 7 prototypes, tol. 20 / marge 5 |
| `player_train_e561118fd8115c8f.json` | `07c80853f00bf79d` | V2, human_confirmed, 10 exemples, 8 prototypes, tol. 20 / marge 5 |
| `team_markers_9a0751d48681994e.json` | `d73b1b26e17ec93b` | v1, joueur 3,78, ennemi 120,52, partiel ennemi autorisé |
| `team_markers_e561118fd8115c8f.json` | `5f36b11df6767dc6` | v1, joueur 2,20, ennemi 118,84, partiel refusé |
| `entity_split_registry.v2.json` | `7376dfad9c9aa412` | v2, 10 groupes, 1 migration, TEST gelé conservé |
| `installation.json` | `d5689612c23d7d52` | provenance TRAIN |

**Dry-run, sans écriture.** `prepare_installation` sur le corpus runtime avec le registre actif :
les six fichiers produits sont **identiques** aux fichiers actifs, hors dates et empreinte
corpus. Pourtant le corpus a reçu depuis 13 h 33 les confirmations d'identité et des vérités
HUD : ces changements n'affectent pas les profils TRAIN. Corpus runtime avant et après : 980
fichiers, 0 différence.

```text
RUNTIME INSTALL: ALREADY ACTIVE
```

Aucune réinstallation. Sauvegarde `data\backup\entity-runtime-20260925-133324-6172ee19` :
`backup.json` et registre vide. Elle décrit l'état antérieur à la première installation :
aucun pointeur, aucun profil. Empreintes de la sauvegarde intactes. Une restauration retirerait
le pointeur (vérifié sans restauration réelle).

**Pointeur atomique.** Un correctif de chargement : `load_team_profile` pouvait compléter une
génération active privée de profil d'équipe par l'ancien `team_markers.json` racine, donc mélanger
deux générations. Avec `active.json`, ce repli est désormais interdit ; il reste seulement sans
génération active (compatibilité 3B-5). Test ajouté
`test_runtime_pointer_failures_are_prudent`. Génération inexistante, `active.json` illisible,
chemin hors des générations et JSON non objet donnent tous zéro profil. Un profil joueur corrompu
donne un joueur absent. Un profil d'équipe manquant donne une équipe absente, sans repli.

**Chargement réel par RealCombatObserver** (`data/validation/lot3b5c-fix/runtime-load-proof.json`).
Stockage réel `%LOCALAPPDATA%\PythonBot\data`, calibration réelle du profil 1 lue en lecture seule,
profils chargés par le constructeur `RealCombatObserver` (donc `_load_profiles`) :

```text
LAYOUT: 9a0751d48681994e            LAYOUT: e561118fd8115c8f
PLAYER PROFILE: player_train_9a0751d48681994e.json    PLAYER PROFILE: player_train_e561118fd8115c8f.json
PLAYER PROFILE VERSION: 2           PLAYER PROFILE VERSION: 2
PLAYER SAMPLES: 9 (7 prototypes)    PLAYER SAMPLES: 10 (8 prototypes)
TEAM PROFILE: team_markers_9a0751d48681994e.json      TEAM PROFILE: team_markers_e561118fd8115c8f.json
LOAD STATUS: PASS                   LOAD STATUS: PASS
PROFILE ROOT: C:\Users\Alpha5\AppData\Local\PythonBot\data\entity_profiles\generations\20260925-133324-6172ee19
```

- La calibration courante de l'utilisateur correspond au nouveau layout `9a0751d48681994e` et
  charge ses profils (PASS).
- Layout inconnu : aucun profil chargé (`NO_PROFILE`), aucun profil d'un autre layout.
- Aucun repli caché : il n'existe aucune désignation manuelle `player_<id>.json` dans les
  données. Si une désignation existait pour un autre layout, elle serait écartée par le contrôle
  de compatibilité.
- Stockage inchangé pendant la preuve : profils (7 fichiers), corpus (980), gabarits HUD (21),
  base SQLite.

**Build canonique et smoke.** `.\build_exe.ps1` : `dist\PythonBot\PythonBot.exe`, 339,1 Mio.
Smoke `--package-smoke-test` depuis cet exécutable, sur une copie des données runtime vérifiée
identique fichier par fichier (corpus, profils, gabarits HUD, GameData, SQLite) :
`success=true`, `frozen=true`.

- Génération active et deux layouts chargés par `RealCombatObserver`, PASS ; layout inconnu :
  aucun profil.
- `PlayerVisualProfileV2`, `TeamMarkerProfile`, `CellEntityDetector`, `EntityTracker` présents.
- UI de suivi importable, séquence-scopée, message d'avertissement présent.
- Grille GameData 560 cellules (`GridProjector`), HUD (fixture 7), RapidOCR (« 3 PA Portee 1-4 »,
  0,9995), `action_executor_invoked=false`.
- Détection de fenêtre DOFUS volontairement désactivée : la fenêtre du jeu n'est pas activée.
  Le libellé « aucune fenêtre détectée » du rapport désigne cette désactivation, pas une recherche.

**Non-régressions**, sur une copie fidèle du corpus runtime (980 fichiers identiques). Lancé
directement sur le corpus runtime, le banc HUD réécrirait `hud_manifest.json`, le registre HUD
et les gabarits installés, ce qui contredit le corpus immuable. Seul le `hud_manifest.json` de la
copie a changé ; celui du corpus runtime reste daté du 24/09 14 h 38.

- GRID : exploration faux combat 0/14, combat 12/12, visibilité 26/26, alignement 12/12.
- HUD : 142 vérités humaines, précision acceptée 1,000 en TRAIN, VALIDATION et TEST
  (spécialisé et combiné), 0 lecture acceptée fausse, 1/7 VALIDATED, PASS. Gabarits recalculés
  identiques aux gabarits installés.

**Tracking gelé.** Aucune modification du tracker, des coûts, de la persistance ni des seuils.
Aucun bug métrique trouvé. Mesures conservées : TEST 35/45 associations, 3 ID switches,
5 fragmentations, 2 réassociations fausses ; référence gloutonne 9 switches.

**Tests.** `test_entity_runtime` 8, `test_entity_corpus` 18, `test_entity_tracking` 18,
`test_entity_repair` 15, `test_tracking_review_ui` 7 ; suite complète **391 réussis** ;
`compileall` et `git diff --check` réussis.

**Limites.** Détection et suivi inchangés, donc toujours PARTIAL. Aucune occultation réelle.
Aucune vérité EMPTY suffisante. Nouveau layout sans VALIDATION indépendante. Le 4 PM du HUD n'a
pas d'exemple hors TRAIN. Aucune vérification visuelle en partie réelle dans ce sous-lot.

```text
IMPLEMENTATION: PASS
REAL ENTITY CORPUS: PARTIAL
PLAYER DETECTION: PARTIAL
ENEMY DETECTION: PARTIAL
GLOBAL TRACKING: PARTIAL
OCCLUSION HANDLING: NOT OBSERVED
CELL OCCUPANCY: PARTIAL
HUD REGRESSION: NONE
GRID REGRESSION: NONE
RUNTIME PROFILES: PASS
TRACKING UI: PASS
ACTIONS: NONE
```

IMPLEMENTATION PASS porte sur ce sous-lot seulement. LOT 3B-5 non finalisé, pas de 0.5.0,
pas de LOT 3B-6. Décision suivante à prendre par l'utilisateur : (A) améliorer 3B-5 sur de
nouvelles données indépendantes, ou (B) accepter 3B-5 en PARTIAL prudent et ouvrir 3B-6 en
lecture seule.

## 16. 3B-5D — validation indépendante sur PC local

Reprise du 25 septembre 2026 sur un second PC (compte Windows `Thoma`). Version **0.4.3**
inchangée. Détecteur, tracker, seuils, coûts et profils **non modifiés**. Aucune action DOFUS.

### 16.1 Récupération Git et environnement

- `C:\Users\Thoma\Documents\pythonbot_test` absent : clone de `alexan-t/botdofus2`
  (authentification Git déjà configurée). Aucun dossier local écrasé. L'ancien dossier
  `OneDrive\Documents\pythonbot_test` de ce PC est vide.
- `origin/main` = `origin/lot-3b-5-entity-tracking` = `8a324a2` (aucun commit plus récent) ;
  `22e1f35`, `5812af5`, `577d089`, `8a324a2` ancêtres de HEAD ; `pull --ff-only` sans changement.
- Branche `lot-3b-5d-independent-validation` créée depuis `main` propre.
- Aucun Python global utilisable (alias Microsoft Store, `py` sans interpréteur). `.venv` créé
  avec le seul Python 3.12 présent (3.12.14, runtime local sous `~/.cache/codex-runtimes`),
  puis `requirements-dev.txt` installé dans le venv uniquement (PySide6 6.11.2, OpenCV 4.14,
  NumPy 2.5.3, RapidOCR 3.9.2, onnxruntime 1.30, PyInstaller 6.22.3). Requirements inchangés.
- Baseline avant développement : **391 tests réussis**, `compileall` et `git diff --check` OK.

### 16.2 Client local

- `C:\Users\Thoma\AppData\Local\Alea\Client` : présent, `Dofus.exe` (188 704 o, SHA-256
  `206e91fc…e56835`), `data/` et `content/maps` présents.
- Topologie GameData chargée en lecture seule : 12 154 maps, map témoin 560 cellules, 1,7 s.
- Chemin enregistré par le mécanisme existant (réglage `dofus_client_directory` de
  `%LOCALAPPDATA%\PythonBot\data\pythonbot.sqlite3`). Aucun chemin codé en dur ; aucune
  occurrence de `Alpha5` dans le code.

### 16.3 Runtime local avant 3B-5D

`%LOCALAPPDATA%\PythonBot` **n'existait pas** : pas de SQLite, corpus (0 fichier,
0 observation), gabarits HUD, `entity_profiles`, `active.json`, layout connu ni calibration.
Rien à hacher ; l'inventaire est dans `data/validation/lot3b5d-local-resume/runtime-before.json`
(ignoré par Git). La seule écriture runtime de cette reprise est la création de la base SQLite
avec le réglage du dossier client. Aucun profil de l'autre PC importé.

Conséquences :
- **Layout local : inconnu tant qu'aucune calibration n'est faite sur ce PC** (la signature
  dépend des zones calibrées). Même s'il égalait `e561118fd8115c8f` ou `9a0751d48681994e`
  (cas A), aucun profil compatible n'existe localement : on traite donc le cas B — nouveau
  TRAIN, VALIDATION puis TEST propres à ce PC.
- Le TEST historique (16 frames) et les corpus GRID/HUD de référence ne sont pas sur ce PC :
  ils ne peuvent être ni réutilisés ni rejoués ici.

### 16.4 Build initial

`.\build_exe.ps1` : `dist\PythonBot\PythonBot.exe`, 342,2 Mio. Smoke `--package-smoke-test`
(données isolées, fenêtre DOFUS non recherchée) : `success=true`, `frozen=true`, grille GameData
560 cellules, HUD fixture 7, RapidOCR « 3 PA Portee 1-4 » (0,9995), détecteur/tracker/UI de
suivi présents, `action_executor_invoked=false`, `action_executed=false`.

### 16.5 Outillage 3B-5D (commit `de260f5`)

- **Vérités EMPTY aveugles** (`combatbot/corpus/empty_sampling.py`) : par frame, 9 cellules
  traversables entièrement visibles, une par zone 3 × 3 (haut/centre/bas × gauche/centre/droite),
  ordonnées par SHA-256 de (version, observation, cell ID). La sélection ne lit ni prédiction ni
  annotation. Dans « Annoter les entités », elles apparaissent en jaune « ? » ; menu
  « Échantillon : VIDE / OCCUPÉE / INCONNU ». Une cellule annotée JOUEUR/E1… vaut OCCUPÉE.
  « Confirmer cette frame » est refusé tant qu'une cellule jaune n'est pas tranchée. Stockage :
  `sampled_cells_truth` + `sampled_cells_version` (les VIDE alimentent aussi
  `empty_confirmed_cells`). L'outil n'affiche toujours aucune prédiction.
- **Split déclaré par combat** : liste « Split : TRAIN / VALIDATION / TEST » à côté de
  « Enregistrer la séquence », figée pendant l'observation, écrite dans chaque observation
  (`capture.entity_split_declared`). Le split déclaré s'impose ; la couverture de layout ne
  déplace jamais un combat déclaré ; un combat aux frames de splits différents, ou en désaccord
  avec le registre, est une erreur.
- **Mesures** : occupation sur vérités humaines seulement (EMPTY échantillonnées ; OCCUPÉES
  échantillonnées + cellules JOUEUR/ENNEMI) : FREE correct, faux FREE, faux FREE sur
  joueur/ennemi, UNKNOWN, précision et couverture FREE. Suivi : portée VALIDATION, taux de
  switch et de réassociation fausse. Performance : P95 et effectifs.
- **Critères fixés avant TEST** (`combatbot/corpus/validation_3b5d.py`) : joueur précision
  acceptée 1,000 et couverture ≥ 0,90 ; ennemis précision ≥ 0,98 et rappel ≥ 0,90 ; suivi
  switch ≤ 5 % et réassociation fausse ≤ 5 % ; occupation 0 faux FREE sur joueur/ennemi,
  précision FREE ≥ 0,98, au moins 50 vérités EMPTY sinon PARTIAL.
- **Garde TEST** : `python -m combatbot.benchmark --entities-3b5d --entity-splits test
  --freeze-sha <SHA>` refuse si `combatbot/` diffère du commit de gel ou si ces combats TEST
  ont déjà été mesurés (registre `entity_test_runs.json` ; `--diagnostic-rerun` marque la
  mesure « diagnostic », plus jamais TEST indépendant).
- Installation runtime des profils sans registre historique : seuls les combats déclarés
  sont lus, profils appris sur TRAIN uniquement.
- Tests : 12 nouveaux (`tests/test_entity_3b5d.py`) ; 4 tests UI existants tranchent désormais
  l'échantillon avant d'enregistrer ; suite complète **403 réussis** ; `compileall` et
  `git diff --check` OK. Rebuild + smoke : `success=true`, mêmes contrôles qu'en 16.4.

### 16.6 Corpus, splits et mesures

| Élément | État |
|---|---|
| Combats TRAIN / VALIDATION / TEST | 0 / 0 / 0 — collecte à faire par l'utilisateur |
| Frames, vérités PLAYER / ENEMY / identités / EMPTY | 0 |
| SHA de gel | à créer après TRAIN/VALIDATION |
| TEST final | non commencé |
| Performance locale (detector/tracker/observer) | non mesurable sans corpus local |

### 16.7 Non-régressions sur ce PC

- GRID : tests unitaires et smoke (projection 560 cellules, visibilité/alignement synthétiques)
  OK. Le corpus GRID de référence (14/12/26/12) n'est pas sur ce PC : pas de nouvelle mesure réelle.
- HUD : tests et fixture 7 OK ; corpus HUD de référence absent : 1/7 reste validé sur l'autre PC,
  non remesuré ici. Les nouvelles mesures locales seront rapportées séparément.

### 16.8 Statuts à ce point d'arrêt

```text
IMPLEMENTATION: PASS (outillage 3B-5D)
RUNTIME PROFILES: PASS (hérité 3B-5C-FIX ; aucun profil local — nouveau layout à apprendre)
TRACKING UI: PASS
REAL ENTITY CORPUS (PC local): EMPTY — collecte requise
PLAYER DETECTION: PARTIAL (inchangé, aucune donnée indépendante)
ENEMY DETECTION: PARTIAL (inchangé)
GLOBAL TRACKING: PARTIAL (inchangé)
CELL OCCUPANCY: PARTIAL (mécanisme EMPTY prêt, 0 vérité)
OCCLUSION HANDLING: NOT OBSERVED
GRID REGRESSION: NONE (tests/smoke ; corpus de référence absent de ce PC)
HUD REGRESSION: NONE (tests/smoke ; corpus de référence absent de ce PC)
ACTIONS: NONE
```

LOT 3B-5 non clôturé, pas de 0.5.0, pas de 3B-6. Prochaine étape : calibration locale puis
collecte TRAIN (≥ 2 combats) et VALIDATION (≥ 1 combat) par l'utilisateur, annotation, puis
profils TRAIN, diagnostic VALIDATION et gel.
