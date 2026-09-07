import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from .common import LAB, UPSTREAM, checked_id, digest, read_json, work_dirs, write_json, inherited_job_fds


def doctor(root):
    report = {"python": sys.version, "architecture": platform.machine(), "checks": {}, "versions": {}}
    for module in ("torch", "onnxruntime", "PyNvVideoCodec", "safetensors", "openpilot.cereal", "tensorboard"):
        try:
            loaded = importlib.import_module(module)
            if module == "onnxruntime":
                loaded.disable_telemetry_events()
            report["checks"][module] = True
        except Exception as e:
            report["checks"][module] = str(e)
    for package in ("torch", "torchvision", "onnxruntime-gpu", "pynvvideocodec", "torchao"):
        try: report["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: report["versions"][package] = None
    if report["checks"]["torch"] is True:
        import torch
        report["cuda_available"] = torch.cuda.is_available()
        report["cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            report["gpu"] = torch.cuda.get_device_name(0)
            report["gpu_capability"] = list(torch.cuda.get_device_capability(0))
            x = torch.randn(64, 64, device="cuda", dtype=torch.bfloat16, requires_grad=True)
            (x @ x.T).float().square().mean().backward()
            torch.cuda.synchronize()
            report["bf16_backward_finite"] = bool(torch.isfinite(x.grad).all())
    if report["checks"]["onnxruntime"] is True:
        import onnxruntime as ort
        report["onnx_providers"] = ort.get_available_providers()
    report["ready_for_smoke_test"] = (all(v is True for v in report["checks"].values())
        and report.get("bf16_backward_finite", False) and "CUDAExecutionProvider" in report.get("onnx_providers", []))
    write_json(root / "reports" / "environment.json", report)
    return report


def rl_links(root):
    # Upstream RL entry points use paths relative to their repository root.
    for name in ("data", "models"):
        link = UPSTREAM / name
        if link.is_symlink() and link.resolve() == (root / name).resolve():
            continue
        if link.exists() or link.is_symlink():
            raise ValueError(f"{link} 已存在且不是本工作目錄，拒絕替換")
        link.symlink_to(root / name, target_is_directory=True)


def main():
    p = argparse.ArgumentParser(description="DGX Spark 駕駛模型實驗室")
    p.add_argument("--work", type=Path, default=LAB / "work")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor"); sub.add_parser("inventory"); sub.add_parser("fetch-teacher")
    a = sub.add_parser("fetch-device"); a.add_argument("--host", required=True); a.add_argument("--route", required=True)
    a = sub.add_parser("fetch-public"); a.add_argument("--segments", nargs="+", required=True); a.add_argument("--revision", default="main")
    for cmd in ("prepare", "prepare-route"):
        a = sub.add_parser(cmd); a.add_argument("--route", required=True)
        a.add_argument("--method", choices=["raw-gnss", "logged-ecef"], default="raw-gnss")
        if cmd == "prepare": a.add_argument("--source", type=Path, required=True)
    a = sub.add_parser("localize"); a.add_argument("--segment", type=checked_id, required=True)
    a.add_argument("--method", choices=["raw-gnss", "logged-ecef"], default="raw-gnss")
    a = sub.add_parser("split"); a.add_argument("--smoke", action="store_true"); a.add_argument("--allow-assisted", action="store_true")
    a = sub.add_parser("train"); a.add_argument("--name", required=True); a.add_argument("--steps", type=int, default=100)
    a.add_argument("--batch-size", type=int, default=1); a.add_argument("--learning-rate", type=float, default=3e-4)
    a.add_argument("--validate-every", type=int, default=20); a.add_argument("--resume", action="store_true")
    a = sub.add_parser("evaluate"); a.add_argument("--name", required=True); a.add_argument("--checkpoint", default="best.pt")
    a.add_argument("--batch-size", type=int, default=1)
    a.add_argument("--on-policy")
    a = sub.add_parser("export"); a.add_argument("--name", required=True); a.add_argument("--release", required=True)
    a.add_argument("--checkpoint", default="best.pt")
    a.add_argument("--on-policy")
    a = sub.add_parser("worldmodel-download"); a.add_argument("--revision", default="main")
    sub.add_parser("worldmodel-server")
    for cmd in ("simulate", "rl-train"):
        a = sub.add_parser(cmd); a.add_argument("--name", required=True); a.add_argument("--checkpoint", default="best.pt")
        a.add_argument("--segment", type=checked_id, required=True)
        if cmd == "rl-train": a.add_argument("--steps", type=int, default=10)
        else: a.add_argument("--on-policy", type=Path)
    args = p.parse_args(); root = work_dirs(args.work)
    for field in ("name", "release", "checkpoint"):
        value = getattr(args, field, None)
        if value and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in {".", ".."}):
            p.error(f"{field} 只能包含英數、底線、連字號與點，不能使用路徑")
    cmd = args.command
    if cmd == "doctor":
        result = doctor(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ready_for_smoke_test"]: raise SystemExit(2)
        return
    elif cmd == "inventory":
        result = {"raw": [d.name for d in sorted((root / "raw").iterdir()) if d.is_dir()],
                  "prepared": [read_json(p) for p in sorted((root / "data").glob("*/source.json"))]}
    elif cmd.startswith("fetch-"):
        from . import acquire
        if cmd == "fetch-device": result = acquire.fetch_device(root, args.host, args.route)
        elif cmd == "fetch-public": result = acquire.fetch_public(root, args.segments, args.revision)
        else: result = str(acquire.fetch_teacher(root))
    elif cmd in ("prepare", "prepare-route", "localize"):
        from .acquire import copy_segment
        from .localization import prepare
        if cmd == "localize": result = prepare(root / "data" / args.segment, args.method)
        elif cmd == "prepare": result = prepare(copy_segment(root, args.source, args.route), args.method)
        else:
            directories = sorted(d for d in (root / "raw").iterdir() if d.is_dir() and d.name.startswith(args.route + "--"))
            if not directories: raise ValueError("raw/ 下找不到該行程")
            result = []
            for directory in directories:
                try:
                    target = copy_segment(root, directory, args.route)
                    result.append({"source": directory.name, "id": target.name, "result": prepare(target, args.method)})
                except Exception as e:
                    result.append({"source": directory.name, "error": str(e)})
            write_json(root / "reports" / "prepare-route.json", result)
            if any("error" in r for r in result):
                print(json.dumps(result, ensure_ascii=False, indent=2))
                raise SystemExit(2)
    elif cmd == "split":
        from .training import dataset_manifest
        result = dataset_manifest(root, args.smoke, args.allow_assisted)
    elif cmd == "train":
        from .training import train
        result = str(train(root, args.name, args.steps, args.batch_size, args.learning_rate, args.validate_every, args.resume))
    elif cmd == "evaluate":
        from .training import evaluate_checkpoint
        if args.on_policy and Path(args.on_policy).name != args.on_policy: p.error("--on-policy 指定實驗目錄內的檔名")
        result = evaluate_checkpoint(root, args.name, args.checkpoint, args.batch_size, args.on_policy)
    elif cmd == "export":
        from .exporting import export_candidate
        if args.on_policy and Path(args.on_policy).name != args.on_policy: p.error("--on-policy 指定實驗目錄內的檔名")
        result = str(export_candidate(root, args.name, args.release, args.checkpoint, args.on_policy))
    elif cmd == "worldmodel-download":
        from huggingface_hub import HfApi, hf_hub_download
        import shutil
        revision = HfApi().model_info("commaai/worldmodel-4B", revision=args.revision).sha
        target = root / "models" / "model.fp8_nvfp4.torchpackage"
        if target.exists(): raise FileExistsError(target)
        source = hf_hub_download("commaai/worldmodel-4B", "model.fp8_nvfp4.torchpackage", revision=revision)
        shutil.copy2(source, target)
        write_json(target.with_suffix(".source.json"), {"revision": revision, "sha256": digest(target)})
        result = str(target)
    elif cmd in ("worldmodel-server", "simulate", "rl-train"):
        rl_links(root)
        if cmd == "worldmodel-server":
            command = [sys.executable, "-m", "rl.server", "--host", "127.0.0.1"]
        else:
            checkpoint = root / "runs" / args.name / args.checkpoint
            if not checkpoint.is_file(): raise FileNotFoundError(checkpoint)
            module = "rl.train" if cmd == "rl-train" else "rl.env"
            command = [sys.executable, "-m", module, "--model", str(checkpoint), "--segment", args.segment,
                       "--cache-dir", str(root / "runs" / args.name / "rl-cache")]
            if cmd == "rl-train":
                manifest = read_json(root / "dataset.json")
                config = read_json(root / "runs" / args.name / "config.json")
                if digest(root / "dataset.json") != config["dataset_sha256"] or args.segment not in manifest["train"]:
                    raise ValueError("RL 只能使用同一實驗資料切分中的訓練片段，不能使用驗證組")
                if (root / "runs" / args.name / "on_policy.pt").exists():
                    raise FileExistsError("on_policy.pt 已存在，請先另名保存，避免覆蓋已有微調結果")
                command += ["--steps", str(args.steps), "--output", str(root / "runs" / args.name / "on_policy.pt")]
            else:
                command += ["--actor", "supercombo"]
                if args.on_policy: command += ["--on-policy", str(args.on_policy.resolve())]
        subprocess.run(command, cwd=UPSTREAM, check=True, pass_fds=inherited_job_fds())
        if cmd == "rl-train":
            write_json(root / "runs" / args.name / "rl_provenance.json", {
                "base_weights_sha256": digest(checkpoint), "dataset_sha256": digest(root / "dataset.json"),
                "training_segment": args.segment, "steps": args.steps,
                "on_policy_sha256": digest(root / "runs" / args.name / "on_policy.pt")})
        result = "completed"
    else: raise ValueError(cmd)
    print(json.dumps(result, ensure_ascii=False, indent=2))
