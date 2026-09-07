from pathlib import Path
import json
import random
import time
import numpy as np

from .common import DISTILL_SHA, digest, read_json, write_json


def dataset_manifest(root, smoke=False, allow_assisted=False):
    from safetensors.numpy import load_file
    from .common import route_split
    from .localization import check_labels
    records, rejected = [], []
    for directory in sorted((root / "data").iterdir()):
        if not directory.is_dir():
            continue
        try:
            source = read_json(directory / "source.json")
            check_labels(load_file(directory / "frame_info.safetensors"), load_file(directory / "localizer.safetensors"))
            if source["source"] == "local_device" and not allow_assisted:
                fraction = source.get("assisted_fraction")
                if fraction is None or fraction > 0.05:
                    raise ValueError("系統控制占比未知或超過 5%；若要蒸餾系統駕駛而非個人風格，明確指定 --allow-assisted")
            source["files_sha256"] = {p.name: digest(p) for p in directory.iterdir()
                                      if p.name in {"fcamera.hevc", "ecamera.hevc", "localizer.safetensors", "frame_info.safetensors"}}
            records.append(source)
        except (ValueError, OSError, KeyError) as e:
            rejected.append({"id": directory.name, "reason": str(e)})
    if not records:
        write_json(root / "reports" / "rejected.json", rejected)
        raise ValueError("沒有合格片段；詳見 reports/rejected.json")
    split = {"train": [r["id"] for r in records], "val": [], "routes": {}} if smoke else route_split(records)
    manifest = {**split, "records": records, "rejected": rejected, "smoke_only": smoke,
                "route_isolation_verified": all(r.get("route_identity_known", False) for r in records),
                "distill_commit": DISTILL_SHA}
    write_json(root / "dataset.json", manifest)
    return manifest


def load_model(weights=None, on_policy=None):
    import torch
    from supervised.model import Supercombo, SupercomboConfig, VisionConfig, rl_policies
    config = SupercomboConfig(vision=VisionConfig(pretrained=weights is None))
    model = Supercombo(config, rl_policies(config) if on_policy else None)
    if weights is not None:
        state = torch.load(weights, map_location="cpu", weights_only=True)
        if on_policy:
            result = model.load_state_dict(state, strict=False)
            if result.unexpected_keys or any(not key.startswith("on_policy_temporal.") for key in result.missing_keys):
                raise ValueError("supervised checkpoint 與 RL 模型架構不相容")
            model.on_policy_temporal.load_state_dict(torch.load(on_policy, map_location="cpu", weights_only=True), strict=True)
        else:
            model.load_state_dict(state, strict=True)
    return model


def sample(root, segment, batch_size, val=False):
    import fsspec
    from supervised.dataset import DatasetConfig, _load_segment
    return _load_segment(fsspec.filesystem("file"), str(root / "data" / segment),
                         DatasetConfig(str(root), batch_size=batch_size), val, 0)


def torch_batch(values, device):
    import torch
    return {k: torch.as_tensor(v, device=device) for k, v in values.items()}


def teacher_model(path):
    from supervised.model_for_inference import ORTSupercomboForInference
    import onnxruntime as ort
    ort.disable_telemetry_events()
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("教師模型需要可用的 ONNX Runtime CUDA provider")
    model = ORTSupercomboForInference.from_supercombo(path, providers=["CUDAExecutionProvider"])
    for session in (model.vision_session, model.policies_session):
        if session.get_providers()[0] != "CUDAExecutionProvider":
            raise RuntimeError("ONNX Runtime 回退至 CPU；請先修正 CUDA runtime")
    return model


def verify_dataset(root, manifest):
    for r in manifest["records"]:
        for name, expected in r["files_sha256"].items():
            if digest(root / "data" / r["id"] / name) != expected:
                raise ValueError("資料在建立 manifest 後變更，請重新切分並建立實驗")


def evaluate(root, model, teacher, segments, batch_size, output=None):
    import torch
    from supervised.model_for_inference import InputQueues
    if not segments:
        raise ValueError("沒有獨立驗證資料，smoke 測試不能當成驗證結果")
    rng_py, rng_np, rng_torch = random.getstate(), np.random.get_state(), torch.get_rng_state()
    random.seed(607); np.random.seed(607); torch.manual_seed(607)
    device = next(model.parameters()).device
    model.eval()
    errors, teacher_errors, counts = [], [], []
    example = None
    try:
        with torch.no_grad():
            for segment in segments:
                inputs, targets = sample(root, segment, batch_size, val=True)
                if not len(inputs["img"]):
                    raise ValueError(f"{segment}: 驗證片段沒有有效視野樣本")
                pred = model(torch_batch(inputs, device))
                queues = InputQueues.from_model(teacher, batch_size=len(inputs["img"]))
                tp = teacher.prefill(inputs, queues, dense=True)
                def metrics(outputs):
                    p = outputs["plan"]
                    a = outputs["action"]
                    if isinstance(p, torch.Tensor):
                        p, a = p.float().cpu().numpy(), a.float().cpu().numpy()
                    plan = p[:, -1, :495].reshape(-1, 33, 15)
                    target = targets["plan"][:, -1].reshape(-1, 33, 15)
                    action = a[:, -1, :2]
                    if not np.isfinite(plan).all() or not np.isfinite(action).all():
                        raise ValueError("驗證出現 NaN/Inf")
                    return [float(np.abs(plan[..., 1] - target[..., 1]).mean()),
                            float(np.linalg.norm(plan[..., :3] - target[..., :3], axis=-1).mean()),
                            float(np.abs(action[:, 0] - targets["action"][:, -1, 0]).mean()),
                            float(np.abs(action[:, 1] - targets["action"][:, -1, 1]).mean())]
                errors.append(metrics(pred)); teacher_errors.append(metrics(tp)); counts.append(len(inputs["img"]))
                if example is None:
                    example = {**{"input_" + k: v[:1] for k, v in inputs.items()},
                               "rgb": targets["imgs"][:1],
                               "target_plan": targets["plan"][:1, -1].reshape(1, 33, 15),
                               "student_plan": pred["plan"][:1, -1, :495].float().cpu().numpy().reshape(1, 33, 15),
                               "teacher_plan": tp["plan"][:1, -1, :495].reshape(1, 33, 15)}
        keys = ["lateral_mae_m", "trajectory_ade_m", "lateral_accel_mae_mps2", "long_accel_mae_mps2"]
        report = {"student": dict(zip(keys, np.average(errors, axis=0, weights=counts).tolist())),
                  "teacher": dict(zip(keys, np.average(teacher_errors, axis=0, weights=counts).tolist())),
                  "segments": segments, "samples": sum(counts), "finite": True,
                  "evaluation_type": "open_loop", "road_validation": False}
        if output:
            write_json(output, report)
            np.savez_compressed(Path(output).with_suffix(".example.npz"), **example)
        return report
    finally:
        random.setstate(rng_py); np.random.set_state(rng_np); torch.set_rng_state(rng_torch)


def train(root, name, steps, batch_size=1, lr=3e-4, validate_every=100, resume=False):
    import torch
    from torch.utils.tensorboard import SummaryWriter
    from supervised.train import loss_fn
    from supervised.model_for_inference import InputQueues
    if not torch.cuda.is_available():
        raise RuntimeError("訓練需要 CUDA；先執行 doctor")
    if steps < 1 or batch_size < 1 or validate_every < 1:
        raise ValueError("steps、batch-size、validate-every 必須為正整數")
    manifest = read_json(root / "dataset.json")
    verify_dataset(root, manifest)
    if not manifest["train"]:
        raise ValueError("訓練資料為空")
    run = root / "runs" / name
    if run.exists() and not resume:
        raise FileExistsError("同名實驗已存在；另取名稱或使用 --resume")
    run.mkdir(parents=True, exist_ok=True)
    random.seed(42); np.random.seed(42); torch.manual_seed(42)
    torch.cuda.set_device(0)
    model = load_model(run / "last.pt" if resume else None).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    teacher_path = root / "models" / "big_driving_supercombo.onnx"
    teacher = teacher_model(teacher_path)
    config = {"dataset_sha256": digest(root / "dataset.json"), "teacher_sha256": digest(teacher_path),
              "distill_commit": DISTILL_SHA, "batch_size": batch_size, "learning_rate": lr,
              "smoke_only": manifest["smoke_only"], "route_isolation_verified": manifest["route_isolation_verified"]}
    first, best = 1, float("inf")
    if resume:
        previous = read_json(run / "config.json")
        if previous != config:
            raise ValueError("續訓的資料、教師、batch size 或 learning rate 已改變，請建立新實驗")
        saved = torch.load(run / "resume.pt", map_location="cpu", weights_only=False)
        if saved.get("weights_sha256") != digest(run / "last.pt"):
            raise ValueError("checkpoint 寫入曾中斷，模型與 optimizer 不一致；請使用先前完整備份")
        optimizer.load_state_dict(saved["optimizer"])
        first, best = saved["step"] + 1, saved["best"]
        random.setstate(saved["python_rng"]); np.random.set_state(saved["numpy_rng"])
        torch.set_rng_state(saved["torch_rng"]); torch.cuda.set_rng_state(saved["cuda_rng"])
    if steps < first:
        raise ValueError("--steps 是累積總步數，必須大於上次完成步數")
    write_json(run / "config.json", config)
    writer = SummaryWriter(str(run / "tensorboard"))
    def save(step):
        torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, run / "last.pt.tmp")
        (run / "last.pt.tmp").replace(run / "last.pt")
        torch.save({"step": step, "best": best, "weights_sha256": digest(run / "last.pt"), "optimizer": optimizer.state_dict(),
                    "python_rng": random.getstate(), "numpy_rng": np.random.get_state(),
                    "torch_rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state()}, run / "resume.pt.tmp")
        (run / "resume.pt.tmp").replace(run / "resume.pt")
        write_json(run / "status.json", {"step": step, "weights_sha256": digest(run / "last.pt"),
                   "state": "checkpoint_saved", "best_validation_ade_m": best if np.isfinite(best) else None})
    try:
        for step in range(first, steps + 1):
            start = time.perf_counter()
            inputs = targets = None
            for attempt in range(max(10, len(manifest["train"]) * 2)):
                inputs, targets = sample(root, random.choice(manifest["train"]), batch_size)
                if len(inputs["img"]):
                    break
            if inputs is None or not len(inputs["img"]):
                raise ValueError("連續樣本視野皆無效，請檢查校正與攝影機參數")
            queues = InputQueues.from_model(teacher, batch_size=len(inputs["img"]))
            teacher_outputs = teacher.prefill(inputs, queues, dense=True)
            model.train(); optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                pred = model(torch_batch(inputs, "cuda"))
                loss, components = loss_fn(pred, torch_batch(targets, "cuda"), torch_batch(teacher_outputs, "cuda"))
            if not torch.isfinite(loss):
                raise ValueError("loss 出現 NaN/Inf，已停止本次更新")
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step(); torch.cuda.synchronize()
            metrics = {"step": step, "loss": loss.item(), "grad_norm": float(norm),
                       "step_seconds": time.perf_counter() - start,
                       "gpu_peak_allocated_gb": torch.cuda.max_memory_allocated() / 1e9,
                       **{k: float(v.detach()) for k, v in components.items()}}
            for k, v in metrics.items():
                if k != "step":
                    writer.add_scalar("train/" + k, v, step)
            if step == 1 or step % validate_every == 0:
                writer.add_image("frames/narrow", targets["imgs"][0, ..., :3], step, dataformats="HWC")
                writer.add_image("frames/wide", targets["imgs"][0, ..., 3:], step, dataformats="HWC")
            if manifest["val"] and (step % validate_every == 0 or step == steps):
                report = evaluate(root, model, teacher, manifest["val"], batch_size, run / "validation.json")
                for who in ("student", "teacher"):
                    for k, v in report[who].items():
                        writer.add_scalar("validation/" + who + "/" + k, v, step)
                score = report["student"]["trajectory_ade_m"]
                if score < best:
                    best = score
                    torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, run / "best.pt")
            if step % validate_every == 0 or step == steps:
                save(step)
            with (run / "metrics.jsonl").open("a") as f:
                f.write(json.dumps(metrics, allow_nan=False) + "\n")
            writer.flush()
            print(json.dumps(metrics, allow_nan=False), flush=True)
        write_json(run / "status.json", {"step": steps, "weights_sha256": digest(run / "last.pt"), "state": "completed"})
    finally:
        writer.close()
    return run


def evaluate_checkpoint(root, run, checkpoint="best.pt", batch_size=1, on_policy=None):
    run = root / "runs" / run
    config = read_json(run / "config.json")
    if digest(root / "dataset.json") != config["dataset_sha256"]:
        raise ValueError("資料切分已變更，不能沿用該實驗的驗證")
    manifest = read_json(root / "dataset.json")
    verify_dataset(root, manifest)
    weights = run / checkpoint
    model = load_model(weights, run / on_policy if on_policy else None).cuda()
    teacher_path = root / "models" / "big_driving_supercombo.onnx"
    if digest(teacher_path) != config["teacher_sha256"]:
        raise ValueError("教師模型已變更")
    report = evaluate(root, model, teacher_model(teacher_path), manifest["val"], batch_size, run / "evaluation.json")
    report.update({"weights_sha256": digest(weights), "dataset_sha256": config["dataset_sha256"],
                   "teacher_sha256": config["teacher_sha256"], "checkpoint": checkpoint,
                   "route_isolation_verified": manifest["route_isolation_verified"]})
    report["on_policy_sha256"] = digest(run / on_policy) if on_policy else None
    write_json(run / "evaluation.json", report)
    return report
