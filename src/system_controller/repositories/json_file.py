import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def read_json(path: Path) -> Any:
    if not path.exists(): return None

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    except (json.JSONDecodeError, OSError):
        logger.exception("無法讀取 %s", path)
        return None


def write_json_atomic(path: Path, data: Any) -> None:
    """先寫入暫存檔再原子性取代，避免寫到一半當機導致檔案損毀。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())

        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)

    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)

        raise
