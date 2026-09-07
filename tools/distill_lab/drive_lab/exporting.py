"""Static, state-explicit ONNX export matching sunnypilot's modeld contract."""
from pathlib import Path
import base64
import pickle
import shutil
import numpy as np

from .common import LAB, DISTILL_SHA, SUNNYPILOT_SHA, digest, read_json, write_json

OUTPUTS = ("lane_lines", "lane_lines_prob", "road_edges", "meta", "desire_pred", "road_transform",
           "wide_from_device_euler", "pose", "plan", "lead", "lead_prob", "action", "desire_state", "hidden_state")
INPUTS = ("img", "big_img", "features_buffer", "desire_pulse", "traffic_convention", "action_t")


def make_wrapper(model):
    import torch
    class CarModel(torch.nn.Module):
        def __init__(self, student):
            super().__init__()
            self.student = student

        def forward(self, img, big_img, features_buffer, desire_pulse, traffic_convention, action_t):
            current = self.student.vision({"img": img, "big_img": big_img})[:, None]
            features = torch.cat((features_buffer, current), dim=1)
            t = self.student.config.temporal_len
            traffic = traffic_convention[:, None].expand(-1, t, -1)
            action = action_t[:, None].expand(-1, t, -1)
            outputs = {}
            for name in self.student.policy_order:
                if name == "image_policy":
                    continue
                for key, value in getattr(self.student, name)(features, desire_pulse, traffic, action).items():
                    outputs[key] = value[:, -1].flatten(1)
            outputs["hidden_state"] = current[:, 0].flatten(1)
            return torch.cat([outputs[k] for k in OUTPUTS], dim=1)
    return CarModel(model).eval()


def export_candidate(root, name, release, checkpoint="best.pt", on_policy=None):
    import torch
    import onnx
    import onnxruntime as ort
    ort.disable_telemetry_events()
    from .training import load_model
    run = root / "runs" / name
    weights = run / checkpoint
    report = read_json(run / "evaluation.json")
    if report.get("weights_sha256") != digest(weights) or not report.get("finite"):
        raise ValueError("請先對要匯出的同一份 checkpoint 執行 evaluate")
    rl_hash = digest(run / on_policy) if on_policy else None
    if report.get("on_policy_sha256") != rl_hash:
        raise ValueError("RL 權重與評估不符；請以相同 --on-policy 再執行 evaluate")
    target = root / "releases" / release
    if target.exists():
        raise FileExistsError("release 名稱已存在，不覆蓋已封存候選模型")
    target.mkdir(parents=True)
    model = load_model(weights, run / on_policy if on_policy else None).cpu().eval()
    wrapper = make_wrapper(model)
    config = model.config
    data = np.load(run / "evaluation.example.npz", allow_pickle=False)
    with torch.no_grad():
        imgs = {k: torch.from_numpy(data["input_" + k][:1]) for k in ("img", "big_img")}
        history = model.vision({k: v.reshape(-1, *v.shape[-3:]) for k, v in imgs.items()})
        history = history.reshape(1, config.history_len, *history.shape[1:])
        tensors = (imgs["img"][:, -1], imgs["big_img"][:, -1], history[:, :-1],
                   torch.from_numpy(data["input_desire_pulse"][:1]),
                   torch.from_numpy(data["input_traffic_convention"][:1, -1]),
                   torch.from_numpy(data["input_action_t"][:1, -1]))
        dense = model({**imgs, "desire_pulse": tensors[3],
                       "traffic_convention": torch.from_numpy(data["input_traffic_convention"][:1]),
                       "action_t": torch.from_numpy(data["input_action_t"][:1])})
        sizes = {k: dense[k][:, -1].numel() for k in OUTPUTS if k != "hidden_state"}
        sizes["hidden_state"] = history[:, -1].numel()
        slices, start = {}, 0
        for k in OUTPUTS:
            slices[k] = slice(start, start + sizes[k]); start += sizes[k]
        reference = wrapper(*tensors).numpy()
        for k in OUTPUTS[:-1]:
            np.testing.assert_allclose(reference[:, slices[k]], dense[k][:, -1].numpy().reshape(1, -1), rtol=1e-4, atol=1e-4)
        output = target / "driving_supercombo.onnx"
        torch.onnx.export(wrapper, tensors, str(output), input_names=list(INPUTS), output_names=["outputs"],
                          opset_version=18, dynamo=False, do_constant_folding=True)
    graph = onnx.load(output)
    onnx.helper.set_model_props(graph, {
        "output_slices": base64.b64encode(pickle.dumps(slices, protocol=4)).decode(),
        "model_checkpoint": digest(weights) + (":" + rl_hash if rl_hash else ""), "distill_commit": DISTILL_SHA,
        "model_trained_fps": "5", "deployment_status": "research_candidate",
    })
    onnx.save_model(graph, output)
    onnx.checker.check_model(str(output))
    session = ort.InferenceSession(str(output), providers=["CPUExecutionProvider"])
    numpy_inputs = {k: v.numpy() for k, v in zip(INPUTS, tensors)}
    result = session.run(None, numpy_inputs)[0]
    np.testing.assert_allclose(result, reference, rtol=2e-3, atol=2e-3)
    maximum = float(np.max(np.abs(result - reference)))
    # Recurrent parity: update each backend from its own previous features.
    torch_state = tensors[2].clone()
    ort_state = numpy_inputs["features_buffer"].copy()
    with torch.no_grad():
        for i in range(8):
            img = imgs["img"][:, i]
            big = imgs["big_img"][:, i]
            tp = wrapper(img, big, torch_state, *tensors[3:]).numpy()
            values = dict(numpy_inputs, img=img.numpy(), big_img=big.numpy(), features_buffer=ort_state)
            op = session.run(None, values)[0]
            np.testing.assert_allclose(tp, op, rtol=2e-3, atol=2e-3)
            maximum = max(maximum, float(np.max(np.abs(tp - op))))
            shape = tuple(tensors[2].shape[2:])
            torch_state = torch.cat((torch_state[:, 1:], torch.from_numpy(tp[:, slices["hidden_state"]].reshape(1, 1, *shape))), dim=1)
            ort_state = np.concatenate((ort_state[:, 1:], op[:, slices["hidden_state"]].reshape(1, 1, *shape)), axis=1)
    np.savez_compressed(target / "parity.npz", **numpy_inputs, expected=reference)
    shutil.copy2(run / "evaluation.json", target / "evaluation.json")
    contract = read_json(LAB / "target_contract.json")
    manifest = {"format": 1, "status": "research_candidate", "release": release,
                "distill_commit": DISTILL_SHA, "sunnypilot_reference": SUNNYPILOT_SHA,
                "target_contract": contract, "weights_sha256": digest(weights),
                "on_policy_sha256": rl_hash,
                "output_sizes": sizes, "input_shapes": {k: list(v.shape) for k, v in numpy_inputs.items()},
                "model_size": [512, 256], "frame_skip": 4,
                "onnx_parity": {"passed": True, "max_absolute_error": maximum, "recurrent_steps": 8},
                "unsupported": ["validated lane-change desire training", "public-road qualification"],
                "files": {p.name: digest(p) for p in target.iterdir() if p.is_file()}}
    write_json(target / "manifest.json", manifest)
    return target
