import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from data_analysis.data_preparation import data_preparation_start
from data_analysis.model_loader import load_random_forest_model
from utils.constants import TOP_PERCENT
from utils.logger import logger
from utils.paths import (
    RANDOM_FOREST_MODEL_PATH,
    LOG2_RANDOM_FOREST_MODEL_PATH,
    DATA_ANALYSIS_DIR,
    USER_METRICS_PATH,
    MODEL_DIR,
)


def generate_predictions_for_visualization(model, model_type='original', exclude_star_features=False):
    predictions_path = DATA_ANALYSIS_DIR / f"test_predictions_{model_type}.csv"

    if predictions_path.exists():
        logger.info(f"Loading existing predictions: {predictions_path}")
        return pd.read_csv(predictions_path)

    logger.info("Generating predictions for visualization...")

    X_train, X_test, y_train, y_test = data_preparation_start(exclude_star_features=exclude_star_features)

    if 'log2' in model_type:
        y_pred_log = model.predict(X_test)
        y_pred = np.power(2, y_pred_log) - 1
    else:
        y_pred = model.predict(X_test)

    df = X_test.copy()
    df['true_h'] = y_test.values
    df['pred_h'] = y_pred

    df.to_csv(predictions_path, index=False)
    logger.info(f"Predictions saved to: {predictions_path}")
    logger.info(f"  Shape: {df.shape}")
    logger.info(f"  true_h range: [{df['true_h'].min():.1f}, {df['true_h'].max():.1f}]")
    logger.info(f"  pred_h range: [{df['pred_h'].min():.1f}, {df['pred_h'].max():.1f}]")

    return df


def plot_feature_importance(model, save_path=None, top_n=10):
    logger.info("Generating feature importance plot...")

    feature_names = model.feature_names_in_
    importances = model.feature_importances_

    indices = np.argsort(importances)[::-1][:top_n]

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(indices)))

    bars = ax.barh(range(len(indices)), importances[indices][::-1], color=colors)

    ax.set_yticks(range(len(indices)))
    ax.set_yticklabels([feature_names[i] for i in indices][::-1], fontsize=10)
    ax.set_xlabel('Feature Importance', fontsize=12, fontweight='bold')
    ax.set_title(f'Top {top_n} Most Important Features (Random Forest)', fontsize=14, fontweight='bold', pad=15)

    for bar, val in zip(bars, importances[indices][::-1]):
        ax.text(bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2,
                f'{val:.4f}', va='center', fontsize=9, color='darkblue')

    ax.grid(axis='x', alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()

    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"Feature importance plot saved to: {save_path}")

    plt.close()
    return fig


def plot_feature_importance_square(feature_importances, feature_names, save_path=None, size_inches=4):
    color_scheme = {'top': '#2ea44f', 'medium': '#cb2431', 'low': '#6a737d'}

    import matplotlib.pyplot as plt
    import numpy as np
    from pathlib import Path
    from matplotlib.patches import Patch

    indices = np.argsort(feature_importances)[::-1]
    names = [feature_names[i] for i in indices]
    values = [feature_importances[i] for i in indices]

    high_threshold = np.percentile(values, 70)
    low_threshold = np.percentile(values, 50)

    colors = []
    for v in values:
        if v >= high_threshold:
            colors.append(color_scheme['top'])
        elif v <= low_threshold:
            colors.append(color_scheme['low'])
        else:
            colors.append(color_scheme['medium'])

    fig, ax = plt.subplots(figsize=(size_inches, size_inches), dpi=150)

    y_pos = np.arange(len(names))[::-1]

    name_map = {
        'total_stars': 'total_stars',
        'stars_per_repo': 'stars_per_repo',
        'median_stars': 'median_stars',
        'max_stars': 'max_stars',
        'repo_count': 'repo_count',
        'star_variance': 'star_variance',
        'followers_count': 'followers_count',
        'following_count': 'following_count',
        'followers_per_repo': 'followers_per_repo',
        'following_to_followers': 'following_to_followers',
        'organizations_count': 'organizations_count',
        'bio_length': 'bio_length',
        'bio_length_log': 'bio_length_log',
    }
    short_names = [name_map.get(n, n) for n in names]

    bars = ax.barh(y_pos, values, color=colors, edgecolor='white', linewidth=0.5)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(short_names, fontsize=7, fontweight='medium')
    ax.set_xlabel('Importance', fontsize=8, fontweight='bold')
    ax.set_title('Feature Importance', fontsize=9, fontweight='bold', pad=8)

    for i, (bar, val) in enumerate(zip(bars, values)):
        ax.text(val + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                    f'{val:.3f}', va='center', fontsize=6, fontweight='bold')

    legend_elements = [
        Patch(facecolor=color_scheme['top'], edgecolor='white', label='Top predictors'),
        Patch(facecolor=color_scheme['medium'], edgecolor='white', label='Low impact'),
        Patch(facecolor=color_scheme['low'], edgecolor='white', label='Negligible')
    ]
    ax.legend(handles=legend_elements, loc='lower right', fontsize=6, frameon=True, framealpha=0.9)

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.tick_params(left=False)
    ax.grid(axis='x', alpha=0.2, linestyle='--', linewidth=0.5)

    plt.tight_layout()

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"✓ Square feature importance saved: {save_path}")

    plt.close()
    return fig


def plot_correlation_matrix(save_path=None):
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns
    from pathlib import Path

    if save_path is None:
        save_path = DATA_ANALYSIS_DIR / "correlation_matrix.png"

    logger.info("Generating correlation matrix...")

    df = pd.read_csv(USER_METRICS_PATH)
    df.drop(columns=['avg_stars', 'median_stars', 'star_variance'], inplace=True, errors='ignore')
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.dropna(subset=['h_index'])
    df.drop(columns=['user_id'], inplace=True, errors='ignore')

    for col in df.select_dtypes(include=['float64', 'int64']).columns:
        df[col] = df[col].fillna(df[col].median())

    df['followers_per_repo'] = df['followers_count'] / (df['repo_count'] + 1)
    df['following_to_followers'] = df['following_count'] / (df['followers_count'] + 1)
    df['stars_per_repo'] = df['total_stars'] / (df['repo_count'] + 1)
    df['max_stars_log'] = np.log1p(df['max_stars'])
    df['bio_length_log'] = np.log1p(df['bio_length'])

    numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns.tolist()
    DONT_LOG = {'followers_count', 'following_count', 'organizations_count', 'bio_length', 'repo_count'}
    to_transform = [c for c in numeric_cols if c not in DONT_LOG]

    skews = df[to_transform].skew()
    skewed = skews[abs(skews) > 0.8].index

    for col in skewed:
        if df[col].min() >= 0:
            df[col] = np.log1p(df[col])

    corr_matrix = df.corr(method='spearman')

    fig, ax = plt.subplots(figsize=(14, 12))
    sns.heatmap(
        corr_matrix,
        annot=True,
        fmt='.2f',
        cmap='coolwarm',
        center=0,
        square=True,
        linewidths=0.5,
        cbar_kws={'shrink': 0.8, 'label': 'Spearman Correlation'},
        ax=ax
    )
    ax.set_title('Spearman Correlation Matrix', fontsize=14, fontweight='bold', pad=15)
    plt.xticks(rotation=45, ha='right', fontsize=9)
    plt.yticks(rotation=0, fontsize=9)
    plt.tight_layout()

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    logger.info(f"Correlation matrix saved to: {save_path}")

    plt.close()
    return fig


def plot_error_distribution(df, save_path=None, highlight_top_percent=10, error_range=(-20, 20)):
    logger.info("Generating error distribution plot...")

    true_h = df['true_h'].values
    pred_h = df['pred_h'].values
    errors = pred_h - true_h

    threshold = np.percentile(true_h, 100 - highlight_top_percent)
    high_h_mask = true_h >= threshold

    plot_range = error_range

    fig, ax = plt.subplots(figsize=(12, 6))

    errors_typical = errors[~high_h_mask]
    ax.hist(errors_typical, bins=60, range=plot_range,
            color='steelblue', alpha=0.6, label=f'Bottom {100 - highlight_top_percent}%',
            edgecolor='white', linewidth=0.5, zorder=1)

    errors_top = errors[high_h_mask]
    ax.hist(errors_top, bins=60, range=plot_range,
            color='crimson', alpha=0.85, label=f'Top {highlight_top_percent}% (h≥{threshold:.0f})',
            edgecolor='darkred', linewidth=1, zorder=2)

    from scipy.stats import gaussian_kde
    if len(errors) > 100:
        kde = gaussian_kde(errors)
        x_grid = np.linspace(plot_range[0], plot_range[1], 500)
        bin_width = (plot_range[1] - plot_range[0]) / 60
        ax.plot(x_grid, kde(x_grid) * len(errors) * bin_width,
                color='darkgreen', linewidth=2.5, label='Density (KDE)', zorder=3)

    ax.axvline(x=0, color='black', linestyle='--', linewidth=2, alpha=0.8, label='Zero Error')

    ax.set_xlabel('Prediction Error (Predicted − True)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Frequency (log scale)', fontsize=12, fontweight='bold')
    ax.set_title('Distribution of Prediction Errors', fontsize=14, fontweight='bold', pad=15)

    ax.legend(loc='upper right', fontsize=10, framealpha=0.95)
    ax.grid(axis='y', alpha=0.3, linestyle='--', which='both')
    ax.grid(axis='x', alpha=0.2, linestyle=':')

    ax.set_xlim(plot_range)
    range_width = plot_range[1] - plot_range[0]
    if range_width <= 10:
        x_step = 1
    elif range_width <= 20:
        x_step = 2
    elif range_width <= 40:
        x_step = 5
    else:
        x_step = 10
    ax.set_xticks(np.arange(plot_range[0], plot_range[1] + x_step / 2, x_step))

    ax.set_yscale('log')
    ax.set_ylim(bottom=0.1)

    mae_overall = mean_absolute_error(true_h, pred_h)
    mean_err = np.mean(errors)
    std_err = np.std(errors)

    within_1 = np.mean(np.abs(errors) <= 1) * 100
    within_2 = np.mean(np.abs(errors) <= 2) * 100
    within_10 = np.mean(np.abs(errors) <= 10) * 100

    stats_text = (f'Overall MAE: {mae_overall:.3f}\n'
                  f'Mean Error: {mean_err:.3f}\n'
                  f'Std Error: {std_err:.2f}\n'
                  f'Within ±1: {within_1:.1f}%\n'
                  f'Within ±2: {within_2:.1f}%\n'
                  f'Within ±10: {within_10:.1f}%')

    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.85))

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()

    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"Error distribution plot saved to: {save_path}")

    plt.close()
    return fig


def generate_case_studies_table(df, save_path=None, n_cases=10, exclude_zero_h=True):
    logger.info("Generating case studies table...")

    df = df.copy()

    if exclude_zero_h:
        initial_count = len(df)
        df = df[(df['true_h'] > 0) | (df['pred_h'] > 0)]
        filtered_count = initial_count - len(df)
        if filtered_count > 0:
            logger.info(f"Excluded {filtered_count} users with true_h=0 and pred_h=0")

    df['error'] = df['pred_h'] - df['true_h']
    df['abs_error'] = np.abs(df['error'])

    top_errors = df.nlargest(n_cases // 2, 'abs_error')
    high_true = df.nlargest(n_cases // 4, 'true_h')
    high_pred = df.nlargest(n_cases // 4, 'pred_h')
    well_predicted = df.nsmallest(n_cases // 4, 'abs_error')

    cases = pd.concat([top_errors, high_true, high_pred, well_predicted]).drop_duplicates()
    cases = cases.head(n_cases).sort_values('true_h', ascending=False)

    def make_profile(row):
        parts = [f"{int(row['repo_count'])} repos"]

        if 'followers_count' in row and pd.notna(row['followers_count']): parts.append(f"{int(row['followers_count']):,} followers")
        if 'max_stars' in row and pd.notna(row['max_stars']) and row['max_stars'] > 1000: parts.append(f"max_stars={int(row['max_stars']):,}")
        if 'organizations_count' in row and pd.notna(row['organizations_count']) and row['organizations_count'] == 0: parts.append("no organizations")
        if 'following_count' in row and pd.notna(row['following_count']) and row['following_count'] > 100: parts.append(f"high following_count ({int(row['following_count'])})")

        return ", ".join(parts)

    cases['profile'] = cases.apply(make_profile, axis=1)

    latex_rows = []
    for idx, (_, row) in enumerate(cases.iterrows()):
        user_label = chr(65 + idx)
        err_str = f"{row['error']:+.1f}" if row['error'] >= 0 else f"{row['error']:.1f}"
        latex_rows.append(f"{user_label} & {int(row['true_h'])} & {row['pred_h']:.1f} & {err_str} & {row['profile']} \\\\")

    save_path.parent.mkdir(parents=True, exist_ok=True)
    cases.to_csv(save_path, index=False)
    logger.info(f"Case studies table saved to: {save_path}")

def generate_all_visualizations(model_type='original', no_stars=False):
    logger.info("STARTING VISUALIZATION GENERATION")

    suffix = "_no_stars" if no_stars else ""

    if model_type == 'log2':
        model_path = LOG2_RANDOM_FOREST_MODEL_PATH if not suffix else MODEL_DIR / f"log2_random_forest_model{suffix}.joblib"
        output_dir = DATA_ANALYSIS_DIR / "visualizations_log2" if not suffix else DATA_ANALYSIS_DIR / f"visualizations_log2{suffix}"
        pred_type = f"log2{suffix}"
    else:
        model_path = RANDOM_FOREST_MODEL_PATH if not suffix else MODEL_DIR / f"random_forest_model{suffix}.joblib"
        output_dir = DATA_ANALYSIS_DIR / "visualizations" if not suffix else DATA_ANALYSIS_DIR / f"visualizations{suffix}"
        pred_type = f"original{suffix}"

    output_dir.mkdir(parents=True, exist_ok=True)

    model = load_random_forest_model(model_path)
    df = generate_predictions_for_visualization(model, model_type=pred_type, exclude_star_features=no_stars)

    plot_feature_importance(model, save_path=output_dir / "feature_importance.png")
    plot_error_distribution(df, save_path=output_dir / "error_distribution.png", highlight_top_percent=TOP_PERCENT)
    plot_feature_importance_square(
        model.feature_importances_,
        model.feature_names_in_,
        save_path=output_dir / "feature_importance_square.png",
        size_inches=4,
    )

    generate_case_studies_table(df, save_path=output_dir / "case_studies.csv", n_cases=10)

    logger.info("ALL VISUALIZATIONS GENERATED SUCCESSFULLY")
    logger.info(f"Output directory: {output_dir}")

if __name__ == "__main__":
    generate_all_visualizations(model_type='origin')
    generate_all_visualizations(model_type='log2')