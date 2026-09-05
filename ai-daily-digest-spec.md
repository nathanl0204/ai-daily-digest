# Technical Specification: Daily AI News Digest Pipeline (`ai-daily-digest`)

> **Spec-Driven Development Notice**
> Ce document sert de source de vérité contractuelle pour le développement incrémental assisté par agent IA. Les sections sont modularisées par tâches indépendantes et testables. Chaque section comporte des critères d'acceptation stricts (DoD).

---

## 1. Contexte & Architecture Globale

### 1.1 Objectif

Construire un pipeline d'ingestion, de sélection intelligente et de diffusion d'un récapitulatif quotidien des actualités IA majeures directement sur smartphone, 100 % gratuit et sans serveur dédié.

### 1.2 Schéma d'Architecture

```
[ 27 Flux RSS / Atom ]
          │ (Async HTTP GET - aiohttp)
          ▼
[ GitHub Actions Runner (Cron 06:00 UTC) ]
          │
          ├── Step 1: Parse & Clean (feedparser, BeautifulSoup)
          │           Filtre temporel [T - 24h, T]
          │
          ├── Step 2: Synthèse, Dédoublonnage & Sélection (LLM API)
          │           Inférence sur Top 5 actus à impact
          │
          └── Step 3: Dispatch Push Notification (ntfy.sh)
                      HTTP POST payload Markdown
          │
          ▼
   [ ntfy.sh Topic ] ──(Push APNs / FCM)──> [ Smartphone (App ntfy) ]
```

### 1.3 Matrice des Composants & Dépendances

| Composant | Rôle | Techno / Service | Quota / Contrainte |
|---|---|---|---|
| Ingestion | Collecte asynchrone des flux | Python (aiohttp, feedparser, beautifulsoup4) | Timeout global ≤ 60s |
| Sélection/LLM | Dédoublonnage & synthèse | Google AI Studio (gemini-1.5-flash) ou Groq (llama-3.3-70b) | Free tier (≤ 15 RPM) |
| Diffusion | Envoi notification push | Endpoint HTTP ntfy.sh (App ntfy sur mobile) | Payload ≤ 4096 octets |
| Orchestrateur | Exécution planifiée | GitHub Actions | 2 000 min/mois gratuites |

---

## 2. Sécurité & Gestion des Secrets

**Contrainte critique :** Le dépôt GitHub est strictement public. Aucune donnée confidentielle ou identifiant personnel ne doit être hardcodé ou commité dans Git.

### 2.1 Variables d'Environnement Requises

Les paramètres sensibles sont injectés via GitHub Repository Secrets (`Settings > Secrets and variables > Actions > Repository secrets`) :

- `GEMINI_API_KEY` : Clé API Google AI Studio (ou `GROQ_API_KEY`).
- `NTFY_TOPIC` : Identifiant aléatoire et secret du topic ntfy (ex: chaîne UUIDv4 générée côté client `a8b7c3d4-e5f6-4a1b-9c2d-3e4f5a6b7c8d`).

### 2.2 Stratégie Anti-Fuite

Fichier `.gitignore` obligatoire à la racine incluant :

```
.env, .env.*
__pycache__/, *.pyc
.pytest_cache/, .coverage
```

- Fichier `.env.example` versionné documentant les clés requises sans valeurs réelles.
- Vérification automatisée dans la CI : échec immédiat du workflow si une variable d'environnement critique est vide ou non définie.

---

## 3. Spécifications Détaillées des Modules

### Module A : Ingestion & Parsing des Flux

#### A.1 Registre des Sources

Le registre contient exactement 27 sources réparties par catégorie dans un fichier de configuration découplé `sources.json` :

```json
[
  {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml", "category": "lab"},
  {"name": "Google DeepMind Blog", "url": "https://deepmind.google/blog/rss.xml", "category": "lab"},
  {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "category": "lab"},
  {"name": "Microsoft Research AI", "url": "https://www.microsoft.com/en-us/research/feed/", "category": "lab"},
  {"name": "AWS Machine Learning Blog", "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "category": "cloud"},
  {"name": "arXiv CS.AI", "url": "https://rss.arxiv.org/rss/cs.AI", "category": "research"},
  {"name": "arXiv CS.LG", "url": "https://rss.arxiv.org/rss/cs.LG", "category": "research"},
  {"name": "arXiv CS.CL", "url": "https://rss.arxiv.org/rss/cs.CL", "category": "research"},
  {"name": "MIT Technology Review AI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed/", "category": "press"},
  {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "press"},
  {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "category": "press"},
  {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "category": "press"},
  {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "category": "press"},
  {"name": "MarkTechPost", "url": "https://www.marktechpost.com/feed/", "category": "press"},
  {"name": "Import AI (Jack Clark)", "url": "https://importai.substack.com/feed", "category": "newsletter"},
  {"name": "Ahead of AI (Sebastian Raschka)", "url": "https://magazine.sebastianraschka.com/feed", "category": "newsletter"},
  {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "category": "blog"},
  {"name": "Last Week in AI", "url": "https://lastweekin.ai/feed", "category": "newsletter"},
  {"name": "Anthropic", "url": "https://rsshub.bestblogs.dev/anthropic/news", "category": "lab"},
  {"name": "ActuIA", "url": "https://www.actuia.com/feed/", "category": "fr_press"},
  {"name": "Le Monde Informatique IA", "url": "https://www.lemondeinformatique.fr/flux-rss/thematique/intelligence-artificielle/rss.xml", "category": "fr_press"},
  {"name": "Développez.com IA", "url": "https://intelligence-artificielle.developpez.com/index/rss", "category": "fr_press"},
  {"name": "Silicon.fr Data & IA", "url": "https://www.silicon.fr/thematique/data-ia-1372/feed", "category": "fr_press"},
  {"name": "Journal du Net IA", "url": "https://www.journaldunet.com/intelligence-artificielle/rss/", "category": "fr_press"},
  {"name": "Le Journal du Hacker IA", "url": "https://www.journalduhacker.net/t/intelligence%20artificielle.rss", "category": "fr_community"},
  {"name": "Clubic IA", "url": "https://www.clubic.com/feed/technologies-d-avenir/intelligence-artificielle/rss", "category": "fr_press"},
  {"name": "Sciences et Avenir IA", "url": "https://www.sciencesetavenir.fr/high-tech/intelligence-artificielle/rss.xml", "category": "fr_press"}
]
```

#### A.2 Règles d'Ingestion & Normalisation

**Concurrence & Résilience :**
- Récupération HTTP asynchrone (aiohttp) avec timeout par source fixé à 5 secondes.
- Si une source renvoie un code HTTP ≥ 400, timeout ou XML invalide : ignorer silencieusement la source en consignant un log d'avertissement (WARNING), sans faire échouer le pipeline.

**Fenêtre Glissante :**
- Ne conserver que les articles dont la date de publication vérifie :

  $$T_{\text{actuel}} - 26\text{h} \le T_{\text{publication}} \le T_{\text{actuel}}$$

  (marge de sécurité de 2h pour parer aux décalages d'horloge des serveurs).
- Si la date de publication est absente du flux, l'entrée est rejetée par défaut.

**Nettoyage & Format Pivot :**
- Retrait strict de tout balisage HTML résiduel dans le résumé (`BeautifulSoup(summary, 'html.parser').get_text()`).
- Troncature du résumé brut à 250 caractères par article pour optimiser la taille du prompt.

**Modèle de données pivot :**

```python
@dataclass
class ArticleCandidate:
    source_name: str
    title: str
    link: str
    summary: str
    published_at: datetime
```

#### Critères d'Acceptation (DoD - Module A)

- [ ] Le module télécharge 27 flux en moins de 15 secondes au total via pooling asynchrone.
- [ ] La panne ou le blocage réseau d'un ou plusieurs flux ne bloque pas l'exécution des autres.
- [ ] Tous les résumés sont purgés de tout fragment HTML (`<p>`, `<img>`, etc.).

---

### Module B : Traitement Sémantique & Inférence LLM

#### B.1 Rôle & Règles Métier

Le LLM a un rôle d'éditeur en chef senior en IA. Il doit :

- **Dédoublonner sémantiquement** : Si plusieurs médias traitent de la même annonce, regrouper l'information sous une seule entrée en retenant le lien le plus primaire (ex: blog officiel plutôt qu'article de presse dérivé).
- **Sélectionner le Top 5** : Choisir strictement les 5 faits techniques ou stratégiques les plus structurants de la veille (priorité : releases open-weight, hardware/scaling, papiers fondateurs arXiv, déploiements majeurs en production).
- **Synthétiser** : Rédiger pour chaque item un résumé dense de 2 phrases en français expliquant le contenu et son impact pour un ingénieur.

#### B.2 Schéma du Prompt LLM

```
SYSTEM:
Tu es un curateur expert et ingénieur IA senior.
Ta mission : analyser les articles des dernières 24h, fusionner les doublons, sélectionner STRICTEMENT les 5 actualités les plus majeures/structurantes, et les résumer pour un pair ingénieur.

Règles strictes :
1. Langue de réponse : Français.
2. Exactement 5 items (numérotés de 1 à 5).
3. Structure par item :
   - Titre concis avec émoji évocateur
   - Synthèse de 2 phrases (contexte + impact technique concret)
   - Lien exact vers la source (priorise la source primaire / officielle)
4. Pas de bavardage d'introduction ni de conclusion.
5. Utilise du Markdown propre compatible ntfy.sh (pas de HTML).

USER:
Voici la liste des actualités candidates :
{raw_articles_context}
```

#### B.3 Gestion des Limites d'API

- Si la liste agrégée dépasse 60 articles candidats, appliquer un pré-filtrage déterministe (priorisation des sources lab/research sur la presse généraliste) afin de limiter la taille du prompt à moins de 8k tokens.
- Retry automatique (1 tentative) avec backoff exponentiel en cas d'erreur 429 ou 503 du fournisseur d'API.

#### Critères d'Acceptation (DoD - Module B)

- [ ] Le payload de sortie contient rigoureusement 5 entrées distinctes.
- [ ] Aucun doublon thématique n'est présent dans le top 5.
- [ ] Les liens retournés sont des URLs valides issues des données fournies en entrée.

---

### Module C : Dispatch Push (ntfy.sh)

#### C.1 Spécifications de la Requête

- **Protocole :** HTTP POST
- **URL :** `https://ntfy.sh/{NTFY_TOPIC}`
- **Headers :**
  - `Title` : `☕ AI Daily Digest` (texte simple)
  - `Priority` : `default`
  - `Tags` : `robot,newspaper`
  - `Markdown` : `yes`
- **Body :** UTF-8 encoded Markdown (taille garantie ≤ 4096 octets).
- **Code HTTP attendu :** `200 OK`.

#### C.2 Format Type du Rendu Mobile Reçu

```markdown
**1. 🚀 OpenAI déploie un nouveau modèle de raisonnement**
OpenAI annonce la disponibilité d'une nouvelle architecture axée sur le test-time compute. L'impact est significatif sur les tâches de benchmarks complexes en mathématiques et code.
🔗 https://openai.com/news/...

---

**2. 🔬 DeepMind : Découverte d'algorithmes de tri via RL**
Nouvelle publication démontrant l'optimisation de noyaux de bas niveau par apprentissage par renforcement. Le gain mesuré atteint 15 % sur les routines x86 critiques.
🔗 https://deepmind.google/blog/...

[... Items 3, 4, 5 ...]
```

#### Critères d'Acceptation (DoD - Module C)

- [ ] Requête exécutée avec succès (`status == 200`).
- [ ] Le payload ne dépasse jamais 4096 octets (troncature logicielle défensive en cas d'anomalie de génération).
- [ ] Les hyperliens sont cliquables et fonctionnels dans l'application ntfy.

---

### Module D : Automatisation GitHub Actions

#### D.1 Déclencheurs

- **Planifié :** `cron: '0 6 * * *'` (tous les jours à 06:00 UTC).
- **Manuel :** `workflow_dispatch` (déclenchement via interface web sans paramètre pour tests immédiats).

#### D.2 Structure du Workflow (`.github/workflows/digest.yml`)

```yaml
name: AI Daily Digest

on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Execute digest script
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python -m src.main
```

#### Critères d'Acceptation (DoD - Module D)

- [ ] Le workflow s'exécute avec succès manuellement via `workflow_dispatch`.
- [ ] L'exécution totale prend moins de 45 secondes.
- [ ] Les secrets sont masqués automatiquement dans les logs de GitHub Actions.

---

## 4. Arborescence du Dépôt Git

```
ai-daily-digest/
├── .github/
│   └── workflows/
│       └── digest.yml       # Définition CI/CD
├── src/
│   ├── __init__.py
│   ├── config.py           # Chargement de sources.json et des variables d'environnement
│   ├── fetcher.py          # Récupération asynchrone et filtrage des flux RSS (Module A)
│   ├── summarizer.py       # Client LLM et logique de prompt (Module B)
│   ├── notifier.py         # Client d'envoi ntfy.sh (Module C)
│   └── main.py             # Point d'entrée orchestration
├── tests/
│   ├── test_fetcher.py
│   ├── test_summarizer.py
│   └── test_notifier.py
├── sources.json            # Liste déclarative des 27 sources
├── requirements.txt        # Dépendances épinglées
├── .env.example            # Template de configuration sans valeurs sensibles
├── .gitignore              # Exclusion des secrets et artefacts locaux
├── LICENSE                 # Ex: MIT
└── README.md               # Guide d'installation et de mise en place
```

---

## 5. Guide d'Itération pour l'Agent IA (Spec-Driven Execution Plan)

Lors des sessions de développement avec l'agent IA, implémenter les étapes dans cet ordre séquentiel strict :

**Phase 1 : Bootstrap & Structure**
- Créer l'architecture de fichiers, `.gitignore`, `.env.example`, `requirements.txt` et `sources.json`.

**Phase 2 : Ingestion Asynchrone (`fetcher.py`)**
- Implémenter le fetching non bloquant avec `aiohttp` et `feedparser`.
- Écrire un test unitaire avec un mock de réponse XML pour valider le filtrage temporel (< 24h) et le nettoyage HTML.

**Phase 3 : Synthèse LLM (`summarizer.py`)**
- Implémenter le wrapper Google Gemini (ou Groq).
- Valider la gestion d'erreur (fallback si réponse vide ou API en panne).

**Phase 4 : Client ntfy (`notifier.py`)**
- Coder l'envoi HTTP POST avec gestion des headers Markdown et validation de la taille limite (4 Ko).

**Phase 5 : Assemblage (`main.py`) & Workflow CI**
- Lier les briques dans le point d'entrée principal.
- Rédiger et tester le workflow GitHub Actions.
