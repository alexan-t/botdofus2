# Audit UI, performance et mémoire — avant modification

> Branche `lot-performance-ui-memory` (base `a174375`, `lot-windows-live-readiness`).
>
> **Contexte de mesure.** Conteneur Linux, Qt offscreen, Python 3.11. Le PC de l'utilisateur, DOFUS et RapidOCR ne sont pas disponibles ici. Les mesures sont reproductibles avec `python -m combatbot.benchmark --runtime-health`, qui pilote la vraie fenêtre DofBot2 avec des frames synthétiques de taille réelle (zone combat 2100×1000, client 2560×1377).
>
> **Aucune conclusion « fuite » sans mesure.** Chaque point ci-dessous est vérifié dans le code et, lorsque c'est indiqué, mesuré avant correction.

## 1. Constats par zone

| Zone | Constat (avant) | Mesure avant | Gravité |
|---|---|---|---|
| **Threads de fond (`JobRunner`)** | `QRunnable` Python avec `setAutoDelete(False)` : PySide transfère le runnable au pool, qui ne le libère jamais. La connexion `done → lambda: self._done(job)` forme en plus un cycle via Qt, invisible pour le GC. **Chaque job terminé reste vivant avec ses rappels et tout ce qu'ils capturent.** Chaque tick d'observation = 1 job ; chaque démarrage d'observation garde l'observateur précédent (tracker, modèle de fond, service de map…). | Test minimal : 30 jobs → 30 jobs et 30 captures de 1 Mo vivants. Harnais : 40 cycles démarrer/arrêter → RSS 236 → 452 Mo, tas Python 45 → 267 Mo | **Fuite réelle** |
| **Accordéons (page Observation)** | Signature `(texte de map, coordonnées, …)` : chaque changement du texte de map détruit et recrée **tous** les accordéons (≈ 25 widgets). Le texte change souvent en session (TRANSITION / AMBIGUË / RÉSOLUE, alignement, candidats). | 60 ticks avec une map qui varie : 46 reconstructions, 42,3 ms/tick. Harnais 60 s : 67 reconstructions | Élevée |
| **Mises à jour 800 ms (AdvancedView, Observation, Connexion, statut)** | `setText` + `setStyleSheet` (re-polish de style) sur ≈ 30 labels, `repolish` du bouton, `adjustSize` de la bulle de survol à **chaque tick**, même sans aucun changement | 6,05 ms/tick à valeurs identiques | Moyenne |
| **`ObservationPreview.set_image`** | `bgr_to_pixmap` pleine résolution (3 copies : `cvtColor`, `QImage.copy`, `QPixmap.fromImage`), puis `scaled(SmoothTransformation)` à chaque observation, même onglet caché ou fenêtre réduite | 17,4 ms de thread UI par frame (2100×1000 → 900×500) | Moyenne |
| **`FramePreview` (Connexion : aperçu + vignette 44×30)** | Même frame convertie deux fois en `QImage` pleine résolution (≈ 14 Mo chacune pour 2560×1377), conservées ; réduction lissée **à chaque repaint** ; cache par `id(image)` (identifiant réutilisable par Python pour une autre frame) | 14,8 ms par nouvelle frame ; ≈ 28 Mo de `QImage` gardés | Moyenne |
| **`client_panel.set_frame`** | Même schéma : pixmap pleine résolution puis réduction lissée | inclus ci-dessus | Faible |
| **`LogsPage`** | `QTextEdit.append` sans limite (le tableau de bord a `setMaximumBlockCount(100)`) | 20 000 lignes → 20 200 blocs, +9,2 Mo RSS | Moyenne (sessions longues) |
| **Journal avancé** | Relit 120 événements SQLite à chaque tick pour savoir si rien n'a changé ; reconstruit 80 lignes à chaque nouvel événement | 0,153 ms/tick (contre 0,006 ms pour `MAX(id)`) | Faible |
| **`Storage.record_event`** | INSERT + COMMIT par événement (journal `delete`, `synchronous=FULL`) | 0,8 ms/événement ; l'observation ne produit **aucun** événement par frame (seulement démarrage, arrêt, actions) | Négligeable : non modifié |
| **Boucle d'observation** | Timer 400 ms ; `_observe_once` saute le tick si un job est en vol (`_observation_pending` / `jobs.active`) : **aucune file cachée**, jamais deux observations simultanées | 0 tick sauté mesuré, un seul job en vol | Conforme |
| **Timers Qt** | observation 400 ms, connexion 2 s, vue avancée 800 ms (arrêté sur `hideEvent`), écrans d'accueil (50 ms seulement pendant le splash, 3 s sur le choix de fenêtre visible), compte à rebours du scan 1 s | 1 timer actif après les cycles démarrer/arrêter (stable) | Conforme |
| **Contrôle de connexion (2 s)** | `inspect_dofus_window` + lecture de calibration SQLite + `zones_needing_review` (arithmétique de rectangles, sans traitement d'image) | négligeable | Conforme |
| **OCR / ONNX Runtime** | Un seul moteur RapidOCR par processus (`lru_cache(maxsize=1)`, partagé par map, tooltips, profil). Threads ONNX : **réglages par défaut** (peut occuper tous les cœurs pendant chaque lecture de map, ≈ 0,8–1 s toutes les 2 s sur le PC n° 1) | non mesurable ici (RapidOCR absent) | À mesurer sur le PC |
| **Capture** | Déjà corrigée au lot précédent (`8b655bd`) : une conversion RGB, test d'uniformité sous-échantillonné | 185 → 27 ms (synthétique) | Traité |
| **GameData / caches** | Topologies et projections : LRU 8 ; formes de map : `max_cache` ; empreintes : `FINGERPRINT_MAX_PER_MAP` par map ; confirmations dédupliquées ; icônes : `lru_cache(64)` | tous bornés | Conforme |
| **EntityDetector / suivi / modèle de fond** | Fond : 12 échantillons par cellule, 560 cellules au plus ; suivi et fond réinitialisés à chaque changement de map ; pistes perdues limitées aux entités d'un combat | bornés | Conforme |
| **Toasts** | 3 au plus, `deleteLater` | bornés | Conforme |
| **Objets conservés après arrêt** | `_last_observation` et `_last_packet` : **dernier paquet seulement** (état voulu, pour l'enregistrement et le survol) ; observateur remis à `None` ; service de map fermé | 1 paquet vivant après arrêt | Conforme, une fois la fuite `JobRunner` corrigée |

## 2. Propriétaires des images (une frame d'observation)

| Propriétaire | Nature |
|---|---|
| `CapturedFrame.image` | frame client (thread de vision), libérée à la fin de `observe()` sauf les recadrages |
| `ObservationPacket.original` | zone combat : **vue** sur la frame (recadrage numpy) |
| `ObservationPacket.annotated` | copie dessinée de la zone combat (nécessaire : overlay) |
| `CombatPage._last_packet` / `MainWindow._last_observation` | **référence** au même paquet (aucune copie) |
| `ObservationPreview._source` | **référence** à `packet.annotated` (aucune copie) |
| `ObservationPreview` pixmap | **avant** : 3 copies pleine résolution + 1 réduite ; **après** : 1 copie réduite |
| `FramePreview.image` (×2) | **avant** : 2 `QImage` pleine résolution ; **après** : 2 images à la taille du widget |

## 3. Conclusion de l'audit

- **Fuite mémoire : OUI, démontrée.** `JobRunner` ne libérait jamais ses jobs, ni tout ce que leurs rappels capturaient.
- **Coûts CPU/UI :** reconstructions d'accordéons, réécritures de style à chaque tick, aperçus pleine résolution, rendu d'aperçus invisibles.
- **Non démontrés ici :** GDI/USER et handles Windows (mesurés par le harnais sur le PC), et la pression CPU de RapidOCR.
