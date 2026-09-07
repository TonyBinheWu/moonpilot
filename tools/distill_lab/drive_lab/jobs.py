"""Durable local jobs; browser sessions never own training processes."""
from contextlib import contextmanager
from pathlib import Path
import json
import fcntl
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from .common import LAB

ACTIVE = ("queued", "running", "stopping", "orphaned")


def locked(path):
    """A kernel-held lock survives PID reuse and PID namespace differences."""
    if not path.exists(): return False
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        handle.close()


class JobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "jobs.sqlite3"
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                resource TEXT NOT NULL, cancellable INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, started REAL, finished REAL, exit_code INTEGER,
                worker_pid INTEGER, worker_created REAL, child_pid INTEGER, child_created REAL,
                cancel_requested INTEGER DEFAULT 0, error TEXT DEFAULT '')""")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def get(self, job_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("找不到工作")
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def update(self, job_id, **values):
        allowed = {"status", "started", "finished", "exit_code", "worker_pid", "worker_created",
                   "child_pid", "child_created", "cancel_requested", "error"}
        if not values or not set(values) <= allowed:
            raise ValueError("工作欄位錯誤")
        with self.connect() as c:
            c.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                      (*values.values(), job_id))

    def reconcile(self):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM jobs WHERE status IN ('queued','running','stopping','orphaned')").fetchall()
            for row in rows:
                # Leave a newly submitted worker time to start and publish its identity.
                if row["status"] == "queued" and time.time() - row["created"] < 30:
                    continue
                if locked(self.directory / (row["id"] + ".worker.lock")):
                    continue
                if locked(self.directory / (row["id"] + ".child.lock")):
                    c.execute("UPDATE jobs SET status='orphaned', error=? WHERE id=?",
                              ("背景管理程序已中斷，但工作程序仍存在；保留資源鎖。", row["id"]))
                else:
                    c.execute("UPDATE jobs SET status='interrupted', finished=?, error=? WHERE id=?",
                              (time.time(), "工作程序已離開，未取得正常結束紀錄；不會自動重跑。", row["id"]))

    def list(self, limit=100):
        self.reconcile()
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM jobs ORDER BY CASE WHEN status IN ('queued','running','stopping','orphaned') THEN 0 ELSE 1 END, created DESC LIMIT ?", (limit,))]
        return [self.get(i) for i in ids]

    def submit(self, kind, payload):
        from .tasks import validate, resource_for
        payload = validate(self.root, kind, payload)
        resource = resource_for(kind)
        cancellable = not kind.startswith("car-")
        self.reconcile()
        job_id = uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            busy = c.execute("SELECT kind,resource FROM jobs WHERE status IN ('queued','running','stopping','orphaned')").fetchall()
            if any(r["resource"] == resource for r in busy):
                raise ValueError("同類工作正在執行，請先等待完成或到工作紀錄停止它。")
            service = any(r["kind"] == "worldmodel-server" for r in busy)
            if kind in {"train", "evaluate", "export"} and service:
                raise ValueError("請先停止世界模型服務，釋放 GPU 資源。")
            if kind == "worldmodel-server" and any(r["resource"] == "workspace" for r in busy):
                raise ValueError("先等待目前資料／訓練工作完成，再啟動世界模型服務。")
            c.execute("INSERT INTO jobs(id,kind,payload,resource,cancellable,status,created) VALUES(?,?,?,?,?,'queued',?)",
                      (job_id, kind, json.dumps(payload, ensure_ascii=False), resource, cancellable, time.time()))
        env = os.environ.copy()
        env.update(ORT_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_TELEMETRY="1", WANDB_MODE="offline",
                   PYTHONUNBUFFERED="1")
        try:
            with self.log_path(job_id).open("ab", buffering=0) as log:
                p = subprocess.Popen([sys.executable, str(LAB / "gui_worker.py"), "--work", str(self.root), "--job", job_id],
                                     cwd=LAB, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
            # The worker publishes its own PID; avoid a parent update racing with completion.
            return job_id
        except Exception as e:
            self.update(job_id, status="failed", finished=time.time(), error=str(e))
            raise

    def cancel(self, job_id):
        job = self.get(job_id)
        if not job["cancellable"]:
            raise ValueError("車端操作開始後不可從網頁中斷；請等待結果並檢查車端狀態。")
        if job["status"] not in ACTIVE:
            return
        if job["status"] == "orphaned":
            raise ValueError("管理程序已中斷，工作仍持有資源鎖；請在 Spark 終端機核對該工作程序。介面不會猜測 PID 並終止其他程序。")
        self.update(job_id, cancel_requested=1)

    def log_path(self, job_id):
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("工作 ID 格式錯誤")
        return self.directory / (job_id + ".log")

    def tail(self, job_id, size=24000):
        path = self.log_path(job_id)
        if not path.exists():
            return "等待工作啟動。"
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - size))
            return f.read(size).decode("utf-8", errors="replace")


def supervise(store, job_id, command):
    """Own one process group and persist its terminal result, including cancellation."""
    p = None
    worker_lock = (store.directory / (job_id + ".worker.lock")).open("a")
    fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child_lock = None
    try:
        store.update(job_id, worker_pid=os.getpid(), worker_created=time.time(), status="running", started=time.time())
        if store.get(job_id)["cancel_requested"]:
            store.update(job_id, status="cancelled", finished=time.time())
            return
        child_lock = (store.directory / (job_id + ".child.lock")).open("a")
        fcntl.flock(child_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = os.environ.copy()
        env["DISTILL_LAB_JOB_LOCK_FD"] = str(child_lock.fileno())
        p = subprocess.Popen(command, cwd=LAB, stdin=subprocess.DEVNULL, start_new_session=True,
                             env=env, pass_fds=(child_lock.fileno(),))
        child_lock.close(); child_lock = None  # Only descendants now own the execution lock.
        store.update(job_id, child_pid=p.pid, child_created=time.time())
        stopping = None
        while p.poll() is None:
            job = store.get(job_id)
            if job["cancel_requested"] and job["cancellable"]:
                if stopping is None:
                    store.update(job_id, status="stopping")
                    os.killpg(p.pid, signal.SIGINT)
                    stopping = time.monotonic()
                elif time.monotonic() - stopping > 45:
                    os.killpg(p.pid, signal.SIGKILL)
                elif time.monotonic() - stopping > 30:
                    os.killpg(p.pid, signal.SIGTERM)
            time.sleep(.25)
        status = "cancelled" if stopping is not None else "succeeded" if p.returncode == 0 else "failed"
        if locked(store.directory / (job_id + ".child.lock")):
            store.update(job_id, status="orphaned", exit_code=p.returncode,
                         error="工作入口已離開，但子程序仍持有執行鎖；保留資源，請在 Spark 核對。")
            return
        store.update(job_id, status=status, exit_code=p.returncode, finished=time.time())
    except BaseException as e:
        # Unexpected manager death must not silently release the workspace for a second trainer.
        if p is not None and p.poll() is None:
            store.update(job_id, status="orphaned", error=str(e))
        else:
            store.update(job_id, status="failed", finished=time.time(), error=str(e))
        raise
    finally:
        if child_lock is not None: child_lock.close()
        worker_lock.close()
