import os
from dotenv import load_dotenv
from utils.paths import PROJECT_ROOT
from utils.logger import logger

ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE)

def _parse_tokens():
    tokens_str = os.getenv("GITHUB_TOKENS", "")
    tokens = []

    if tokens_str: tokens = [t.strip() for t in tokens_str.split(",") if t.strip()]
    else:
        single = os.getenv("GITHUB_TOKEN", "").strip()
        if single: tokens = [single]

    valid_tokens = []
    for t in tokens:
        if t.startswith(("ghp_", "github_pat_", "gho_", "ghu_", "ghs_")):
            valid_tokens.append(t)
        else:
            logger.warning(f"Token starting with {t[:4]}... looks invalid (should start with ghp_ or github_pat_)")
            valid_tokens.append(t)

    return valid_tokens


GITHUB_TOKENS = _parse_tokens()
GITHUB_TOKEN = GITHUB_TOKENS[0] if GITHUB_TOKENS else ""

if not GITHUB_TOKENS:
    logger.warning("No GitHub tokens found in environment variables.")
    logger.warning("Please set GITHUB_TOKENS or GITHUB_TOKEN in your ..env file.")
else:
    logger.info(f"Loaded {len(GITHUB_TOKENS)} GitHub token(s) from environment.")