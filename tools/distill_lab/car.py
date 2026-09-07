#!/usr/bin/env python3
"""Run ON THE DEVICE: stage, compile, benchmark, activate or restore a candidate.

No CAN sending, controller changes, safety-limit changes, or automatic reboot.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

BASE = Path("/data/distill-lab")
PREFIXES = ("big_driving_supercombo.onnx", "big_driving_tinygrad.pkl")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def offroad():
    p = Path("/data/params/d/IsOnroad")
    if not p.is_file() or p.read_bytes().strip() != b"0":
        raise RuntimeError("必須確認裝置處於 offroad；未取得明確狀態也會停止")


def validate_bundle(bundle):
    bundle = Path(bundle).resolve()
    m = read(bundle / "manifest.json")
    if m.get("format") != 1 or not m.get("onnx_parity", {}).get("passed"):
        raise ValueError("不支援的 bundle 或缺少 ONNX parity")
    for required in ("driving_supercombo.onnx", "parity.npz", "evaluation.json"):
        if required not in m.get("files", {}):
            raise ValueError("bundle 缺少必要檔案")
    for name, expected in m["files"].items():
        if Path(name).name != name or (bundle / name).is_symlink():
            raise ValueError("拒絕 bundle 路徑跳脫或符號連結")
        if sha(bundle / name) != expected:
            raise ValueError("bundle 檔案 checksum 不符：" + name)
    ev = read(bundle / "evaluation.json")
    if ev.get("weights_sha256") != m.get("weights_sha256") or not ev.get("finite"):
        raise ValueError("評估與模型權重不一致")
    if ev.get("on_policy_sha256") != m.get("on_policy_sha256"):
        raise ValueError("RL 權重與評估不一致")
    return m


def contract_check(repo, manifest):
    for name, expected in manifest["target_contract"]["files"].items():
        path = (repo / name).resolve()
        if not path.is_relative_to(repo.resolve()) or sha(path) != expected:
            raise ValueError("車端介面與驗證基準不符：" + name + "；需重新審查／匯出，不能略過")


def candidate_files(directory):
    return [p for p in directory.iterdir() if p.is_file() and any(p.name == prefix or p.name.startswith(prefix + ".chunk") for prefix in PREFIXES)]


def stage(bundle, release):
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", release):
        raise ValueError("release 名稱只能使用英數、底線與連字號")
    validate_bundle(bundle)
    target = BASE / "candidates" / release
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    m = read(Path(bundle) / "manifest.json")
    for name in ("manifest.json", *m["files"]):
        shutil.copy2(Path(bundle) / name, target / name)
    return target


def compile_candidate(bundle, repo, camera):
    offroad()
    manifest = validate_bundle(bundle)
    contract_check(repo, manifest)
    if camera not in {"1928x1208", "1344x760"}:
        raise ValueError("請使用 rlog 中的原始攝影機解析度")
    env = os.environ.copy()
    env.update(DEV="USB+AMD:LLVM", FRAME_DEV="CPU", FLOAT16="1", JIT_BATCH_SIZE="0",
               GMMU="0", TC_OPT="2", TC_OCCUPANCY_OPT="1")
    compiled = bundle / "compiled.pkl"
    command = [sys.executable, str(repo / "openpilot/selfdrive/modeld/compile_modeld.py"),
               "--model-size", "512x256", "--camera-resolutions", camera,
               "--onnx", str(bundle / "driving_supercombo.onnx"), "--output", str(compiled),
               "--frame-skip", "4", "--benchmark-runs", "20"]
    with (bundle / "compile.log").open("w") as f:
        subprocess.run(command, cwd=repo, env=env, stdout=f, stderr=subprocess.STDOUT, check=True)
    write(bundle / "compile.json", {"compiled_sha256": sha(compiled), "camera": camera,
          "model_sha256": manifest["files"]["driving_supercombo.onnx"], "backend": "USB+AMD:LLVM"})
    return compiled


def benchmark(bundle, repo, runs=100, max_p95_ms=45.0):
    offroad()
    manifest = validate_bundle(bundle); contract_check(repo, manifest)
    record = read(bundle / "compile.json")
    if sha(bundle / "compiled.pkl") != record["compiled_sha256"]:
        raise ValueError("編譯產物 checksum 不符")
    os.environ.update(DEV="USB+AMD:LLVM", GMMU="0")
    sys.path.insert(0, str(repo))
    import numpy as np
    from tinygrad import Tensor
    from tinygrad.nn.onnx import OnnxRunner
    from tinygrad.device import Device
    from openpilot.selfdrive.modeld.helpers import load_oob
    from openpilot.selfdrive.modeld.compile_modeld import make_input_queues, MODELD_INPUTS, nv12_copy_size
    from openpilot.system.camerad.cameras.nv12_info import get_nv12_info
    # Compare actual AMD output against the Spark-exported CPU ONNX/PyTorch reference.
    with np.load(bundle / "parity.npz", allow_pickle=False) as fixture:
        runner = OnnxRunner(str(bundle / "driving_supercombo.onnx"))
        result = next(iter(runner({k: Tensor(fixture[k], device=Device.DEFAULT) for k in manifest["input_shapes"]}).values())).numpy()
        np.testing.assert_allclose(result, fixture["expected"], rtol=1e-2, atol=1e-2)
        parity_error = float(np.max(np.abs(result - fixture["expected"])))
    del runner
    with (bundle / "compiled.pkl").open("rb") as f:
        compiled = load_oob(f)
    camera = tuple(map(int, record["camera"].split("x")))
    size = nv12_copy_size(*get_nv12_info(*camera)[:3])
    queues, npy, _ = make_input_queues(compiled["metadata"]["input_shapes"], 4,
                                     device=compiled["input_devices"]["model"], frame_copy_size=size)
    npy["tfm"][:] = np.eye(3); npy["big_tfm"][:] = np.eye(3)
    npy["traffic_convention"][:] = [1, 0]; npy["action_t"][:] = 0.2
    timings = []
    # Synthetic frames test the complete compiled warp + queue + network path, not driving quality.
    for i in range(runs + 10):
        start = time.perf_counter()
        out = compiled["run_model"][camera](**{k: queues[k] for k in MODELD_INPUTS})[0].numpy()
        if not np.isfinite(out).all():
            raise ValueError("AMD 執行出現 NaN/Inf")
        npy["prev_feat"][:] = out[:, compiled["metadata"]["output_slices"]["hidden_state"]]
        if i >= 10:
            timings.append((time.perf_counter() - start) * 1000)
    p95 = float(np.percentile(timings, 95))
    report = {"compiled_sha256": record["compiled_sha256"], "model_sha256": record["model_sha256"],
              "runtime_parity_passed": True, "parity_max_absolute_error": parity_error,
              "runs": runs, "p50_ms": float(np.median(timings)), "p95_ms": p95,
              "max_p95_ms": max_p95_ms, "latency_passed": p95 < max_p95_ms,
              "synthetic_frames_only": True, "full_stack_onroad_latency_verified": False}
    write(bundle / "benchmark.json", report)
    if not report["latency_passed"]:
        raise ValueError("推論時間未達設定門檻，詳見 benchmark.json")
    return report


def restore(backup, repo):
    offroad()
    manifest = read(backup / "backup.json")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or sha(backup / name) != expected:
            raise ValueError("備份不完整")
    model_dir = repo / "openpilot/selfdrive/modeld/models"
    for p in candidate_files(model_dir):
        p.unlink()
    for name in manifest["files"]:
        shutil.copy2(backup / name, model_dir / name)
    write(BASE / "active.json", {"state": "restored", "backup": str(backup)})
    return "原模型已回復；請手動重新啟動裝置"


def activate(bundle, repo, closed_course=False):
    offroad()
    for parameter in ("ModelManager_ActiveBundle", "ModelManager_ActiveBundleChestnut", "ModelManager_DownloadRef"):
        path = Path("/data/params/d") / parameter
        if path.exists() and path.read_bytes().strip() not in (b"", b"null"):
            raise ValueError("請先在 sunnypilot 模型選單選回預設模型並完成重啟，停止下載；偵測到 " + parameter)
    if not closed_course:
        raise ValueError("本候選模型僅供封閉場地研究；使用 --closed-course 明確指定測試用途")
    m = validate_bundle(bundle); contract_check(repo, m)
    b = read(bundle / "benchmark.json")
    if (not b.get("runtime_parity_passed") or not b.get("latency_passed")
            or b.get("compiled_sha256") != sha(bundle / "compiled.pkl")
            or b.get("model_sha256") != m["files"]["driving_supercombo.onnx"]):
        raise ValueError("需要同一候選模型的車端 parity 與 benchmark 結果")
    model_dir = repo / "openpilot/selfdrive/modeld/models"
    files = candidate_files(model_dir)
    if not files or not any(p.name.startswith("big_driving_tinygrad.pkl") for p in files):
        raise ValueError("找不到可完整備份的原 Chestnut 模型")
    backup = BASE / "backups" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(parents=True)
    for p in files:
        shutil.copy2(p, backup / p.name)
    write(backup / "backup.json", {"files": {p.name: sha(p) for p in files}})
    write(BASE / "active.json", {"state": "installing", "backup": str(backup)})
    sys.path.insert(0, str(repo))
    from openpilot.common.file_chunker import chunk_file, get_chunk_targets
    try:
        offroad()
        for p in files:
            p.unlink()
        shutil.copy2(bundle / "driving_supercombo.onnx", model_dir / PREFIXES[0])
        destination = model_dir / PREFIXES[1]
        shutil.copy2(bundle / "compiled.pkl", destination)
        chunk_file(str(destination), get_chunk_targets(str(destination), destination.stat().st_size))
        write(BASE / "active.json", {"state": "candidate_installed", "backup": str(backup),
              "bundle": str(bundle), "files": {p.name: sha(p) for p in candidate_files(model_dir)}})
    except Exception:
        restore(backup, repo)
        raise
    return {"backup": str(backup), "next": "手動重新啟動後執行 verify-active；封閉場地測試前先檢查實際載入模型"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("/data/openpilot"))
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("stage"); a.add_argument("--bundle", type=Path, required=True); a.add_argument("--release", required=True)
    for cmd in ("compile", "benchmark", "activate"):
        a = s.add_parser(cmd); a.add_argument("--bundle", type=Path, required=True)
        if cmd == "compile":
            a.add_argument("--camera", required=True)
        if cmd == "benchmark":
            a.add_argument("--runs", type=int, default=100); a.add_argument("--max-p95-ms", type=float, default=45)
        if cmd == "activate":
            a.add_argument("--closed-course", action="store_true")
    a = s.add_parser("restore"); a.add_argument("--backup", type=Path, required=True)
    s.add_parser("verify-active")
    args = p.parse_args(); repo = args.repo.resolve()
    import fcntl
    BASE.mkdir(parents=True, exist_ok=True)
    lock = (BASE / "operation.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.cmd == "stage": result = str(stage(args.bundle, args.release))
    elif args.cmd == "compile": result = str(compile_candidate(args.bundle.resolve(), repo, args.camera))
    elif args.cmd == "benchmark":
        if args.runs < 20 or not 0 < args.max_p95_ms < 50: p.error("runs 至少 20；p95 門檻須小於 20Hz 的 50ms 週期")
        result = benchmark(args.bundle.resolve(), repo, args.runs, args.max_p95_ms)
    elif args.cmd == "activate": result = activate(args.bundle.resolve(), repo, args.closed_course)
    elif args.cmd == "restore": result = restore(args.backup.resolve(), repo)
    else:
        state = read(BASE / "active.json")
        if state["state"] != "candidate_installed": raise ValueError("目前未記錄候選模型安裝狀態")
        for name, expected in state["files"].items():
            if sha(repo / "openpilot/selfdrive/modeld/models" / name) != expected:
                raise ValueError("模型被更新器／模型管理器取代：" + name)
        active = Path("/data/params/d/ChestnutActive")
        error = Path("/data/params/d/ChestnutModelError")
        result = {"files_match": True, "chestnut_active": active.exists() and active.read_bytes().strip() == b"1",
                  "chestnut_model_error": error.exists() and error.read_bytes().strip() == b"1",
                  "runtime_loaded_identity": "檔案 hash 與 Chestnut 狀態不等於載入證明；請核對 modeld 啟動紀錄與輸出"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
