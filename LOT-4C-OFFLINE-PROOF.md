# 4C — Recherche d'une preuve offline de la portée et de la ligne de vue (DOFUS 2.64.5)

Date : 28/09/2026 · branche `lot-fasttrack-execution-core`.

**Verdict : UNVERIFIED.** Aucune règle ne passe à VERIFIED. `RangeMetric.UNVERIFIED` et
l'absence d'oracle LOS restent les valeurs par défaut. Le ciblage répond donc UNKNOWN, et le planner
bloque, tant qu'une hypothèse n'est pas activée explicitement.

## Exigence appliquée

Pour qu'une règle passe à VERIFIED, il faut :
- soit **deux preuves indépendantes** ;
- soit **une preuve primaire forte**.

Dans les deux cas, la preuve doit porter explicitement sur **DOFUS 2.64.x**. Une source sans licence compatible ne peut servir qu'à recouper, jamais à copier du code. Le client décompilé est exclu : c'est de la rétro-ingénierie du client, sans licence de réutilisation.

## Sources examinées

| # | Source | Version visée | Ce qu'elle dit | Niveau | Retenue ? |
|---|---|---|---|---|---|
| 1 | Code du dépôt (`combatbot/`, `GAME-DATA-*.md`, `LOT-3B-2`) | 2.64.5 (GameData) | Donne les drapeaux statiques `line_of_sight` (bit 3) et `walkable` des 560 cellules. La distance de Manhattan en (x, y) GameData est égale au nombre de **pas** en 4-voisinage : c'est un fait de topologie démontré. Rien sur la portée d'un sort ni sur l'algorithme LOS. | primaire pour la topologie, nul pour 4C | non (ne couvre pas la règle) |
| 2 | GameData `Spells.d2o` / `SpellLevels.d2o` (client local) | 2.64.5 | Contiennent des **paramètres** : portée min/max, lancer en ligne, test de LOS, lancer en diagonale. Pas l'**algorithme** (métrique, cases traversées). | primaire pour les données, nul pour l'algorithme | non |
| 3 | Wiki communautaire « Line of Sight » (fandom), cité par un moteur de recherche | non versionné | Qualitatif : « ligne tracée du centre de la case du lanceur au centre de la cible ; toute case traversée doit être libre ; obstacles de map et entités bloquent ». Ne précise ni les coins, ni les cas d'égalité, ni la métrique de portée. Page inaccessible depuis cet environnement (proxy). | secondaire, non versionné | non |
| 4 | [ArakneUtils](https://github.com/Arakne/ArakneUtils) (LGPLv3) | **1.29** | Utilitaires « pathfinding, line of sight » pour Dofus 1.29. Autre version du jeu, autre client. | secondaire, version différente | non |
| 5 | [JondoEmu](https://github.com/Keka-Bron/JondoEmu) | **Unity 3.x** | Émulateur : « LOS tracée entre centres de cases contre son propre ensemble d'obstacles ». C'est une réimplémentation d'émulateur, pas le comportement du client 2.64.5. | tertiaire, version différente | non |
| 6 | [Dofus-fight-simulator](https://github.com/fbarre96/Dofus-fight-simulator) | non précisé | Son README déclare le moteur « codé entièrement à vu de nez ». | non fiable par déclaration de l'auteur | non |
| 7 | Forum officiel « Insight — line of sight simulator » (dofus.com) | inconnu | Outil communautaire. Page inaccessible (proxy). | inconnu | non |
| 8 | Emudofus/Dofus (« Dofus client's sources ») | 2.x | Sources décompilées du client. | — | **exclue** (rétro-ingénierie, aucune licence) |

## Recoupement

- **Métrique de portée.** Les sources 3 à 5 laissent penser que la portée se compte en cases sur la grille, ce qui est cohérent avec « distance de Manhattan en (x, y) ». Aucune n'est à la fois versionnée 2.64.x et indépendante, et aucune ne traite les cas limites : diagonale, bonus de portée, portée minimale 0. → **UNVERIFIED.**
- **LOS.** Seule une description qualitative existe (centre à centre). La règle exacte sur les coins, les cellules effleurées, les entités et les cases non-LOS de GameData n'est prouvée par aucune source 2.64.x. → **UNVERIFIED.**
- **Divergence connue.** Les implémentations 1.29 et Unity 3.x peuvent différer de 2.64.5. Les utiliser reviendrait à deviner, ce que les règles du lot interdisent.

## Piste de preuve primaire la plus courte (nécessite une collecte, donc l'utilisateur)

Le client 2.64.5 affiche lui-même les cases ciblables quand un sort est sélectionné : une zone de portée bleue, avec les cases hors LOS rendues autrement. Ce rendu est **l'oracle primaire** du jeu, sans rétro-ingénierie. Protocole proposé pour un lot futur :

1. L'utilisateur sélectionne un sort connu, sans le lancer, et capture la frame. La case du joueur, la map et le sort doivent être connus.
2. Une vérité humaine indique, pour chaque case, si elle est ciblable.
3. Le moteur compare cette vérité à chaque hypothèse (Manhattan, variantes de LOS). Il faut 0 erreur sur un corpus TRAIN, puis 0 erreur sur un TEST aveugle, sur au moins deux maps.

Seul ce protocole, ou une source officielle versionnée, peut faire passer `RangeMetric` ou un `los_oracle` à VERIFIED. D'ici là, `--assume-logical-range` reste une hypothèse explicite, tracée dans `assumptions`.
