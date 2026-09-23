# LOT 3B-2A — rapport Game Data Probe

Date : 23 septembre 2026. Version livrée : **0.3.3-gamedata**, préalable au LOT 3B-2.
Projet traité : C:\Users\Thoma\OneDrive\Documents\pythonbot_test.

> **Mise à jour (LOT 3B-2A-R, 0.3.3-gamedata.1)** : ce rapport décrit le probe validé
> sur fixtures synthétiques. La validation sur le client réel, qui remplace les
> conclusions « Non testé » ci-dessous, est dans [GAME-DATA-REAL-VALIDATION.md](GAME-DATA-REAL-VALIDATION.md).

## Résultat principal

**NO — le remplacement de la découverte visuelle de la topologie n'est pas démontré
avec les données disponibles pendant cette intervention.**

Ce résultat exprime une absence de preuve, pas une impossibilité du format :
aucun dossier du client privé n'était enregistré dans les réglages du projet,
ni fourni pendant l'intervention. Le chemin Alpha5 cité dans le cahier des
charges n'existe pas sur cet hôte ; le projet effectivement accessible est celui
indiqué ci-dessus. Aucun disque n'a été parcouru à la recherche du client.

Un socle indépendant est ajouté et testé. Sur des données synthétiques, il sait
indexer D2P/D2O et lire les cellules du schéma historique DLM v11 non chiffré.
Le verdict d'une telle map est **PARTIAL**, avec coordonnées, voisinage,
placements de combat et compatibilité cible explicitement non vérifiés.

**Recommandation : B provisoire — conserver la vision et évaluer GameData comme
source partielle.** La première étape nécessaire est une analyse du dossier réel.
Ce n'est pas une autorisation de commencer le LOT 3B-2. La piste A exige une
validation réelle des coordonnées et de la topologie ; la piste C serait
prématurée sans avoir pu examiner les fichiers du client.

## État des preuves

| Étape | Fixtures synthétiques | Client privé 2.64.5 |
| --- | --- | --- |
| FORMAT PARSÉ | Index D2P 2.1, index D2O, enveloppe et schéma DLM v11 non chiffré | Non testé |
| MAP PARSÉE | Oui, avec lecture exacte et refus des données résiduelles | Non testé |
| TOPOLOGIE EXTRAITE | Non complète : IDs et propriétés, sans coordonnées ni voisinage vérifiés | Non démontré |
| FIGHT CELLS TROUVÉES | Non ; seuls des indices rouge/bleu sont conservés | Inconnu |
| COMPATIBILITÉ PYTHONBOT DÉMONTRÉE | Contrat provider et UI testés ; pas d'intégration au pipeline de grille | Non démontrée |

Le simple fait de reconnaître un index ne certifie pas tout son format, ses
objets ou ses maps. La version déclarée dans un fichier de version ne certifie
pas davantage le contenu binaire.

## Les 19 éléments du livrable

| Élément | Résultat |
| --- | --- |
| 1. Chemin client testé | Aucun client réel. Arrêt propre avec « Dossier du client DOFUS non configuré ». Répertoires de fixtures sous .pytest_tmp/ et data/gamedata/synthetic-*/ uniquement. |
| 2. Version détectée | Client réel : non déterminée. PythonBot : 0.3.3-gamedata. Les tests vérifient des déclarations version.txt/application.xml sans les assimiler à une certification. |
| 3. Fichiers trouvés | Client réel : inventaire non effectué. Benchmark : une archive entièrement synthétique de 59 904 octets, 1 000 entrées candidates. |
| 4. Formats reconnus | D2P 2.1 : index et propriétés ; D2O brut : header et index seulement ; DLM brut/zlib : enveloppe, puis schéma v11 non chiffré. Autres versions, AKSF et chiffrement non pris en charge. |
| 5. Architecture | discovery → lecteurs formats internes → LocalGameDataProvider → modèles purs → diagnostic UI. Le contrat GameDataProvider n'expose aucune structure binaire. |
| 6. Maps lisibles | Réel : inconnu, pas « zéro map existante ». Benchmark : 1 map effectivement chargée parmi 1 000 IDs indexés. L'index seul ne prouve jamais la lisibilité de toutes les maps. |
| 7. Nombre/type de cellules | Le schéma historique accepté exige 560 enregistrements, dont les sentinelles de cellules absentes. Ce nombre n'a pas été mesuré sur le client cible. Le benchmark inclut 6 cellules renseignées et 554 sentinelles. |
| 8. Cell IDs | Indices implicites 0..559 dans l'ordre du flux du schéma historique ; contrôles des IDs graphiques et de l'identifiant de map. Stabilité réelle 2.64.5 non vérifiée. DofusCellId est distinct de Cell(x,y). |
| 9. Conversion cell ID → grille | Non implémentée : aucune formule n'est promue sans validation réelle. GridCoordinate est défini, logical_position reste None ; voisinage et largeur/hauteur restent inconnus. |
| 10. Walkability | Bit de mouvement lu dans le schéma v11 ; restriction combat et restriction RP séparées. Une cellule marchable hors combat n'est pas automatiquement utilisable en combat. Sentinelles : None. |
| 11. LOS | Drapeau statique du schéma historique, séparé de la walkability. Les obstacles dynamiques ne sont pas déterminés. |
| 12. Fight cells | Inconnu. fight_start_allowed et get_fight_cells restent None. Les hints rouge/bleu et non_walkable_during_fight ne constituent pas une preuve de placement d'équipe. |
| 13. Code tiers réutilisé | Aucun code provenant des dépôts étudiés n'est intégré. Aucun framework ajouté. |
| 14. Licences | Inventaire avec commits et fichiers dans GAME-DATA-SOURCES.md. LaBot : MIT, non utilisé ; PyDofus/VLD : CONCEPT_ONLY faute de licence explicite identifiée. |
| 15. Code indépendant | Lecture binaire bornée, contrôles D2P/D2O, lecteur DLM expérimental, inventaire, cache, provider, modèles, CLI, UI et tests. |
| 16. Tests | 117 tests de la suite complète réussis en 9,89 s ; 45 concernent directement le nouveau module et son UI. Détail ci-dessous. |
| 17. Performances | Mesures synthétiques uniquement, tableau ci-dessous. Aucune extrapolation au volume d'un client réel. |
| 18. Limites | Aucun fichier réel, pas de déchiffrement, pas de coordonnées vérifiées, pas de classes D2O ni placement d'équipe décodés, pas de renderer, pas de build EXE dans ce lot. |
| 19. Recommandation | B provisoire pour poursuivre la validation du probe, en gardant la topologie visuelle actuelle. Aucun démarrage automatique de 3B-2. |

## Architecture et garanties

### Lecture et modèles

Les modèles GameMap, GameMapCell et GridTopology sont indépendants de Qt, OpenCV,
du simulateur, de l'observateur et de LayoutTransform. Le provider propose :

- scan_client() ;
- list_maps() — IDs candidats, pas garantie de lisibilité ;
- get_map(map_id), get_map_cells(map_id) ;
- get_fight_cells(map_id) — None lorsque l'information n'est pas vérifiée ;
- get_map_topology(map_id) — propriétés partielles, coordinates_verified=False et adjacency=None.

Pour D2P, un ID extrait du nom est vérifié contre l'en-tête DLM au chargement.
Les doublons de map ID sont refusés, sans choix implicite de version.
Pour les DLM isolés, l'identifiant vient de l'en-tête, pas du nom de fichier.

Le lecteur v11 parcourt strictement les métadonnées, saute les blocs graphiques
connus puis lit les cellules. Un élément inconnu, un flux tronqué ou un reliquat
inattendu provoque un refus. Le floor sentinelle ne devient pas une cellule
marchable par défaut. Le champ historique speed n'est pas traduit en coût de
déplacement, car cette équivalence n'est pas démontrée.

### Lecture seule, configuration et frontières

Le chemin est persisté avec la clé globale dofus_client_directory, dans la table
settings déjà existante ; aucune migration SQL. Le sélecteur ne cherche pas le
client automatiquement. Les liens/jonctions sont ignorés, et une racine de disque
est refusée. Les propriétés de liaison D2P ne déclenchent aucune ouverture de
chemin externe. Les archives ne sont pas extraites globalement.

Aucun accès au processus DOFUS, aucun mécanisme réseau du jeu, aucune entrée
clavier/souris ni modification des fichiers client. Les seuls accès Internet
de l'intervention concernent les références GitHub publiques et la préparation
de l'environnement de tests, pas le fonctionnement du probe.

Tous les caches/exports sont dans les données PythonBot. Une destination située
dans le client est refusée, même si elle a été explicitement choisie comme cache.
Les tests vérifient l'identité des octets et dates des fixtures après lecture.

### Cache et limites

Un scan est lancé uniquement par l'utilisateur ou le CLI, jamais à chaque frame.
Un nouveau clic Analyser reparcourt le dossier choisi pour invalider l'index
par chemin, taille et mtime_ns. Le cache inclut une version du parser et le chemin
racine. Un JSON invalide ou incomplet est reconstruit. Les maps décodées disposent
d'un cache LRU de huit éléments, avec vérification des fichiers avant réutilisation.

Limites : 100 000 fichiers inventoriés, index limité à 32 Mio, map et résultat
décompressé limités à 16 Mio. Le hash SHA-256 est proposé dans l'inventaire via
hashes=True, mais n'est pas recalculé à chaque scan courant. Un remplacement de
fichier conservant exactement sa taille et sa date n'est donc pas détecté par le
cache par défaut. Un nouveau fichier nécessite un clic Analyser.

Les mesures mémoire utilisent tracemalloc : pic d'allocations Python, pas RSS,
mémoire native complète ou mémoire du client DOFUS.

## Tests et performances

Commandes réellement exécutées :

```powershell
.\.venv\validation\Scripts\python.exe -m pytest tests/test_gamedata.py tests/test_gamedata_ui.py -q
.\.venv\validation\Scripts\python.exe -m pytest -q
.\.venv\validation\Scripts\python.exe -m compileall -q combatbot/gamedata combatbot/ui/gamedata_panel.py scripts/benchmark_gamedata.py
.\.venv\validation\Scripts\python.exe -m combatbot.gamedata --output data/gamedata/no-client
.\.venv\validation\Scripts\python.exe scripts/benchmark_gamedata.py
```

La première exécution ciblée a passé 43 tests. Deux cas complémentaires de
corruption/validation ont ensuite été ajoutés : le dernier passage complet
contient **117 tests réussis**, dont **45 GameData/UI**. Le CLI sans client se
termine volontairement avec le code 1 et un rapport NOT_CONFIGURED, sans traceback.

Couverture : mauvais dossier, dossier vide, permissions refusées, magie/version
inconnue, index corrompu, tronquage, bornes d'archive, chaînes dangereuses,
décompression limitée, versions/chiffrement non pris en charge, map absente,
ID contradictoire ou ambigu, cellules absentes, flags de mouvement/LOS,
non-assimilation des hints aux fight cells, sérialisation JSON, cache périmé,
cache invalide, exports hors client, persistance du dossier et tâches Qt.
Les anciens tests de vision, corpus, coordonnées, stockage, packaging et
simulation passent également.

Toutes les fixtures GameData sont construites dans tests/test_gamedata.py.
Elles ne proviennent pas d'un client DOFUS. Le script de benchmark réutilise
leurs constructeurs et stocke ses résultats dans data/, ignoré du dépôt.

Dernière mesure, Windows, Python 3.12.14, une archive synthétique de 1 000 maps :

| Mesure | Résultat |
| --- | ---: |
| Scan initial, index compris | 172,697 ms |
| Construction/écriture de l'index | 159,847 ms |
| Nouveau scan avec cache d'index | 27,426 ms |
| Lecture/validation de l'index en cache | 25,525 ms |
| Chargement froid d'une map, sous tracemalloc | 8,497 ms |
| Chargement de la même map en mémoire | 0,559 ms |
| Pic allocations Python, scan initial | 701 827 octets |
| Pic allocations Python, scan avec cache | 630 251 octets |
| Pic allocations Python, lecture de map | 154 729 octets |

Un premier passage donnait 128 ms pour le scan initial et 6,6 ms pour la map.
Ces variations et la très petite taille des données synthétiques interdisent
une estimation fiable pour une installation réelle. Détails mesurés dans
data/gamedata/benchmark-synthetic.json.

Le .venv fourni pointait vers l'interpréteur absent d'un autre compte Windows.
Les tests utilisent un environnement isolé .venv/validation avec les dépendances
de requirements.txt. Ce fichier de dépendances n'a pas été modifié.

## Fichiers concernés

- combatbot/gamedata/ : modèles, discovery, erreurs, lecteurs internes, provider et CLI.
- combatbot/ui/gamedata_panel.py : nouvel outil de diagnostic.
- combatbot/ui/pages.py : nouvel onglet dans Paramètres.
- combatbot/ui/main_window.py : partage du gestionnaire de travaux existant pour gérer correctement la fermeture.
- combatbot/__init__.py : version pré-3B-2.
- tests/test_gamedata.py et tests/test_gamedata_ui.py : fixtures et vérifications.
- scripts/benchmark_gamedata.py : mesure reproductible synthétique.
- README.md, GAME-DATA-SOURCES.md et ce rapport.

Aucun algorithme du moteur de combat, de calibration, de grille visuelle ou de
LayoutTransform n'a été modifié. Le paquet dist/PythonBot existant n'a pas été
reconstruit ; lancer les sources pour accéder au nouvel onglet.

## Validation réelle à effectuer avant décision 3B-2

1. Renseigner le dossier exact du client privé dans Paramètres → Données du client.
2. Lancer Analyser et examiner les preuves de version et les diagnostics par format.
3. Charger plusieurs IDs représentatifs ; conserver les rapports des succès et refus.
4. Vérifier les IDs, flags et coordonnées contre une source indépendante du serveur
   privé ou des données réelles de référence. Une simple réussite de parsing ne suffit pas.
5. Identifier précisément les placements si une source locale explicite existe ;
   ne pas les dériver de la walkability.
6. Seulement ensuite décider d'une conversion cellId/grille et d'une intégration
   GameData + transformation visuelle. LayoutTransform reste nécessaire pour les pixels.

**Arrêt à la fin de ce lot. Validation utilisateur requise avant le LOT 3B-2.**
