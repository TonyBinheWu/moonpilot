"""GUI and process tests use isolated fixtures; no Spark, vehicle, or network access."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

pytest.importorskip("streamlit")
import fcntl
from streamlit.testing.v1 import AppTest

from drive_lab.common import LAB, work_dirs, write_json
from drive_lab.jobs import JobStore, locked, supervise
from drive_lab.tasks import validate, cli_command, ssh, execute, device_report_path


def fixture_job(store, kind="inventory", resource="workspace", status="queued", cancellable=True):
    job_id = uuid.uuid4().hex
    with store.connect() as c:
        c.execute("INSERT INTO jobs(id,kind,payload,resource,cancellable,status,created) VALUES(?,?,?,?,?,?,?)",
                  (job_id, kind, "{}", resource, cancellable, status, time.time()))
    return job_id


def until(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError("timed out waiting for local test process")


def test_detached_inventory_job_survives_new_store(tmp_path):
    root = work_dirs(tmp_path)
    first = JobStore(root)
    job_id = first.submit("inventory", {})
    del first
    reopened = JobStore(root)
    until(lambda: reopened.get(job_id)["status"] not in {"queued", "running"})
    result = reopened.get(job_id)
    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0
    assert '"raw": []' in reopened.tail(job_id)


def test_cancellation_stops_owned_process_and_releases_resource(tmp_path):
    store = JobStore(work_dirs(tmp_path))
    job_id = fixture_job(store)
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    worker = threading.Thread(target=supervise, args=(store, job_id, command))
    worker.start()
    try:
        until(lambda: bool(store.get(job_id)["child_pid"]))
        store.cancel(job_id)
    finally:
        worker.join(12)
    job = store.get(job_id)
    assert job["status"] == "cancelled"
    assert not locked(store.directory / (job_id + ".child.lock"))


def test_simultaneous_submissions_cannot_take_same_resource(tmp_path):
    root = work_dirs(tmp_path)
    store = JobStore(root)
    fixture_job(store)
    with pytest.raises(ValueError, match="同類工作"):
        JobStore(root).submit("inventory", {})


def test_orphan_keeps_lock_until_actual_child_exits(tmp_path):
    store = JobStore(work_dirs(tmp_path))
    job = fixture_job(store, status="running")
    lease = (store.directory / (job + ".child.lock")).open("a")
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store.reconcile()
    assert store.get(job)["status"] == "orphaned"
    with pytest.raises(ValueError, match="同類工作"): store.submit("inventory", {})
    lease.close()  # Only release the resource after all execution lease holders exit.
    store.reconcile()
    assert store.get(job)["status"] == "interrupted"


def test_car_job_cannot_be_cancelled(tmp_path):
    store = JobStore(work_dirs(tmp_path))
    job = fixture_job(store, kind="car-activate", resource="device", cancellable=False)
    with pytest.raises(ValueError, match="不可"): store.cancel(job)
    assert not store.get(job)["cancel_requested"]


@pytest.mark.parametrize("kind,payload", [
    ("shell", {"command": "id"}), ("inventory", {"command": "id"}),
    ("fetch-device", {"host": "comma;id", "route": "test"}),
    ("fetch-device", {"host": "-oProxyCommand=id", "route": "test"}),
    ("fetch-public", {"segments": ["../x"]}),
    ("device-info", {"host": "comma", "repo": "/data/../secret"}),
    ("evaluate", {"name": "test", "checkpoint": "../last.pt"}),
])
def test_reject_untrusted_operation_inputs(tmp_path, kind, payload):
    with pytest.raises(ValueError): validate(work_dirs(tmp_path), kind, payload)


def test_activation_requires_exact_target_and_candidate(tmp_path):
    root = work_dirs(tmp_path)
    base = {"host": "comma", "repo": "/data/openpilot", "target_id": "a" * 64, "release": "ev6-r001",
            "closed_course": True, "confirmation": "comma/ev6-r002"}
    with pytest.raises(ValueError, match="完整輸入"): validate(root, "car-activate", base)
    base["confirmation"] = "comma/ev6-r001"
    assert validate(root, "car-activate", base)["release"] == "ev6-r001"


def test_remote_identity_mismatch_prevents_all_writes(tmp_path, monkeypatch):
    root = work_dirs(tmp_path)
    calls = []
    monkeypatch.setattr("drive_lab.tasks.probe", lambda *args: {"target_id": "b" * 64, "offroad": True})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="身分"):
        execute(root, "car-verify-active", {"host": "comma", "repo": "/data/openpilot", "target_id": "a" * 64})
    assert calls == []


def test_remote_non_offroad_prevents_all_writes(tmp_path, monkeypatch):
    root = work_dirs(tmp_path)
    calls = []
    monkeypatch.setattr("drive_lab.tasks.probe", lambda *args: {"target_id": "a" * 64, "offroad": False})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="offroad"):
        execute(root, "car-compile", {"host": "comma", "repo": "/data/openpilot", "target_id": "a" * 64,
                                     "release": "ev6-r001", "camera": "1928x1208"})
    assert calls == []


def test_remote_command_preserves_argv_and_enforces_ssh_host_keys():
    import shlex
    command = ["python3", "-c", "print('value with spaces')"]
    output = ssh("comma", command)
    assert shlex.split(output[-1]) == command
    assert "BatchMode=yes" in output and "StrictHostKeyChecking=yes" in output


def test_worker_errors_are_persisted(tmp_path):
    store = JobStore(work_dirs(tmp_path))
    job = fixture_job(store)
    supervise(store, job, [sys.executable, "-c", "raise SystemExit(7)"])
    assert store.get(job)["exit_code"] == 7
    assert store.get(job)["status"] == "failed"


def test_surviving_descendant_keeps_workspace_busy(tmp_path):
    store = JobStore(work_dirs(tmp_path))
    job = fixture_job(store)
    script = "import os,subprocess,sys; fd=int(os.environ['DISTILL_LAB_JOB_LOCK_FD']); subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'],pass_fds=(fd,))"
    supervise(store, job, [sys.executable, "-c", script])
    assert store.get(job)["status"] == "orphaned"
    with pytest.raises(ValueError, match="同類工作"): store.submit("inventory", {})
    until(lambda: not locked(store.directory / (job + ".child.lock")))
    store.reconcile()
    assert store.get(job)["status"] == "interrupted"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DISTILL_LAB_WORK", str(tmp_path))
    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    return AppTest.from_file(str(LAB / "webui.py"), default_timeout=15)


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_all_empty_pages_render_without_starting_any_jobs(app):
    app.run()
    for page in ["總覽", "資料", "訓練", "評估與匯出", "模擬與微調", "車端部署", "工作紀錄"]:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert not app.error
    assert not JobStore(Path(os.environ["DISTILL_LAB_WORK"])).list()


def test_gui_environment_button_submits_real_operation(app, monkeypatch):
    calls = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: calls.append((kind, payload)) or "a" * 32)
    app.run()
    button(app, "執行環境檢查").click().run()
    assert calls == [("doctor", {})]
    assert app.success and not app.exception


def test_gui_public_download_preserves_selected_ids(app, monkeypatch):
    calls = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: calls.append((kind, payload)) or "b" * 32)
    app.run().sidebar.radio[0].set_value("資料").run()
    next(r for r in app.radio if r.label == "資料來源").set_value("公開資料集").run()
    app.text_area[0].set_value("a" * 32 + "\n" + "b" * 32)
    button(app, "下載片段").click().run()
    assert calls == [("fetch-public", {"segments": ["a" * 32, "b" * 32], "revision": "main"})]


def test_gui_train_form_routes_parameters(app, monkeypatch):
    calls = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: calls.append((kind, payload)) or "c" * 32)
    app.run().sidebar.radio[0].set_value("訓練").run()
    next(n for n in app.number_input if n.label == "目標總步數").set_value(250)
    button(app, "開始訓練").click().run()
    assert calls[0][0] == "train" and calls[0][1]["steps"] == 250
    assert calls[0][1]["resume"] is False


def test_gui_evaluation_export_and_comparison_with_local_fixtures(app, monkeypatch):
    root = work_dirs(Path(os.environ["DISTILL_LAB_WORK"]))
    run = root / "runs/test-run"; run.mkdir()
    (run / "best.pt").write_bytes(b"test fixture only; never loaded")
    write_json(run / "evaluation.json", {"checkpoint": "best.pt", "samples": 2, "student": {"trajectory_ade_m": 1.0},
                                        "teacher": {"trajectory_ade_m": .8}, "finite": True})
    calls = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: calls.append((kind, payload)) or "d" * 32)
    app.run().sidebar.radio[0].set_value("評估與匯出").run()
    assert not app.error and not app.exception
    button(app, "評估選定權重").click().run()
    button(app, "驗證並匯出候選").click().run()
    assert [r[0] for r in calls] == ["evaluate", "export"]
    assert all(r[1]["checkpoint"] == "best.pt" for r in calls)


def test_gui_deployment_uses_reported_device_identity(app, monkeypatch):
    root = work_dirs(Path(os.environ["DISTILL_LAB_WORK"]))
    write_json(device_report_path(root, "comma"), {"host": "comma", "repo": "/data/openpilot", "checked_at": time.time(),
        "hostname": "test-device", "serial": "fixture", "target_id": "e" * 64, "offroad": True, "commit": "test",
        "routes": [], "candidates": {"test-release": {"benchmark": None}}, "backups": [], "active": None})
    calls = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: calls.append((kind, payload)) or "e" * 32)
    app.run().sidebar.radio[0].set_value("車端部署").run()
    assert not app.error and not app.exception
    button(app, "在 Chestnut 編譯").click().run()
    assert calls[0][0] == "car-compile"
    assert calls[0][1]["target_id"] == "e" * 64
    assert calls[0][1]["release"] == "test-release"
