"""Config loading. One file, one place, no defaults scattered in code."""

from pathlib import Path

import yaml

_cache: dict = {}


def path(root: Path) -> Path:
    return Path(root) / ".qops" / "config.yml"


def load(root: Path) -> dict:
    p = path(root)
    key = str(p)
    if key not in _cache:
        _cache[key] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return _cache[key]


def find_root(start: Path | None = None) -> Path:
    """Nearest ancestor holding .qops/config.yml. Hooks run with an arbitrary cwd."""
    cur = Path(start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        if (d / ".qops" / "config.yml").exists():
            return d
    return cur
