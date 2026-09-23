# AI Daily Digest

Pipeline automatisé qui collecte, analyse et diffuse un récapitulatif quotidien des actualités IA majeures directement sur smartphone via push notification.

## Architecture

```
26 Flux RSS/Atom → Fetch asynchrone (aiohttp) → LLM Gemini (sélection Top 5) → Push ntfy.sh
```

Exécuté tous les jours à 06:00 UTC via GitHub Actions.

## Prérequis

- Python 3.11+
- Une clé API [Google AI Studio](https://aistudio.google.com/apikey) (gratuite)
- L'application [ntfy](https://ntfy.sh) installée sur smartphone

## Installation

```bash
git clone https://github.com/nathanl0204/ai-daily-digest.git
cd ai-daily-digest
pip install -r requirements.txt
```

## Configuration

1. Copier le fichier d'exemple :

```bash
cp .env.example .env
```

2. Renseigner vos clés dans `.env` :

```
GEMINI_API_KEY=clé_api_gemini
NTFY_TOPIC=topic_secret_ntfy
```

3. Pour obtenir un `NTFY_TOPIC` : ouvrir l'app ntfy → **Add subscription** → entrer un nom secret (ex: un UUID).

## Utilisation

### Exécution manuelle

```bash
python -m src.main
```

Vous recevrez 5 notifications push, une par article sélectionné.

### Tests

```bash
python -m pytest tests/ -v
```

### Automatisation (GitHub Actions)

Le workflow se déclenche automatiquement chaque jour à 06:00 UTC. Pour le tester manuellement :

1. Pousser le dépôt sur GitHub
2. Configurer les **Repository Secrets** (`GEMINI_API_KEY` et `NTFY_TOPIC`)
3. Onglet **Actions** → **AI Daily Digest** → **Run workflow**

## Sources

Le fichier `sources.json` contient les 26 flux RSS/Atom configurés :

| Catégorie | Sources |
|---|---|
| Lab | OpenAI, DeepMind, Hugging Face, Microsoft Research, Anthropic |
| Cloud | AWS ML Blog |
| Research | arXiv CS.AI, CS.LG, CS.CL |
| Press | MIT Tech Review, TechCrunch, VentureBeat, Ars Technica, MarkTechPost |
| Newsletter | Import AI, Ahead of AI, Last Week in AI |
| Blog | Simon Willison |
| FR Press | ActuIA, Le Monde Informatique, Développez.com, Silicon.fr, Journal du Net, Clubic, Sciences et Avenir |
| FR Community | Le Journal du Hacker |

Pour ajouter ou retirer une source, éditer `sources.json`.

## Structure du projet

```
ai-daily-digest/
├── .github/workflows/digest.yml   # CI/CD GitHub Actions
├── src/
│   ├── config.py                  # Chargement config et validation env
│   ├── fetcher.py                 # Fetch async des flux RSS
│   ├── summarizer.py              # Sélection LLM et synthèse
│   ├── notifier.py                # Envoi push ntfy.sh
│   └── main.py                    # Point d'entrée
├── tests/                         # Tests unitaires
├── sources.json                   # Registre des sources
├── requirements.txt
└── .env.example
```

## Stack technique

- **Ingestion** : Python, aiohttp, feedparser, BeautifulSoup
- **LLM** : Google Gemini (gemini-3.5-flash)
- **Notification** : ntfy.sh (HTTP POST, Markdown)
- **Orchestration** : GitHub Actions (cron + workflow_dispatch)
