# PythonBot — LOT 3B-2A-R : Game Data validée sur client réel (0.3.3-gamedata.1)

Application Windows/PySide6 avec le **simulateur du LOT 1**, la calibration et le scan de sorts du **LOT 2**, puis une session d'**observation réelle en lecture seule** pour le LOT 3. L'observateur analyse uniquement les pixels de la fenêtre choisie. Il ne lance aucun sort, ne déplace aucun personnage et n'envoie aucun clic au client. Il ne lit ni la mémoire du processus ni le réseau.

Le LOT 3B-0 ajoute un corpus volontaire, une annotation humaine indépendante et un banc de mesure. Le LOT 3B-1 formalise les référentiels géométriques sans modifier les algorithmes de grille, de PA/PM, de détection ou de suivi du LOT 3.

## Données locales du client — probe expérimental

Dans **Paramètres → Données du client**, sélectionnez explicitement le dossier du
client privé avec **Parcourir…**, puis cliquez sur **Analyser**. Ce chemin est
enregistré dans la configuration SQLite. Aucun disque n'est parcouru à sa place.
Sans dossier, le message est **Dossier du client DOFUS non configuré**.

Le probe inventorie les fichiers, contrôle les index D2P 2.1 (deux dispositions
réelles : données→index et index→données) et D2O, puis permet de charger
manuellement un map ID. Le lecteur DLM v11 non chiffré lit à l'octet près
12 153 des 12 154 maps du client privé 2.64.5 ; les versions inconnues sont
refusées avec un diagnostic `DLM_PARSE_ERROR`. **Valider toutes les maps** parcourt
toutes les maps en tâche de fond (annulable) et affiche versions, chiffrement,
nombre de cellules et critères de topologie. La topologie exposée est **logique**
(cellId ↔ (x, y), voisinage) ; elle ne dit rien des pixels. Les indices rouge/bleu
ne sont pas déclarés cellules de placement. La grille visuelle, GridCalibration
et LayoutTransform restent indépendants de ce module.

L'analyse s'exécute dans un worker. Les index sont mis en cache selon les chemins,
tailles et dates ; les maps sont chargées à la demande. Les rapports, caches et
exports JSON sont écrits dans le dossier de données de PythonBot, sous
`data/gamedata/`, jamais dans le client. Le bouton **Exporter JSON** produit
`data/gamedata/debug/map_<id>.json`. Seules les maps effectivement chargées sont
comptées comme lisibles ; une entrée d'index ne suffit pas.

En ligne de commande après activation de l'environnement Python :

```powershell
python -m combatbot.gamedata --client "CHEMIN_CHOISI" --map-id 123 --output data/gamedata/diagnostic
python scripts/benchmark_gamedata.py
python scripts/validate_gamedata_real.py --client "CHEMIN_CHOISI"
```

La deuxième commande crée uniquement un jeu de données synthétique sous `data/`
et mesure le scan, l'index, le cache et le chargement. Elle ne démontre aucune
compatibilité avec des fichiers propriétaires. La troisième lit le client en
lecture seule et écrit uniquement des diagnostics JSON sous
`data/gamedata/real-validation/`. Voir [GAME-DATA-REAL-VALIDATION.md](GAME-DATA-REAL-VALIDATION.md),
[GAME-DATA-REPORT.md](GAME-DATA-REPORT.md) et [GAME-DATA-SOURCES.md](GAME-DATA-SOURCES.md)
pour les résultats, licences et limites.
Le LOT 3B-2 n'est pas commencé.

## Conventions géométriques

Les calculs de vision utilisent les pixels physiques de l'image capturée :

- `ScreenPoint` part de l'origine du bureau virtuel Windows ;
- `ClientPoint(0, 0)` est le coin supérieur gauche du contenu client, hors bordure et barre de titre ;
- `NormalizedPoint` utilise le client complet entre `0` et `1` ;
- `CombatPoint(0, 0)` est le coin supérieur gauche de la zone combat calibrée ;
- `Cell(x, y)` est une coordonnée logique et ne représente jamais un pixel.

`LayoutTransform` centralise les conversions écran, client, normalisé et combat. Les transformations gardent des flottants ; la conversion finale en indice pixel emploie `round()`. La capture et la géométrie Win32 sont déjà en pixels physiques : le DPI est conservé comme métadonnée et n'est pas appliqué une seconde fois. Dans la calibration, une unité de `QGraphicsScene` correspond à un pixel de l'image cliente.

Les nouvelles signatures de disposition sont des JSON versionnés enregistrés dans la colonne SQLite `layout_signature` existante. Elles ignorent le HWND et la position de la fenêtre. Une ancienne signature ou un ancien hash reste chargé avec ses zones, puis l'interface demande leur revalidation avant l'observation réelle. Aucune migration SQL supplémentaire n'est nécessaire pour le LOT 3B-1.

## Installation et lancement

Python 3.12 ou plus récent et une session Windows graphique sont requis. Depuis ce dossier :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
python main.py
```

La capture utilise PyAutoGUI, la segmentation OpenCV et la lecture de texte RapidOCR/ONNX Runtime en local. Le premier OCR peut prendre quelques secondes pour charger les modèles. Les fichiers de modèles sont fournis par le paquet installé dans `.venv`.

Le bouton **Plein écran** en bas de la barre latérale ou la touche **F11** bascule l'application en plein écran. **Échap** en sort et retrouve la taille précédente de la fenêtre. La fenêtre de calibration s'ouvre directement en plein écran pour agrandir la capture ; son bouton **Mode fenêtre** ou **F11** permet de changer de mode.

## Exécutable Windows

Depuis PowerShell, à la racine du projet :

```powershell
.\build_exe.ps1
```

Le script vérifie `.venv`, installe PyInstaller si sa version n'est pas compatible, nettoie les anciens résultats et construit le package ONEDIR décrit par `PythonBot.spec`. Le programme final est `dist\PythonBot\PythonBot.exe` et se lance par double-clic sans console. Si `assets\pythonbot.ico` existe, elle est automatiquement utilisée ; sinon le build conserve l'icône Windows par défaut.

La version packagée conserve ses données dans `%LOCALAPPDATA%\PythonBot\data\pythonbot.sqlite3` et ses diagnostics dans `%LOCALAPPDATA%\PythonBot\logs\pythonbot.log`. Au premier lancement, si aucune base locale n'existe et que l'exécutable se trouve encore dans le dossier `dist` de ce projet, la base `data\pythonbot.sqlite3` est copiée de façon atomique avant les migrations. Une base locale déjà présente est toujours prioritaire et n'est pas remplacée. En développement, `python main.py` continue d'utiliser les dossiers `data\` et `logs\` du projet.

Le package embarque explicitement les trois modèles ONNX, les configurations YAML de RapidOCR, le moteur dynamique RapidOCR ONNX Runtime et les DLL ONNX Runtime. Les moteurs OCR facultatifs Paddle, Torch, OpenVINO, TensorRT et MNN sont exclus.

## Parcours d'observation

1. **Paramètres → Client DOFUS** : choisissez un profil, cliquez sur **Actualiser**, sélectionnez la fenêtre dont le titre contient « DOFUS » et cliquez sur **Connecter**. L'application affiche le handle et la taille de la zone cliente. Une fenêtre fermée ou minimisée est signalée. Le handle est enregistré avec le profil, mais une reconnexion manuelle est nécessaire après un nouveau lancement du client.
2. Cliquez sur **Capture d'écran** pour afficher un diagnostic. PythonBot se masque brièvement et tente de placer la fenêtre choisie au premier plan. Il essaie d'abord une capture de la fenêtre par handle, recadrée à la zone cliente ; si elle échoue, il capture la portion visible du bureau. Le résultat apparaît dans l'aperçu. Windows peut refuser l'activation ; l'application le signale. La capture par handle peut être vide pour certains rendus de jeu et le repli visible peut contenir une fenêtre qui recouvre DOFUS : vérifiez toujours l'aperçu.
3. Cliquez sur **Calibrer / Recalibrer**. Déplacez et redimensionnez les six rectangles obligatoires sur la capture : combat, barre de sorts, PV, PA, PM et fin de tour. Le carré en bas à droite redimensionne chaque rectangle. La zone supplémentaire nom/classe est facultative. La calibration est enregistrée relativement à la zone cliente ; un fort changement de taille ou de rapport largeur/hauteur l'invalide et demande une nouvelle calibration. Un déplacement interne de l'interface sans changement de taille peut aussi nécessiter une recalibration manuelle.
4. **Sorts → Scanner le client** : indiquez explicitement la page affichée, le nombre de colonnes et de rangées, puis le seuil de reconnaissance. Délimitez uniquement les icônes de la barre dans la calibration : excluez les commandes de pagination. Cliquez sur **Scanner mes sorts**. Les cases vides et doublons de la page sont signalés ; les icônes inconnues n'obtiennent aucun nom inventé. Une page supplémentaire se scanne après l'avoir sélectionnée vous-même dans DOFUS et avoir changé le numéro de page dans PythonBot.
5. Sélectionnez une icône scannée, puis cliquez sur **Analyser l'infobulle (3 s)**. PythonBot se masque ; survolez le sort dans DOFUS pendant le compte à rebours. Délimitez ensuite l'infobulle sur la capture. L'OCR propose uniquement les valeurs qu'il a pu lire et conserve les autres comme **Inconnu**. Vérifiez et corrigez tous les champs dans le formulaire.
6. Cliquez sur **Valider ce sort** ou **Valider les sélectionnés** une fois les champs requis renseignés. **Enregistrer configuration** conserve les modifications en statut « À vérifier » ; **Ignorer l'emplacement** supprime le résultat correspondant. Seules les icônes « Confirmé » servent de références visuelles lors des scans ultérieurs.
7. **Paramètres → Lire le profil visible** propose les PV, PA, PM et, si la zone facultative contient des libellés explicites, le nom et la classe. Les champs incertains restent vides. Corrigez-les puis cliquez sur **Enregistrer le profil**. Chaque profil a sa calibration, ses scans et ses paramètres de reconnaissance.

La reconnaissance visuelle ne déduit jamais les caractéristiques à partir d'une icône. Un sort scanné reste dans les tables de profil ; il n'est **jamais injecté** dans les sorts du simulateur de combat. Le tableau de bord continue de lancer uniquement la simulation du LOT 1.

## Recette manuelle du LOT 3

1. Lancez DOFUS, puis lancez PythonBot. Dans **Paramètres**, choisissez le profil, actualisez la liste des fenêtres et connectez le client DOFUS.
2. Vérifiez l'aperçu et cliquez sur **Confirmer la capture affichée**. Recalibrez si nécessaire. La zone **Combat** doit être présente et confirmée ; les zones **PA**, **PM**, **Fin de tour** et **Barre de sorts** améliorent l'observation.
3. Entrez vous-même dans un combat. PythonBot ne réalise pas cette étape.
4. Ouvrez **Combat**, choisissez **Vision réelle**, puis cliquez sur **Démarrer l'observation**.
5. Jouez normalement. L'écran actualise environ 2,5 fois par seconde : état du combat, tour, PA, PM, cellule du joueur, ennemis, grille et scores de confiance. L'overlay reste dans l'aperçu PythonBot.
6. Si la cellule du joueur reste inconnue, cliquez sur **Voici mon personnage**, puis cliquez sur le marqueur coloré du personnage dans l'aperçu PythonBot. La signature et la grille observée sont conservées avec le profil.
7. Cliquez volontairement sur **Enregistrer cette observation** pour produire une image originale, une image annotée et un JSON dans `data\debug\`. Aucune frame n'est enregistrée automatiquement.
8. Cliquez sur **Arrêter l'observation** avant de fermer ou de changer de fenêtre.

Une valeur illisible reste `Inconnu`. Une cellule `UNKNOWN` n'est jamais considérée comme libre. Le champ interne `safe_for_decision` reste faux dès qu'une donnée indispensable ou la qualité globale est insuffisante. Ce champ ne déclenche aucune action dans le LOT 3.

## Constituer le corpus LOT 3B-0

Les observations de debug restent intactes dans `data\debug\`. Leur entrée dans le corpus demande une action explicite.

1. Connectez DOFUS et effectuez la recette d'observation ci-dessus.
2. Pendant un combat, enregistrez plusieurs frames successives avec **Enregistrer cette observation**. La sauvegarde contient la zone combat, l'overlay, la prédiction JSON et, quand les zones sont calibrées, `hud/ap_original.png` et `hud/mp_original.png`.
3. Ouvrez **Corpus / Annotation** dans la barre latérale.
4. Cliquez **Importer un debug** et sélectionnez son fichier `*-observation.json`. PythonBot copie seulement les fichiers utiles vers le corpus et ne modifie pas le debug source.
5. Sélectionnez l'observation et ouvrez l'annotation. Vous pouvez alterner capture originale et overlay.
6. Indiquez seulement les vérités que vous connaissez : combat, tour, PA et PM. Une valeur laissée sur **Non annoté** reste absente du JSON.
7. Choisissez un type de clic, puis cliquez le centre pixel réel du joueur, des ennemis, des cellules visibles ou des ancres. La coordonnée logique actuelle est affichée et conservée comme suggestion si elle existe ; le centre pixel humain reste la donnée principale.
8. Cochez **Confusion 1 ↔ 7 pertinente** pour les cas correspondants, puis enregistrez.
9. Répétez sur plusieurs frames de la même session et sur plusieurs tailles de fenêtre.
10. Utilisez **Promouvoir en fixture de test** uniquement pour une observation annotée et stable. Le nom doit être lisible, par exemple `pa_7_basic`, `pa_1_basic`, `grid_sprite_occlusion_01` ou `grid_false_diamond_01`.

Collectez en priorité PA 1 et 7, PM 1 et 7 si possible, grille propre, joueur seul, ennemi seul, plusieurs ennemis, sprite occultant une cellule, animation, faux losange du décor, début et fin de tour, et tailles de fenêtre différentes. Une capture isolée ne mesure pas les sauts d'origine : conservez des frames consécutives dans la même session.

### Format local

En développement, la racine est `data\corpus\`. Dans l'exécutable, elle est `%LOCALAPPDATA%\PythonBot\data\corpus\`.

```text
data/corpus/
├── manifests/corpus_manifest.json
├── annotations/                 # réservé aux exports/outils futurs
└── sessions/<session_id>/<observation_id>/
    ├── frame.png
    ├── overlay.png
    ├── observation.json
    ├── annotation.json          # seulement après annotation humaine
    └── hud/
        ├── ap_original.png
        └── mp_original.png
```

Le manifeste utilise des chemins relatifs et attribue exactement un usage parmi `train`, `validation`, `test` et `diagnostic`. Une observation employée pour construire de futurs templates devra rester en `train` et ne devra pas être promue comme test indépendant.

L'annotation porte des champs facultatifs `combat_truth`, `player_turn_truth`, `ap_truth`, `mp_truth`, `player`, `enemies`, `reference_cells`, `reference_cells_complete`, `grid_anchors`, `digit_issue` et `comments`. Elle ne reprend jamais automatiquement les valeurs prédites.

## Mesurer la baseline

Depuis la racine du projet :

```powershell
.\.venv\Scripts\python.exe -m combatbot.benchmark
```

Les rapports sont écrits dans `data\benchmarks\latest.json` et `data\benchmarks\latest.md`, ou dans `%LOCALAPPDATA%\PythonBot\data\benchmarks\` pour l'exécutable. Pour un corpus isolé :

```powershell
.\.venv\Scripts\python.exe -m combatbot.benchmark --corpus-root C:\chemin\corpus --output-dir C:\chemin\rapports
```

Le rapport mesure uniquement les vérités disponibles : accuracy et matrice de confusion PA/PM, erreurs 1→7 et 7→1, états combat/tour, centre du joueur, associations logiques, nombres et confiance de cellules, ainsi que les apparitions, disparitions et renumérotations strictement observables entre frames. Une valeur impossible à calculer est affichée `N/A`.

## Persistance et migration

SQLite stocke les réglages existants, les combats simulés, les profils, les calibrations, les icônes, les caractéristiques lues, leurs scores distincts et les statuts. Avant chaque migration d'une base existante, l'application crée **une sauvegarde** dans `data/` sous le nom `pythonbot.pre-lot2-*.bak`. Elle conserve les données précédentes et passe le schéma à la version 4. Le LOT 3B-1 réutilise les colonnes existantes et n'ajoute aucune migration. Les captures diagnostiques ne sont pas enregistrées automatiquement.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Les tests couvrent le simulateur existant, la migration SQLite, les profils, la calibration, la capture bornée, la reconnaissance de sorts ainsi que les scénarios LOT 3. Le LOT 3B-0 couvre aussi le manifeste et le banc de mesure. Le LOT 3B-1 couvre les allers-retours entre référentiels, les changements de taille et de position, les bords, l'arrondi, les signatures, le chargement legacy et la sérialisation des annotations. `tests/fixtures/user_spellbar.png` est une capture statique réelle fournie par l'utilisateur ; les tests de combat présents avant constitution du corpus restent synthétiques et ne constituent pas une validation en conditions réelles.

## Architecture

- `combatbot/engine.py`, `models.py`, `grid.py`, `simulation.py` : simulation conservée, isolée du client réel.
- `combatbot/vision/window.py`, `capture.py` : handle, zone cliente, DPI, fermeture/minimisation et capture.
- `combatbot/vision/coordinates.py` : types de points, rectangles, signatures et `LayoutTransform` immuables.
- `combatbot/vision/models.py`, `icons.py`, `tooltip.py`, `character.py` : façades de calibration compatibles et reconnaissance indépendante de Qt.
- `combatbot/vision/combat_models.py`, `combat_grid.py`, `combat_ocr.py` : modèle réel, grille isométrique et lecture ciblée des compteurs.
- `combatbot/vision/combat_observer.py`, `combat_tracker.py` : analyse d'une frame, overlay, sauvegarde volontaire et stabilisation temporelle.
- `combatbot/corpus/`, `combatbot/benchmark.py` : manifeste, vérité terrain, import, promotion et métriques de baseline.
- `combatbot/ui/corpus_page.py` : annotation visuelle locale, sans action envoyée au jeu.
- `combatbot/storage.py` : migration et tables SQLite séparant simulation et observation.
- `combatbot/ui/` : écrans existants enrichis, calibration interactive et tâches de vision en arrière-plan.
- `combatbot/ports.py` : contrats du moteur ; l'exécuteur d'actions n'est connecté à aucun écran d'observation.
