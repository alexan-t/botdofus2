# Recette live fast-track — procédure guidée (une seule session)

> Serveur privé/test uniquement. **DofBot2 n'envoie aucune action à DOFUS pendant cette recette.** Vous jouez vous-même. L'application observe, enregistre et mesure. F9 reste actif.

Durée indicative : 30 à 45 minutes. Toutes les mesures sont rassemblées à la fin par **une seule commande**.

## Avant de commencer (une fois)

```
powershell -ExecutionPolicy Bypass -File scripts\run_windows_acceptance.ps1
```

Si le script s'arrête sur « choix ambigu », relancez avec l'identifiant indiqué, par exemple `-ProfileId 2`. Sans DOFUS ouvert, ce script vérifie les tests Windows, la chaîne d'exécution, les sorts et le profil.

## Étape 1 — Connexion, fenêtre, layout

1. Lancez DOFUS en mode fenêtré, à la taille habituelle.
2. Dans DofBot2, **Connecter** la fenêtre et vérifiez l'aperçu : « Oui, l'aperçu montre DOFUS ».
3. **Calibrer les zones**. Les zones `combat`, `spell_bar`, `ap`, `mp` et `end_turn` doivent être au statut **confirmée**.

## Étape 2 — Map et grille

1. Dans Outils avancés › Observation, cliquez **Démarrer l'observation**. Le journal de session démarre automatiquement.
2. Vérifiez que la map est **résolue automatiquement**. Sinon, utilisez `/mapid` puis « Utiliser ce mapId manuellement ».
3. **Projection de grille** : la grille GameData doit être alignée. Au besoin, lancez **Recette de grille…**.

## Étape 3 — Un combat joué manuellement

1. Laissez l'observation tourner. Lancez un combat vous-même, placez-vous vous-même, cliquez vous-même « Prêt ».
2. Jouez normalement au moins **3 tours**, jusqu'à la fin du combat et l'écran de résultats.

## Étape 4 — PA/PM, joueur, ennemis, phase et tour

Pendant le combat, sur 3 à 5 moments variés (placement, mon tour, tour ennemi, animation), cliquez **Enregistrer cette observation**. Ces frames serviront aux vérités humaines (annotation entités et phase/tour), sans aucun réglage sur un TEST.

## Étape 5 — Changements de map

L'observation continue. Enchaînez, selon ce qui est accessible :
- une map extérieure à pied ;
- une entrée en intérieur (maison ou mine) ;
- une salle → salle de donjon ;
- un zaap.

Notez ce que vous avez fait. Vous le reporterez dans `summary.md`.

## Étape 6 — Preuve portée / LOS (4C)

Pour 2 sorts dont la portée min/max est renseignée, sur **2 maps différentes** :
1. En combat, à votre tour, **sélectionnez le sort sans le lancer**. Le client affiche les cellules ciblables.
2. Cliquez **Enregistrer cette observation**, puis annulez la sélection dans DOFUS.
3. Couvrez si possible les cas suivants :
   - une cible en ligne et une cible hors ligne ;
   - un obstacle entre vous et la cible, et une vue dégagée ;
   - la portée minimale, la portée maximale, et une case juste au-delà ;
   - un sort à portée modifiable.

Après le combat, dans Outils avancés › Corpus, ouvrez **Preuve portée / LOS**. Pour chaque frame :
- choisissez le sort et vérifiez le lanceur ;
- indiquez le bonus de portée si vous le connaissez ;
- marquez les cellules : ciblable, non ciblable, obstacle ;
- cliquez « Enregistrer l'échantillon ».

## Étape 7 — Résultats et dossier

1. Cliquez **Arrêter l'observation** : le journal de session est clos.
2. Onglet Sorts : si un sort est prêt, cliquez **Confirmer : la barre affiche cette page**. Cette confirmation devient caduque dès que la barre change.
3. Générez le dossier :

   ```
   .venv\Scripts\python -m combatbot.benchmark --live-fasttrack-report --profile-id N
   ```

   Il crée `reports\live-fasttrack-<date>\` avec les fichiers suivants :
   - `summary.md`
   - `telemetry.json` (latence par étape, sous-étapes de capture, de grille et d'entités)
   - `map.json` et `grid.json` (changements de map, alignement après chaque changement)
   - `entities.json` (raison de chaque cellule joueur inconnue)
   - `hud.json` (PA/PM inconnus en combat)
   - `combat-state.json` (aucun « mon tour » sans modèle)
   - `4c-proof.json`
   - `readiness.json` (profil, sorts, page confirmée, porte d'entrée réelle)
4. Complétez la section « À compléter » de `summary.md` et transmettez le dossier.

## Ce que cette recette ne fait jamais

- Aucun clic, aucun mouvement de souris, aucune touche envoyée par DofBot2.
- La porte d'entrée réelle reste fermée ; elle est vérifiée dans `readiness.json`.
- Aucune vérité TEST n'est modifiée et aucun seuil n'est réglé.
- Aucun domaine n'est déclaré PASS sans preuve mesurée.
