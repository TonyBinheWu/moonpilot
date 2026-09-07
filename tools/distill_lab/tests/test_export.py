"""Real CPU model + ONNX tests. Random weights are test fixtures, never deliverables."""
from pathlib import Path
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("timm")
pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from supervised.model import Supercombo, SupercomboConfig, VisionConfig, rl_policies
from drive_lab.common import write_json, digest, work_dirs
from drive_lab.exporting import export_candidate, OUTPUTS, make_wrapper
import car


@pytest.mark.parametrize("use_rl", [False, True])
def test_real_model_export_recurrence_and_vehicle_parser(tmp_path, use_rl):
    torch.set_num_threads(2); torch.manual_seed(11)
    root = work_dirs(tmp_path)
    run = root / "runs" / "test"; run.mkdir()
    model = Supercombo(SupercomboConfig(vision=VisionConfig(pretrained=False))).eval()
    weights = run / "best.pt"; torch.save(model.state_dict(), weights)
    on_policy = None
    if use_rl:
        config_rl = SupercomboConfig(vision=VisionConfig(pretrained=False))
        rl_model = Supercombo(config_rl, rl_policies(config_rl))
        on_policy = "on_policy.pt"
        torch.save(rl_model.on_policy_temporal.state_dict(), run / on_policy)
    config = model.config
    rng = np.random.default_rng(11)
    data = {
        "input_img": rng.integers(0, 256, (1, 9, 12, 128, 256), dtype=np.uint8),
        "input_big_img": rng.integers(0, 256, (1, 9, 12, 128, 256), dtype=np.uint8),
        "input_desire_pulse": np.zeros((1, 33, 8), np.float32),
        "input_traffic_convention": np.tile(np.array([1, 0], np.float32), (1, 33, 1)),
        "input_action_t": np.full((1, 33, 2), .2, np.float32),
    }
    np.savez_compressed(run / "evaluation.example.npz", **data)
    write_json(run / "evaluation.json", {"weights_sha256": digest(weights), "finite": True, "test_fixture": True,
               "on_policy_sha256": digest(run / on_policy) if on_policy else None})
    target = export_candidate(root, "test", "test-candidate", on_policy=on_policy)
    manifest = car.validate_bundle(target)
    assert manifest["onnx_parity"]["recurrent_steps"] == 8
    assert manifest["input_shapes"]["features_buffer"][1] == 8
    assert manifest["output_sizes"]["plan"] == 990
    assert manifest["output_sizes"]["action"] == 4
    assert manifest["output_sizes"]["hidden_state"] == 32 * 512
    from openpilot.selfdrive.modeld.parse_model_outputs import Parser
    with np.load(target / "parity.npz") as f: raw = f["expected"]
    start, outputs = 0, {}
    for key in OUTPUTS:
        n = manifest["output_sizes"][key]; outputs[key] = raw[:, start:start + n].copy(); start += n
    parsed = Parser().parse_outputs(outputs)
    assert parsed["plan"].shape == (1, 33, 15)
    assert parsed["lane_lines"].shape == (1, 4, 33, 2)
    assert all(np.isfinite(v).all() for v in parsed.values())


def test_export_refuses_mismatched_evaluation(tmp_path):
    root = work_dirs(tmp_path); run = root / "runs" / "x"; run.mkdir()
    (run / "best.pt").write_bytes(b"new-weights")
    write_json(run / "evaluation.json", {"weights_sha256": "stale", "finite": True})
    with pytest.raises(ValueError): export_candidate(root, "x", "release")
