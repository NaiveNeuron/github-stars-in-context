import json
import os
from datetime import date
from typing import Optional, Tuple, List

from utils.logger import logger
from utils.paths import CHECKPOINT_FILE


class CheckpointManager:
    @staticmethod
    def load_band_checkpoint() -> Optional[Tuple[int, List[Tuple[date, date, int, int]]]]:
        if not os.path.exists(CHECKPOINT_FILE): return None

        try:
            with open(CHECKPOINT_FILE, 'r') as f: content = f.read().strip()

            if content.startswith("max_followers:"): return CheckpointManager._convert_old_checkpoint(content)

            data = json.loads(content)
            band_index = data["band_index"]

            stack = []
            for s in data["stack"]:
                start = date.fromisoformat(s[0])
                end = date.fromisoformat(s[1])
                stack.append((start, end, s[2], s[3]))

            logger.info(f"Loaded checkpoint for band {band_index} with {len(stack)} slices remaining")
            return band_index, stack

        except Exception as e:
            logger.error(f"Error loading checkpoint: {str(e)}")
            return None

    @staticmethod
    def _convert_old_checkpoint(content: str) -> Optional[Tuple[int, List[Tuple[date, date, int, int]]]]:
        try:
            value = int(content.split(":", 1)[1])
            if value == 0: return None

            from data_fetching.users_fetching.popular_users import FOLLOWER_BANDS
            for idx, (min_f, _) in enumerate(FOLLOWER_BANDS):
                if min_f == value:
                    band_index = idx
                    break
            else: band_index = 0

            min_f, max_f = FOLLOWER_BANDS[band_index]
            today = date.today()
            stack = [(date(2007, 1, 1), today, min_f, max_f)]

            CheckpointManager.save_band_checkpoint(band_index, stack)
            logger.info(f"Converted old checkpoint to new format for band {band_index}")
            return band_index, stack

        except Exception as e:
            logger.error(f"Error converting old checkpoint: {str(e)}")
            return None

    @staticmethod
    def save_band_checkpoint(band_index: int, stack: List[Tuple[date, date, int, int]]) -> None:
        try:
            stack_serializable = [[s[0].isoformat(), s[1].isoformat(), s[2], s[3]] for s in stack]

            data = {"band_index": band_index, "stack": stack_serializable}

            with open(CHECKPOINT_FILE, 'w') as f: json.dump(data, f, indent=2)
            logger.debug(f"Checkpoint saved for band {band_index} with {len(stack)} slices")
        except Exception as e:
            logger.error(f"Error saving checkpoint: {str(e)}")

    @staticmethod
    def clear_band_checkpoint() -> None:
        try:
            if os.path.exists(CHECKPOINT_FILE):
                os.remove(CHECKPOINT_FILE)
                logger.info("Checkpoint file cleared after successful completion")
        except Exception as e:
            logger.error(f"Error clearing checkpoint: {str(e)}")