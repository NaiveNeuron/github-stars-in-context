import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from utils.logger import logger, log_block
import time

from utils.paths import MODEL_DIR, LOG2_RANDOM_FOREST_MODEL_PATH, DATA_ANALYSIS_DIR


def log2_train_random_forest(X_train, X_test, y_train, y_test, suffix=""):

    model_path = LOG2_RANDOM_FOREST_MODEL_PATH if not suffix else MODEL_DIR / f"log2_random_forest_model{suffix}.joblib"
    predictions_path = DATA_ANALYSIS_DIR / f"test_predictions_log2{suffix}.csv" if suffix else None
    log_block("MODEL TRAINING START",
              f"Train shape: {X_train.shape}",
              f" Test shape: {X_test.shape}",
              f"h-index → train: [{y_train.min():.1f} – {y_train.max():.1f}]",
              f"           test: [{y_test.min():.1f} – {y_test.max():.1f}]"
              )

    logger.info("Applying log2(1 + h_index) transformation on target variable...")

    y_train_log = np.log2(y_train + 1)
    y_test_log = np.log2(y_test + 1)

    logger.info(f"Transformed h-index → train: [{y_train_log.min():.3f} – {y_train_log.max():.3f}]")
    logger.info(f"Transformed h-index → test:  [{y_test_log.min():.3f} – {y_test_log.max():.3f}]")

    start = time.time()

    model = RandomForestRegressor(n_estimators=160, max_depth=18, min_samples_leaf=4, max_features='sqrt', random_state=42, n_jobs=-1, verbose=1)

    logger.info("Fitting Random Forest on log2-transformed target...")
    model.fit(X_train, y_train_log)

    elapsed = time.time() - start
    logger.info(f"Training finished in {elapsed:.1f}s  ({elapsed / 60:.1f} min)")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_package = {'model': model, 'target_transform': 'log2', 'target_transform_offset': 1, 'feature_columns': X_train.columns.tolist()}
    joblib.dump(model_package, model_path)
    logger.info(f"Random Forest model saved to: {model_path}")

    y_pred_log = model.predict(X_test)

    y_pred = np.power(2, y_pred_log) - 1

    logger.info("Predictions inverse-transformed from log2 to original h-index scale")

    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    top_threshold = np.percentile(y_test, 90)
    top_mask = y_test >= top_threshold

    if top_mask.any():
        mae_top = mean_absolute_error(y_test[top_mask], y_pred[top_mask])
        r2_top = r2_score(y_test[top_mask], y_pred[top_mask])
    else:
        mae_top = r2_top = np.nan

    log_block("MODEL PERFORMANCE",
              f"Overall        → MAE: {mae:.3f}   |   R²: {r2:.4f}",
              f"Top 10% (≥{top_threshold:.1f}) → MAE: {mae_top:.3f}   |   R²: {r2_top:.4f}",
              f"Users in top 10%: {top_mask.sum()} / {len(y_test)} "
              f"({top_mask.mean():.1%})"
              )

    importances = pd.Series(model.feature_importances_, index=X_train.columns).sort_values(ascending=False)

    log_block("TOP 15 MOST IMPORTANT FEATURES",
              *[f"{i + 1:2d}. {feat:36} → {imp:.5f}"
                for i, (feat, imp) in enumerate(importances.head(15).items())]
              )

    top_df = X_test.copy()
    top_df['true_h'] = y_test.values
    top_df['pred_h'] = y_pred

    if predictions_path:
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        top_df.to_csv(predictions_path, index=False)
        logger.info(f"Predictions saved to: {predictions_path}")

    show_cols = ['repo_count', 'max_stars', 'avg_stars', 'followers_count', 'following_count', 'organizations_count', 'bio_length', 'true_h', 'pred_h']

    show_cols = [c for c in show_cols if c in top_df.columns]

    top_view = top_df[top_mask].sort_values('pred_h', ascending=False).head(30)

    log_block("TOP 30 PREDICTED HIGH h-index USERS",
              "Columns: " + ", ".join(show_cols),
              "\n" + top_view[show_cols].round(2).to_string(index=False)
              )

    logger.info("TRANSFORMATION IMPACT ANALYSIS")
    logger.info(f"Original target range: [{y_test.min():.1f}, {y_test.max():.1f}]")
    logger.info(f"Log2-transformed range: [{y_test_log.min():.3f}, {y_test_log.max():.3f}]")
    logger.info(f"Skewness reduction: Target was highly skewed, log2 compresses extreme values")

    highest_10_mask = y_test >= np.percentile(y_test, 99)
    if highest_10_mask.any():
        mae_highest = mean_absolute_error(y_test[highest_10_mask], y_pred[highest_10_mask])
        logger.info(f"MAE for top 1% h-index users: {mae_highest:.3f}")

    return {
        'model': model,
        'metrics': {'mae': mae, 'r2': r2, 'mae_top10': mae_top, 'r2_top10': r2_top, 'top10_threshold': top_threshold},
        'importances': importances,
        'top_users_view': top_view,
        'transformation': {'type': 'log2', 'offset': 1, 'inverse_formula': '2^x - 1'}
    }