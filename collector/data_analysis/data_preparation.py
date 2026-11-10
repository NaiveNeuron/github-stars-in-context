import pandas as pd
import numpy as np
from matplotlib import ticker
from sklearn.model_selection import train_test_split
import matplotlib.pyplot as plt
import seaborn as sns
import os

from utils.logger import logger, log_block
from utils.paths import H_INDEX_DISTRIBUTION_PATH, DATA_ANALYSIS_DIR, USER_METRICS_PATH


STAR_FEATURES = ['total_stars', 'avg_stars', 'median_stars', 'max_stars', 'star_variance', 'stars_per_repo', 'max_stars_log']

NO_STAR_FEATURES = ['repo_count', 'followers_count', 'following_count', 'organizations_count', 'bio_length', 'followers_per_repo', 'following_to_followers', 'bio_length_log']


def data_preparation_start(exclude_star_features=False):
    DATA_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    RANDOM_STATE = 42
    np.random.seed(RANDOM_STATE)

    logger.info(f"Loading data from: {USER_METRICS_PATH}")
    if not os.path.exists(USER_METRICS_PATH):
        raise FileNotFoundError(f"Data file not found → {USER_METRICS_PATH}")

    df = pd.read_csv(USER_METRICS_PATH)
    df.drop(columns=['avg_stars', 'median_stars', 'star_variance'], inplace=True, errors='ignore')

    log_block(
        "RAW DATASET OVERVIEW",
        f"Shape: {df.shape}",
        "Columns:",
        df.columns.tolist(),
        "Missing values:\n" + df.isnull().sum().to_string(),
        "dtypes:\n" + df.dtypes.astype(str).to_string()
    )

    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    initial_rows = len(df)
    df = df.dropna(subset=['h_index'])
    logger.info(f"Dropped {initial_rows - len(df)} rows → missing h_index")

    missing_ratio = df.isnull().mean()
    bad_cols = missing_ratio[missing_ratio > 0.70].index.tolist()
    if bad_cols:
        logger.warning(f"Dropping columns with >70% missing: {bad_cols}")
        df.drop(columns=bad_cols, inplace=True)

    for col in df.columns:
        if col in ['user_id', 'h_index']:
            continue

        if df[col].dtype in ['float64', 'int64']:
            df[col] = df[col].fillna(df[col].median())
        else:
            df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else "unknown")

    df['followers_per_repo'] = df['followers_count'] / (df['repo_count'] + 1)
    df['following_to_followers'] = df['following_count'] / (df['followers_count'] + 1)
    df['stars_per_repo'] = df['total_stars'] / (df['repo_count'] + 1)
    df['max_stars_log'] = np.log1p(df['max_stars'])
    df['bio_length_log'] = np.log1p(df['bio_length'])

    X = df.drop(columns=['h_index', 'user_id'], errors='ignore')
    y = df['h_index']

    if exclude_star_features:
        logger.info("Excluding star-related features")
        cols_to_drop = [c for c in STAR_FEATURES if c in X.columns]
        X.drop(columns=cols_to_drop, inplace=True, errors='ignore')
        keep_cols = [c for c in NO_STAR_FEATURES if c in X.columns]
        missing = [c for c in NO_STAR_FEATURES if c not in X.columns]
        if missing:
            logger.warning(f"Expected features not found: {missing}")
        X = X[keep_cols]
        logger.info(f"No-stars feature set: {X.columns.tolist()}")

    SKEW_THRESHOLD = 0.8

    numeric_cols = X.select_dtypes(include=['float64', 'int64']).columns.tolist()

    DONT_LOG = {'followers_count', 'following_count', 'organizations_count', 'bio_length', 'repo_count'}

    to_transform = [c for c in numeric_cols if c not in DONT_LOG]

    skews = X[to_transform].skew()
    skewed = skews[abs(skews) > SKEW_THRESHOLD].index

    for col in skewed:
        if X[col].min() >= 0:
            X[col] = np.log1p(X[col])
            logger.info(f"log1p applied → {col:24} (skew was {skews[col]:+.2f})")

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.20, random_state=RANDOM_STATE)

    log_block(
        "FINAL DATA AFTER PREPARATION",
        f"Mode        : {'NO-STARS' if exclude_star_features else 'FULL (with stars)'}",
        f"Train shape : {X_train.shape}",
        f"Test shape  : {X_test.shape}",
        "Features used:",
        "  " + "\n  ".join(X_train.columns.tolist())
    )

    fig, ax = plt.subplots(figsize=(10, 6))
    sns.histplot(y, bins=120, ax=ax, color='cornflowerblue', edgecolor='navy')
    ax.set_title('h-index Distribution', fontsize=13, pad=12)
    ax.set_yscale('log')
    ax.set_xlabel('h-index')
    ax.set_ylabel('Count (log scale)')
    ax.xaxis.set_major_locator(ticker.MaxNLocator(14, integer=True))
    ax.xaxis.set_minor_locator(ticker.AutoMinorLocator(4))
    ax.minorticks_on()
    plt.tight_layout()
    plt.savefig(H_INDEX_DISTRIBUTION_PATH, dpi=320, bbox_inches='tight')
    plt.close()
    logger.info(f"Saved h-index distribution → {H_INDEX_DISTRIBUTION_PATH}")

    logger.info("Data preparation finished ✓")
    return X_train, X_test, y_train, y_test