import time
import threading
from typing import Optional, Tuple, List
from utils.logger import logger
from utils.secrets import GITHUB_TOKENS


class TokenManager:
    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.lock = threading.Lock()
        self.token_index = 0
        self.token_cooldowns = {}

    def _get_token_hash(self, token: str) -> str:
        return token[:8]

    def get_available_token(self) -> Optional[Tuple[str, bool]]:
        with self.lock:
            current_time = time.time()
            for token in list(self.token_cooldowns.keys()):
                if current_time >= self.token_cooldowns[token]:
                    del self.token_cooldowns[token]
                    return token, True

            if self.tokens:
                token = self.tokens[self.token_index]
                self.token_index = (self.token_index + 1) % len(self.tokens)
                return token, False
            return None, False

    def mark_rate_limited(self, token: str, reset_time: int) -> None:
        with self.lock:
            self.token_cooldowns[self._get_token_hash(token)] = max(time.time() + 5, reset_time)
            logger.debug(f"Token {self._get_token_hash(token)} rate limited until {reset_time}")

    def get_all_tokens(self) -> List[str]:
        return self.tokens.copy()


if GITHUB_TOKENS:
    GLOBAL_TOKEN_MANAGER = TokenManager(GITHUB_TOKENS)
    logger.info(f"TokenManager initialized with {len(GITHUB_TOKENS)} tokens")
else:
    GLOBAL_TOKEN_MANAGER = None
    logger.warning("TokenManager initialized with NO tokens")