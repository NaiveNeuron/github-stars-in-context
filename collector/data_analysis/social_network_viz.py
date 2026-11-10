import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

import matplotlib.pyplot as plt
import pandas as pd

from utils.paths import DB_PATH, DATA_ANALYSIS_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SocialNetworkVisualizer:
    PLATFORM_MAPPING = {
        'twitter': 'Twitter/X',
        'x': 'Twitter/X',
        'twitter_x': 'Twitter/X',
        'instagram': 'Instagram',
        'youtube': 'YouTube',
        'linkedin': 'LinkedIn',
        'mastodon': 'Mastodon',
        'facebook': 'Facebook',
        'tiktok': 'TikTok',
        'medium': 'Medium',
        'dev': 'Dev.to',
        'dev_to': 'Dev.to',
        'hashnode': 'Hashnode',
        'telegram': 'Telegram',
        'unknown': 'Unknown',
        'other': 'Other',
    }

    PLATFORM_COLORS = {
        'Twitter/X': '#1DA1F2',
        'Instagram': '#E4405F',
        'YouTube': '#FF0000',
        'LinkedIn': '#0A66C2',
        'Mastodon': '#6364FF',
        'Facebook': '#1877F2',
        'TikTok': '#000000',
        'Medium': '#00AB6C',
        'Dev.to': '#0A0A0A',
        'Hashnode': '#2962FF',
        'Telegram': '#0088CC',
        'Unknown': '#95A5A6',
        'Other': '#ADB5BD',
    }

    def __init__(self, db_path: Path = DB_PATH, output_dir: Path = DATA_ANALYSIS_DIR):
        self.db_path = db_path
        self.output_dir = output_dir / "social_network_viz"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _get_link_distribution(self) -> pd.DataFrame:
        try:
            with self._get_db_connection() as conn:
                query = """
                WITH user_platforms AS (
                    SELECT DISTINCT 
                        user_id,
                        CASE 
                            WHEN link_type IN ('twitter', 'x') THEN 'twitter_x'
                            WHEN link_type = 'instagram' THEN 'instagram'
                            WHEN link_type = 'youtube' THEN 'youtube'
                            WHEN link_type = 'linkedin' THEN 'linkedin'
                            WHEN link_type = 'mastodon' THEN 'mastodon'
                            WHEN link_type = 'facebook' THEN 'facebook'
                            WHEN link_type = 'tiktok' THEN 'tiktok'
                            WHEN link_type = 'medium' THEN 'medium'
                            WHEN link_type IN ('dev', 'dev_to') THEN 'dev_to'
                            WHEN link_type = 'hashnode' THEN 'hashnode'
                            WHEN link_type = 'telegram' THEN 'telegram'
                            -- Explicitly excluding website and email
                            ELSE 'other'
                        END AS platform
                    FROM user_links
                    WHERE link_type NOT IN ('website', 'email')
                ),
                total_users AS (
                    -- Count users who actually have social links (excluding website-only users)
                    SELECT COUNT(DISTINCT user_id) as total FROM user_platforms
                )
                SELECT 
                    platform,
                    COUNT(DISTINCT user_id) as user_count,
                    (SELECT total FROM total_users) as total_parsed_users
                FROM user_platforms
                GROUP BY platform
                ORDER BY user_count DESC
                """

                df = pd.read_sql_query(query, conn)

                if df.empty:
                    logger.warning("No social network data found.")
                    return pd.DataFrame()

                df['percentage'] = (df['user_count'] / df['total_parsed_users'] * 100).round(2)
                df['platform_name'] = df['platform'].map(
                    lambda x: self.PLATFORM_MAPPING.get(x, x.replace('_', ' ').title())
                )

                logger.info(f"Retrieved distribution for {len(df)} platforms")
                return df

        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            raise

    def plot_platform_distribution(self, df: pd.DataFrame, save_path: Path, top_n: int = 12, min_percentage: float = 0.5) -> Optional[plt.Figure]:
        if df.empty:
            return None

        plot_df = df[
            (df['platform'] != 'other')
        ].head(top_n).copy()

        plot_df = plot_df[plot_df['percentage'] >= min_percentage]

        if len(plot_df) == 0:
            logger.warning("No platforms meet minimum percentage threshold")
            return None

        total_visible_users = plot_df['user_count'].sum()
        if total_visible_users == 0:
            return None

        plot_df['chart_percentage'] = (plot_df['user_count'] / total_visible_users * 100).round(2)

        plot_df = plot_df.sort_values('user_count', ascending=True)

        colors = [
            self.PLATFORM_COLORS.get(name, '#95A5A6')
            for name in plot_df['platform_name']
        ]

        plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'sans-serif']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(12, 8))
        bars = ax.barh(
            range(len(plot_df)),
            plot_df['user_count'],
            color=colors,
            edgecolor='white',
            linewidth=1.5
        )

        ax.set_yticks(range(len(plot_df)))
        ax.set_yticklabels(plot_df['platform_name'], fontsize=11, fontweight='medium')
        ax.set_xlabel('Number of GitHub Users', fontsize=12, fontweight='bold')

        ax.set_title('Social Network Distribution (Top Platforms)\n(Percentages sum to 100% of displayed platforms)', fontsize=14, fontweight='bold', pad=15)

        for i, (bar, count, pct) in enumerate(zip(bars, plot_df['user_count'], plot_df['chart_percentage'])):
            ax.text(bar.get_width() + 50, bar.get_y() + bar.get_height() / 2, f'{int(count):,} ({pct:.1f}%)', va='center', fontsize=10, color='#2C3E50', fontweight='medium')

        ax.grid(axis='x', alpha=0.3, linestyle='--', linewidth=0.8)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.set_axisbelow(True)

        ax.text(0.5, -0.15, f'Total users on chart: {total_visible_users:,}', transform=ax.transAxes, fontsize=10, ha='center', style='italic', bbox=dict(boxstyle='round', facecolor='#F8F9FA', alpha=0.8))

        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"Platform distribution plot saved to: {save_path}")
        plt.close()

        return fig

    def generate_all_visualizations(self) -> Dict:
        logger.info("STARTING SOCIAL NETWORK VISUALIZATION GENERATION")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        df = self._get_link_distribution()

        if df.empty:
            logger.error("No data to visualize. Exiting.")
            return {}

        total_social_users = df['total_parsed_users'].iloc[0] if not df.empty else 0

        logger.info(f"Total users with social links (excl. website): {total_social_users:,}")
        logger.info(f"Platforms detected: {len(df)}")

        results = {}

        results['distribution'] = self.plot_platform_distribution(df, save_path=self.output_dir / "platform_distribution.png", top_n=12, min_percentage=0.5)

        csv_path = self.output_dir / "social_networks_data.csv"
        df.to_csv(csv_path, index=False)
        results['csv_path'] = csv_path

        logger.info("ALL SOCIAL NETWORK VISUALIZATIONS GENERATED SUCCESSFULLY")
        logger.info(f"Output directory: {self.output_dir}")

        return results


if __name__ == "__main__":
    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}")
    else:
        viz = SocialNetworkVisualizer()
        results = viz.generate_all_visualizations()

        logger.info("\nGenerated Files:")
        for key, value in results.items():
            if value and hasattr(value, 'exists'):
                logger.info(f"  {key}: {value}")
            elif isinstance(value, Path):
                logger.info(f"  {key}: {value}")