# LOT 3B-6C — Détection automatique de map, chargement GameData, calibration réutilisée

Branche `lot-3b-6c-auto-map-detection` (depuis `d2d6ac9`). Lecture seule : aucun clic, aucune
lecture réseau ou mémoire, aucune injection, aucune modification du client. **ACTIONS : NONE.**

## 1. Audit de l'existant

- `MapIdentityProvider` (grid_profile) existait avec une origine `DETECTED` réservée ; seule
  `ManualMapIdentity` l'implémentait. `GameDataGridResolver` charge la topologie de la map fournie
  par ce provider. Le profil de projection `CombatGridProfileV2` est **déjà limité au layout**
  (`scope = "layout"`) : la calibration ne dépend pas de la map.
- `RealCombatObserver` réinitialise déjà `EntityTracker` + `CellBackgroundModel` quand
  `grid.map_id_declared` change, en gardant les profils visuels.
- `MapConsistencyTracker`, `GridAlignmentValidator` (3B-3) valident déjà la grille projetée.
- `D2OFile` lisait `MapPositions.d2o` (validation GameData 3B-2A-R).

→ Pas de système parallèle : 3B-6C ajoute une implémentation `AutoMapIdentity` du provider
existant ; le reste (projection, alignement, reset du suivi) est réutilisé tel quel.

## 2. Audit des logs locaux — LOCAL LOG MAP SOURCE : **UNAVAILABLE**

- Le client Alea (Adobe AIR, DOFUS 2) configure `log4as.xml` avec une seule cible :
  `SOSTarget` → socket **127.0.0.1:4444**. Aucun fichier de log n'est écrit.
  L'écouter serait une interception de socket ; changer la cible serait une modification du client.
  Les deux sont interdits → non utilisé.
- `%APPDATA%\zaap\gamesLogs\dofus-*` : logs des clients officiels (Dofus 3, beta), pas d'Alea.
- `%APPDATA%\Dofus\*.dat` (stockage local AIR) : réglages d'interface uniquement, aucun mapId.
- À vérifier pendant le test live : aucun fichier local ne change à chaque changement de map.

## 3. Statistiques d'ambiguïté GameData (client réel, 12 154 maps)

Fichier : `data/validation/lot3b6c/map-coordinate-ambiguity.json`.

| Clé | Clés distinctes | Maps à candidat unique | 2 | 3 | 4–9 | 10+ |
|---|---|---|---|---|---|---|
| (x, y) | 6 556 | **41,5 %** (5 042) | 1 552 | 792 | 2 052 | 2 716 |
| (x, y, worldMap) | 8 233 | 59,2 % | 1 140 | 450 | 1 511 | 1 858 |
| (x, y, worldMap, niveau) | 8 652 | 63,7 % | 960 | 360 | 1 541 | 1 550 |
| (x, y, sous-zone) = ce que l'écran affiche | 8 243 | **58,2 %** | 1 118 | 651 | 1 999 | 1 310 |
| maps extérieures worldMap 1, (x, y) | — | 84,8 % | | | | |

Pire cas : (0,0) 392 maps, (-13,-29) 331 maps (intérieurs, donjons). Cas typique observé dans le
corpus : une map extérieure et un intérieur (maison, souterrain) partagent **coordonnées ET nom de
sous-zone** — ex. (0,-32) Tainéla : 120062467 (extérieur) / 122946049 (intérieur).
Conclusion : l'OCR seul ne suffit pas pour ≈ 42 % des maps ; l'architecture prévue (contexte de map
précédente, empreinte, confirmation mémorisée, UNKNOWN/AMBIGUOUS sinon) est nécessaire et suffisante.
Aucun changement d'architecture imposé → pas de STOP à ce stade.

## 4. Architecture

```
client (capture) ─► MapCoordinateReader (ROI haut-gauche, relative au client)
                         │  RapidOCR, thread dédié, 1 lecture / 2 s (+ après transition)
                         ▼
                   parse_map_info (strict) ─► CoordinateConsensus (2 lectures sur 3, dont la dernière)
                         ▼
MapTransitionDetector ─► MapContextResolver ◄── MapSpatialIndex (GameData, cache runtime)
                         │                  ◄── MapKnowledge (empreintes, confirmations humaines)
                         ▼
                   MapResolution ─► AutoMapIdentity ─► GameDataGridResolver (profil layout réutilisé)
                                                        ─► GridAlignmentValidator ─► garde d'enregistrement
```

### 4.1 Index spatial (`combatbot/gamedata/map_index.py`) — **PASS**
`MapPositions.d2o` (x, y, worldMap, sous-zone, outdoor, transition), `SubAreas.d2o` (zone, niveau),
`Areas.d2o`, `i18n_fr.d2i` (nouveau lecteur `formats/d2i.py`, noms affichés), voisins des en-têtes
DLM + `MapScrollActions.d2o`. API : `candidates_by_coords`, `get_map`, `neighbours`,
`candidates_by_subarea`, `world_context`. Construction 84 s la première fois, puis 0,17 s depuis
`data/gamedata/cache/map_spatial_index.json` ; empreinte (taille + mtime) des tables et archives
de maps → reconstruction automatique si le client change.

### 4.2 Lecteur de coordonnées (`combatbot/vision/map_reader.py`) — **PARTIAL**
Le client affiche « Zone (Sous-zone) » puis « x,y, Niveau N ». Une lecture n'est **complète** que si
les deux lignes sont entières, « Niveau » présent, scores OCR ≥ 0,9. Aucun chiffre n'est corrigé.
Cas réel du corpus qui justifie cette règle : map 68419589 affiche « 7,-2 » mais un personnage
masque le texte ; RapidOCR lit « 7,-4 » avec 0,98 de confiance — et (7,-4) est une vraie map de la
**même sous-zone au même niveau** (68419587). Le parseur la refuse (ligne « Niveau » incomplète).
PARTIAL parce que seul RapidOCR lit le texte (le chemin gabarits spécialisé n'est pas fait) ; la
corroboration vient des noms et du niveau GameData. Coût : ≈ 1,1 s par lecture, en arrière-plan.

### 4.3 Résolveur (`combatbot/vision/map_resolver.py`) — **PASS offline**
Étapes, chacune explicable dans `MapResolution.contributions` :
1. log local (API gardée, aucune source réelle) — prioritaire si frais et présent dans GameData,
   INCONSISTENT s'il contredit les coordonnées lues ;
2. candidates par coordonnées ; aucune → UNKNOWN ;
3. filtre noms de zone + sous-zone (Levenshtein normalisé ≥ 0,85) + niveau ; aucune → INCONSISTENT ;
4. une seule → RESOLVED (`OCR_COORDS_UNIQUE` / `OCR_COORDS + AREA_NAMES`) ;
5. map précédente : même map **seulement si la scène n'a pas changé** (entrer dans une maison garde
   coordonnées et noms) ; sinon voisine unique dans le graphe GameData → `PREVIOUS_MAP_GRAPH` ;
   aucune voisine → `non_local_transition` (zaap, porte) : pas de choix forcé ;
6. empreinte visuelle (dHash 64×36, bandeau haut et bas masqués) comparée **aux seules candidates**
   déjà connues : distance ≤ 0,10 et marge ≥ 0,08 → `FINGERPRINT` ;
7. sinon AMBIGUOUS avec la liste des candidates. Jamais de choix au hasard.

Statuts : RESOLVED, AMBIGUOUS, UNKNOWN, TRANSITION, STALE, INCONSISTENT.

### 4.4 Changement de map — **PARTIAL (tests unitaires, live à faire)**
`MapTransitionDetector` (écran sombre ou > 55 % de la zone qui change) signale seulement « la map
change probablement ». Pendant une transition ou tant que le consensus n'est pas atteint : statut
TRANSITION, l'identité de suivi est **conservée** (aucun reset sur une lecture bruitée), mais rien
n'est enregistré. Un changement confirmé change l'identité → l'observateur réinitialise suivi et
fond, profils visuels conservés. Journal `data/logs/map-resolution.jsonl` : une ligne par
changement ou nouvel état (MAP_CHANGED, AMBIGUOUS…), jamais une par frame.
OCR en thread dédié ; un vieux résultat (génération plus ancienne ou capturé avant une transition)
n'écrase jamais un plus récent.

### 4.5 GameData et calibration — **PASS (tests), live à confirmer**
`AutoMapIdentity` alimente `GameDataGridResolver` : la topologie de la nouvelle map est chargée et
projetée avec le **même** `CombatGridProfileV2` du layout (`b2b36fdf07e78000`), puis revalidée par
`GridAlignmentValidator`. Test : deux maps successives → même transformation, aucune recalibration.

### 4.6 Fallback manuel et mémoire
Le champ mapId reste, en secours : « Utiliser ce mapId manuellement ». Si la lecture courante
correspond à cette map, la confirmation (coordonnées, noms, contexte monde, empreinte, layout) est
mémorisée dans `data/map_knowledge.json` → la prochaine visite se résout seule par l'empreinte.
« Revenir à la détection automatique » reprend la détection.

### 4.7 Garde d'enregistrement du corpus (`combatbot/corpus/recording_guard.py`)
Une frame n'est enregistrée que si : map RESOLVED (ou mapId manuel), grille chargée = map résolue,
grille VISIBLE + ALIGNED — ou clairement NOT_VISIBLE (hors combat, résultats : utile à la phase/tour
de 3B-6B). Sinon « Enregistrement bloqué : map/grille non résolue » ou « projection à vérifier ».

## 5. Benchmark offline (`python -m combatbot.benchmark --map-resolution`)

Corpus réel : 201 frames, 177 avec mapId déclaré historiquement (11 maps, 13 sessions). La vérité
ne sert qu'au score. Chaque session repart sans map connue ; connaissance vierge au départ.

| Scénario | Correctes | **Mauvaises** | Ambiguës | Inconnues | Transition | Précision acceptée | Couverture |
|---|---|---|---|---|---|---|---|
| Entièrement automatique | 99 | **0** | 61 | 40 | 1 | **1,000** | 55,9 % |
| + 1 réponse humaine par lieu ambigu (simulée) | 137 | **0** | 23 | 40 | 1 | **1,000** | 77,4 % |

- Les 3 maps ambiguës (120062467, 191105024, 88087301) sont des paires extérieur/intérieur ; chaque
  session y démarre directement, sans map précédente. Une réponse humaine suffit : les frames et
  sessions suivantes se résolvent par l'empreinte, sans aucune mauvaise map.
- Inconnues : 1–3 premières frames de chaque session (consensus) et textes masqués par un
  personnage (191105024 : 12 frames) — refus volontaires.
- Première résolution après 2 à 7 frames par session. Lecteur ≈ 1,1 s (médiane), résolveur ≈ 10 ms.

## 6. Tests

`tests/test_map_resolution.py` (39 + 1 ignoré sans fixture de crop réel) : index (vraies maps,
coordonnées uniques/dupliquées, désambiguïsation par nom, inconnues, invalidation du cache), OCR
(positifs, négatifs, espaces, double virgule, chiffre partiel, lignes réelles, consensus), résolveur
(log frais/absent/hors GameData, coordonnées uniques, graphe, téléportation, même coordonnées après
changement de scène, nom de zone, empreinte en repli seulement, ambigu, incohérent), changement de
map (lecture isolée sans reset, changement confirmé, reset suivi/fond, profils conservés, calibration
réutilisée, transition bloque l'enregistrement, vieux job ignoré, secours manuel mémorisé), sécurité
corpus (map inconnue, ambiguë, grille non alignée, cas autorisés), aucune importation d'exécuteur.
Suite complète : 505 tests OK.

## 7. Statuts

```
LOCAL LOG MAP SOURCE:            UNAVAILABLE
COORDINATE READER:               PARTIAL   (RapidOCR + parseur strict ; pas de gabarits spécialisés)
GAMEDATA SPATIAL INDEX:          PASS
MAP RESOLVER:                    PASS offline (live à confirmer)
MAP CHANGE DETECTION:            PARTIAL   (tests unitaires ; live à faire)
GAMEDATA AUTOLOAD:               PASS (tests) ; live à confirmer
CALIBRATION REUSE:               PASS (tests) ; live à confirmer
GRID ALIGNMENT AFTER MAP CHANGE: PARTIAL   (live à faire)
WRONG MAPS:                      0
UNKNOWN MAPS:                    40 frames (+ 61 ambiguës, 1 transition) sur 177
MAP ACCEPTED PRECISION:          1.000
ACTIONS:                         NONE
```

## 8. Limites

- ≈ 42 % des maps ne sont pas distinguables par ce qui est écrit à l'écran ; elles demandent le
  contexte de déplacement, une empreinte déjà apprise ou une réponse humaine (une fois).
- Lecture lente (≈ 1,1 s) : la détection d'un changement de map prend ≈ 4–6 s (2 lectures).
- Texte masqué par un personnage → lecture refusée (UNKNOWN), jamais corrigée.
- Empreinte apprise en exploration ou en combat : l'overlay de combat peut l'éloigner ; elle ne
  sert qu'au départage et s'abstient en cas de doute.
- Aucun test live encore : voir § 9.

## 9. Test live — premier retour (27/09) et correction donjons

Retour utilisateur : « ça détecte bien les maps sauf celles des donjons ». Journal : Astrub résolu à
pied, y compris par le graphe (191105024 via 191105026, 120063489 via 120063490). Deux observations
dans le donjon Bouftou restaient bloquées en TRANSITION / AWAITING_CONSENSUS.

Cause : dans un donjon, le client n'affiche pas « Zone (Sous-zone) / x,y, Niveau N » mais le **nom
de la salle** puis « x,y » seul : « Cour du Bouftou Royal - Première salle / 2,-34 ». Le parseur
strict ne connaissait que le premier format et refusait toutes les lectures.

Correction :
- l'index lit le nom propre de chaque map (`MapPositions.nameId` → i18n) : 2 217 maps nommées ;
  à (2,-34), chaque salle a un nom distinct (Première, Deuxième, Troisième, Quatrième, Dernière,
  Sortie) ; l'ancien index en cache est mis à niveau en 0,9 s sans relire les DLM ;
- second format de lecture « nom de la map » + ligne contenant uniquement « x,y » ; un
  « Zone (Sous-zone) » sans niveau reste une lecture incomplète (texte masqué) ;
- résolution par **égalité exacte** du nom normalisé (casse, accents, tirets) : « Première salle »
  et « Dernière salle » ne diffèrent que de 3 lettres, un rapprochement approximatif serait dangereux.

Rejeu des deux observations réelles : Première salle → **121373185**, Deuxième salle →
**121374209** (RESOLVED, source `OCR_COORDS + MAP_NAME`). Benchmark corpus inchangé (0 mauvaise map).

## 10. Test live demandé (STOP)

1. Lancer PythonBot, démarrer la Vision réelle **sans** saisir /mapid.
2. Rester sur une map : le bloc Map doit passer de « Détection de la map… » à l'ID détecté.
3. Aller sur une map voisine, puis encore une autre (à pied).
4. Si possible : un zaap / une porte (changement non local).
5. Noter à chaque fois : ID, coordonnées, source, GameData, projection (bloc Map de Vision réelle).
