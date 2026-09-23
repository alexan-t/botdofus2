# GameData — sources et licences

Audit du 23 septembre 2026. Périmètre : fichiers locaux, formats et modèles de données.
Les commits ci-dessous ont été résolus par l'API publique GitHub. Aucune dépendance
issue de ces dépôts n'est intégrée. Aucun code tiers n'a été copié.

## Dépôts consultés

| Dépôt | Commit vérifié | Licence observée | Décision |
| --- | --- | --- | --- |
| [LuaxY/PyDofus](https://github.com/LuaxY/PyDofus) | [7d60089111631e534a3664d84324e47bbf145d13](https://github.com/LuaxY/PyDofus/tree/7d60089111631e534a3664d84324e47bbf145d13) | Aucune licence explicite identifiée à la racine ou dans les fichiers consultés ; métadonnée GitHub absente | CONCEPT_ONLY |
| [100N0m/VLDofusBotTest](https://github.com/100N0m/VLDofusBotTest) — contient VLDofusBotCore | [fb3160bb9a7f77fcf187ef1913386a730cad17c9](https://github.com/100N0m/VLDofusBotTest/tree/fb3160bb9a7f77fcf187ef1913386a730cad17c9) | Aucune licence du projet explicitement identifiée ; métadonnée GitHub absente. Des composants embarqués peuvent avoir leurs propres licences, sans autoriser la copie du projet entier | CONCEPT_ONLY |
| [louisabraham/LaBot](https://github.com/louisabraham/LaBot) | [468721437952c460f038642c742adeb9d232ff3d](https://github.com/louisabraham/LaBot/tree/468721437952c460f038642c742adeb9d232ff3d) | [MIT, LICENSE.txt, Copyright 2017 Louis Abraham](https://github.com/louisabraham/LaBot/blob/468721437952c460f038642c742adeb9d232ff3d/LICENSE.txt) | NOT_USED |
| [marvinroger/Dofus-Tools](https://github.com/marvinroger/Dofus-Tools) | [f9d254020404a2121a41f59e33ae42c02eb936d5](https://github.com/marvinroger/Dofus-Tools/tree/f9d254020404a2121a41f59e33ae42c02eb936d5) | Aucune licence explicite identifiée dans l'inventaire GitHub ; métadonnée absente | NOT_USED |

L'absence de licence n'est pas une autorisation de copie. Les lecteurs ajoutés à
PythonBot ont été écrits indépendamment à partir de la description des champs.
Aucun extrait propriétaire, archive réelle, sprite ou map réelle n'a été importé.
Aucun THIRD_PARTY_NOTICES.md supplémentaire n'est nécessaire pour ce lot.

## Fichiers étudiés et portée des observations

### PyDofus

Au commit fixé ci-dessus :

- [pydofus/d2p.py](https://github.com/LuaxY/PyDofus/blob/7d60089111631e534a3664d84324e47bbf145d13/pydofus/d2p.py) : structure d'archive 2.1, footer, index de noms/offsets/tailles, propriétés.
- [pydofus/d2o.py](https://github.com/LuaxY/PyDofus/blob/7d60089111631e534a3664d84324e47bbf145d13/pydofus/d2o.py) : magic D2O, offset d'index, couples identifiant/position, schémas de classes. PythonBot ne lit que l'index ; l'enveloppe AKSF et les classes ne sont pas implémentées.
- [pydofus/dlm.py](https://github.com/LuaxY/PyDofus/blob/7d60089111631e534a3664d84324e47bbf145d13/pydofus/dlm.py) : compression zlib, en-tête de map, variations selon version, couches et données des cellules. Les branches anciennes comportent des champs audio. Ce fichier ne démontre pas la compatibilité avec DOFUS 2.64.5.

### VLDofusBotCore / VLDofusBotTest

Dans le même commit du dépôt parent :

- [D2PMapsAdapter.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotCore/src/main/kotlin/fr/lewon/dofus/bot/core/d2p/maps/D2PMapsAdapter.kt) : magic 77, map ID, version, branche chiffrée, couches, séquence de 560 cellules et champ tacticalModeTemplateId après la version 10.
- [CellData.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotCore/src/main/kotlin/fr/lewon/dofus/bot/core/d2p/maps/cell/CellData.kt) : floor, flags de mouvement/LOS, rouge/bleu, restrictions de combat/RP, champ linkedZone conditionnel après la version 10.
- [GraphicalElement.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotCore/src/main/kotlin/fr/lewon/dofus/bot/core/d2p/maps/element/GraphicalElement.kt) : taille des éléments graphiques, uniquement pour franchir leurs données sans renderer.
- [MapManager.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotCore/src/main/kotlin/fr/lewon/dofus/bot/core/d2o/managers/map/MapManager.kt) et [DofusMap.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotCore/src/main/kotlin/fr/lewon/dofus/bot/core/model/maps/DofusMap.kt) : métadonnées MapPositions. Les positions de maps dans le monde ne sont pas les coordonnées de leurs cellules.
- [DofusCell.kt](https://github.com/100N0m/VLDofusBotTest/blob/fb3160bb9a7f77fcf187ef1913386a730cad17c9/VLDofusBotApp/src/main/kotlin/fr/lewon/dofus/bot/game/DofusCell.kt) : séparation entre cellId, row/col, voisinage et bornes visuelles ; la possibilité de marcher en combat combine plusieurs drapeaux.

Les identifiants et bits observés constituent des hypothèses de format historique.
Ni ces noms de champs, ni la possibilité de marcher, ne prouvent des positions de
placement d'équipe applicables au client privé visé.

### LaBot et autres projets

LaBot a une licence MIT explicite, mais son architecture est principalement liée
aux données réseau. Aucun module de sniffer, MITM, proxy, lecture/écriture de
paquets ou injection n'a été importé ou exécuté. Seuls README, arbre du dépôt,
LICENSE.txt, .gitmodules et le petit dumper générique ont été consultés.

Le fichier [.gitmodules](https://github.com/louisabraham/LaBot/blob/468721437952c460f038642c742adeb9d232ff3d/.gitmodules)
désigne un autre PyDofus, **balciseri/PyDofus**. La licence MIT de LaBot n'est pas
considérée comme la licence de ce sous-module. Ce sous-module n'est pas intégré
ni utilisé comme source de code.

Dofus-Tools a été identifié comme référence historique de D2P/SWL ; son inventaire
mentionne pydofus/d2p.py et des samples. Ses samples n'ont pas été téléchargés ;
aucun de ses fichiers n'est réutilisé.

## Décisions de réalisation

- D2P : lecteur d'index indépendant, borné et sans extraction des archives complètes.
- D2O : contrôle limité à l'en-tête et l'index. Aucun objet métier D2O n'est revendiqué.
- DLM : lecteur expérimental du seul schéma **v11 non chiffré**, avec refus des versions inconnues, éléments inconnus, longueurs incohérentes et données résiduelles.
- Aucun déchiffrement, aucune récupération de clé, aucun accès au processus client.
- Les indices implicites 0..559 du schéma historique restent distincts de Cell(x,y).
- Aucune formule de conversion n'est activée sans vérification sur le client cible.
- Rouge/bleu : indices de diagnostic seulement ; fight_start_allowed reste inconnu.
- Aucun nouveau paquet tiers dans requirements.txt.

## Différences établies / vérifications encore nécessaires

Les deux lecteurs historiques ne consomment pas les mêmes champs avant les
fixtures/couches. Le lecteur plus récent expose un modèle tactique et des zones
liées conditionnelles, absents du chemin ancien correspondant. Appliquer
indistinctement un lecteur ancien désalignerait potentiellement le flux.

Aucune différence avec des fichiers **réels 2.64.5** n'est certifiée ici :
aucun dossier client n'était configuré ou fourni lors du probe. La prochaine
validation doit comparer magic, version, index, compression, fin exacte de flux,
champs absents et cas chiffrés à partir du dossier explicitement choisi.

## Addendum LOT 3B-2A-R — validation sur le client réel (23 septembre 2026)

Aucune nouvelle source tierce n'a été consultée pendant cette reprise ; aucun code
tiers n'a été copié. Les ajouts sont des réimplémentations indépendantes, guidées
par les octets mesurés dans le client privé (lecture seule) :

- **D2P** : deux dispositions démontrées par les offsets du footer sur les 248 archives
  (`formats/archives.py`, `D2PLayout`). La disposition « index avant données » n'est
  décrite par aucune des sources auditées ; elle a été établie uniquement par mesure.
- **DLM v11** : l'ordre des champs d'en-tête correspond à la description historique
  (voisins, couleurs, zoom, `tacticalModeTemplateId` après la version 10). La lecture
  exacte de 12 153 maps et l'égalité avec `MapPositions.d2o` (sous-zone et modèle
  tactique) le confirment indépendamment des sources.
- **D2O** : lecteur de schéma de classes et d'objets (`formats/d2o.py`) fondé sur la
  structure décrite dans PyDofus `d2o.py` (CONCEPT_ONLY), réécrit sans reprise de code.
- **Géométrie des cellules** (`topology.py`) : disposition historique 14 × 20 × 2
  (demi-rangées décalées) et coordonnées logiques (x, y), connues par les
  implémentations publiques auditées (`DofusCell.kt`, CONCEPT_ONLY). La formule a été
  écrite indépendamment, puis **vérifiée sur les données réelles** (voir
  GAME-DATA-REAL-VALIDATION.md) : elle n'est pas acceptée sur la seule foi des sources.

`scripts/inspect_client_abc.py` (inspection statique SWF/ABC, laissé par une intervention
antérieure) n'a jamais été utilisé ; il a été **supprimé au LOT 3B-2**, avant le premier commit
Git, car aucune conclusion ni aucun code n'en dépendait.
