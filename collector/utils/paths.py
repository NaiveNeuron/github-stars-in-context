from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
DB_DIR = DATA_DIR / "db"
CSV_DIR = DATA_DIR / "csv"
MODEL_DIR = DATA_DIR / "model"
CHECKPOINT_FILE = DATA_DIR / "processing.checkpoint"

SCHEMA_PATH = DB_DIR / "schema.sql"
DB_PATH = DB_DIR / "github_hindex.db"
CSV_PATH = CSV_DIR / "github_hindex.csv"
USER_METRICS_PATH = CSV_DIR / "user_metrics.csv"

DATA_ANALYSIS_DIR = DATA_DIR / "data_analysis"
H_INDEX_DISTRIBUTION_PATH = DATA_ANALYSIS_DIR / "h_index_distribution.png"
RANDOM_FOREST_MODEL_PATH = MODEL_DIR / "random_forest_model.joblib"
LOG2_RANDOM_FOREST_MODEL_PATH = MODEL_DIR / "log2_random_forest_model.joblib"