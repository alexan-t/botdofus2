# LOT 3B-2A-R — validation GameData sur le client réel

Date : 23 septembre 2026. Reprise du LOT 3B-2A après le premier scan réel.
Client analysé en **lecture seule** : `C:\Users\Thoma\AppData\Local\Alea\Client`.
Version PythonBot : **0.3.3-gamedata.1** (voir « Version » en fin de rapport).

Exports produits (uniquement nos diagnostics, aucune copie de D2P/DLM/D2O ni d'asset) :
`data/gamedata/real-validation/` → `inventory.json`, `d2p-layouts.json`,
`map-validation.json`, `summary.json`, `map_<id>.json` (10 maps), `run.log`.
`inventory-initial.json` et `archive-preflight.json` sont les traces du premier
scan de l'intervention précédente ; ils sont conservés tels quels.

## Résumé

| Niveau de preuve | Résultat réel |
| --- | --- |
| ARCHIVE RECONNUE | 248/248 D2P ; 8 archives de maps |
| ENTRY INDEXÉE | 12 154 entrées DLM, 12 154 map IDs uniques |
| DLM EXTRAIT LOGIQUEMENT | 12 154 (zlib + enveloppe valide) |
| DLM PARSÉ | **12 153** (v11, lu à l'octet près) ; 1 refus (map 0) |
| CELLULES PARSÉES | 12 153 maps × 560 cellules |
| TOPOLOGIE DÉMONTRÉE | **Oui, topologie logique** (cellId ↔ (x, y), voisinage). **Non** pour la projection écran |
| FIGHT CELLS DÉMONTRÉES | **Non** : indices rouge/bleu très plausibles, sans preuve de placement |

**Verdict : A — GameData comme source de topologie logique**, la vision restant
obligatoire pour la projection écran et l'état dynamique (détails au point 24).

## État de reprise constaté

- Le dossier n'est **pas un dépôt git** : `git status`, `git branch` et `git log`
  échouent (« not a git repository »). Les fichiers modifiés par l'intervention
  précédente ont été conservés. Une copie de sauvegarde a été faite avant toute
  modification, dans le scratchpad de session.
- `formats/archives.py` contenait déjà une branche « index initial » non terminée.
  Elle ne vérifiait pas le pavage exact des régions et refusait encore l'archive vide
  `gfx26.d2p`.
- `scripts/inspect_client_abc.py` (inspection statique SWF/ABC) n'a pas été utilisé ; il a été
  supprimé au LOT 3B-2 (voir LOT-3B-2-GAMEDATA-GRID.md).
- `.pytest_tmp` (basetemp de `pytest.ini`) est verrouillé par ses droits d'accès ;
  les tests ont été lancés avec `--basetemp` vers un dossier temporaire.

## Réponses aux 24 questions

### 1. Combien de D2P réels ?
**248** : 114 `gfx/sprites`, 74 `audio`, 32 `gfx/world`, 10 `gfx/items`,
8 `maps`, 7 `gfx/maps`, 3 `gfx/monsters`. **0 erreur** après correction.

### 2. Combien de D2O réels ?
**165** dans `data/common/`. Leur index est valide pour les 165.

### 3. Pourquoi le premier scan trouvait 0 map ?
Le lecteur imposait la disposition Ankama classique
`en-tête | données | index | propriétés | footer`, avec
`début données + taille données ≤ début index`.
Les 40 archives Alea `content/maps/*` et `content/gfx/world/*` utilisent
`en-tête | propriétés | index | données | footer`. Le contrôle levait alors
« Bornes données/index D2P incohérentes » sur les 8 archives de maps : aucune entrée DLM n'était indexée.
Le scan initial montrait exactement 40 erreurs D2P, soit 8 maps et 32 gfx/world.

### 4. Quelle correction D2P a été réalisée ?
Détection structurée, pure et testable (`detect_d2p_layout`, `D2PLayout`,
`D2PLayoutVariant`), fondée **uniquement sur les offsets du footer**, jamais sur le
nom du fichier :

| Variante | Condition exacte | Régions (demi-ouvertes) |
| --- | --- | --- |
| `DATA_BEFORE_INDEX` (208 archives) | f0 = 2, f2 = f0 + f1, f2 ≤ f4 ≤ fin | données [f0, f2), index [f2, f4), propriétés [f4, fin) ; f5 = nb propriétés |
| `INDEX_BEFORE_DATA` (40 archives) | f4 = 2, f1 = f3, f4 ≤ f2 ≤ f0 ≤ fin, ou archive vide f0 = 0, f3 = 0, f2 = fin | propriétés [2, f2), index [f2, f0), données [f0, fin) ; f5 = fin des propriétés (37 archives) ou nb propriétés (3) |
| `UNKNOWN` | tout le reste | refus `UNKNOWN_LAYOUT` |

(fin = taille − 24 ; footer = six entiers non signés big-endian f0..f5.)

Garanties : les régions pavent le fichier sans trou ni chevauchement.
L'index est consommé exactement et le nombre d'entrées est borné par la taille
de l'index. Les offsets sont relatifs à la région de données, et
`offset + taille ≤ taille données` est vérifié sans débordement possible.
Les offsets et tailles négatifs sont refusés, comme les entrées qui se
chevauchent (aucune mesurée), les doublons et les noms dangereux.
Les propriétés sont consommées exactement et le champ f5 doit correspondre.
Mesure réelle : 0 octet de données non référencé, 0 chevauchement et
0 entrée de taille nulle sur les 248 archives.

L'ancienne fixture synthétique (`make_d2p`) représente fidèlement la variante
`DATA_BEFORE_INDEX` des 208 archives réelles. Elle était donc correcte et a été conservée.

### 5. Combien d'archives maps ?
**8**, chaînées par la propriété `link` (qui n'est jamais suivie) :

| Archive | Taille (octets) | Entrées | DLM | Variante | Erreurs |
| --- | ---: | ---: | ---: | --- | --- |
| content/maps/maps0.d2p | 9 435 648 | 2 774 | 2 774 | INDEX_BEFORE_DATA | 0 index ; map 0 refusée au parsing |
| content/maps/maps1.d2p | 10 238 079 | 2 322 | 2 322 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps2.d2p | 8 363 321 | 1 436 | 1 436 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps3.d2p | 11 428 173 | 1 809 | 1 809 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps4.d2p | 7 778 802 | 1 708 | 1 708 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps5.d2p | 4 898 735 | 1 655 | 1 655 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps6.d2p | 1 690 131 | 440 | 440 | INDEX_BEFORE_DATA | 0 |
| content/maps/maps7.d2p | 36 596 | 10 | 10 | INDEX_BEFORE_DATA (sans propriété) | 0 |

Aucune autre archive ne contient d'entrée `.dlm`. `gfx/maps/worldmap*.d2p` contient
des images de carte du monde, pas des maps.

### 6. Combien d'entrées DLM ?
**12 154**, toutes nommées `<n>/<mapId>.dlm`. 12 154 IDs uniques : aucun doublon
entre archives, et le nom correspond à l'ID d'en-tête dans 12 154 cas sur 12 154.

### 7. Combien de maps ont réellement été parsées ?
**12 153 / 12 154**, lecture exacte jusqu'au dernier octet (le reliquat est refusé).
Validation complète en **64 s**.

Échec unique : **map 0** (`maps0.d2p::0/0.dlm`) :
`DLM_PARSE_ERROR map=0 version=11 offset=9436 stage=end_of_stream expected=end_of_stream remaining=41531 last=cell[559].floor next=8080ff00007b92038901bc13b3fb7812`.
L'analyse forensique hors code montre un compteur de fixtures de premier plan
(1 octet) valant 161, alors que 417 fixtures sont écrites (417 mod 256 = 161).
Avec 417, le flux se termine exactement. Il s'agit d'un débordement à l'écriture
d'une map technique (sous-zone 10, voisins incohérents). Cette réparation
**n'est pas implémentée** : aucune heuristique ne corrige un compteur.

Dix maps réelles exportées (`map_<id>.json`) :

| Map ID | Entrée | Magic | Version | Compression | Chiffrée | Compressé | Décodé | Résultat |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |
| 104334849 | 9/104334849.dlm | M | v11 | zlib | non | 3 082 | 13 722 | 560 cellules, 168 marchables, rouge/bleu 12/12 |
| 106698254 | 4/106698254.dlm | M | v11 | zlib | non | 2 789 | 12 375 | 560, 95, 12/12 |
| 25427968 | 8/25427968.dlm | M | v11 | zlib | non | 5 615 | 34 342 | 560, 238, 8/8 |
| 34473218 | 8/34473218.dlm | M | v11 | zlib | non | 3 330 | 18 814 | 560, 188, 12/12 |
| 66847744 | 4/66847744.dlm | M | v11 | zlib | non | 898 | 6 619 | 560, 99, 11/11 |
| 87294471 | 1/87294471.dlm | M | v11 | zlib | non | 2 660 | 12 249 | 560, 145, 10/10 |
| 99096073 | 3/99096073.dlm | M | v11 | zlib | non | 561 | 4 395 | 560, 7, 3/3 |
| 134657 | 7/134657.dlm | M | v11 | zlib | non | 4 438 | 23 589 | 560, 229, 0/0 |
| 155661 | 1/155661.dlm | M | v11 | zlib | non | 6 671 | 39 766 | 560, 506, 0/0 |
| 159755 | 5/159755.dlm | M | v11 | zlib | non | 8 817 | 41 131 | 560, 479, 0/0 |

### 8. Quelles versions DLM ?
v9 : 0 · v10 : 0 · **v11 : 12 154** (12 153 parsées, 1 refus de reliquat) · v12 : 0 · inconnue : 0.
Toute autre version est refusée (`UNKNOWN_VERSION`), sans forçage vers le lecteur v11.

### 9. Maps chiffrées ?
**0.** Toutes les maps ont un drapeau de chiffrement à 0,
`encryptionVersion = 1`, et une longueur déclarée égale au contenu restant.
Aucune clé n'a été recherchée ni utilisée. Une map marquée chiffrée serait signalée
`ENCRYPTED` sans être lue (testé).

### 10. Nombre réel de cellules ?
**560 pour 12 153 / 12 153 maps** (min = max = 560). Aucune cellule absente
(marqueur de sol −128 jamais rencontré). 560 est une constante du schéma et non un
champ du fichier. La lecture exacte jusqu'à la fin du flux valide ce nombre sur
tout l'échantillon réel : c'est un fait observé sur ce client, pas une vérité universelle.

Champs réellement rencontrés :
- sol de −1000 à 820 (104 valeurs) ;
- vitesse : −5, 0 ou 5 ;
- `moveZone` : 0 ou 1 ;
- `linkedZone`, présente seulement si la cellule est marchable et que le bit 7 est à 0 : valeur 17 majoritaire ;
- `mapChangeData`.

Bits de drapeaux (nombre de cellules / de maps) :

| Bit | Nom historique (hypothèse) | Cellules | Maps |
| --- | --- | ---: | ---: |
| 0 | non marchable | 3 433 492 | 12 141 |
| 1 | non marchable en combat | 104 108 | 9 028 |
| 2 | non marchable en RP | 2 149 | 1 371 |
| 3 | bloque la LOS | 503 344 | 11 899 |
| 4 | bleu | 21 201 | 2 500 |
| 5 | rouge | 21 471 | 2 500 |
| 6 | visible | 6 805 680 | 12 153 |
| 7 | ferme (conditionne `linkedZone`) | 34 107 | 539 |
| 8 | havre-sac | 9 381 | 57 |
| 9–12 | flèches | 127 / 108 / 34 / 64 | 35 / 33 / 9 / 16 |
| 13 | inconnu | 9 | 4 |
| 14–15 | — | 0 | 0 |

Seuls les bits 0 à 5 sont décodés en champs nommés ; tous restent disponibles dans `raw_flags`.

### 11. Cell IDs disponibles ?
**Oui.** Un DofusCellId est l'**indice implicite 0..559 dans l'ordre du flux**.
Les couches graphiques portent aussi des cellId explicites dans le même espace
(contrôlés : < 560 et uniques par couche). Preuve indépendante :
les **473/473** cellules référencées par `MapReferences.d2o` (mapId + cellId) sont
marchables, contre 49,5 % en moyenne. La probabilité d'un tel résultat par hasard
est négligeable. `DofusCellId` reste un type distinct de `Cell(x, y)` de la vision ;
aucune conversion vers la grille visuelle n'est faite.

### 12. CellId → GridCoordinate démontré ?
**Oui, pour les coordonnées logiques** (`combatbot/gamedata/topology.py`,
`cell_to_grid`). Disposition historique : 40 demi-rangées de 14 cellules,
une sur deux décalée d'une demi-cellule. x − y = demi-rangée, x + y = 2 × colonne + parité.
Critères, tous satisfaits (`validation.topology_decision`) :

1. Bijection sur 560 cellules, aucune collision : x ∈ [0, 33], y ∈ [−19, 13].
   Aucun point extérieur n'est ramené à une cellule.
2. **Bords réels** : les bits `mapChangeData` 0/2/4/6 sont à **100 %** sur la
   colonne 13, les demi-rangées 38–39, la colonne 0 et les demi-rangées 0–1.
   Ce sont exactement les bords droit, bas, gauche et haut prévus.
   Les bits diagonaux 1/3/5/7 se partagent entre les deux bords attendus
   (par exemple bit 1 : 44 % en bas, 56 % à droite). Cela démontre l'ordre
   « 14 colonnes par demi-rangée, depuis le haut à gauche ».
3. **Voisinage physique** : désaccord de marchabilité entre voisins,
   8,80 % pour la formule, 10,83 % pour un rectangle 14 × 40 naïf,
   13,15 % pour le décalage inverse et 36,81 % pour des paires aléatoires.
   Pour la LOS : 7,75 %, 9,50 %, 10,02 % et 13,33 %.
   Map par map, la formule bat le décalage inverse sur **12 137** maps
   (13 égalités, 3 contraires). Le sens du décalage est donc démontré.

### 13. GridCoordinate → CellId démontré ?
**Oui** (`grid_to_cell`) : l'aller-retour est exact sur les 560 cellules et
`None` est renvoyé hors de la map (testé, y compris sur des points parasites).

### 14. Voisinage démontré ?
**Oui, voisinage logique à 4 (arêtes partagées)**, avec en option les 4 coins
(`neighbors(cell, corners=True)`). Il est symétrique. Degrés : 494 cellules à 4,
64 à 2, 2 à 1 (coins 0 et 559). Il est testé au centre, aux bords, aux coins et sur
les IDs invalides. La walkability ne modifie jamais la topologie (testé).
`provider.get_map_topology()` expose `GridTopology(adjacency, coordinates,
coordinates_verified=True)`, où `coordinates_verified` porte **uniquement** sur la
logique, pas sur les pixels. **Non connecté** au combat réel.

### 15. Walkability disponible ?
**Oui, statique** : bit 0 (hors combat). Les bits « non marchable en combat »
(bit 1) et « en RP » (bit 2) sont séparés. Une cellule marchable n'est pas
automatiquement utilisable en combat. Les occupants (joueurs, monstres,
invocations) ne sont **pas** dans ces données.

### 16. LOS disponible ?
**Oui, statique** (bit 3), séparée de la walkability. Les obstacles dynamiques ne le sont pas.

### 17. Red/blue disponibles ?
**Oui**, sur **2 500 maps**, toujours les mêmes (2 500 avec rouge, 2 500 avec bleu,
2 500 avec les deux). Même nombre de rouges et de bleus sur 2 334 d'entre elles.
Les effectifs par couleur culminent à 10 et 12 (rouges : 801 maps à 12 et 635 à 10).
42 665 des 42 666 cellules colorées sont marchables ; 5 sont non marchables en
combat et 6 portent les deux couleurs.

### 18. Red/blue = placement démontré ou non ?
**Non démontré.** Les indices sont très cohérents avec des zones de placement :
paires équilibrées, cellules marchables, petits groupes. Mais aucune capture de
phase de placement n'a pu être comparée : pas d'action dans le jeu pendant ce lot,
et le serveur privé peut imposer ses propres positions. `fight_start_allowed`
reste `None` et `get_fight_cells()` renvoie `None`. Seuls `red_hint`/`blue_hint` sont exposés.

Protocole proposé : l'utilisateur fournit manuellement un map ID connu et une
capture de la phase de placement sur cette map. On superpose ensuite les cellules
rouge/bleu une fois la projection écran calibrée au LOT 3B-2. Il faut au moins
5 maps et les deux camps.

### 19. Fight cells disponibles ?
**Non comme vérité.** Les candidats sont les indices rouge/bleu, avec les bits 1
(interdit en combat) et 0 pour la marchabilité. Il faut une validation manuelle (point 18).

### 20. tacticalModeTemplateId présent ?
**Oui, sur toutes les maps parsées** (champ d'en-tête après le zoom), avec
115 valeurs de 1 à 115. Il est **identique à `MapPositions.tacticalModeTemplateId`
pour 12 153/12 153 maps**. Il est exposé en métadonnée
(`GameMap.metadata["tactical_mode_template_id"]`) sans sémantique supposée.
Le fichier `content/maps/tactical_mode_templates.bin` (zlib) existe, mais n'a pas été décodé.

### 21. Quelles informations D2O utiles ont été trouvées ?
Lecteur de schéma et d'objets ajouté (`formats/d2o.py`, borné, en lecture seule) :

- **MapPositions** (12 154 objets) : `posX`/`posY` (**coordonnées monde de la map**,
  sans rapport avec les coordonnées de cellules), `outdoor`, `subAreaId`, `worldMap`,
  `capabilities`, `nameId`, `tacticalModeTemplateId`… `subAreaId` et le modèle
  tactique concordent avec les DLM à 100 %.
- **MapScrollActions** (2 209) : remplacements des voisins de défilement. 3 412 cibles
  pointent toutes vers des maps existantes. Les voisins d'en-tête DLM qu'elles
  remplacent n'existent presque jamais dans ce client (34 sur 3 412).
  Quand le voisin DLM existe, il est adjacent dans la bonne direction en
  coordonnées monde dans **20 826/20 943** cas (99,4 %) ; 27 669 voisins désignent
  des maps absentes du client.
- **MapReferences** (476) : couples map/cellule, utilisés comme preuve des cell IDs (point 11).
- **MapCoordinates** (6 556) : coordonnées compressées → liste de mapIds. Une même
  coordonnée monde peut désigner plusieurs maps : ambiguïté à prendre en compte.
- **SubAreas** (461), **Areas** (63), **SuperAreas** (9), **WorldMaps** (30) :
  hiérarchie, bornes et mapIds des sous-zones.

Aucune détection automatique de la map active n'a été ajoutée : pas de mémoire,
pas de réseau.

### 22. Quelles données doivent toujours venir de la vision ?
- L'identité de la map active : saisie manuelle pour l'instant. La vision pourrait
  l'estimer via les coordonnées monde affichées et `MapPositions`/`MapCoordinates`,
  mais l'ambiguïté du point 21 impose une confirmation.
- La **projection écran** des 560 cellules : GridCalibration/GridTransform,
  non modifiés dans ce lot.
- Les positions des entités, les obstacles dynamiques et l'occupation des cellules.
- Les cellules de placement réellement proposées, la phase de combat, le tour, les PA/PM et les sorts.
- Toute différence éventuelle entre le serveur privé et les données du client.

### 23. Gain attendu pour LOT 3B-2 ?
- La vision n'a plus à **découvrir** la grille : la topologie (560 cellules,
  voisinage, bords) est connue exactement. La calibration peut se réduire à ajuster
  une projection d'une grille connue, au lieu d'inférer cellules et adjacence.
- Walkability et LOS statiques sont fournies par map, ce qui réduit la vision au
  dynamique (entités, surbrillances).
- Un chemin et une portée calculés sur `DofusCellId` peuvent être vérifiés offline.
- Les indices rouge/bleu donnent des candidats de placement à confirmer visuellement.
- Préalable : connaître le map ID courant (manuel pour démarrer).

### 24. Verdict
**A — GAMEDATA COMME SOURCE DE TOPOLOGIE (topologie logique).**
Les cell IDs, les coordonnées logiques aller-retour, le voisinage et la stabilité
(12 153 maps, un seul schéma, 560 cellules) sont démontrés sur les données réelles,
avec des contrôles indépendants : D2O, bords réels, marchabilité et hypothèses
concurrentes. Réserves explicites, qui ne relèvent pas de la topologie :
projection écran non démontrée, fight cells non démontrées et map active non
détectée automatiquement. La vision reste indispensable pour ces trois points.
Le provider **n'est pas** connecté au combat réel : l'intégration relève du LOT suivant.

## Diagnostics forensiques

Tout échec DLM lève `DlmParseError` avec `diagnostic` borné :
`map`, `version`, `offset` (dans le flux décodé), `stage` (par exemple `cell[559]`,
`layer.graphical_element[1]`), `expected`, `remaining`, `last` (dernier champ
validé) et `next` (16 octets en hexadécimal au maximum). Pas de méga-dump.
L'en-tête complet est exposé dans `GameMap.metadata` : sous-zone, voisins, couleurs,
zoom, modèle tactique, nombre de fixtures, CRC du sol, couches, éléments graphiques
et enveloppe.

## Performances

### SYNTHÉTIQUE (inchangé dans son principe, rejoué)
Archive synthétique de 1 000 maps, 59 904 octets (`data/gamedata/benchmark-synthetic.json`) :
scan initial 0,149 s ; index 0,140 s ; scan avec cache 0,026 s ; map froide
0,011 s ; map en mémoire 0,0006 s ; pic Python 0,70 Mo. **Non représentatif du client.**

### CLIENT RÉEL (Windows, Python 3.12.14 ; `summary.json`)

| Mesure | Résultat |
| --- | ---: |
| Scan initial (inventaire de 10 940 fichiers + index 248 D2P + 165 D2O) | 22,96 s |
| dont construction de l'index | 14,09 s |
| Scan avec cache d'index (inventaire + lecture du cache) | 8,75 s |
| dont lecture du cache d'index | 0,19 s |
| Chargement froid d'une map (sous tracemalloc) | 0,035 s |
| Chargement de la même map depuis le cache mémoire | 0,001 s |
| Validation complète des 12 154 maps (sans tracemalloc) | 64,4 s (≈ 5,3 ms/map) |
| Pic d'allocations Python : scan initial / scan en cache | 16,8 Mo / 16,5 Mo |
| Pic d'allocations Python : map froide | 0,20 Mo |
| Pic d'allocations Python : validation de 500 maps (27,7 s sous tracemalloc) | 1,0 Mo |

Le scan en cache reste dominé par l'inventaire : lecture de 32 octets d'en-tête
pour 10 940 fichiers. La validation de toutes les maps n'est jamais automatique :
elle se lance par un bouton ou par le script. tracemalloc mesure les allocations
Python, pas la mémoire RSS.

## Interface

**Paramètres → Données du client** affiche désormais : dossier, D2P (par variante),
D2O, maps candidates et archives de maps, maps lisibles, versions DLM, maps
chiffrées, cellules, topologie logique vérifiée (oui/non **pour ce dossier**) et
fight cells vérifiées (non). Le bouton **Valider toutes les maps** travaille en
tâche de fond, avec progression et possibilité d'arrêt. Il écrit
`data/gamedata/validation-summary.json`. Le bouton **Détails** affiche les
diagnostics. La fiche d'une map indique aussi la sous-zone et le modèle tactique.

## Tests et validation exécutés

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -q --basetemp=<temp>                  # 168 passed
.\.venv\validation\Scripts\python.exe -m pytest -q tests/test_gamedata*.py --basetemp=<temp>   # 96 passed
.\.venv\validation\Scripts\python.exe -m compileall -q combatbot scripts tests main.py   # OK
.\.venv\validation\Scripts\python.exe scripts/validate_gamedata_real.py --client "C:\Users\Thoma\AppData\Local\Alea\Client"
.\.venv\validation\Scripts\python.exe scripts/benchmark_gamedata.py
.\.venv\validation\Scripts\python.exe -m combatbot.gamedata --client "…\Alea\Client" --map-id 87294471 --output <temp>
```

Nouveau fichier `tests/test_gamedata_real_formats.py` (50 tests, fixtures générées
par code et minimales), plus un test UI de validation complète :

- **D2P** : index avant données ; données avant index ; entrée après l'index ;
  entrée hors région (offset, taille, négatif, 2³¹−1) ; région d'index trop petite
  ou non consommée ; 7 footers incohérents refusés ; entrées chevauchantes ;
  archive vide chaînée (43 octets, forme mesurée) ; variantes du champ propriétés ;
  détection indépendante du nom de fichier ; découverte de maps sur la disposition
  réelle ; map IDs dupliqués refusés.
- **DLM** : enveloppe v11 réelle ; 560 cellules et métadonnées ; v9/v10/v12 refusées ;
  map chiffrée signalée et non lue ; tronquage avec diagnostic ; reliquat refusé ;
  débordement de compteur non réparé ; drapeaux et octets bruts préservés.
- **Topologie** : bijection, coordonnées connues, voisinage centre/bords/coins,
  IDs invalides, points extérieurs, indépendance vis-à-vis de la walkability.
- **D2O** : schéma et objets (vecteurs, types primitifs), table de classes corrompue.

Anciennes garanties : vision, corpus, coordonnées, simulation, stockage, packaging
et GameData passent. Un seul ancien test a changé : dans
`test_provider_cache_readonly_and_exports`, `adjacency is None` devient une
topologie logique de 560 cellules, puisque la topologie est désormais démontrée.

**Instabilité préexistante, non corrigée ici** : la suite complète s'interrompt
parfois (« Fatal Python error: Aborted ») dans
`tests/test_simulation.py::test_worker_pause_resume_stop`
(`combatbot/simulation.py`, `_clear_thread`). Mesures :

- 1 fois sur 12 sur une copie de l'état **d'avant** cette reprise ;
- 5 fois sur 23 avec l'état actuel ;
- 0 fois sur 25 quand ce fichier de tests est lancé seul.

C'est une course à l'arrêt d'un QThread de simulation, hors périmètre GameData.
Une tâche séparée est proposée.

**Non fait** : build ONEDIR et smoke test. `build_exe.ps1` utilise
`.venv\Scripts\python.exe`, dont l'interpréteur est introuvable (constat du lot
précédent) ; il supprime `build/` et `dist/` et peut installer PyInstaller par le
réseau. Le paquet `dist/PythonBot` existant n'est donc pas à jour ; lancer les sources.

**Client non modifié** : aucun fichier du client n'a une date postérieure à 21:46
le 23 septembre. Les 8 fichiers datés de ce jour (21:41–21:46) proviennent de la
synchronisation du lanceur Alea, antérieure au travail GameData (21:48). Toutes
les écritures de PythonBot sont sous `data/`.

## Fichiers modifiés ou ajoutés

- `combatbot/gamedata/formats/archives.py` : détection de disposition D2P, lecteur strict.
- `combatbot/gamedata/formats/maps.py` : DLM v11 forensique, métadonnées, champs de cellule.
- `combatbot/gamedata/formats/d2o.py` (nouveau) : schéma et objets D2O.
- `combatbot/gamedata/topology.py` (nouveau) : cellId ↔ (x, y), voisinage, `build_topology`.
- `combatbot/gamedata/validation.py` (nouveau) : validation de masse, contrôles et critères.
- `combatbot/gamedata/models.py` : champs de cellule bruts, `GameMap.metadata`, `GridTopology.coordinates`.
- `combatbot/gamedata/provider.py` : détails de disposition, `get_map(track=False)`,
  `map_source`, `export_json`, topologie logique, schéma de cache v4.
- `combatbot/ui/gamedata_panel.py` : résumé enrichi, validation en tâche de fond, détails.
- `scripts/validate_gamedata_real.py` (nouveau) ; `tests/test_gamedata_real_formats.py` (nouveau) ;
  `tests/test_gamedata.py` (1 assertion) ; `tests/test_gamedata_ui.py` (1 test).
- `combatbot/__init__.py` (version), `README.md`, `GAME-DATA-SOURCES.md` (addendum), ce rapport.

Non modifiés : `combat_grid.py`, GridCalibration, GridTransform, RealCombatObserver,
simulation, vision. Aucune dépendance ajoutée : numpy était déjà requis.

## Version

**0.3.3-gamedata.1.** Cette reprise ajoute la compatibilité réelle du probe, la
topologie logique, le lecteur D2O et la validation de masse, mais pas encore
l'intégration au combat. La version définitive 0.3.3 attend la décision de
l'utilisateur.

**Arrêt ici. LOT 3B-2 non commencé : aucun moteur de décision, aucun clic dans DOFUS,
aucune automatisation de combat.**
