from pathlib import Path
import json
import numpy as np


def training_curves(run):
    import matplotlib.pyplot as plt
    path = Path(run) / "metrics.jsonl"
    if not path.exists():
        print("尚無訓練結果。開始訓練後按重新整理；此介面不產生展示用假數據。")
        return
    records = []
    for line in path.read_text().splitlines():
        try: records.append(json.loads(line))
        except json.JSONDecodeError: continue  # a live writer may be midway through its last line
    if not records:
        return
    # Resumed steps can repeat after the last saved checkpoint.
    by_step = {r["step"]: r for r in records}
    records = [by_step[k] for k in sorted(by_step)]
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.5))
    for ax, key, title in zip(axes, ["loss", "step_seconds", "gpu_peak_allocated_gb"],
                              ["Training loss", "Seconds per step", "GPU allocation peak (GB)"]):
        ax.plot([r["step"] for r in records], [r[key] for r in records], color="#0f766e")
        ax.set_title(title); ax.set_xlabel("Step"); ax.grid(alpha=0.2)
    fig.tight_layout(); plt.show()


def compare_example(run):
    import matplotlib.pyplot as plt
    file = Path(run) / "evaluation.example.npz"
    if not file.exists():
        print("先執行 evaluate，才會顯示真實驗證樣本。")
        return
    with np.load(file, allow_pickle=False) as d:
        fig, axes = plt.subplots(1, 3, figsize=(15, 4))
        axes[0].imshow(d["rgb"][0, ..., :3]); axes[0].set_title("Narrow camera · model crop"); axes[0].axis("off")
        axes[1].imshow(d["rgb"][0, ..., 3:]); axes[1].set_title("Wide camera · model crop"); axes[1].axis("off")
        for key, label, color in [("target_plan", "Recorded path", "#111827"),
                                   ("teacher_plan", "Teacher", "#2563eb"), ("student_plan", "Student", "#f97316")]:
            xy = d[key][0, :, :2]
            axes[2].plot(xy[:, 1], xy[:, 0], label=label, color=color)
        axes[2].set_xlabel("Right (m)"); axes[2].set_ylabel("Forward (m)")
        axes[2].set_title("Open-loop trajectory comparison"); axes[2].legend(); axes[2].grid(alpha=.2)
        fig.tight_layout(); plt.show()


def segment_preview(directory, frame=200):
    import cv2
    import matplotlib.pyplot as plt
    from safetensors.numpy import load_file
    directory = Path(directory)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for ax, camera in zip(axes, ("fcamera", "ecamera")):
        cap = cv2.VideoCapture(str(directory / (camera + ".hevc")))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ok, image = cap.read(); cap.release()
        if ok: ax.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        ax.set_title(camera + " · raw"); ax.axis("off")
    labels = load_file(directory / "localizer.safetensors")
    speed = np.linalg.norm(labels["frame_states"][:, 7:10], axis=1) * 3.6
    axes[2].plot(labels["frame_t"] - labels["frame_t"][0], speed, color="#0f766e")
    axes[2].set_xlabel("Seconds"); axes[2].set_ylabel("km/h"); axes[2].set_title("Logged speed")
    fig.tight_layout(); plt.show()


def panel(root):
    """Local notebook controls. Only starts training; never activates a car model."""
    import ipywidgets as w
    from IPython.display import display, clear_output
    import subprocess
    import sys
    from .common import LAB
    root = Path(root)
    name = w.Text(value="ev6-001", description="實驗名稱")
    steps = w.IntText(value=100, description="總步數")
    batch = w.IntText(value=1, description="Batch")
    resume = w.Checkbox(value=False, description="從上次 checkpoint 續訓")
    start = w.Button(description="開始訓練", button_style="success")
    refresh = w.Button(description="重新整理曲線")
    stop = w.Button(description="停止訓練", button_style="warning")
    output = w.Output()
    process = {"handle": None}
    def launch(_):
        with output:
            if process["handle"] and process["handle"].poll() is None:
                print("已有訓練執行中"); return
            command = [sys.executable, str(LAB / "lab.py"), "--work", str(root), "train", "--name", name.value,
                       "--steps", str(steps.value), "--batch-size", str(batch.value)]
            if resume.value: command.append("--resume")
            log = root / "reports" / "training-console.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                process["handle"] = subprocess.Popen(command, stdout=f, stderr=subprocess.STDOUT)
            print(f"訓練已啟動，PID={process['handle'].pid}；完整訊息：{log}")
    def reload(_):
        with output:
            clear_output(wait=True)
            handle = process["handle"]
            if handle: print("執行中" if handle.poll() is None else f"已結束，exit={handle.returncode}")
            training_curves(root / "runs" / name.value)
            log = root / "reports" / "training-console.log"
            if log.exists(): print("\n".join(log.read_text().splitlines()[-5:]))
    def halt(_):
        import signal
        if process["handle"] and process["handle"].poll() is None:
            process["handle"].send_signal(signal.SIGINT)
            with output: print("已送出停止訊號；續訓會從最後已保存的 checkpoint 開始。")
    start.on_click(launch); refresh.on_click(reload); stop.on_click(halt)
    display(w.VBox([w.HTML("<h3>駕駛模型訓練</h3><p>顯示本機實驗資料。先完成匯入、標籤檢查與行程切分。</p>"),
                    name, w.HBox([steps, batch]), resume, w.HBox([start, refresh, stop]), output]))
