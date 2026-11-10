from typing import Any

import joblib
from pathlib import Path
from utils.logger import logger
from utils.paths import RANDOM_FOREST_MODEL_PATH


def load_random_forest_model(model_path: object = None) -> Any:
    if model_path is None:
        model_path = RANDOM_FOREST_MODEL_PATH
    elif isinstance(model_path, str):
        model_path = Path(model_path)

    try:
        loaded = joblib.load(model_path)

        if isinstance(loaded, dict):
            for key in ['model', 'rf_model', 'estimator', 'random_forest', 'regressor']:
                if key in loaded:
                    logger.info(f"Extracted model from dict key: '{key}'")
                    model = loaded[key]
                    break
            else:
                logger.warning(f"Unknown dict keys: {list(loaded.keys())}. Using first value.")
                model = next(iter(loaded.values()))
        else:
            model = loaded

        logger.info(f"Loaded Random Forest model from: {model_path}")
        logger.info(f"Model type: {type(model).__name__}")
        return model

    except FileNotFoundError:
        logger.error(f"Model file not found: {model_path}")
        logger.error("Please run model training first using: uv run -m data_analysis.main")
        raise
    except Exception as e:
        logger.error(f"Error loading model: {str(e)}")
        raise