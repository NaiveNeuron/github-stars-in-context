# git-h-index

Measuring Developer Impact on GitHub Using the h-Index

# Installation
```bash
uv venv
uv pip install -e .
```

---

## Setup

### Configure GitHub Tokens

```bash
cp .env.example .env
```

Edit `.env` and add your GitHub Personal Access Token(s):

```bash
GITHUB_TOKENS=ghp_YourTokenHere
```

For multiple tokens (recommended for higher rate limits):
```bash
GITHUB_TOKENS=ghp_Token1,ghp_Token2,ghp_Token3
```

### Token Generation

1. Go to GitHub → Settings → Developer Settings → Personal access tokens
2. Generate new token (classic)
3. Give access to `repo` and `user`
4. Generate and copy token

---

# Usage

## Data Fetching CLI

The project includes a comprehensive CLI for fetching GitHub data:

```bash
uv run python -m data_fetching.main --help
```

### Available Commands

| Command | Description |
|---------|-------------|
| `users` | Fetch popular GitHub users by follower bands |
| `repos` | Fetch repositories for users missing them |
| `profiles` | Enrich user profiles (bio, company, location, etc.) |
| `links` | Extract and parse user links from profiles |
| `reset-links` | Reset links parse timestamps to allow re-parsing |
| `reset-last-updated` | Reset last_updated timestamp to force re-fetching |
| `parse-all` | Full pipeline: users → repos → reset → profiles → reset → links |
| `all` | Standard pipeline: users → repos → profiles → links |

### Running Individual Components

**Order matters!** Run components in this order:

#### 1. Fetch Users (required first step)

```bash
uv run python -m data_fetching.main users
```

Options:
- `--resume/--no-resume` - Resume from checkpoint (default: resume)

#### 2. Fetch Repositories

```bash
uv run python -m data_fetching.main repos
```

Options:
- `--batch-size, -b` - Users per batch (default: 6)
- `--continuous/--single` - Continuously fetch or single batch (default: continuous)

#### 3. Enrich Profiles

```bash
uv run python -m data_fetching.main profiles
```

Options:
- `--batch-size, -b` - Users per batch (default: 120)
- `--workers, -w` - Max parallel workers (default: 24)

#### 4. Parse Links

```bash
uv run python -m data_fetching.main links
```

Options:
- `--batch-size, -b` - Users per batch (default: 120)
- `--workers, -w` - Max parallel workers (default: 24)
- `--days, -d` - Days since last update (default: 30)
- `--no-html` - Disable HTML fallback scraping

### Reset Commands

Reset timestamps to re-process data:

```bash
# Reset last_updated (forces re-fetching profiles/repos)
uv run python -m data_fetching.main reset-last-updated

# Reset links parsing timestamps
uv run python -m data_fetching.main reset-links

# Include failed users in reset
uv run python -m data_fetching.main reset-last-updated --reset-failed
uv run python -m data_fetching.main reset-links --reset-failed

# Custom reset date
uv run python -m data_fetching.main reset-last-updated --date "2000-01-01T00:00:00"
```

### Full Pipeline (Recommended)

**Quick start** - runs everything in correct order with resets:

```bash
uv run python -m data_fetching.main parse-all
```

This runs: **users → repos → reset timestamps → profiles → reset links → links**

Options:
- `--repos-batch` - Repository batch size (default: 6)
- `--profiles-batch` - Profiles batch size (default: 120)
- `--profiles-workers` - Profiles workers (default: 24)
- `--links-batch` - Links batch size (default: 120)
- `--links-workers` - Links workers (default: 24)
- `--keep-failed/--reset-failed` - Keep or reset failed users (default: keep-failed)

---

## Data Analysis CLI

The project includes a CLI for analyzing GitHub developer data:

```bash
uv run python -m data_analysis.main --help
```

### Available Commands

| Command | Description |
|---------|-------------|
| `export` | Export user metrics from SQLite to CSV |
| `train` | Train prediction models (RF and/or log2) |
| `visualize` | Generate analysis visualizations |
| `empirical` | Analyze top h-index developers |
| `pipeline` | Run full pipeline (export → train → visualize → empirical) |

### Running Individual Components

#### 1. Export User Metrics

```bash
uv run python -m data_analysis.main export
```

This exports user metrics from SQLite to CSV at `data/csv/user_metrics.csv`.

#### 2. Train Models

```bash
uv run python -m data_analysis.main train
```

Options:
- `--model-type, -m` - Model type to train: rf, log2, or both (default: both)

Examples:
```bash
# Train only Random Forest model
uv run python -m data_analysis.main train --model-type rf

# Train only Log2 Random Forest model
uv run python -m data_analysis.main train --model-type log2

# Train both models (default)
uv run python -m data_analysis.main train
```

#### 3. Generate Visualizations

```bash
uv run python -m data_analysis.main visualize
```

Options:
- `--model-type, -m` - Model type to visualize: rf, log2, or both (default: both)

Examples:
```bash
# Visualize only original RF model
uv run python -m data_analysis.main visualize --model-type rf

# Visualize only Log2 RF model
uv run python -m data_analysis.main visualize --model-type log2

# Visualize both (default)
uv run python -m data_analysis.main visualize
```

#### 4. Empirical Analysis

```bash
uv run python -m data_analysis.main empirical
```

Options:
- `--top, -n` - Number of top developers to analyze (default: 20)
- `--min-h, -h` - Minimum h-index threshold (default: 10)

Examples:
```bash
# Analyze top 50 developers with min h-index of 20
uv run python -m data_analysis.main empirical --top 50 --min-h 20
```

### Full Pipeline (Recommended)

**Quick start** - runs everything in correct order:

```bash
uv run python -m data_analysis.main pipeline
```

This runs: **export → train (both models) → visualize (both) → empirical**

### Generated Outputs

Running the analysis generates:
- User metrics CSV export (`data/csv/user_metrics.csv`)
- Random Forest model (`data/model/random_forest_model.joblib`)
- Log2 Random Forest model (`data/model/log2_random_forest_model.joblib`)
- Visualizations in `data/data_analysis/visualizations/` and `data/data_analysis/visualizations_log2/`
- Empirical analysis in `data/data_analysis/empirical_analysis/`

---

## Common Workflows

### First Time Setup

```bash
# Fetch everything with resets (fresh start)
uv run python -m data_fetching.main parse-all
```

### Incremental Update

```bash
# Only fetch new/updated data
uv run python -m data_fetching.main repos
uv run python -m data_fetching.main profiles
uv run python -m data_fetching.main links
```

### Re-process All Data

```bash
# Reset everything and re-parse
uv run python -m data_fetching.main reset-last-updated --reset-failed
uv run python -m data_fetching.main reset-links --reset-failed
uv run python -m data_fetching.main parse-all --reset-failed
```
