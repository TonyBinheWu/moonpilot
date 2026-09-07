from pathlib import Path
import json
import numpy as np
import pytest

from drive_lab.common import route_split, checked_id, digest, write_json, work_dirs
from drive_lab.localization import check_labels
from drive_lab.acquire import copy_segment, fetch_device
import car


def fixture_labels():
    t = np.arange(600) * .05 + 100
    s = np.zeros((600, 43)); s[:, 0] = 6.4e6; s[:, 3] = 1; s[:, 7] = 10
    f = {"fcamera/t": t, "ecamera/t": t + .001, "fcamera/frame_count": np.array([600]), "ecamera/frame_count": np.array([600])}
    l = {"frame_t": t.copy(), "frame_states": s, "rpy": np.zeros(3), "wide_from_device_euler": np.zeros(3)}
    return f, l


def test_route_split_has_no_adjacent_segment_leakage():
    rows = [{"id": f"{r}{i}", "route": r} for r in "abcd" for i in range(8)]
    result = route_split(rows)
    assert result == route_split(list(reversed(rows))) or set(result["train"]) == set(route_split(list(reversed(rows)))["train"])
    for route in "abcd":
        ids = {r["id"] for r in rows if r["route"] == route}
        assert ids <= set(result["train"]) or ids <= set(result["val"])
    assert set(result["train"]).isdisjoint(result["val"])


def test_one_route_cannot_claim_validation():
    with pytest.raises(ValueError): route_split([{"id": "a", "route": "one"}])


@pytest.mark.parametrize("value", ["../x", "/tmp/x", "a" * 31, "g" * 32, "A" * 32])
def test_ids_cannot_escape_dataset(value):
    with pytest.raises(ValueError): checked_id(value)


def test_valid_label_check():
    f, l = fixture_labels()
    assert check_labels(f, l)["frames"] == 600


@pytest.mark.parametrize("problem", ["nan", "quat", "time", "gap", "pair", "labels", "fps", "shape"])
def test_bad_labels_are_rejected(problem):
    f, l = fixture_labels()
    if problem == "nan": l["frame_states"][3, 0] = np.nan
    if problem == "quat": l["frame_states"][3, 3] = 2
    if problem == "time": f["fcamera/t"][3] = f["fcamera/t"][2]
    if problem == "gap": f["fcamera/t"][300:] += 1
    if problem == "pair": f["ecamera/t"] += .03
    if problem == "labels": l["frame_t"] += .02
    if problem == "fps": f["fcamera/t"] = np.arange(600) / 30 + 100
    if problem == "shape": l["frame_states"] = l["frame_states"][:, :42]
    with pytest.raises(ValueError): check_labels(f, l)


def test_input_requires_raw_video_and_logs(tmp_path):
    source = tmp_path / "input"; source.mkdir(); (source / "qcamera.ts").write_bytes(b"screen")
    with pytest.raises(ValueError): copy_segment(work_dirs(tmp_path / "work"), source, "route")


def test_raw_copy_is_traceable_and_nonoverwriting(tmp_path):
    source = tmp_path / "route--0"; source.mkdir()
    for name in ("rlog.zst", "fcamera.hevc", "ecamera.hevc"): (source / name).write_bytes(name.encode())
    root = work_dirs(tmp_path / "work")
    target = copy_segment(root, source, "route")
    manifest = json.loads((target / "source.json").read_text())
    assert manifest["raw_sha256"]["rlog.zst"] == digest(source / "rlog.zst")
    with pytest.raises(FileExistsError): copy_segment(root, source, "route")


@pytest.mark.parametrize("host,route", [("-oProxyCommand=evil", "a"), ("comma", "$(id)"), ("comma; id", "a")])
def test_ssh_inputs_are_not_shell_commands(tmp_path, host, route):
    with pytest.raises(ValueError): fetch_device(tmp_path, host, route)


def test_tampered_candidate_rejected(tmp_path):
    p = tmp_path
    (p / "driving_supercombo.onnx").write_bytes(b"candidate")
    (p / "parity.npz").write_bytes(b"fixture")
    write_json(p / "evaluation.json", {"weights_sha256": "abc", "finite": True})
    write_json(p / "manifest.json", {"format": 1, "onnx_parity": {"passed": True}, "weights_sha256": "abc",
               "files": {n: digest(p / n) for n in ("driving_supercombo.onnx", "parity.npz", "evaluation.json")}})
    car.validate_bundle(p)
    (p / "driving_supercombo.onnx").write_bytes(b"changed")
    with pytest.raises(ValueError): car.validate_bundle(p)


def test_restore_preserves_original_chunk_files(tmp_path, monkeypatch):
    monkeypatch.setattr(car, "offroad", lambda: None)
    monkeypatch.setattr(car, "BASE", tmp_path)
    repo = tmp_path / "repo"; models = repo / "openpilot/selfdrive/modeld/models"; models.mkdir(parents=True)
    backup = tmp_path / "backup"; backup.mkdir()
    names = ["big_driving_tinygrad.pkl.chunkmanifest", "big_driving_tinygrad.pkl.chunk01of01", "big_driving_supercombo.onnx"]
    for name in names: (backup / name).write_bytes(("original:" + name).encode())
    write_json(backup / "backup.json", {"files": {n: car.sha(backup / n) for n in names}})
    (models / "big_driving_tinygrad.pkl.chunk01of02").write_bytes(b"new")
    (models / "driving_tinygrad.pkl").write_bytes(b"stock-small")
    car.restore(backup, repo)
    assert not (models / "big_driving_tinygrad.pkl.chunk01of02").exists()
    assert (models / "driving_tinygrad.pkl").read_bytes() == b"stock-small"
    for n in names: assert (models / n).read_bytes() == (backup / n).read_bytes()


def test_contract_refuses_changed_runtime(tmp_path):
    (tmp_path / "model.py").write_bytes(b"changed")
    with pytest.raises(ValueError): car.contract_check(tmp_path, {"target_contract": {"files": {"model.py": "oldhash"}}})


def test_activation_requires_offroad(monkeypatch):
    monkeypatch.setattr(car, "offroad", lambda: (_ for _ in ()).throw(RuntimeError("onroad")))
    with pytest.raises(RuntimeError): car.activate(Path("/missing"), Path("/missing"), True)


def test_camera_profiles_are_not_interchangeable():
    from openpilot.common.transformations.camera import DEVICE_CAMERAS
    tizi = DEVICE_CAMERAS[("tizi", "ar0231")]
    mici = DEVICE_CAMERAS[("mici", "os04c10")]
    assert tizi.narrow_road.size == (1928, 1208)
    assert mici.narrow_road.size == (1344, 760)
    assert not np.allclose(tizi.narrow_road.intrinsics, mici.narrow_road.intrinsics)


def test_notebook_controls_render_without_launching_training(tmp_path, monkeypatch):
    pytest.importorskip("ipywidgets")
    import IPython.display
    from drive_lab.visualization import panel
    displayed = []
    monkeypatch.setattr(IPython.display, "display", lambda x: displayed.append(x))
    panel(tmp_path)
    assert len(displayed) == 1
    assert displayed[0].children[-2].children[0].description == "開始訓練"
