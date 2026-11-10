import logging
import os
from datetime import datetime
from pathlib import Path

from config import LOG_SEPARATOR
from utils.paths import LOG_DIR, PROJECT_ROOT

os.makedirs(LOG_DIR, exist_ok=True)

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_file = os.path.join(LOG_DIR, f"app_{timestamp}.log")

class ProjectPathFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            relpath = Path(record.pathname).resolve().relative_to(PROJECT_ROOT)
            record.project_path = str(relpath)
        except Exception:
            record.project_path = record.filename
        return super().format(record)


logger: logging.Logger = logging.getLogger("git-h-index")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    formatter = ProjectPathFormatter(
        fmt=(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | "
            "%(project_path)s:%(lineno)d | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

def log_block(*lines, level: str = "info"):
    log_method = getattr(logger, level, logger.info)

    log_method(LOG_SEPARATOR)
    for line in lines: log_method(line)
    log_method(LOG_SEPARATOR)
