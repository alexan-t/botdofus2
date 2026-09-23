# Audit comparatif des bots et outils DOFUS pour PythonBot

**Date de l'audit :** 23 septembre 2026  
**Projet audité :** PythonBot, DOFUS 2.64.5.0  
**Portée :** perception visuelle, géométrie, suivi, lecture PA/PM et architecture. Aucun code tiers n'a été intégré et aucun fichier Python du projet n'a été modifié.

## 1. Résumé exécutif

### Faits observés

- Les quatre dépôts imposés ne contiennent pas de solution générique et robuste de reconstruction complète d'une grille isométrique DOFUS.
- Les bots visuels qui obtiennent un comportement exploitable réduisent fortement le domaine : résolution fixe, petites régions d'intérêt, couleurs exactes, cartes connues, positions pré-calibrées ou mode tactique.
- `gilliorem/dofus_retro_bot` exploite surtout des pixels et positions utiles. Il ne reconstruit pas une grille logique complète.
- `aalachi/dofus-job-bot` apporte des idées de découpage et de régions proportionnelles, mais son implémentation est incomplète et comporte des incohérences exécutables.
- `AdnaneSaber/dofuzen` formalise une résolution de référence et une conversion de coordonnées, mais capture tout l'écran et mélange plusieurs sens de transformation.
- `pm-leg/dofus-opti` ne traite pas la vision. Son intérêt concerne les modèles métier purs, les contraintes de PA et la validation explicite des inconnues.
- Le dépôt complémentaire `R3conS/Sadidauto-Dofus-Retro-Bot` confirme une tendance utile : détection de marqueurs visuels dans des zones limitées, mode tactique, coordonnées connues par carte et machine à états. Il reste fortement lié à une résolution et à des pixels fixes.

### Conclusions

1. La détection de tous les losanges à chaque image ne doit plus définir l'identité ni la numérotation des cellules. Un décor ou un sprite peut ajouter, masquer ou déformer un contour, ce qui déplace actuellement l'origine et renumérote toute la grille.
2. La meilleure base pour PythonBot est une **approche hybride** : topologie logique persistante, transformation géométrique calibrée, projection déterministe des cellules, puis vérification visuelle locale. La vision doit mesurer la qualité de l'alignement sans recréer la grille à chaque image.
3. Pour les petits compteurs PA/PM stables, un lecteur spécialisé par gabarits de glyphes et règles temporelles est plus adapté qu'un OCR général. RapidOCR peut rester un signal secondaire.
4. L'association joueur/ennemis doit avoir lieu après projection de la grille, cellule par cellule, en ciblant les marqueurs au sol et en suivant les identités dans le temps. La couleur HSV d'un sprite entier est trop ambiguë.
5. Avant toute refonte, PythonBot a besoin d'un corpus de captures réelles annotées. Sans ce corpus, une amélioration peut seulement déplacer les erreurs.

### Recommandation principale

Adopter l'option D : une grille logique indépendante des pixels, un `LayoutTransform` unique en coordonnées client, une calibration par plusieurs points, une projection persistante et un ajustement visuel borné. Conserver l'état `UNKNOWN`, les scores de confiance, la capture limitée au client et le suivi temporel déjà présents dans PythonBot.

## 2. Méthode et vocabulaire de preuve

Le README, l'arborescence et les fichiers pertinents de chaque dépôt ont été inspectés. Les conclusions ci-dessous se fondent sur le code du commit indiqué, et pas uniquement sur la présentation du dépôt.

Les mentions suivantes séparent les niveaux d'affirmation :

- **Fait observé** : comportement directement visible dans le code ou la documentation du commit audité.
- **Conclusion** : interprétation technique déduite de plusieurs faits observés.
- **Recommandation** : choix proposé pour PythonBot ; ce n'est pas une capacité déjà validée.
- **Inconnue** : point non démontré par les dépôts ou par les essais actuels de PythonBot.

Les dépôts ont été clonés pour lecture dans un répertoire temporaire, sans copie de code ou d'asset dans PythonBot. Les nombres de commits affichés par GitHub donnent seulement un indicateur de profondeur publique ; ils ne mesurent pas la qualité ni l'activité récente.

## 3. Dépôts, versions et licences

| Dépôt | Commit inspecté | Historique affiché par GitHub | Licence observée | Conséquence pour une réutilisation éventuelle |
|---|---|---:|---|---|
| [gilliorem/dofus_retro_bot](https://github.com/gilliorem/dofus_retro_bot) | [`0e63d1d7d4b5fa84df824e27f180a8d32361fab7`](https://github.com/gilliorem/dofus_retro_bot/tree/0e63d1d7d4b5fa84df824e27f180a8d32361fab7), 23 février 2026 | 5 commits | Aucun fichier de licence et aucune concession identifiée | Le dépôt public reste protégé par défaut. Ne pas copier code ou assets sans autorisation ; réimplémenter seulement les idées générales. |
| [aalachi/dofus-job-bot](https://github.com/aalachi/dofus-job-bot) | [`f02a5cbd32fb294ba7490d914ff87f9dd9f3ddf3`](https://github.com/aalachi/dofus-job-bot/tree/f02a5cbd32fb294ba7490d914ff87f9dd9f3ddf3), 1er mai 2023 | 31 commits | Aucun fichier de licence et aucune concession identifiée | Même implication : concepts généraux seulement, sans copie de code ni d'assets. |
| [AdnaneSaber/dofuzen](https://github.com/AdnaneSaber/dofuzen) | [`0dec4a473f3f772a6f76a660040b8db306abd833`](https://github.com/AdnaneSaber/dofuzen/tree/0dec4a473f3f772a6f76a660040b8db306abd833), 17 février 2025 | 9 commits | Le README mentionne « MIT License », mais aucun fichier `LICENSE` ni texte complet n'a été trouvé dans le commit | L'intention est probablement MIT, mais les conditions et l'attribution ne sont pas matériellement fournies. Clarification requise avant toute copie. |
| [pm-leg/dofus-opti](https://github.com/pm-leg/dofus-opti) | [`6dad29346c428cf4ac49c15d99982bce1db5a313`](https://github.com/pm-leg/dofus-opti/tree/6dad29346c428cf4ac49c15d99982bce1db5a313), 26 juillet 2026 | 5 commits | Pas de licence logicielle identifiée ; la section « Licence et statut » décrit surtout l'absence d'affiliation et la propriété des marques | Ne pas réutiliser le code sans autorisation. Les modèles et principes peuvent inspirer une implémentation indépendante. |
| [R3conS/Sadidauto-Dofus-Retro-Bot](https://github.com/R3conS/Sadidauto-Dofus-Retro-Bot) (complément) | [`6ef1ecf80959cfc0dff0429173ee3e7f10e15e39`](https://github.com/R3conS/Sadidauto-Dofus-Retro-Bot/tree/6ef1ecf80959cfc0dff0429173ee3e7f10e15e39), 29 mars 2024 | 539 commits | MIT, texte complet dans `LICENSE` | Réutilisation possible sous les conditions MIT, avec conservation de l'avis. Pour PythonBot, les techniques suffisent et aucune copie n'est nécessaire. |

**Conclusion licence :** la visibilité publique d'un dépôt n'accorde pas automatiquement un droit de copie. Trois dépôts imposés ne fournissent aucune licence exploitable dans le snapshot audité ; `dofuzen` annonce MIT sans joindre la concession. Le présent rapport recommande donc uniquement des principes techniques réimplémentés indépendamment.

## 4. Audit de `gilliorem/dofus_retro_bot`

### 4.1 Architecture

**Faits observés :** le dépôt est organisé autour de scripts de scénario (`main.py`, `rooms.py`, `donjon_bouf.py`) et de petits modules (`combat.py`, `placement.py`, `spells_img.py`, `take_screenshot.py`, `utils/`). Les fonctions de perception appellent directement PyAutoGUI, puis les scénarios enchaînent captures, clics et `sleep()`. Le README exige une résolution stable et des images d'interface pré-capturées.

**Conclusion :** il existe une séparation par sujet, mais pas une séparation forte entre acquisition, observation, décision et exécution. La stratégie reste déterministe et adaptée à des salles connues.

### 4.2 Localisation des éléments de combat

**Faits observés :**

- `combat.py:get_squares()` capture une région absolue `(430, 108, 1100, 700)`, parcourt les pixels et retient ceux dont le RGB est exactement égal à une couleur demandée.
- `placement.py` et `utils/placement.py` manipulent des positions connues. Certaines positions diagonales sont engendrées par pas constants de 45 pixels en X et 25 pixels en Y.
- `utils/placement.py:check_square()` inspecte une petite zone de 10 × 10 pixels autour d'une position connue et compte les pixels rouges exacts.
- `utils/movement.py` cherche le personnage ou des monstres par couleurs RGB dans des régions absolues.
- `donjon_bouf.py` emploie `locateOnScreen` pour le bouton Prêt, la fin du combat, des sorts et certaines entités, avec des seuils de confiance selon l'asset.

**Réponse précise :** le bot ne construit pas une grille complète avec identifiants stables. Il cherche des positions utiles à l'action, puis conserve ou transforme directement des coordonnées pixel. Le pas diagonal sert de géométrie locale, sans modèle global de topologie.

### 4.3 Tour, placement et fin de combat

**Faits observés :**

- Le tour du joueur est déduit d'un seul pixel à `(1106, 931)`, comparé à deux couleurs exactes.
- La position du personnage dans la barre de tours est cherchée dans une bande fixe par une couleur exacte ; la dernière correspondance rencontrée est utilisée.
- La phase de placement repose sur les couleurs rouge/bleu de cellules et des positions propres aux salles.
- La fin de combat est recherchée par template en niveaux de gris dans une région fixe, avec un seuil relativement bas dans certains appels.

**Conclusion :** ces détecteurs fonctionnent comme des sondes de contexte. Ils sont rapides mais ne produisent ni état `UNKNOWN` explicite, ni preuve multi-signal, ni stabilité temporelle.

### 4.4 Gestion des faux positifs et changements visuels

**Faits observés :** les principaux garde-fous sont la réduction des régions, le seuil de template et le comptage de plusieurs pixels dans un carré. Aucun mécanisme général de consensus temporel, de marge entre premier et second candidat, de compensation de résolution ou de recalibration n'a été identifié.

**Conclusion :** lorsque la résolution, l'échelle, la teinte, le décor ou l'animation changent, la plupart des règles cessent simplement de correspondre ou correspondent au mauvais objet. Le code ne possède pas de chemin général de récupération.

### 4.5 Ce qui est intéressant pour PythonBot

- Employer des régions d'intérêt très petites pour chaque signal sémantique.
- Préférer un template d'un contrôle stable à une mesure générique de « texture ».
- Agréger plusieurs pixels dans une zone plutôt que dépendre d'un seul pixel.
- Pour une carte connue, persister des positions utiles et les revérifier localement au lieu de redécouvrir toute la scène.

### 4.6 Ce qu'il ne faut pas reprendre

- Coordonnées écran absolues disséminées.
- Couleurs RGB exactes sans tolérance photométrique.
- Un pixel unique pour le tour.
- Origines et pas codés dans les scripts de salles.
- `sleep()` comme mécanisme principal de synchronisation.
- Actions PyAutoGUI mélangées aux détecteurs.
- Absence de confiance et d'état inconnu.

## 5. Audit de `aalachi/dofus-job-bot`

### 5.1 Architecture et séparation perception/action

**Faits observés :** le dossier `src/` définit notamment `Monitor`, `Window`, `OCR`, `RecoltableScanner`, `UIHandler`, `Action`, `Character` et `Pathfinder`. Le README place le combat et la pêche dans les développements futurs ; la partie concrète concerne la récolte. Le découpage nominal sépare la capture, l'OCR, la recherche de ressources et l'action.

**Faits observés sur la qualité :** plusieurs interfaces ne concordent pas, certaines opérations sur les tuples et dimensions sont invalides, et la configuration auditée initialise des pas de balayage à zéro, ce qui rend le scanner inexécutable sans correction. L'unique test repéré est davantage un script expérimental qu'une suite de non-régression.

**Conclusion :** le découpage est une source d'idées, mais l'implémentation n'est pas une référence fiable à intégrer.

### 5.2 Screenshots et fenêtres

**Faits observés :**

- `Monitor.get_box()` exprime des régions en proportions de largeur et hauteur.
- La variante Windows énumère les fenêtres dont le titre contient DOFUS, les active et peut les maximiser.
- Les captures utilisent `ImageGrab` ou l'écran global. Elles ne sont pas limitées au rectangle client réel.
- La largeur et la hauteur peuvent provenir d'une fenêtre tandis que les offsets proviennent du moniteur, ce qui mélange deux référentiels.

**Conclusion :** l'idée d'une région normalisée est bonne ; son référentiel doit être explicite. Une proportion ne résout pas à elle seule les bordures de fenêtre, le DPI ou l'origine du client.

### 5.3 Reconnaissance et navigation

**Faits observés :** le scanner parcourt une grille de positions proportionnelles, déplace le pointeur, attend l'infobulle, extrait une zone noire par seuillage/contour puis lit son texte avec pytesseract. Les positions découvertes sont sauvegardées par carte dans un CSV et revérifiées par survol lors d'une utilisation ultérieure. L'OCR limite l'alphabet via la configuration Tesseract.

**Conclusion :** le pattern « découvrir, persister, revérifier » est pertinent. Il réduit le coût de perception après calibration. Il ne constitue cependant pas une solution de grille de combat.

### 5.4 Templates et résolution

**Faits observés :** le dépôt ne s'appuie pas principalement sur une bibliothèque structurée de templates ; l'infobulle et son OCR dominent. Les positions sont partiellement proportionnelles, partiellement absolues, et l'adaptation à la résolution n'est pas cohérente de bout en bout.

### 5.5 Patterns utiles pour PythonBot

- Interfaces séparées pour fenêtre, capture, perception et action.
- Régions exprimées dans un repère normalisé documenté.
- Cache persistant d'une observation coûteuse, assorti d'une revalidation locale.
- Alphabet OCR restreint au domaine attendu.
- Adaptateurs Windows isolés de la logique métier.

### 5.6 À ne pas reprendre

- Mélanger repère moniteur et repère fenêtre.
- Déplacer la souris pour faire fonctionner la perception normale.
- Balayage systématique coûteux avec pauses fixes.
- Sauvegarder une position sans version de layout, taille client et mesure de validité.
- Considérer une abstraction nominale comme fiable sans tests de transformation aller-retour.

## 6. Audit de `AdnaneSaber/dofuzen`

### 6.1 Résolution logique et normalisation

**Faits observés :** `ScreenManager` définit une résolution cible de 1920 × 1080 dans le code, tandis que le README indique 1930 × 1066. La capture de l'écran entier est redimensionnée vers la cible. Les facteurs sont calculés comme `target/current`. `InputManager` reconvertit les coordonnées de référence vers l'écran réel en divisant par ces facteurs.

**Réponse précise :** oui, le dépôt utilise un espace logique de référence. Une position de la résolution cible est transformée au moment d'agir. Les boîtes de `bot/boxes.py` restent majoritairement exprimées dans cet espace.

### 6.2 Fenêtre et rectangle client

**Faits observés :** les processus `Dofus.exe` sont énumérés et une fenêtre est activée par son handle. La capture reste toutefois celle du bureau entier. Le rectangle client, son origine, ses bordures et son facteur DPI ne structurent pas la conversion.

**Conclusion :** une fenêtre détectée n'implique pas que les pixels capturés utilisent son repère. Si la fenêtre n'occupe pas exactement l'écran attendu, les coordonnées logiques ne pointent plus vers le bon contenu.

### 6.3 Reconnaissance d'image

**Faits observés :** les templates sont classés par fonctions dans `images/`. Le matching utilise OpenCV, typiquement `TM_CCOEFF_NORMED`, avec des seuils élevés. Certaines couleurs et régions Bonta/Brâkmar sont fixes. `find_image_on_screen()` opère sur une capture déjà ramenée à la résolution cible, redimensionne aussi le template selon les facteurs et reconvertit encore la position retournée.

**Conclusion prudente :** sauf convention d'assets non documentée, cette double adaptation paraît incohérente et risque de mettre le template et la capture à des échelles différentes. Ce point n'a pas été validé par une exécution réelle.

### 6.4 Apports possibles à `GridCalibration`

**Recommandations inspirées du dépôt, avec corrections :**

- Définir un seul espace canonique : coordonnées **du client**, pas du bureau.
- Centraliser toute conversion dans un objet immuable `LayoutTransform`.
- Conserver séparément taille client physique, zone de combat normalisée et coordonnées de grille.
- Fournir `client_to_normalized`, `normalized_to_client`, `cell_to_client` et, pour le diagnostic seulement, `client_to_nearest_cell`.
- Tester l'aller-retour avec plusieurs tailles, ratios et offsets.
- Ne pas redimensionner toute l'image pour masquer une différence de ratio. Appliquer une transformation aux points et adapter explicitement les templates si nécessaire.
- Invalider une calibration lorsque le ratio, la signature du layout ou l'erreur de reprojection sortent des tolérances.

**Réponse précise :** oui, les coordonnées logiques de combat peuvent et doivent être totalement séparées des pixels. Une cellule est un identifiant topologique ; sa position pixel est le résultat d'une transformation et d'un profil de calibration versionné.

## 7. Audit de `pm-leg/dofus-opti`

### 7.1 Portée

**Fait observé :** ce dépôt est un optimiseur de stuff et de dégâts pour DOFUS 3. Il ne contient pas une couche de capture pertinente pour PythonBot 2.64.5. L'audit se limite donc à son domaine métier.

### 7.2 Représentation des sorts et cibles

**Faits observés :** les modèles emploient des dataclasses typées et souvent immuables pour les jets, niveaux de sorts, sorts de classe, monstres et grades. Un sort représente notamment le coût PA, les jets, la portée et les limites de lancer. Une cible porte ses résistances fixes, pourcentages et vulnérabilités. Le contexte de lancer rend explicites les paramètres comme arme, distance et multiplicateurs finaux.

### 7.3 Contraintes PA et rotation

**Faits observés :** la meilleure rotation sous budget de PA est formulée comme un sac à dos borné exact, avec limites de lancer. Le résultat expose les lancers, PA utilisés, PA perdus et dégâts. L'optimisation d'équipement utilise séparément CP-SAT. Les formules de dégâts appliquent les troncatures par étapes et documentent les variantes ou incertitudes.

### 7.4 Séparation données/moteur

**Faits observés :** ingestion, normalisation, modèles, formules, rotation, solveur et export sont séparés. Les identifiants inconnus font échouer l'ingestion explicitement. La base enregistre la version des données. Le README annonce 259 tests, des échantillons de référence et des limites connues, notamment des classes non exercées, poisons absents et ordre de certaines troncatures encore supposé.

### 7.5 Intérêt ultérieur pour PythonBot

- Modèles métier purs et indépendants de Qt, OpenCV et PyAutoGUI.
- Contraintes explicites : coût PA, lancers par tour/cible, portée, résistances.
- Fonction de score déterministe testable sur un état de combat immuable.
- Échec explicite ou état inconnu pour les données non reconnues.
- Jeux d'or et fixtures issus de mesures réelles.
- Version de jeu attachée aux données.

**Recommandation :** reprendre ces principes lors d'un lot futur de décision. Ne pas intégrer aujourd'hui ses formules ou données : le dépôt vise DOFUS 3 et leur compatibilité avec 2.64.5 n'est pas démontrée.

## 8. Dépôt complémentaire : `R3conS/Sadidauto-Dofus-Retro-Bot`

### 8.1 Pourquoi ce dépôt est pertinent

Il contient une machine à états de combat, des détecteurs de tour, des marqueurs d'entités et des données de cellules par carte. Il fournit donc un contrepoint plus direct aux quatre dépôts imposés.

### 8.2 Techniques observées

- Capture PyAutoGUI d'une zone fixe `GAME_WINDOW_AREA = (0, 0, 933, 755)`.
- État combat détecté par template du compteur PA ou du bouton Prêt, chacun dans une petite région.
- Sous-états `PREPARING` et `FIGHTING`, contrôleurs dédiés et exceptions récupérables.
- Activation du mode tactique pendant la préparation.
- Positions de départ et déplacements stockés en tables de pixels pour treize cartes connues.
- Templates avec canal alpha utilisé comme masque et regroupement des rectangles OpenCV.
- Détection des cercles rouges/bleus sous les personnages ; identification du personnage par survol, infobulle et OCR du nom.
- Début de tour déterminé par plusieurs signaux : illustration et OCR du nom, remplissage du minuteur, présence du compteur PA.
- Fin du combat déterminée par la disparition du compteur puis présence de la fenêtre de résultats.

### 8.3 Enseignements

**Conclusion :** le dépôt évite de reconstruire la grille complète. Il exploite le mode tactique et des marqueurs sémantiques, puis des positions par carte. La précision vient surtout du domaine fermé, pas d'une vision générale.

**Concepts utiles :** templates masqués, déduplication des résultats, plusieurs signaux pour un état, exceptions récupérables, marqueur au sol plus stable que l'apparence du sprite, données de carte hors de la logique, mode tactique.

**Limites :** résolution fixe, capture au coin du bureau, nombreux pixels exacts, tables par carte, boucles d'attente actives, interactions de survol pour identifier le personnage et absence de transformation client. Cette solution n'est donc pas directement transposable à DOFUS 2.64.5.

## 9. Comparaison avec PythonBot actuel

### 9.1 Ce que PythonBot fait déjà mieux

**Faits observés dans le projet local :**

- `capture_client()` capture le rectangle client et vérifie que la géométrie reste cohérente avant et après la capture.
- Les zones sont des `RelativeRect` exprimés relativement au client.
- Observation, modèles, suivi et interface Qt sont séparés.
- `CombatObservation` porte des scores et accepte des valeurs inconnues.
- `CombatObservationTracker` applique un consensus temporel et conserve brièvement un ennemi disparu.
- Les actions réelles restent séparées et le LOT 3 est en observation.
- Une calibration manuelle peut stocker origine, taille des cellules, liste logique et référence HSV du joueur.

Ces bases doivent être conservées.

### 9.2 Cause de l'instabilité de la grille actuelle

**Faits observés dans `combat_grid.py` :**

1. Les contours sont extraits après flou, Canny et fermeture morphologique.
2. Les quadrilatères convexes sont filtrés par aire, ratio et symétrie.
3. La largeur et la hauteur médianes des candidats servent de pas.
4. Le candidat le plus haut, puis le plus à gauche, devient l'ancre.
5. Les coordonnées logiques sont arrondies depuis le déplacement à cette ancre.
6. En mode manuel, les cellules sont projetées et leur confiance provient du support de contours sur leur bord.

**Conclusion :** l'identité de toute la grille dépend d'un candidat visuel extrême. Si un sprite cache la vraie cellule supérieure ou si un décor produit un faux losange au-dessus, l'ancre change. Même si les autres centres sont correctement détectés, tous leurs identifiants peuvent être décalés. La médiane du pas peut aussi être biaisée lorsque les contours visibles ne représentent pas un échantillon régulier. La déduplication locale empêche quelques doublons, mais ne vérifie ni cohérence globale de réseau, ni voisinage topologique, ni erreur de reprojection.

### 9.3 Occupation des cellules

**Faits observés :** l'ennemi est actuellement détecté par proportion de rouge saturé autour du centre ; le joueur par proximité HSV avec une référence cliquée. Une cellule sans signal reste `UNKNOWN`. Le détecteur n'établit pas encore de distinction fiable entre libre et bloquée.

**Conclusion :** le bon principe est de classer une fois les cellules projetées. La zone actuelle, centrée sur la cellule entière, reste exposée au sprite, au décor et aux effets. Le marqueur au sol, son anneau ou une petite bande proche des pieds constitue une cible plus discriminante.

### 9.4 OCR PA/PM

**Faits observés dans `combat_ocr.py` :** quatre variantes sont produites : normalisation, Otsu, Otsu inversé et seuil adaptatif. RapidOCR est appliqué à chacune ; les nombres de un ou deux chiffres sont regroupés par vote et score.

**Conclusion :** le vote corrige certaines erreurs de prétraitement, mais les quatre candidats partagent le même modèle OCR général. Si le modèle confond systématiquement le glyphe `7` avec `1`, le vote renforce l'erreur. Il manque une seconde famille de reconnaissance indépendante.

### 9.5 Suivi temporel

**Faits observés dans `combat_tracker.py` :** les booléens se stabilisent sur deux des trois dernières images. Un ennemi est associé au plus proche dans un rayon logique de deux cellules, puis conservé deux images en cas de disparition.

**Conclusion :** c'est une bonne base de prudence, mais l'affectation gloutonne dépend de coordonnées de grille déjà stables. Plusieurs ennemis proches peuvent échanger leurs identités. Le rayon constant ne tient pas compte du nombre de PM, du temps écoulé, de l'occlusion ou d'une confiance de mesure.

### 9.6 Détection combat/tour

**Faits observés :** l'observateur combine confiance de grille, activité visuelle des compteurs, bouton de fin de tour et barre de sorts. L'activité est une combinaison générique d'écart type et de densité de contours.

**Conclusion :** la fusion de plusieurs signaux est saine. Le contenu visuel générique n'identifie toutefois pas la sémantique active/inactive. Un décor chargé ou une animation peut produire une forte activité. Des templates d'état ou des profils de couleur propres au contrôle seraient plus précis.

## 10. Analyse des stratégies de grille

| Critère | A — Détecter toutes les cellules à chaque image | B — Calibrer une grille théorique une fois | C — Topologie DOFUS connue + transformation seule | D — Hybride persistante + vérification visuelle |
|---|---|---|---|---|
| Stabilité d'identité | Faible : le jeu de contours change | Bonne tant que le layout ne dérive pas | Très bonne si topologie et transformation sont exactes | Très bonne avec invalidation contrôlée |
| Précision pixel | Variable, locale | Bonne au moment de la calibration, puis dérive possible | Bonne si les ancres sont exactes | Bonne, avec correction locale bornée |
| Coût CPU | Élevé : contours et regroupement complets | Faible | Très faible | Faible à moyen selon fréquence de vérification |
| Sensibilité au décor | Forte | Faible après calibration | Faible | Faible ; la vision ne sert qu'à valider/ajuster |
| Sensibilité aux sprites/animations | Forte | Faible pour la géométrie | Faible pour la géométrie | Faible si les ancres sont sélectionnées hors zones occultées |
| Changement de résolution | Redétection possible, mais instable | Recalibration ou transform versionné | Nouvelle transformation | Recalibration guidée, puis persistance |
| Changement de map | Peut retrouver seulement les cellules visibles | Nécessite masque/topologie de map ou mode générique | Nécessite connaître le masque/topologie | Peut projeter un réseau canonique puis apprendre/charger le masque de carte |
| Difficulté de calibration | Faible en apparence, forte à fiabiliser | Moyenne | Moyenne à forte au premier modèle | Moyenne, mais explicable et diagnostiquable |
| Gestion d'une preuve insuffisante | Souvent une grille fausse | Calibration invalide détectable | Transformation invalide détectable | Résidu et qualité des ancres produisent `UNKNOWN` |
| Verdict | Utile comme outil de calibration/diagnostic | Bon premier palier | Excellente cible si la topologie exacte est disponible | **Choix recommandé** |

### 10.1 Pourquoi l'option D

L'option D combine la stabilité topologique de B/C et la capacité de la vision à détecter une dérive. Le coût et le risque restent bornés parce que l'image ne décide plus du nombre et du nom des cellules à chaque frame.

### 10.2 Géométrie proposée

La grille logique utilise deux indices entiers `(u, v)`. La projection client s'écrit conceptuellement :

```text
p_client = origine + u × base_u + v × base_v
```

`origine`, `base_u` et `base_v` forment une transformation affine. Pour une isométrie parfaite, les bases sont symétriques ; l'affine permet de tolérer une légère anisotropie de capture. Une homographie ne doit être introduite que si les mesures réelles montrent une perspective non représentable par l'affine.

La calibration doit utiliser au moins trois centres de cellules non colinéaires et, si possible, davantage de points pour estimer la transformation par moindres carrés robustes. L'erreur de reprojection médiane et maximale devient une donnée de calibration. Au-delà d'un seuil mesuré sur le corpus, l'observation passe à `UNKNOWN` et demande une recalibration.

La topologie et la géométrie doivent être séparées :

- **Topologie** : identifiants, voisins, masque des cellules existantes ou jouables.
- **Géométrie** : transformation de ces identifiants vers le rectangle client courant.
- **Évidence visuelle** : score local indiquant si le centre/bord projeté concorde encore avec l'image.

### 10.3 Rôle résiduel des contours

Conserver les contours comme :

- aide interactive à la calibration ;
- détection de lignes compatibles près d'une position projetée ;
- estimation d'un petit décalage global ;
- diagnostic visuel et génération de candidats.

Ne plus les utiliser comme autorité sur l'origine, l'identité ou le nombre des cellules. Un ajustement automatique doit être global et robuste : rechercher une petite translation/échelle autour de la calibration, minimiser l'erreur sur plusieurs ancres, rejeter les valeurs aberrantes, puis refuser la correction si le gain est insuffisant.

### 10.4 Conserver, supprimer, remplacer, compléter

| Action | Élément de PythonBot |
|---|---|
| Conserver | Capture client, `RelativeRect`, modèles typés, `UNKNOWN`, scores, overlay, persistance de calibration, séparation observation/action, suivi temporel. |
| Supprimer comme source d'identité | Choix systématique du losange le plus haut/gauche comme origine. |
| Remplacer | Reconstruction complète par contours à chaque image → projection d'une topologie persistante par transformation calibrée. |
| Compléter | Calibration multi-points, résidu de reprojection, signature de layout, masque de carte, validation d'ancres, tests aller-retour et dataset réel. |
| Conserver en outil secondaire | Détecteur de losanges pour suggérer des centres et mesurer l'alignement local. |

## 11. Recommandation PA/PM

### 11.1 Comparaison des méthodes

| Méthode | Atout | Faiblesse pour PA/PM | Avis |
|---|---|---|---|
| OCR général RapidOCR/Tesseract | Aucun apprentissage local, accepte du texte varié | Modèles optimisés pour des textes plus grands ; confusion systématique possible entre glyphes proches | Signal secondaire seulement |
| Templates de nombres complets | Très simple et fiable si taille/layout fixes | Un template par valeur, thème et échelle | Bon premier prototype pour une plage courte |
| Templates de glyphes 0–9 | Peu de données, interprétable, comparaison explicite 1/7 | Demande segmentation et normalisation stables | **Choix principal recommandé** |
| Perceptual hash | Très rapide | Perd les détails fins qui distinguent 1 et 7 | Filtre grossier, pas classifieur final |
| Contours seuls | Interprétables | Sensibles au seuil et aux antialiasings | Caractéristiques auxiliaires |
| HOG + classifieur linéaire | Robuste à de petites variations avec peu de calcul | Nécessite un corpus annoté représentatif | Plan B après mesure des templates |
| Réseau neuronal spécialisé | Peut absorber de fortes variations | Complexité, dataset, packaging et diagnostic | Non justifié à ce stade |

### 11.2 Pipeline recommandé

1. Capturer uniquement les ROI PA et PM dans le repère client calibré.
2. Normaliser localement luminosité et contraste sans agrandir plus que nécessaire.
3. Produire un masque du glyphe par un seuil appris sur captures réelles, avec variantes clair/sombre limitées.
4. Extraire les composantes connexes, éliminer l'icône et les éléments de bord, puis cadrer le ou les chiffres.
5. Normaliser chaque glyphe vers une petite taille canonique en conservant le ratio.
6. Comparer à plusieurs templates réels par chiffre avec corrélation normalisée ou distance de masque.
7. Utiliser la marge `score_meilleur - score_second` comme confiance. Une forte ressemblance entre 1 et 7 doit produire `UNKNOWN`, pas une valeur forcée.
8. Ajouter une caractéristique explicite pour 1/7 : occupation horizontale dans la bande supérieure, extension gauche/droite du sommet, puis forme de la hampe inférieure.
9. Contraindre par le domaine configuré du personnage, sans inventer une valeur : plage plausible, transitions possibles après action observée, stabilité sur plusieurs frames.
10. Conserver RapidOCR comme vote indépendant ou repli ; ne valider que si la lecture spécialisée, la marge et la cohérence temporelle sont suffisantes.

**Conclusion :** les templates de glyphes sont appropriés parce que le vocabulaire ne contient que dix classes, la police et la zone sont stables, et l'erreur 1/7 peut être expliquée. Un HOG linéaire ne devient utile que si un benchmark montre des variations que les templates multi-échelles ne couvrent pas.

## 12. Recommandation joueur, ennemis et cellules

### 12.1 Détection par cellule projetée

Pour chaque cellule projetée, définir plusieurs sous-régions :

- anneau proche du sol pour les cercles ou marqueurs d'équipe ;
- petite région centrale basse pour les pieds ;
- intérieur de cellule pour libre/bloqué ;
- bord de cellule uniquement pour l'alignement géométrique.

Le score d'entité doit combiner teinte ou distance Lab, saturation, forme d'anneau et cohérence entre images. Les seuils doivent être appris à partir du corpus réel, pas déduits d'une seule capture.

### 12.2 Références adaptatives

- Pendant le placement ou sur des cellules confirmées vides, mémoriser un fond local par cellule ou par famille de textures.
- Soustraire ce fond ou comparer les descripteurs afin de repérer une nouvelle présence.
- En mode tactique, privilégier les marqueurs d'équipe visibles et stables. L'activation éventuelle doit rester un choix utilisateur tant que l'interaction réelle n'est pas dans le lot.
- Utiliser la référence HSV actuelle du joueur comme indice, puis la compléter par une confirmation manuelle initiale de cellule et le suivi temporel.

### 12.3 Suivi d'identité

Remplacer l'association gloutonne par une affectation globale entre pistes et observations, avec matrice de coût :

- distance sur la grille ;
- déplacement maximal compatible avec le temps et, si connu, les PM ;
- similarité de marqueur ;
- confiance de l'observation ;
- pénalité pour changement d'équipe ou saut impossible.

L'algorithme hongrois suffit pour le petit nombre d'entités. Chaque piste porte un état `OBSERVED`, `OCCLUDED`, `LOST` avec âge, dernière cellule fiable et confiance décroissante. Une identité ne change pas de cellule sur une observation faible seule.

### 12.4 Libre, bloqué et inconnu

Ne pas conclure `FREE` parce qu'aucun ennemi n'est détecté. La cellule peut être occultée ou sa texture inconnue. Une cellule devient :

- `OCCUPIED` si un marqueur/une entité est confirmé ;
- `BLOCKED` si un modèle de carte ou une évidence persistante le confirme ;
- `FREE` si la topologie la rend jouable et que plusieurs images fournissent un fond compatible sans occupation ;
- `UNKNOWN` dans les autres cas.

## 13. Tableau comparatif des techniques

L'estimation de fiabilité décrit l'usage observé dans son domaine propre, pas une mesure expérimentale commune.

| Technique | Projet | Problème traité | Fiabilité estimée | Dépendance résolution | Réutilisable conceptuellement | Applicable à PythonBot | Remarques |
|---|---|---|---|---|---|---|---|
| Pixel RGB unique | gilliorem, Sadidauto | Tour, minuterie, état local | Faible hors setup exact | Très forte | Faiblement | Non comme preuve unique | Rapide, mais aucun score ni tolérance structurelle. |
| Comptage RGB dans une petite zone | gilliorem | Cellule de placement | Moyenne dans un mode stable | Forte | Oui | Oui comme signal secondaire | Meilleur qu'un pixel unique ; utiliser HSV/Lab et ratio. |
| Template matching dans une ROI | gilliorem, dofuzen, Sadidauto | Boutons, compteurs, sorts, fin de combat | Moyenne à bonne si asset stable | Moyenne à forte | Oui | Oui | Ajouter multi-échelle bornée, marge et état inconnu. |
| Template avec masque alpha | Sadidauto | Cercles d'équipe, compteur PA | Bonne dans le setup ciblé | Forte | Oui | Oui | Pertinent pour isoler la forme et ignorer le fond. |
| Capture client réelle | PythonBot | Isoler la fenêtre | Bonne | Faible si DPI géré | Oui | Déjà présent | Base plus saine que les captures plein écran des dépôts. |
| Capture plein écran redimensionnée | dofuzen | Uniformiser l'entrée | Faible si fenêtre déplacée/ratio différent | Forte | Idée à corriger | Non telle quelle | Ignore origine du client, bordures et déformation. |
| ROI proportionnelle | aalachi, PythonBot | Adapter les zones au client | Moyenne à bonne | Faible à moyenne | Oui | Déjà présent | Le repère doit être explicitement celui du client. |
| Résolution logique de référence | dofuzen | Adapter les actions | Moyenne dans un plein écran stable | Moyenne | Oui | Oui avec `LayoutTransform` | Centraliser le sens de conversion et tester l'aller-retour. |
| Positions par salle/carte | gilliorem, Sadidauto | Placement et mouvement | Bonne sur cartes connues | Très forte sans transform | Oui sous forme de profil | Partiellement | Persister topologie/masque, pas des pixels bruts seuls. |
| Pas diagonal fixe | gilliorem | Générer quelques positions | Moyenne localement | Très forte | Oui comme base vectorielle | Oui après calibration | Devient `base_u`, `base_v`, estimées et versionnées. |
| Détection de tous les losanges | PythonBot | Reconstruire la grille | Faible en combat réel | Moyenne | Oui pour calibration | Non comme autorité par frame | Décor, sprites et ancre extrême déstabilisent l'identité. |
| Grille persistante projetée | PythonBot manuel, principe confirmé par tables externes | Identité stable des cellules | Potentiellement élevée | Faible avec transform | Oui | **Oui, prioritaire** | Exige calibration multi-points et validation du résidu. |
| OCR Tesseract générique | aalachi, Sadidauto | Infobulles et noms | Moyenne sur texte préparé | Moyenne | Oui | Pour textes, pas idéal pour PA/PM | Restreindre alphabet et mesurer confiance. |
| RapidOCR multi-prétraitements | PythonBot | PA/PM | Moyenne, confusion 1/7 constatée | Faible à moyenne | Déjà présent | En signal secondaire | Les votes ne sont pas indépendants du modèle. |
| Templates de glyphes | Recommandation issue de la stabilité HUD | Petits chiffres | Potentiellement élevée | Faible avec normalisation | Oui | **Oui, prioritaire** | Nécessite un petit corpus par échelle/thème. |
| Survol + OCR d'infobulle | aalachi, Sadidauto | Identifier ressource/personnage | Moyenne | Forte et intrusive | Partiellement | Non pour la boucle normale | Modifie la scène et dépend du pointeur. |
| Cercles d'équipe au sol | Sadidauto | Joueur/ennemis | Bonne en mode tactique ciblé | Moyenne à forte | Oui | Oui après validation 2.64.5 | Plus stable que le sprite entier. |
| HSV autour du centre | PythonBot | Joueur/ennemi | Moyenne à faible actuellement | Faible à moyenne | Oui | À compléter | Utiliser sous-régions, fond, forme et temps. |
| Suivi temporel majoritaire | PythonBot | Stabiliser combat/tour | Bonne base | Aucune | Oui | Déjà présent | Paramètres à calibrer sur vidéos annotées. |
| Association gloutonne au plus proche | PythonBot | Identité ennemis | Moyenne avec une cible | Aucune | Oui pour prototype | À remplacer | Risque d'échange lorsque plusieurs entités sont proches. |
| Fusion de plusieurs signaux d'état | PythonBot, Sadidauto | Combat/tour/fin | Bonne conception | Selon les signaux | Oui | **Oui** | Remplacer l'activité générique par des détecteurs sémantiques. |
| Machine à états et exceptions récupérables | Sadidauto | Orchestration | Bonne conception | Aucune | Oui | Oui | Compatible avec l'état métier existant. |
| Rotation exacte sous budget PA | dofus-opti | Décision de sorts | Élevée dans son modèle | Aucune | Oui | Plus tard | Les règles 2.64.5 doivent être revalidées. |
| Données versionnées et inconnus bloquants | dofus-opti | Éviter calculs silencieusement faux | Élevée | Aucune | Oui | Oui | Très utile pour profils, sorts et calibrations. |

## 14. Architecture cible pour PythonBot

```text
WindowLocator
     ↓
ClientCapture
     ↓
LayoutSignature ───────────────┐
     ↓                         │ invalidation/version
LayoutTransform               │
     ↓                         │
CombatGridProfile ← CalibrationStore
     ↓
GridProjector
     ↓
GridAlignmentValidator
     ├──────────────┐
     ↓              ↓
EntityDetector    HUDReader
     ↓              ↓
EntityTracker    TurnStateDetector
     └──────┬───────┘
            ↓
CombatObservationAssembler
            ↓
CombatObservation (avec UNKNOWN et preuves)
```

### Rôle des composants

#### `WindowLocator`

Trouve le handle du client ciblé et expose ses métadonnées. Il ne capture pas et ne prend aucune décision.

#### `ClientCapture`

Produit une image BGR du rectangle client avec taille, origine écran, DPI pertinent, handle et horodatage. Il refuse une image si la géométrie change pendant la capture.

#### `LayoutSignature`

Décrit la variante visuelle : taille client, ratio, zones d'interface et empreintes de quelques contrôles stables. Une signature incompatible invalide le profil avant toute observation.

#### `LayoutTransform`

Centralise toutes les conversions entre coordonnées normalisées, client et écran. Il ne connaît pas les entités ni les sorts. Ses fonctions aller-retour sont testées.

#### `CombatGridProfile`

Contient topologie logique, masque de cellules, points d'ancrage, transformation grille→client canonique, métriques de calibration et version. Les données restent indépendantes d'une frame.

#### `CalibrationStore`

Persiste et migre les profils SQLite avec version de schéma, signature de layout, date, qualité et provenance manuelle/automatique. Une ancienne calibration n'est jamais appliquée silencieusement à un layout incompatible.

#### `GridProjector`

Projette toutes les cellules connues dans l'image courante. Il produit centre, polygone et voisinage sans analyser les contours.

#### `GridAlignmentValidator`

Mesure l'accord local entre cellules projetées et indices visuels. Il peut estimer un petit ajustement global borné. Il publie erreur, couverture et confiance ; il n'invente jamais une nouvelle numérotation.

#### `EntityDetector`

Calcule, pour chaque cellule projetée, des preuves de joueur, ennemi, occupation, libre ou bloqué. Il cible les marqueurs au sol et conserve les scores par classe.

#### `HUDReader`

Lit PA/PM avec le lecteur spécialisé de glyphes, puis éventuellement RapidOCR comme signal secondaire. Il expose valeur, meilleur score, seconde classe, marge et motif d'échec.

#### `TurnStateDetector`

Reconnaît des états sémantiques des contrôles : bouton de fin de tour actif/inactif, compteur présent, barre des tours et fenêtre de résultats. Il fusionne plusieurs signaux et conserve `UNKNOWN` en cas de désaccord.

#### `EntityTracker`

Associe globalement les observations aux pistes, gère occlusion et disparition, et refuse les déplacements impossibles. Il travaille en coordonnées logiques.

#### `CombatObservationAssembler`

Assemble une observation immuable avec preuves, confiance, raisons d'invalidation et temps de calcul. La couche de décision consomme seulement cet objet et ne lit jamais directement une image.

## 15. Plan de migration par lots

Chaque lot garde le simulateur, l'ancienne observation derrière une option de compatibilité et ajoute des tests ciblés. Aucun lot ne doit activer d'action réelle.

### LOT 3B-0 — Corpus et banc de mesure

**But :** rendre les erreurs mesurables avant la refonte.

- Collecter des captures originales de plusieurs combats, cartes, tours, animations, tailles de fenêtre et valeurs PA/PM, sans données sensibles.
- Ajouter un manifeste d'annotations : points d'ancrage, cellules visibles, cellule joueur, cellules ennemies, PA, PM, état du tour.
- Définir les métriques : erreur pixel des centres, exactitude d'identité cellule, rappel/précision des entités, matrice de confusion chiffres, taux `UNKNOWN`, latence.
- Tests : lecteur du corpus, validation du manifeste et benchmark reproductible.

**Critère de sortie :** jeu minimal couvrant explicitement la confusion 1/7 et les cas de contours perturbés.

### LOT 3B-1 — Référentiels et `LayoutTransform`

**But :** éliminer toute ambiguïté entre écran, client, zone combat et grille.

- Introduire les types de coordonnées et la transformation unique.
- Ajouter signature de layout et invalidation documentée.
- Migrer les calibrations existantes sans les supprimer ; marquer celles dont la conversion est incertaine.
- Tests : aller-retour, offsets, tailles, ratios, incompatibilités et sérialisation SQLite.

**Compatibilité :** aucun changement du simulateur ou du résultat métier.

### LOT 3B-2 — Grille logique projetée

**But :** stabiliser origine et numérotation.

- Créer topologie, profil et projecteur indépendants d'OpenCV.
- Calibration interactive par au moins trois points, affichée en plein écran comme l'outil actuel.
- Persister transformation, résidus et masque de cellules.
- Conserver le détecteur de contours comme suggestion et overlay, sans autorité sur les identifiants.
- Tests : projections synthétiques, bruit d'annotation, cellules manquantes, stabilité de numérotation entre frames.

**Critère de sortie :** aucune cellule ne change d'identifiant lorsqu'un contour est masqué dans les fixtures.

### LOT 3B-3 — Validation et ajustement de grille

**But :** détecter une dérive sans reconstruire la topologie.

- Score de bord/centre sur plusieurs ancres réparties.
- Ajustement borné translation/échelle ou affine robuste, avec rejet des valeurs aberrantes.
- Passage explicite à `UNKNOWN` lorsque résidu ou couverture sont insuffisants.
- Tests : faux losanges, sprites, décor, petite translation, changement de ratio et animation.

### LOT 3B-4 — Lecteur PA/PM spécialisé

**But :** supprimer la confusion récurrente 1/7.

- Construire les templates à partir du corpus réel, avec variantes justifiées.
- Segmenter, normaliser, classer et exposer la marge.
- Ajouter contraintes de plage et consensus temporel.
- Garder RapidOCR comme signal secondaire.
- Tests : matrice 0–9, nombres à deux chiffres, 1 contre 7, images vides, flou et changement d'échelle.

**Critère de sortie :** seuil d'acceptation défini sur un jeu de test séparé ; les cas ambigus retournent `None`.

### LOT 3B-5 — Entités par cellule et suivi global

**But :** fiabiliser joueur et ennemis.

- Détecter marqueurs au sol dans les sous-régions projetées.
- Ajouter comparaison au fond et preuves par classe.
- Confirmer le joueur par calibration manuelle initiale ou signature stable.
- Remplacer l'association gloutonne par une affectation globale avec états d'occlusion.
- Tests : croisements, disparition courte, deux ennemis voisins, animation et déplacement impossible.

### LOT 3B-6 — États combat, tour et fin

**But :** remplacer les scores d'activité génériques par des signaux sémantiques.

- Templates ou classifieurs simples des contrôles actifs/inactifs.
- Fusion temporisée des signaux grille, HUD, bouton et résultats.
- Journaliser les preuves qui ont produit chaque transition.
- Tests : traces de frames annotées avec débuts/fins et désaccords.

### LOT 3B-7 — Recette réelle d'observation

**But :** vérifier l'ensemble sans exécuter d'action de combat.

- Rejouer le corpus et observer des sessions réelles en lecture seule.
- Mesurer les métriques définies en 3B-0 et la latence sur le package Windows.
- Documenter tailles de fenêtre, DPI, mode tactique, thèmes et cas non couverts.
- Conserver l'ancien pipeline disponible jusqu'à atteinte des critères d'acceptation.

**Critère de sortie :** rapport chiffré. Une démonstration visuelle isolée ne suffit pas.

## 16. Techniques à écarter explicitement

- Pixel absolu unique pour une transition d'état.
- Capture du bureau supposée alignée en `(0, 0)`.
- Redimensionnement forcé du bureau vers une résolution cible.
- Origine de grille choisie par l'élément visuel le plus haut.
- Renumérotation de la grille à chaque frame.
- Absence d'état `UNKNOWN` pour forcer une décision.
- Seuil de couleur exact sans tolérance ni contexte.
- Centaines de coordonnées dispersées dans les scénarios.
- Tables de pixels sans taille client, signature et transformation.
- `sleep()` fixe à la place d'un événement observé et temporisé.
- Survol de toutes les entités dans la boucle normale de perception.
- Logique OpenCV ou PyAutoGUI dans le moteur métier.
- Valeur PA/PM corrigée silencieusement par une règle de jeu supposée.
- Machine learning complexe avant l'existence d'un corpus et d'un benchmark.

## 17. Risques et inconnues

1. **Topologie exacte de DOFUS 2.64.5.0.** Les dépôts audités ciblent souvent DOFUS Retro ou DOFUS 3. Aucun ne démontre une table universelle directement compatible avec le client visé.
2. **Masque des cartes.** Une grille théorique donne des centres, mais pas nécessairement les cellules jouables ou bloquées de chaque carte. Il faudra décider si le masque vient d'une calibration, d'une observation prudente ou d'une source autorisée.
3. **Mode tactique.** Sadidauto montre son intérêt sur Retro ; la stabilité et l'apparence précise dans 2.64.5.0 doivent être vérifiées sur le serveur privé.
4. **DPI Windows et rendu.** Les captures peuvent varier selon mise à l'échelle, mode fenêtré, plein écran, antialiasing et méthode de capture. Le corpus doit inclure les configurations supportées.
5. **Animations et occultations.** Certaines cellules ou marqueurs peuvent être invisibles plusieurs frames. Le validateur ne doit pas confondre occlusion et dérive géométrique.
6. **Couleurs d'équipe et thèmes.** Les seuils rouge/bleu observés dans les projets Retro ne prouvent pas ceux de 2.64.5.0.
7. **PA/PM modifiés.** Les plages et transitions varient avec les effets. Elles peuvent rejeter une absurdité visuelle, mais ne doivent pas remplacer la lecture.
8. **Identité des ennemis.** Une piste visuelle ne garantit pas le nom ou les statistiques de l'entité. Le suivi proposé stabilise une identité locale seulement.
9. **Validation réelle actuelle.** PythonBot a été confronté à des erreurs réelles, mais le présent audit n'a pas exécuté les dépôts externes ni validé le nouveau design dans DOFUS. Les fiabilités annoncées sont des estimations d'ingénierie.
10. **Licences et assets.** La plupart des dépôts imposés ne donnent pas une autorisation claire de réutilisation. Les templates d'interface peuvent aussi être soumis aux droits de leur éditeur. PythonBot doit construire ses propres fixtures de test et ses propres implémentations.

## Décision proposée

Commencer par le LOT 3B-0, puis 3B-1 et 3B-2. La correction prioritaire est la stabilité des identifiants de cellules ; elle conditionne la détection des entités, le suivi et toute décision future. Le lecteur PA/PM spécialisé peut ensuite être développé indépendamment en 3B-4 dès que le corpus de glyphes est disponible.

Le résultat attendu n'est pas une grille visuellement parfaite sur chaque frame. C'est une observation stable, traçable et capable de répondre `UNKNOWN` lorsque les preuves ne permettent pas d'agir.
