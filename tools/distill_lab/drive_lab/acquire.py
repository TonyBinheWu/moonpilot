from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import urllib.request

from .common import TEACHER_SHA, checked_id, digest, write_json, inherited_job_fds


def fetch_device(root, host, route):
    from .tasks import SSH_OPTIONS
    # No password storage, no remote shell supplied by the UI, no modification of the device.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise ValueError("請使用 SSH config 的主機別名，例如 comma")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", route):
        raise ValueError("route 請使用 /data/media/0/realdata 下行程名稱，移除最後 --片段序號")
    remote = "import pathlib,json; print(json.dumps([p.name for p in pathlib.Path('/data/media/0/realdata').iterdir() if p.is_dir()]))"
    import shlex
    names = json.loads(subprocess.check_output(["ssh", *SSH_OPTIONS, host, "python3 -c " + shlex.quote(remote)], text=True, timeout=60, pass_fds=inherited_job_fds()))
    selected = sorted(n for n in names if n.startswith(route + "--") and n.rsplit("--", 1)[-1].isdigit())
    if not selected:
        raise ValueError("找不到行程；請確認實際目錄名稱、SSH 設定與裝置仍保有完整紀錄")
    for name in selected:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("裝置回傳非預期的目錄名稱")
        target = root / "raw" / name
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(["rsync", "--archive", "--partial", "--protect-args", "-e", shlex.join(["ssh", *SSH_OPTIONS]),
                        "--include=rlog", "--include=rlog.zst", "--include=rlog.bz2",
                        "--include=fcamera.hevc", "--include=ecamera.hevc", "--exclude=*",
                        f"{host}:/data/media/0/realdata/{name}/", str(target) + "/"], check=True, pass_fds=inherited_job_fds())
    return selected


def fetch_public(root, segments, revision):
    from huggingface_hub import HfApi, hf_hub_download
    info = HfApi().dataset_info("commaai/comma1M", revision=revision)
    revision = info.sha
    files = set(HfApi().list_repo_files("commaai/comma1M", repo_type="dataset", revision=revision))
    required = ("fcamera.hevc", "localizer.safetensors", "frame_info.safetensors")
    for segment in segments:
        checked_id(segment)
        prefix = f"data/{segment}/"
        missing = [f for f in required if prefix + f not in files]
        if missing:
            raise ValueError(f"{segment} 缺少 {missing}；不能以影片幀率虛構 frame_info。請選含完整標籤的資料版本。")
        target = root / "data" / segment
        if target.exists():
            raise FileExistsError(target)
        target.mkdir()
        try:
            for name in (*required, "ecamera.hevc"):
                if prefix + name in files:
                    cached = hf_hub_download("commaai/comma1M", prefix + name, repo_type="dataset", revision=revision)
                    shutil.copy2(cached, target / name)
            write_json(target / "source.json", {"id": segment, "route": "public:" + segment,
                       "source": "commaai/comma1M", "revision": revision,
                       "label_method": "published_offline_localizer", "route_identity_known": False,
                       "note": "公開匿名片段未提供原行程；不能宣稱已排除同路段／同行程洩漏"})
        except Exception:
            shutil.rmtree(target)
            raise
    return revision


def fetch_teacher(root):
    target = root / "models" / "big_driving_supercombo.onnx"
    if target.exists():
        raise FileExistsError("模型已存在；如需更換，先移至其他名稱保存")
    url = f"https://media.githubusercontent.com/media/commaai/openpilot/{TEACHER_SHA}/openpilot/selfdrive/modeld/models/big_driving_supercombo.onnx"
    temporary = target.with_suffix(".partial")
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
        shutil.copyfileobj(response, out)
    import onnx
    onnx.checker.check_model(str(temporary))
    temporary.replace(target)
    write_json(target.with_suffix(".source.json"), {"repository": "commaai/openpilot", "commit": TEACHER_SHA,
                                                  "sha256": digest(target)})
    return target


def copy_segment(root, source, route):
    source = Path(source).expanduser().resolve()
    names = [p for p in (source / "rlog.zst", source / "rlog.bz2", source / "rlog") if p.is_file()]
    if len(names) != 1:
        raise ValueError("每個片段需要且只能有一個完整 rlog／rlog.zst／rlog.bz2")
    required = [*names, source / "fcamera.hevc", source / "ecamera.hevc"]
    if any(not p.is_file() or p.stat().st_size == 0 for p in required):
        raise ValueError("缺少完整 rlog、fcamera.hevc 或 ecamera.hevc；不能使用 qcamera 或螢幕錄影")
    segment = hashlib.sha256((route + "/" + source.name).encode()).hexdigest()[:32]
    target = root / "data" / segment
    if target.exists():
        raise FileExistsError(f"{target} 已存在，拒絕覆蓋")
    target.mkdir()
    try:
        for p in required:
            shutil.copy2(p, target / p.name)
        write_json(target / "source.json", {"id": segment, "route": route, "route_identity_known": True,
                   "source": "local_device", "segment_name": source.name,
                   "raw_sha256": {p.name: digest(p) for p in required}, "label_method": "pending"})
    except Exception:
        shutil.rmtree(target)
        raise
    return target
