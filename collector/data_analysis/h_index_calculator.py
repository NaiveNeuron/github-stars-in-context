from typing import List

from utils.logger import logger


def calculate_h_index_from_stars(star_counts: List[int]) -> int:
    logger.debug(f"Calculating h-index from {len(star_counts)} repositories")

    if not star_counts:
        logger.info("No repositories found — h-index = 0")
        return 0

    for i, stars in enumerate(star_counts):
        if stars < 0:
            error_msg = f"Critical data error: negative star count at index {i}: {stars}"
            logger.critical(error_msg)
            raise ValueError(error_msg)

    sorted_stars = sorted(star_counts, reverse=True)

    h_index = 0
    for i, stars in enumerate(sorted_stars, start=1):
        if stars >= i:
            h_index = i
        else:
            break

    logger.info(f"Computed h-index: {h_index}")
    return h_index