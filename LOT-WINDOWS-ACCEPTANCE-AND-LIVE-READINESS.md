# LOT — Recette Windows et préparation live

> Serveur privé/test uniquement. **ENTRÉE RÉELLE ENVOYÉE : NONE.** Dans cette session : aucun clic, aucune souris, aucune touche, porte d'entrée réelle fermée, F9 actif.

## Base, tête, branche

| | |
|---|---|
| BASE SHA | `3407de71308fdcb291cad11d80cda243a2f8b73a` (`lot-fasttrack-execution-core`, PR #6) |
| Branche | `lot-windows-live-readiness` → PR draft vers `lot-fasttrack-execution-core` |
| HEAD | le commit de ce rapport (`docs: report Windows acceptance and live blockers`) |
| OS / Python de la session | Linux 6.18 (conteneur cloud) / Python 3.11.15 |
| Profil | non déterminable ici (base locale absente). Sur le PC, le choix est automatique seulement s'il n'y a qu'un profil |

**Limite principale.** La session ne tourne pas sur le PC de l'utilisateur. La recette Windows, les sessions réelles et le corpus réel sont inaccessibles. Aucune mesure réelle n'est donc rapportée ici. Le détail est dans `AUDIT-WINDOWS-ACCEPTANCE.md`. Tout a été préparé pour que le PC produise ces mesures en une commande.

## Commits

| SHA | Objet |
|---|---|
| `2040041` | feat: resolve the acceptance profile and report spell readiness |
| `60d4c3e` | feat: persist a user-confirmed spell bar page that expires when the bar changes |
| `8b655bd` | perf: drop duplicate full-frame work from the Windows capture |
| `629b885` | feat: add a per-stage runtime latency profile from real session journals |
| `255bd39` | feat: journal grid and entity sub-timings for the runtime profile |
| `a271529` | feat: add player cell diagnostics with one reason per unknown frame |
| `892fbe4` | feat: add a live targeting proof collector for range and LOS (4C) |
| `20c4d3f` | feat: add the guided live fast-track recipe and its report folder |
| `ebd8bbf` | feat: never guess the acceptance profile and run the live readiness reports |
| `069dd5a` | test: validate DOFUS window enumeration outside Windows |
| (ce commit) | docs: report Windows acceptance and live blockers |

## Tests

| Mesure | Résultat |
|---|---|
| Suite complète | **757 passed, 2 skipped, 1 failed**. L'échec est RapidOCR, absent du conteneur. Base : 730 passed / 3 failed |
| Tests ajoutés | 25 ; aucun supprimé, aucun xfail, aucune vérité TEST touchée |

Tests propres à Windows :
- `test_browser_titled_dofus_is_not_a_game_window` et `test_window_selection_by_handle_and_client_rect` passent désormais aussi sous Linux. Le repli `CFUNCTYPE` n'est utilisé que hors Windows, où `WINFUNCTYPE` n'existe pas.
- `test_rapidocr_reads_synthetic_visible_text` ne peut être vérifié que sur le PC.

## Latence

- **Dernier état réel connu (3B-7, PC n° 1) : FAIL.** p95 979 ms pour un critère de 400 ms.
  - Capture : médiane 236 ms.
  - Entités : ≈ 124 ms.
  - Grille : ≈ 120 ms.
- **Correctif mesuré (`8b655bd`).** La capture faisait deux conversions RGB et deux écarts types sur tout le cadre. Elle fait désormais une conversion, et un écart type sur 1/64 des pixels (calcul complet seulement en cas de doute).
  - Image rendue identique à l'octet près, même critère de rejet.
  - Sur un client synthétique 2560×1377 : **≈ 185 ms → ≈ 27 ms** de traitement après la capture.
  - Le gain réel sur le PC reste à mesurer : la part de `ImageGrab` elle-même n'est pas mesurable ici.
- **Instrumentation ajoutée.**
  - Sous-étapes de capture : activate, geometry, grab_window, grab_desktop, to_rgb, to_bgr, checks.
  - Source de capture : fenêtre ou bureau.
  - Grille : resolve / validate. Entités : détecteur / suivi.
  - `python -m combatbot.benchmark --runtime-profile` donne le top 3 réel.
  - Sur une frame synthétique de taille réelle, la projection GameData ne coûte qu'environ 11 ms. Les 120 ms de l'étape « grille » sont donc ailleurs, probablement dans la validation, et **ne sont pas optimisées à l'aveugle**.

## Cellule joueur

- **Constat.** Le taux « cellule joueur inconnue » de 3B-7 (≈ 90,8 %) était calculé sur **toutes** les frames, exploration comprise.
- **Journalisation.** Le détecteur calculait déjà une décision détaillée (candidats, too_far / ambiguous / conflict_with_prior / selected) mais ne la journalisait pas. C'est maintenant fait (`a271529`), sans rien changer à la décision.
- **`--player-cell-diagnostics`** donne une raison par frame, dans l'ordre de la chaîne :
  1. hors combat (phase connue) ;
  2. map inconnue ;
  3. grille non GameData, non visible ou non alignée ;
  4. pipeline historique ;
  5. profil joueur absent ou d'un autre layout ;
  6. aucun anneau de l'équipe du joueur. Masqué, mode créature ou animation sont indiscernables sans vérité : ils restent regroupés, aucune attribution au hasard ;
  7. candidat trop loin, ambigu ou en conflit ;
  8. perte par le suivi ;
  9. inexplicable pour les anciens journaux.
- Une phase inconnue ne masque jamais la cause technique : la frame est classée selon la chaîne et comptée à part.
- **Aucun seuil modifié.** La distribution réelle viendra du PC.

## Map / grille

- **Déjà prouvé en live (3B-6C).** PASS sur :
  - le résolveur (extérieur, donjon, salles, zaap) ;
  - le changement de map.
  - Benchmark : 377/472 correctes, 0 mauvaise.
- **À prouver en live.**
  - Autoload GameData et réutilisation de la calibration : PASS en tests seulement.
  - Alignement après changement de map.
  - `grid.json` du dossier live mesure le nombre de frames jusqu'à ALIGNED après chaque changement.
- Rien n'est déclaré PASS sans cette mesure.

## PA/PM, phase et tour

- **PA/PM.** Le dernier rapport 3B-4R2 est STALE. `hud.json` mesurera les PA/PM inconnus en combat ; l'exactitude exige la revue HUD humaine.
- **Phase et tour.**
  - Le modèle absent donne un tour UNKNOWN (fail-closed, `912e7bc`), sans heuristique de secours.
  - La présence du modèle est désormais journalisée. `combat-state.json` vérifie qu'aucun « mon tour » n'est affirmé sans modèle.
  - Faux « mon tour » = 0 sur le TEST gelé de 3B-6B (PC du corpus).

## Dry-run

- Non rejoué ici (corpus vide).
- **Analyse du code.** Dès qu'un état est complet, le blocage dominant attendu est « sort possible mais non prouvé » (portée et LOS UNVERIFIED). En amont : `safe_for_decision`, cellule joueur inconnue, pas mon tour.
- Le top 10 réel est dans `dry-run-plans.json`, produit par la recette.

## Sorts et page de sorts

- **Sorts.** `--spell-readiness` liste, pour chaque sort : case, page, caractéristiques, provenance, « prêt » oui/non et raisons. Rien n'est modifié.
- **Page affichée (`60d4c3e`).**
  - Aucune lecture fiable n'existe sans nouvelle vérité : la page n'est **jamais supposée**, jamais « page 1 » par défaut.
  - L'utilisateur confirme « la barre affiche la page N » depuis le panneau de scan.
  - La signature de la barre est mémorisée, puis invalidée dès que la barre change. Elle ne sert qu'à invalider, jamais à déduire une autre page.
  - Page inconnue → CAST refusé par 5A1.
- La page confirmée n'alimente encore aucune exécution, puisqu'aucune n'est branchée.

## 4C portée / LOS

- **Toujours UNVERIFIED.**
- **Collecteur ajouté (`892fbe4`).** L'utilisateur sélectionne lui-même un sort dans DOFUS, puis enregistre l'observation. Dans « Preuve portée / LOS », il marque chaque cellule : ciblable, non ciblable ou obstacle.
- **`--targeting-proof-report`** compare cellule par cellule des règles **candidates** :
  - portée Manhattan ou Chebyshev ;
  - LOS stricte ou tolérante aux coins (géométrie rationnelle exacte), ou sans LOS.
- **Couverture exigée**, sur au moins 2 maps : en ligne, hors ligne, obstacle, sans obstacle, portée min, portée max, juste au-delà, portée modifiable.
- **Aucune règle n'est adoptée automatiquement.**

## Placement et détection de groupe

- **Placement.** Les indices GameData rouge/bleu ont une précision de 1,0 mais un **rappel de 0,53** (3B-2R : 3 maps sur 5 sans aucun indice). Ils restent refusés comme vérité.
  - Un estimateur couleur existe (`observed_placement_cells`). Il n'a jamais été validé sur un jeu séparé et a déjà produit un faux bleu.
  - Des vérités de placement existent pour 5 maps dans les recettes de grille, uniquement sur le PC.
  - **NOT_READY.** Il faut valider l'estimateur sur de nouvelles phases de placement annotées.
- **Groupes attaquables.** Aucun signal ni aucune vérité dans le code ou les données : ni entité d'exploration, ni boîte, ni symbole. **NOT_READY.**
  - Préalable : un outil d'annotation des groupes sur des frames d'exploration. Il n'est pas construit dans ce lot : aucune donnée pour le tester.

## Risques

- La capture accélérée n'a pas été mesurée sur le PC réel.
- La confirmation de la page de sorts dépend de la stabilité de la signature. Elle est testée sur des icônes assombries, mais pas sur de vrais cooldowns.
- Le collecteur 4C suppose une map prouvée et un sort à portée renseignée : il refuse sinon.

## Bloquants utilisateur

1. Lancer la recette Windows (un seul profil, ou `-ProfileId N`).
2. Une session live guidée (`RECETTE-LIVE-FASTTRACK.md`), puis `--live-fasttrack-report`.
3. Confirmer les sorts, les zones `spell_bar` et `end_turn`, et la page de sorts.
4. Collecter les échantillons 4C : 2 sorts, 2 maps, cas exigés.
5. Annoter de nouvelles phases de placement ; plus tard, des groupes en exploration.

## Bloquants techniques

- Top 3 de la latence réelle inconnu tant que `--runtime-profile` n'a pas tourné sur le PC.
- Portée et LOS 2.64.5 non prouvées.
- Aucun détecteur validé : ni cases de placement actives, ni groupes, ni page de sorts.

## Table finale

| Domaine | Verdict |
|---|---|
| Windows tests | **PARTIAL** (2/3 vérifiés, y compris sous Linux ; RapidOCR à vérifier sur le PC) |
| Full suite | **PARTIAL** (757/758 ; seul échec : module absent de l'environnement) |
| Observation e2e | **NOT_EVALUABLE** ici (corpus réel inaccessible) |
| 3B-6C live readiness | **PARTIAL** (outils prêts ; alignement après changement de map à mesurer) |
| 3B-7 acceptance | **FAIL** (dernier état connu : latence) ; non re-mesuré |
| Latency | **FAIL** (dernier connu, p95 979 ms) ; correctif capture −158 ms synthétique, à mesurer |
| Player cell | **PARTIAL** (diagnostic prêt ; distribution réelle à produire) |
| PA/PM | **NOT_EVALUABLE** ici (dernier rapport STALE) |
| Phase/turn | **PARTIAL** (fail-closed vérifié ; « mon tour » à tort = 0 sur le TEST 3B-6B) |
| Dry-run planner | **PARTIAL** (bloquant dominant attendu : portée/LOS non prouvées) |
| Spell readiness | **NOT_EVALUABLE** ici (rapport prêt) |
| Spell page | **PARTIAL** (confirmation humaine persistée et invalidée ; aucun détecteur) |
| 4C range/LOS | **UNVERIFIED** (collecteur et comparateur prêts) |
| Placement detection | **NOT_READY** |
| Group detection | **NOT_READY** |
| Real input gate | **CLOSED** |
| REAL INPUT SENT | **NONE** |
