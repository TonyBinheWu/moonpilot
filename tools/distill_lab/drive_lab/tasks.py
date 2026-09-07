"""Allowlisted GUI operations. Never accept shell commands from a form."""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time

from .common import LAB, checked_id, digest, read_json, write_json, inherited_job_fds

LOCAL = {"doctor", "inventory", "fetch-teacher", "fetch-device", "fetch-public", "prepare", "prepare-route",
         "localize", "split", "train", "evaluate", "export", "worldmodel-download", "worldmodel-server", "simulate", "rl-train"}
CAR = {"car-stage", "car-compile", "car-benchmark", "car-activate", "car-restore", "car-verify-active"}
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]


def run_command(*args, **kwargs):
    kwargs.setdefault("pass_fds", inherited_job_fds())
    return subprocess.run(*args, **kwargs)


def name(value, label="名稱", pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]*"):
    if not isinstance(value, str) or len(value) > 180 or not re.fullmatch(pattern, value) or value in {".", ".."}:
        raise ValueError(label + "格式不正確")
    return value


def host_name(value):
    return name(value, "SSH 別名", r"[A-Za-z0-9_][A-Za-z0-9_.@-]*")


def repo_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in Path(value).parts:
        raise ValueError("車端專案需為絕對路徑，例如 /data/openpilot")
    return value.rstrip("/")


def positive(value, field, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{field} 必須介於 1 與 {maximum}")
    return value


def validate(root, kind, values):
    if kind not in LOCAL | CAR | {"device-info"}:
        raise ValueError("不支援的操作")
    if not isinstance(values, dict):
        raise ValueError("操作參數必須是物件")
    p = dict(values)
    allowed = {
        "doctor": set(), "inventory": set(), "fetch-teacher": set(),
        "fetch-device": {"host", "route"}, "fetch-public": {"segments", "revision"},
        "prepare": {"source", "route", "method"}, "prepare-route": {"route", "method"},
        "localize": {"segment", "method"}, "split": {"smoke", "allow_assisted"},
        "train": {"name", "steps", "batch_size", "learning_rate", "validate_every", "resume"},
        "evaluate": {"name", "checkpoint", "batch_size", "on_policy"},
        "export": {"name", "checkpoint", "release", "on_policy"},
        "worldmodel-download": {"revision"}, "worldmodel-server": set(),
        "simulate": {"name", "checkpoint", "segment", "on_policy"},
        "rl-train": {"name", "checkpoint", "segment", "steps"},
        "device-info": {"host", "repo"},
    }
    common = {"host", "repo", "target_id"}
    allowed.update({"car-stage": common | {"release"}, "car-compile": common | {"release", "camera"},
                    "car-benchmark": common | {"release", "runs", "max_p95_ms"},
                    "car-activate": common | {"release", "confirmation", "closed_course"},
                    "car-restore": common | {"backup", "confirmation"}, "car-verify-active": common})
    if set(p) - allowed[kind]:
        raise ValueError("不接受額外操作參數")
    required = {"fetch-device": {"host", "route"}, "fetch-public": {"segments"},
                "prepare": {"source", "route"}, "prepare-route": {"route"}, "localize": {"segment"},
                "train": {"name", "steps"}, "evaluate": {"name", "checkpoint"},
                "export": {"name", "checkpoint", "release"},
                "simulate": {"name", "checkpoint", "segment"}, "rl-train": {"name", "checkpoint", "segment"},
                "device-info": {"host", "repo"}}
    required.update({k: allowed[k] for k in CAR})
    if not required.get(kind, set()) <= set(p):
        raise ValueError("缺少必要操作參數")
    for field in ("name", "checkpoint", "on_policy"):
        if p.get(field): name(p[field], field)
    if "release" in p:
        name(p["release"], "候選名稱", r"[A-Za-z0-9][A-Za-z0-9_-]*")
    if "route" in p: name(p["route"], "行程", r"[A-Za-z0-9_-]+")
    if "host" in p: host_name(p["host"])
    if "repo" in p: p["repo"] = repo_path(p["repo"])
    if "segment" in p: checked_id(p["segment"])
    if "segments" in p:
        if not isinstance(p["segments"], list) or not 1 <= len(p["segments"]) <= 100:
            raise ValueError("一次選擇 1 至 100 個公開片段")
        for segment in p["segments"]: checked_id(segment)
    for field, maximum in (("steps", 10000000), ("batch_size", 1024), ("validate_every", 1000000), ("runs", 10000)):
        if field in p: positive(p[field], field, maximum)
    if "method" in p and p["method"] not in {"raw-gnss", "logged-ecef"}:
        raise ValueError("定位方式錯誤")
    for field in ("resume", "smoke", "allow_assisted", "closed_course"):
        if field in p and not isinstance(p[field], bool): raise ValueError(field + " 必須是布林值")
    if "learning_rate" in p and (not isinstance(p["learning_rate"], (float, int))
            or not math.isfinite(p["learning_rate"]) or not 0 < p["learning_rate"] <= .1):
        raise ValueError("學習率需大於 0 且不超過 0.1")
    if "revision" in p:
        name(p["revision"], "資料版本", r"[A-Za-z0-9][A-Za-z0-9_./-]*")
    if "source" in p:
        source = Path(p["source"]).expanduser()
        if not source.is_absolute() or not source.is_dir(): raise ValueError("請指定 Spark 上存在的絕對資料夾路徑")
        p["source"] = str(source.resolve())
    if kind == "train":
        if not (root / "dataset.json").is_file(): raise ValueError("先建立資料切分")
        if not (root / "models/big_driving_supercombo.onnx").is_file(): raise ValueError("先下載教師模型")
        run = root / "runs" / p["name"]
        if p.get("resume"):
            if not all((run / f).is_file() for f in ("last.pt", "resume.pt", "config.json")):
                raise ValueError("這個實驗沒有完整的續訓 checkpoint")
        elif run.exists(): raise ValueError("實驗名稱已存在；請勾選續訓或另取名稱")
    if kind in {"evaluate", "export", "simulate", "rl-train"}:
        run = root / "runs" / p["name"]
        if not (run / p["checkpoint"]).is_file(): raise ValueError("找不到指定 checkpoint")
        if p.get("on_policy") and not (run / p["on_policy"]).is_file(): raise ValueError("找不到微調權重")
    if kind == "export" and (root / "releases" / p["release"]).exists():
        raise ValueError("候選名稱已存在；不覆蓋已封存模型")
    if kind == "simulate" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("互動模擬需要 Spark 圖形桌面；目前沒有可用的顯示工作階段")
    if kind in CAR:
        if not re.fullmatch(r"[0-9a-f]{64}", p["target_id"]): raise ValueError("請先讀取裝置身分")
        if kind == "car-compile" and p["camera"] not in {"1928x1208", "1344x760"}: raise ValueError("攝影機解析度不支援")
        if kind == "car-benchmark" and (p["runs"] < 20 or not 0 < p["max_p95_ms"] < 50):
            raise ValueError("至少量測 20 次，p95 門檻需大於 0 且小於 50ms")
        if kind == "car-activate" and (p["closed_course"] is not True or p["confirmation"] != p["host"] + "/" + p["release"]):
            raise ValueError("請確認封閉場地條件，並完整輸入裝置／候選名稱")
        if kind == "car-restore":
            name(p["backup"], "備份名稱")
            if p["confirmation"] != p["host"] + "/" + p["backup"]: raise ValueError("回復確認文字不符")
        if kind == "car-stage":
            import car
            car.validate_bundle(root / "releases" / p["release"])
    return p


def resource_for(kind):
    if kind in CAR or kind in {"device-info", "fetch-device"}: return "device"
    if kind == "worldmodel-server": return "worldmodel"
    return "workspace"


def cli_command(root, kind, p):
    command = [sys.executable, str(LAB / "lab.py"), "--work", str(root), kind]
    for field, value in p.items():
        if value is None or value is False: continue
        flag = "--" + field.replace("_", "-")
        if value is True: command.append(flag)
        elif isinstance(value, list): command.extend([flag, *value])
        elif kind == "simulate" and field == "on_policy": command.extend([flag, str(root / "runs" / p["name"] / value)])
        else: command.extend([flag, str(value)])
    return command


def ssh(host, command):
    return ["ssh", *SSH_OPTIONS, host_name(host), shlex.join(command)]


def device_report_path(root, host):
    return root / "reports" / ("device-" + hashlib.sha256(host.encode()).hexdigest()[:16] + ".json")


def probe(root, host, repo):
    # Only selected non-secret parameters and experiment metadata are returned.
    script = '''import pathlib,json,socket,subprocess,hashlib
p=pathlib.Path('/data/params/d'); base=pathlib.Path('/data/distill-lab')
def param(k):
 f=p/k
 return f.read_text().strip() if f.is_file() else ''
def read(f):
 try: return json.loads(f.read_text()) if f.stat().st_size<2000000 else {'error':'檔案過大'}
 except (OSError,ValueError): return None
hostname=socket.gethostname(); serial=param('HardwareSerial') or param('DongleId')
repo=REPO
commit=subprocess.check_output(['git','-C',repo,'rev-parse','HEAD'],text=True).strip()
raw=pathlib.Path('/data/media/0/realdata')
routes=sorted({d.name.rsplit('--',1)[0] for d in raw.iterdir() if d.is_dir() and d.name.rsplit('--',1)[-1].isdigit()}) if raw.is_dir() else []
candidates={}
if (base/'candidates').is_dir():
 for d in (base/'candidates').iterdir():
  if d.is_dir(): candidates[d.name]={k:read(d/(k+'.json')) for k in ('manifest','compile','benchmark')}
backups=sorted(d.name for d in (base/'backups').iterdir() if d.is_dir() and (d/'backup.json').is_file()) if (base/'backups').is_dir() else []
print(json.dumps({'hostname':hostname,'serial':serial,'target_id':hashlib.sha256((hostname+'|'+serial).encode()).hexdigest() if serial else None,
 'repo':repo,'commit':commit,'offroad':param('IsOnroad')=='0','routes':routes,'candidates':candidates,'backups':backups,
 'active':read(base/'active.json'),'chestnut_active':param('ChestnutActive')=='1','chestnut_error':param('ChestnutModelError')=='1'}))
'''.replace("REPO", repr(repo_path(repo)))
    result = run_command(ssh(host, ["python3", "-c", script]), text=True, capture_output=True, timeout=60, check=True)
    report = json.loads(result.stdout)
    report.update(host=host, checked_at=time.time())
    write_json(device_report_path(root, host), report)
    return report


def car_command(kind, p):
    remote_tool = "/data/distill-lab/gui-tools/" + digest(LAB / "car.py") + "/car.py"
    command = ["python3", remote_tool, "--repo", p["repo"], kind.removeprefix("car-")]
    if kind in {"car-compile", "car-benchmark", "car-activate"}:
        command += ["--bundle", "/data/distill-lab/candidates/" + p["release"]]
    if kind == "car-compile": command += ["--camera", p["camera"]]
    if kind == "car-benchmark": command += ["--runs", str(p["runs"]), "--max-p95-ms", str(p["max_p95_ms"])]
    if kind == "car-activate": command += ["--closed-course"]
    if kind == "car-restore": command += ["--backup", "/data/distill-lab/backups/" + p["backup"]]
    return command


def execute(root, kind, payload):
    p = validate(root, kind, payload)
    if kind in LOCAL:
        # Existing acquisition now uses the same non-interactive strict SSH options.
        run_command(cli_command(root, kind, p), check=True)
        return
    report = probe(root, p["host"], p["repo"])
    if kind == "device-info":
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    if report["target_id"] != p["target_id"] or not report["target_id"]:
        raise ValueError("裝置身分與畫面確認時不同，停止操作")
    if kind != "car-verify-active" and not report["offroad"]:
        raise ValueError("裝置未確認停車／offroad，停止車端操作")
    tool = Path(car_command(kind, p)[1])
    setup = "from pathlib import Path; Path(" + repr(str(tool.parent)) + ").mkdir(parents=True, exist_ok=True)"
    run_command(ssh(p["host"], ["python3", "-c", setup]), check=True)
    transfer = ["rsync", "--archive", "--partial", "--protect-args", "-e", shlex.join(["ssh", *SSH_OPTIONS])]
    run_command([*transfer, str(LAB / "car.py"), p["host"] + ":" + str(tool)], check=True)
    verify = "import hashlib,pathlib; assert hashlib.sha256(pathlib.Path(" + repr(str(tool)) + ").read_bytes()).hexdigest()==" + repr(digest(LAB / "car.py"))
    run_command(ssh(p["host"], ["python3", "-c", verify]), check=True)
    if kind == "car-stage":
        incoming = "/data/distill-lab/incoming/" + p["release"]
        setup = "from pathlib import Path; Path(" + repr(incoming) + ").mkdir(parents=True, exist_ok=True)"
        run_command(ssh(p["host"], ["python3", "-c", setup]), check=True)
        run_command([*transfer, str(root / "releases" / p["release"]) + "/", p["host"] + ":" + incoming + "/"], check=True)
        command = car_command(kind, p) + ["--bundle", incoming, "--release", p["release"]]
    else:
        if "release" in p and p["release"] not in report["candidates"]: raise ValueError("車端尚無這個候選，先傳送並暫存")
        if kind == "car-restore" and p["backup"] not in report["backups"]: raise ValueError("車端找不到指定備份")
        command = car_command(kind, p)
    run_command(ssh(p["host"], command), check=True)
    probe(root, p["host"], p["repo"])
