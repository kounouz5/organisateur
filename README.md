# Mon organisateur (FR)

Organise ton travail en parlant ou en écrivant normalement. Une phrase comme
« rappelle-moi d'appeler le fournisseur demain à 10h, puis réunion avec Sami lundi à 14h30 »
devient des **tâches, rappels, événements et brouillons d'e-mails**, enregistrés et rangés par jour.

## 100 % gratuit

Sans clé API, l'application ne coûte rien et marche hors-ligne :
FastAPI, SQLite, la dictée et la voix du navigateur (Chrome / Edge) sont gratuits.
La clé `ANTHROPIC_API_KEY` est **optionnelle et payante** : laisse-la vide pour rester gratuit.
L'assistant affiche « gratuit, hors-ligne » ou « Claude (payant) » pour que tu saches toujours.

## Assistant intelligent gratuit (IA locale)

L'assistant utilise une IA qui tourne **sur ton ordinateur**, gratuitement, avec [Ollama](https://ollama.com) :

```bash
ollama pull qwen3:4b
```

Dès qu'Ollama est lancé, l'assistant comprend les phrases libres (« faut que je pense à appeler le dentiste
vendredi genre 9h30 »). Sans Ollama, il repasse tout seul en mode simple (règles). Ordre utilisé :
Claude (si clé API, payant) → IA locale (gratuite) → règles. `OLLAMA_MODEL=qwen3:8b` = plus intelligent, plus lent.

## Fonctions

- **Calendrier** : touche un jour pour l'ouvrir en grand (heures, cocher, ajouter à ce jour).
- **📬 Mails reçus** : connecte ta boîte (Gmail, Yahoo, iCloud… via IMAP, gratuit) avec un
  « mot de passe d'application ». L'app affiche tes mails non lus, en fait des tâches en un clic,
  et l'assistant annonce à voix haute chaque nouveau mail (vérifié toutes les 2 minutes, page ouverte).
  **Lecture seule** : rien n'est envoyé, supprimé ni marqué comme lu. Le compte est enregistré
  dans `data/mail.json`, uniquement sur ton ordinateur.
- **Assistant qui parle** (💬 en bas à droite) : « qu'est-ce que j'ai aujourd'hui ? », « organise ma journée »
  (il propose des heures, réponds « oui »), « mets le rapport à 11h », « j'ai fini le rapport ».
  Il lit ses réponses à voix haute et annonce les rappels (10 min avant un rendez-vous, puis à l'heure).

- **Une seule zone de saisie** : écris ou clique sur 🎙, puis Entrée. Plusieurs choses d'un coup, c'est OK.
- **Liste rangée par jour** : En retard · Aujourd'hui · Demain · Cette semaine · Plus tard · Sans date.
- **Cocher** pour terminer (avec « Annuler »), **cliquer** sur un élément pour le modifier (enregistré automatiquement).
- **Supprimer** avec « Annuler » pendant 5 secondes.
- **Filtres** : Tâches, Rappels, Événements, Mails.
- **📅 Ajouter à mon agenda** (fichier `.ics` pour Google / Outlook / Apple) et **✉️ Ouvrir dans ma messagerie** pour les mails.
- **Notifications** quand un rappel arrive (si la page est ouverte).
- Données sauvegardées dans `data/organizer.db` (SQLite).

## Démo

1. `uvicorn app.main:app --reload`
2. Ouvre http://localhost:8000, clique sur 🎙 (Chrome/Edge) ou écris ton message.

> « Rappelle-moi de commander les cartons demain à 9h, puis réunion avec le client vendredi à 15h »
> → 1 rappel (demain 09:00) + 1 événement (vendredi 15:00)

_(ajoute ici un GIF de la démo)_

## Comment ça marche

```
Micro (Web Speech API, fr-FR) → texte → POST /parse → { items: [...] }
                                           ├─ LLM (si ANTHROPIC_API_KEY) : JSON structuré
                                           └─ Parseur par règles (hors-ligne, fallback)
```

- **Deux moteurs :** le LLM comprend les formulations libres ; le parseur par règles garantit que l'app marche sans clé ni réseau.
- **Dégradation propre :** si l'API LLM échoue, la requête retombe sur les règles.
- **Dates relatives** résolues côté serveur (demain, lundi, dans 2 heures, après-demain à 14h30).

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optionnel : ajoute ANTHROPIC_API_KEY
uvicorn app.main:app --reload
pytest
```

## API

`POST /parse` `{ "text": "...", "now": "2026-11-14T12:00:00" }` →

```json
{ "engine": "rules",
  "items": [{ "type": "reminder", "title": "Commander les cartons", "when": "2026-11-15T09:00" }] }
```

Types : `task`, `reminder`, `event`, `email_draft` (avec `to`, `subject`, `body`).

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/add` | Analyse la phrase **et** enregistre les éléments |
| `GET` | `/items` | Liste tous les éléments |
| `PATCH` | `/items/{id}` | Modifie (titre, date, `done`…) |
| `DELETE` | `/items/{id}` | Supprime |
| `GET` | `/items/{id}/ics` | Export agenda `.ics` |

## Limites connues

- Web Speech API : Chrome/Edge uniquement, nécessite une connexion.
- Le parseur par règles ne gère pas les dates absolues ("le 20 novembre").
- Les notifications de rappel ne marchent que si la page est ouverte.
- Un seul utilisateur (pas de comptes).

## Prochaines étapes

- [x] Export `.ics` pour les événements et lien `mailto:` pour les brouillons
- [x] Persistance (SQLite) et liste des éléments créés
- [ ] Transcription serveur (Whisper) pour supporter tous les navigateurs
- [ ] Dates absolues et arabe tunisien / franco-arabe
