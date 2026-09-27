# Handoff : interface DofBot2

## Vue d'ensemble
Nouvelle interface de l'application (renommée **DofBot2**) : démarrage → connexion à la fenêtre du jeu → choix / création de profil → application principale avec navigation en bulles et pages minimalistes à accordéons (Accueil, Donjons, Zones, Sorts, Alertes, Réglages), plus des notifications toast.

## À propos des fichiers
`DofBot2.dc.html` est une **référence de design en HTML** (prototype cliquable), pas du code de production. La tâche est de **recréer cette interface dans le code existant du dépôt `pythonbot_test` (PySide6 / Qt)** avec ses patterns : widgets Qt, feuilles QSS, `QPropertyAnimation`, stockage SQLite existant (`combatbot/storage.py`). Ne pas embarquer le HTML.

Ouvrir `DofBot2.dc.html` dans un navigateur (avec `support.js` à côté) pour voir le comportement.

## Fidélité
**Haute fidélité** : couleurs, typo, espacements, rayons et interactions sont finaux. Les zones hachurées « illustration · … » et « image » sont des emplacements d'images à remplacer par de vrais visuels.

## Fenêtre
- Taille de référence 1200×760, sans cadre système (barre de titre custom 40px, fond `#0a0d0b`, bordure basse `rgba(255,255,255,.05)`).
- Barre de titre : pastille logo 20×20 r6 `#8fd14f` texte « D2 » 800 10px `#0b1408` ; « DofBot2 » 700 12px ; fil d'Ariane « · <onglet> » `#5d6a5f` ; boutons fenêtre 46px (fermer : hover `#c42b1c`).
- Fond fenêtre `#0c100d`, rayon 14, bordure `rgba(255,255,255,.07)`.

## Écrans

### 1. Démarrage (splash)
- Centré, colonne, gap 22.
- Logo 96×96 r28 `#8fd14f`, « D2 » 800 34px, halo `0 0 0 10px rgba(143,209,79,.08), 0 0 60px rgba(143,209,79,.25)`.
- Titre « DofBot2 » 800 34px, tracking -0.03em ; sous-titre « Votre assistant de farm, donjons et zones » 15px `#8e9c8f`.
- Barre de progression 260×4 r2, piste `#1b231e`, remplissage `#8fd14f`, ~1,8 s au total.
- Texte d'état mono 12px `#5d6a5f` : « Chargement des données… » (<40 %), « Recherche des fenêtres du jeu… » (<80 %), « Prêt ».
- À 100 % → écran Connexion. Un clic n'importe où passe l'écran.

### Indicateur d'étapes (écrans 2–4)
En haut, centré, gap 28 : 3 étapes « Connexion », « Profil », « C'est parti ». Rond 24px : fait = fond `#8fd14f` + « ✓ » ; en cours = fond `rgba(143,209,79,.14)`, texte/bordure `#8fd14f` ; à venir = bordure `rgba(255,255,255,.12)`, texte `#5d6a5f`.

### 2. Connexion à la fenêtre
- Grille 2 colonnes égales, gap 56, padding 40/72/56, centrée verticalement.
- Gauche : illustration 320px de haut r18 + 3 légendes (« 1. Lancez le jeu », « 2. Choisissez », « 3. C'est prêt ») 12px, titre en `#8fd14f` 700.
- Droite : titre « Connectez-vous à la fenêtre du jeu » 800 30px ; texte « DofBot2 a trouvé ces fenêtres ouvertes. Choisissez celle du personnage à utiliser. » 15px `#8e9c8f`.
- Liste des fenêtres détectées (utiliser la capture de fenêtre existante `test_window_capture`) : carte r14 padding 14/16, miniature 44×30, titre 700 14px (« Dofus · <perso> »), méta mono 11px (« 1920×1080 · écran 1 »), radio 18px à droite. Sélectionnée : fond `rgba(143,209,79,.07)`, bordure `rgba(143,209,79,.6)`.
- Bouton principal « Se connecter » (voir Boutons) → Profils. Lien « Actualiser la liste » `#8e9c8f` 600 13px.

### 3. Choix du profil
- Titre « Qui joue aujourd'hui ? » 800 30px, sous-titre « Chaque profil garde ses sorts, ses donjons et ses réglages. »
- Cartes 176px, r20, fond `#121813`, padding 26/16/20 ; avatar rond 80px (couleur + initiale 800 30px `#0b1408`), nom 700 16px, méta « <Classe> · niv. <n> » 12px. Hover : bordure `rgba(143,209,79,.5)`, fond `#151d16`.
- Carte « Nouveau profil » : bordure 1.5px pointillée `rgba(143,209,79,.35)`, rond « + » `rgba(143,209,79,.1)`, texte `#8fd14f`, sous-texte « Avatar et nom au choix ».
- Clic profil → app (onglet Accueil). Clic nouveau → Création.

### 4. Création de profil
- Grille 340px / 1fr, gap 56.
- Gauche : aperçu live (label mono « APERÇU », avatar 140px, nom 800 22px ou « Nouveau profil », classe).
- Droite : « Créer un profil » ; champ « Nom du profil » (h48 r12, fond `#121813`, focus bordure `#8fd14f`, placeholder « ex. Kira ») ; « Avatar » : 8 ronds 44px (couleurs ci-dessous) + tuile « ↑ » importer une image ; sélection = anneau `0 0 0 3px #0c100d, 0 0 0 5px <couleur>` ; « Classe · facultatif » : puces (Cra, Iop, Eniripsa, Sram, Enutrof, Féca, Sacrieur, Osamodas, Xélor, Pandawa, Ecaflip, Roublard).
- « Créer le profil » → enregistre et ouvre l'app ; « Retour » → Profils.

### 5. Application principale (structure commune)
- **Barre haute** 64px, padding 0 28 : puce profil (avatar 32px + nom 700 13 + méta 11) cliquable → Profils ; à droite pastille d'état (h32 r16 fond `#121813`, point 7px lumineux ; « En cours » `#8fd14f` / « En pause » `#8e9c8f`) et bouton Démarrer/Arrêter.
- **Contenu** défilant, colonne centrée max 800px, padding 32/24/140, gap 24.
- **En-tête de page** : illustration 200×120 r16 + titre 800 28px + texte 14px `#8e9c8f` max 480px (textes par onglet dans le prototype).
- **Dock de bulles** : flottant, centré, 22px du bas. Conteneur r32 padding 7 gap 6, fond `rgba(18,24,19,.86)` + flou 14px, bordure `rgba(255,255,255,.08)`, ombre `0 16px 40px rgba(0,0,0,.5)`. Bulles 48×48 rondes, icône 20px trait 1.8. Active : fond `#8fd14f`, icône/texte `#0b1408`, s'élargit en pilule avec son libellé (padding 0 18 0 14). Inactive : transparente, `#8e9c8f`, hover `#e6ede4`. Transition 200 ms. Point orange `#e5a13a` 7px sur Accueil quand un nouvel événement arrive hors de l'onglet.
- Icônes : voir `ICONS` dans le JS du prototype (tracés SVG 24×24), à convertir en SVG/QIcon.

### Accordéon (composant clé)
- Carte r16, fond `#121813`, bordure `rgba(255,255,255,.06)` (ouvert : `rgba(143,209,79,.22)`). Gap 10 entre cartes.
- En-tête : padding 16/20, titre 700 15px, badge « facultatif » (mono 600 10px `#6f7f71`, fond `#1b231e`, r6), sous-titre 12px `#8e9c8f`, chevron 18px qui pivote de 180° (200 ms).
- Lignes : padding 13/0, séparateur `rgba(255,255,255,.05)`, libellé 600 14px + description 12px `#8e9c8f`, contrôle à droite :
  - **Interrupteur** 40×22 r11 ; on `#8fd14f` + bouton `#0b1408`, off `#2a332c` + bouton `#8e9c8f` ; bouton 16px, glisse 3px→21px.
  - **Pas à pas** h34 r17 fond `#0c100d` : « − » valeur (mono 600 13px, min 54px) « + ».
  - **Choix / puces** h30 r15 : actif fond `rgba(143,209,79,.16)`, bordure `rgba(143,209,79,.55)`, texte `#b6e68a` ; inactif bordure `rgba(255,255,255,.09)`, texte `#8e9c8f`. Unique ou multiple selon le réglage.
  - **Champ** 300×34 r10, mono 12px.
  - **Info** : valeur mono dans pastille `#0c100d` + lien « Changer ».
- Tous les accordéons Donjons/Zones sont facultatifs : les valeurs par défaut suffisent pour démarrer.

### Onglets
- **Accueil** : carte « ACTIVITÉ PROGRAMMÉE » (nom, résumé ex. « Donjon · 10 runs · boss en priorité · 20 sorts actifs », boutons Modifier → onglet concerné, Démarrer/Arrêter) ; carte « JOURNAL » (heure mono + message, 7 derniers).
- **Donjons** : grille 4 cartes (image 78px + nom + « Niv. · salles »), sélection bordure 1.5px `#8fd14f`. Accordéons : Stratégie de combat (Prioriser le boss ; Ensuite cibler : Plus proche / Moins de PV / Plus dangereux ; Style : Distance / Corps-à-corps / Survie ; PA en réserve), Déroulement (runs, 0 = ∞ ; pause s ; recommencer après défaite), Clés & inventaire (acheter la clé ; retour banque au-delà de % ; recycler), Sécurité (se soigner sous % ; pause si un joueur écrit ; arrêt si mort). Bouton « Programmer ce donjon » + note « Tout est facultatif… ».
- **Zones** : mêmes cartes ; Monstres ciblés (puces multiples, taille max de groupe, niveau max), Trajet (Boucle / Aléatoire ; éviter les maps occupées), Récolte (récolter ; métiers multiples), Sécurité.
- **Sorts** : carte « 20 sorts détectés dans la barre de sorts · il y a … » + « Relancer la détection » ; grille 10 colonnes des icônes détectées (r12, pastille coût PA en haut à droite, sélection anneau vert, sort désactivé opacité .35). Sous la grille : sort sélectionné (icône 56px, nom, emplacement, interrupteur « Utiliser en combat »). Accordéons liés au modèle `Spell` de `combatbot/models.py` :
  - Coût & portée → `ap_cost`, `min_range`, `max_range`, `modifiable_range`
  - Conditions de lancer → `line_of_sight`, `line_cast`, `per_turn`, `per_target`
  - Ciblage → cible (Ennemi / Allié / Soi-même / Case vide), quand (Toujours / Premier tour / PV bas), `priority` — champs cible/quand à ajouter au modèle.
- **Alertes** : bandeau + « Envoyer un test » ; Notifications bureau (Boss vaincu, Run terminé, Inventaire presque plein, Message privé reçu, Personnage mort, Session arrêtée), Quand je fais autre chose (seulement si le jeu n'est pas au premier plan, son, durée 4 s / 8 s / jusqu'au clic), Sur le téléphone (Discord webhook).
- **Réglages** : Connexion (fenêtre, profil → « Changer » renvoie aux écrans 2/3), Raccourcis (F8 démarrer/pause, F9 arrêt d'urgence), Application (lancer avec Windows, réduire dans la barre des tâches, langue).

### Notifications (toasts)
- En bas à droite (20px du bord, 96px du bas), largeur 320, pile de 3 max, gap 10, disparition après 4,5 s (ou selon « Durée d'affichage »).
- Carte r14 fond `#161e17`, bordure `rgba(255,255,255,.08)`, ombre `0 14px 36px rgba(0,0,0,.5)` ; pastille « D2 » 32px r10 ; titre 700 13px ; texte 12px `#8e9c8f`.
- Doivent aussi exister hors de l'application (notification Windows / fenêtre toast toujours au premier plan) quand l'utilisateur fait autre chose.
- Chaque type est filtré par son interrupteur dans Alertes.

## États
- `screen` : splash | connect | profile | create | app
- fenêtre sélectionnée, liste des profils (nom, avatar, classe, niveau) persistée en SQLite
- `tab` actif, accordéons ouverts (au choix : un seul ou plusieurs), valeurs des réglages par profil
- sort sélectionné + configuration par sort
- activité programmée (donjon ou zone), `running`, journal, toasts, indicateur non lu

## Tokens
- Fonds : page `#070908`, fenêtre `#0c100d`, barre titre `#0a0d0b`, surface `#121813`, surface hover `#151d16`, surface 2 `#1b231e`, toast `#161e17`, interrupteur off `#2a332c`
- Vert : principal `#8fd14f`, hover `#a3dc6a`, texte clair `#b6e68a`, teinte `rgba(143,209,79,.07 / .1 / .14 / .16)`
- Texte : `#e6ede4`, secondaire `#8e9c8f`, tertiaire `#6f7f71`, discret `#5d6a5f`, sur vert `#0b1408`
- Alerte : `#e5a13a`
- Bordures : `rgba(255,255,255,.05 / .06 / .08 / .12)`
- Typo : Manrope (400–800) pour l'interface, JetBrains Mono (500–600) pour valeurs, méta et labels en capitales (tracking .08em)
- Tailles : 34 / 30 / 28 / 22 / 20 / 18 / 16 / 15 / 14 / 13 / 12 / 11 / 10
- Rayons : 6, 10, 12, 14, 16, 18, 20, 24, pilules et ronds pleins
- Boutons : principal h46 r23 (ou h38 r19 dans la barre) fond `#8fd14f` texte 800 14px `#0b1408`, hover `#a3dc6a` ; secondaire transparent bordure `rgba(255,255,255,.12)`, hover bordure `#8fd14f` ; « Arrêter » transparent bordure `rgba(255,255,255,.18)`.
- Couleurs d'avatar : `#8fd14f #5cc4d6 #e0a257 #b27ae0 #e0677e #d9cf5a #4fd1a0 #8a97ff`

## Assets
- `assets/spells/s0–s19.png` : icônes 52×52 découpées dans `assets/user_spellbar_reference.png` (barre de sorts de l'utilisateur), en attendant les vraies icônes issues de la détection.
- Illustrations et images de donjons/zones : à fournir (emplacements hachurés dans le prototype).
- Logo « D2 » : provisoire.

## Fichiers
- `DofBot2.dc.html` : prototype complet (template + logique JS en bas du fichier)
- `support.js` : moteur nécessaire pour ouvrir le prototype dans un navigateur
- `assets/spells/` : icônes de sorts
