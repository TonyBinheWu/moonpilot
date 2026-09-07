from pathlib import Path
import hashlib
import json
import os
import tempfile

LAB = Path(__file__).resolve().parents[1]
UPSTREAM = LAB / "upstream"
DISTILL_SHA = "294d57a919cc384386349a22ac5c9cc02d5edb99"
TEACHER_SHA = "084747c75d2cbd23af65ab7a9e770bbd7b98bac9"
SUNNYPILOT_SHA = "ca717be308d052389ac54beaa9eed1923b704e07"


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text())


def route_split(records, validation_fraction=0.2):
    """Deterministic split by full route, never adjacent minutes of a route."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction 必須介於 0 與 1")
    routes = sorted({r["route"] for r in records}, key=lambda x: hashlib.sha256(x.encode()).hexdigest())
    if len(routes) < 2:
        raise ValueError("至少需要兩個獨立行程，才能分開訓練與驗證；單行程只能做 smoke 測試")
    n = min(len(routes) - 1, max(1, round(len(routes) * validation_fraction)))
    val_routes = set(routes[:n])
    return {"train": [r["id"] for r in records if r["route"] not in val_routes],
            "val": [r["id"] for r in records if r["route"] in val_routes],
            "routes": {r: "val" if r in val_routes else "train" for r in routes}}


def checked_id(value):
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("segment ID 必須為 32 個小寫十六進位字元")
    return value


def work_dirs(root):
    root = Path(root).expanduser().resolve()
    for name in ("raw", "data", "models", "runs", "reports", "releases"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root
