"""Local browser interface for the real driving-model pipeline."""
import os
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["WANDB_MODE"] = "offline"

from pathlib import Path
import json
import shutil
import sys
import time

LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(LAB / "upstream"))

import pandas as pd
import streamlit as st

from drive_lab.common import read_json, work_dirs, write_json
from drive_lab.jobs import ACTIVE, JobStore
from drive_lab.tasks import device_report_path, host_name, repo_path

st.set_page_config(page_title="駕駛模型實驗室", page_icon="🛣️", layout="wide")
st.markdown("""<style>
.block-container {max-width:1440px;padding-top:2rem;padding-bottom:3rem}
h1 {font-size:2rem!important;letter-spacing:-.025em}
[data-testid="stMetric"] {border-top:3px solid #087f8c;padding:1rem;background:#f0f5f8;border-radius:.4rem}
[data-testid="stMetricValue"] {font-variant-numeric:tabular-nums}
[data-testid="stCaptionContainer"] {font-size:.9rem}
button,p,label {font-size:1rem}
</style>""", unsafe_allow_html=True)

ROOT = work_dirs(Path(os.environ.get("DISTILL_LAB_WORK", str(LAB / "work"))))
JOBS = JobStore(ROOT)
STATUS = {"queued": "準備啟動", "running": "執行中", "stopping": "正在停止", "succeeded": "已完成",
          "failed": "失敗", "cancelled": "已停止", "interrupted": "程序中斷", "orphaned": "需檢查背景程序"}
LABELS = {"doctor": "環境檢查", "inventory": "資料清單", "fetch-teacher": "下載教師模型",
          "fetch-device": "下載裝置行程", "fetch-public": "下載公開片段", "prepare": "匯入本地片段",
          "prepare-route": "定位整趟行程", "localize": "重新定位片段", "split": "建立資料切分",
          "train": "訓練", "evaluate": "評估", "export": "匯出候選", "device-info": "讀取裝置",
          "worldmodel-download": "下載世界模型", "worldmodel-server": "世界模型服務", "simulate": "互動模擬",
          "rl-train": "模擬微調", "car-stage": "傳送與暫存候選", "car-compile": "Chestnut 編譯",
          "car-benchmark": "車端數值與延遲測試", "car-activate": "啟用候選", "car-restore": "回復備份",
          "car-verify-active": "核對現用檔案"}


def read_optional(path, default=None):
    try: return read_json(path)
    except FileNotFoundError: return default
    except (OSError, ValueError) as e:
        st.warning(f"無法讀取 {Path(path).name}：{e}")
        return default


def submit(kind, **payload):
    try:
        job_id = JOBS.submit(kind, payload)
        st.session_state["selected_job"] = job_id
        st.success(f"{LABELS[kind]}已啟動。可在「工作紀錄」查看進度，關閉網頁不會中斷工作。")
        return job_id
    except Exception as e:
        st.error(str(e))
        return None


def folders(name):
    return sorted(p.name for p in (ROOT / name).iterdir() if p.is_dir() and not p.is_symlink())


def metrics(run):
    path = ROOT / "runs" / run / "metrics.jsonl"
    if not path.exists(): return pd.DataFrame()
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 4000000))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    records = []
    for line in lines:
        try:
            record = json.loads(line)
            if isinstance(record, dict) and "step" in record: records.append(record)
        except ValueError: pass
    if not records: return pd.DataFrame()
    return pd.DataFrame(records).drop_duplicates("step", keep="last").sort_values("step")


@st.fragment(run_every=3)
def live_jobs():
    active = [j for j in JOBS.list() if j["status"] in ACTIVE]
    if active:
        st.info(" · ".join(f"{LABELS.get(j['kind'], j['kind'])}：{STATUS[j['status']]}" for j in active))
    else:
        st.caption("目前沒有背景工作。")


def run_selector(key):
    runs = folders("runs")
    if not runs:
        st.info("尚未建立實驗。先完成資料檢查與切分，再到「訓練」開始。")
        return None, None
    selected = st.selectbox("實驗", runs, key=key + "_run")
    checkpoints = sorted(p.name for p in (ROOT / "runs" / selected).glob("*.pt")
                         if p.name not in {"resume.pt", "on_policy.pt"} and not p.is_symlink())
    if not checkpoints:
        st.info("這個實驗尚無可用的模型 checkpoint。")
        return selected, None
    default = checkpoints.index("best.pt") if "best.pt" in checkpoints else 0
    checkpoint = st.selectbox("Checkpoint", checkpoints, index=default, key=key + "_checkpoint")
    return selected, checkpoint


def home():
    st.title("駕駛模型實驗室")
    st.caption("資料取得、模型訓練與車端候選管理 · DGX Spark")
    columns = st.columns(4)
    dataset = read_optional(ROOT / "dataset.json", {})
    for c, label, value in zip(columns, ["資料片段", "實驗", "候選模型", "可用磁碟"],
                                [len(folders("data")), len(folders("runs")), len(folders("releases")),
                                 f"{shutil.disk_usage(ROOT).free / 2**30:.0f} GB"]):
        c.metric(label, value)
    live_jobs()
    left, right = st.columns([1.3, 1])
    with left:
        st.subheader("開始一次實驗")
        st.write("從左側「資料」選擇裝置行程或公開片段。完成定位與切分後，就能設定訓練。")
        checks = [("環境檢查", bool(read_optional(ROOT / "reports/environment.json", {}).get("ready_for_smoke_test"))),
                  ("教師模型", (ROOT / "models/big_driving_supercombo.onnx").is_file()),
                  ("訓練資料", bool(dataset.get("train"))), ("獨立驗證組", bool(dataset.get("val")))]
        st.dataframe(pd.DataFrame([{"項目": k, "狀態": "已就緒" if v else "尚待完成"} for k, v in checks]),
                     hide_index=True, use_container_width=True)
        if st.button("執行環境檢查", type="primary"): submit("doctor")
        if st.button("下載固定版本教師模型", disabled=checks[1][1]): submit("fetch-teacher")
    with right:
        st.subheader("環境資訊")
        environment = read_optional(ROOT / "reports/environment.json")
        if environment:
            st.write("GPU：" + environment.get("gpu", "未偵測到可用 GPU"))
            st.write("架構：" + environment.get("architecture", "未知"))
            st.write("CUDA：" + str(environment.get("cuda_version", "未取得")))
            with st.expander("查看完整檢查結果"): st.json(environment)
        else: st.info("先執行環境檢查。尚未檢查時不推測 GPU 或可訓練狀態。")
        st.warning("目前是研究流程。候選模型需完成數值、延遲與封閉場地驗證，才能判斷實際表現。")
        st.caption("訓練資料位置")
        st.code(str(ROOT), language=None)


def device_controls(key):
    settings = read_optional(ROOT / "gui-settings.json", {})
    with st.form(key + "_connection"):
        a, b = st.columns(2)
        host = a.text_input("裝置 SSH 別名", value=settings.get("host", "comma"), key=key + "_host")
        repo = b.text_input("車端專案位置", value=settings.get("repo", "/data/openpilot"), key=key + "_repo")
        connect = st.form_submit_button("讀取裝置與行程")
    if connect:
        try:
            host_name(host); repo_path(repo)
            write_json(ROOT / "gui-settings.json", {"host": host, "repo": repo})
            submit("device-info", host=host, repo=repo)
        except ValueError as e: st.error(str(e))
    report = read_optional(device_report_path(ROOT, host)) if host else None
    if report and report.get("repo") != repo: report = None
    if report:
        age = max(0, int(time.time() - report["checked_at"]))
        st.caption(f"裝置 {report['hostname']} · 識別 {report.get('serial') or '未取得'} · {age} 秒前讀取")
    else:
        st.info("先讀取裝置。SSH 金鑰與主機指紋需已在 Spark 設定完成。")
    return host, repo, report


@st.cache_data(show_spinner=False, max_entries=12)
def preview_frame(path, frame, modified):
    import cv2
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
    ok, value = cap.read(); cap.release()
    if not ok: raise ValueError("無法解碼指定影格")
    return cv2.cvtColor(value, cv2.COLOR_BGR2RGB)


def data_page():
    st.title("資料")
    st.caption("保留原始雙鏡頭與完整 rlog；依實際攝影機、時間及定位標籤檢查。")
    acquisition, processing, browse = st.tabs(["取得資料", "定位與切分", "預覽與品質"])
    with acquisition:
        source = st.radio("資料來源", ["Comma 裝置", "Spark 本地資料夾", "公開資料集"], horizontal=True)
        if source == "Comma 裝置":
            host, _, report = device_controls("data")
            if report and report.get("routes"):
                route = st.selectbox("裝置行程", report["routes"])
                if st.button("下載這趟行程", type="primary"): submit("fetch-device", host=host, route=route)
            elif report: st.info("裝置沒有找到可讀取的行程。")
        elif source == "Spark 本地資料夾":
            with st.form("import_local"):
                source_path = st.text_input("原始片段資料夾（Spark 絕對路徑）", placeholder="/home/tony/drives/行程--0")
                route = st.text_input("完整行程名稱（不含最後的片段序號）")
                method = st.selectbox("定位來源", ["raw-gnss", "logged-ecef"], key="local_method")
                st.caption("資料夾需含 fcamera.hevc、ecamera.hevc 與完整 rlog。logged-ecef 只適用於已有有效 ECEF 狀態的紀錄。")
                if st.form_submit_button("匯入並檢查", type="primary"):
                    submit("prepare", source=source_path, route=route, method=method)
        else:
            with st.form("public_data"):
                raw = st.text_area("公開片段 ID（每行一個）", placeholder="2333e255f1de1fe83ea5975b24a3cc67")
                revision = st.text_input("資料版本", "main")
                st.caption("只下載指定片段。匿名公開片段無法保證來自不同原始行程。")
                if st.form_submit_button("下載片段", type="primary"):
                    submit("fetch-public", segments=raw.split(), revision=revision)
    with processing:
        raw_routes = sorted({n.rsplit("--", 1)[0] for n in folders("raw") if n.rsplit("--", 1)[-1].isdigit()})
        st.subheader("定位行程")
        if raw_routes:
            with st.form("prepare_route"):
                route = st.selectbox("已下載行程", raw_routes)
                method = st.selectbox("定位來源", ["raw-gnss", "logged-ecef"])
                if st.form_submit_button("定位與檢查整趟行程"): submit("prepare-route", route=route, method=method)
        else: st.info("尚未下載原始行程。公開資料已附定位標籤，可直接建立切分。")
        ids = folders("data")
        if ids:
            with st.expander("重新處理定位失敗的片段"):
                segment = st.selectbox("片段", ids, key="retry_segment")
                method = st.selectbox("重試定位來源", ["raw-gnss", "logged-ecef"], key="retry_method")
                if st.button("重新定位"): submit("localize", segment=segment, method=method)
        report = read_optional(ROOT / "reports/prepare-route.json")
        if report:
            with st.expander("上次行程檢查結果", expanded=any("error" in r for r in report)):
                st.dataframe(pd.DataFrame([{"來源": r["source"], "結果": r.get("error", "完成"), "片段": r.get("id", "")}
                                           for r in report]), hide_index=True, use_container_width=True)
        st.subheader("建立訓練與驗證組")
        with st.form("split_data"):
            smoke = st.checkbox("只做硬體短測試（單片段、沒有獨立驗證組）")
            assisted = st.checkbox("允許系統控制或控制比例未知的個人片段")
            st.caption("正式實驗至少兩趟行程；同一趟行程放在同一組。重新切分後，既有實驗可能無法續訓。")
            if st.form_submit_button("檢查並建立資料切分", type="primary"):
                submit("split", smoke=smoke, allow_assisted=assisted)
        dataset = read_optional(ROOT / "dataset.json")
        if dataset:
            a, b, c = st.columns(3)
            a.metric("訓練片段", len(dataset["train"])); b.metric("驗證片段", len(dataset["val"]))
            c.metric("排除片段", len(dataset.get("rejected", [])))
            if not dataset.get("route_isolation_verified"): st.warning("這份資料尚未證明原始行程互相獨立。")
            with st.expander("分組與排除原因"): st.json(dataset)
        elif (ROOT / "reports/rejected.json").exists(): st.json(read_optional(ROOT / "reports/rejected.json"))
    with browse:
        ids = folders("data")
        if not ids: st.info("尚無片段。先取得資料，這裡會顯示實際影像與品質報告。")
        else:
            segment = st.selectbox("預覽片段", ids)
            directory = ROOT / "data" / segment
            quality = read_optional(directory / "quality.json", {})
            st.json(read_optional(directory / "source.json", {}), expanded=False)
            frame = st.number_input("影格編號", min_value=0, value=200, step=1)
            if st.button("讀取這一幀"):
                for column, camera, label in zip(st.columns(2), ["fcamera.hevc", "ecamera.hevc"], ["窄角鏡頭", "廣角鏡頭"]):
                    path = directory / camera
                    with column:
                        if not path.exists(): st.info(label + "：片段未提供此影片")
                        else:
                            try: st.image(preview_frame(str(path), frame, path.stat().st_mtime_ns), caption=label, use_container_width=True)
                            except Exception as e: st.error(str(e))
            if quality:
                with st.expander("品質檢查", expanded=True): st.json(quality)
            if (directory / "localizer.safetensors").is_file():
                from safetensors.numpy import load_file
                import numpy as np
                labels = load_file(directory / "localizer.safetensors")
                if "frame_states" in labels and "frame_t" in labels:
                    st.line_chart(pd.DataFrame({"秒": labels["frame_t"] - labels["frame_t"][0],
                                                 "車速 km/h": np.linalg.norm(labels["frame_states"][:, 7:10], axis=1) * 3.6}), x="秒", y="車速 km/h")


@st.fragment(run_every=3)
def training_monitor(run):
    frame = metrics(run)
    if frame.empty:
        st.info("尚無訓練數據。工作啟動後會在此自動更新。")
        return
    latest = frame.iloc[-1]
    a, b, c = st.columns(3)
    a.metric("已記錄步數", int(latest["step"])); b.metric("Loss", f"{latest['loss']:.4f}" if "loss" in latest else "尚未記錄")
    c.metric("每步耗時", f"{latest['step_seconds']:.2f} 秒" if "step_seconds" in latest else "尚未記錄")
    for column, key, label in zip(st.columns(3), ["loss", "step_seconds", "gpu_peak_allocated_gb"],
                                 ["訓練損失", "每步秒數", "GPU 配置峰值 GB"]):
        with column:
            st.write(label)
            if key in frame: st.line_chart(frame, x="step", y=key)


def training_page():
    st.title("訓練")
    live_jobs()
    resume = st.toggle("接續既有實驗")
    runs = folders("runs")
    existing = st.selectbox("接續的實驗", runs) if resume and runs else None
    if resume and not runs: st.info("尚無可續訓的實驗。")
    previous = read_optional(ROOT / "runs" / existing / "config.json", {}) if existing else {}
    with st.form("train"):
        name = st.text_input("實驗名稱", existing or "ev6-001", disabled=resume)
        a, b, c = st.columns(3)
        steps = a.number_input("目標總步數", 1, 10000000, 100)
        batch = b.number_input("Batch size", 1, 1024, int(previous.get("batch_size", 1)))
        every = c.number_input("驗證間隔（步）", 1, 1000000, 20)
        lr = st.number_input("學習率", min_value=.00000001, max_value=.1, value=float(previous.get("learning_rate", .0003)), format="%.8f")
        st.caption("續訓的 batch size、學習率與資料切分需和原實驗一致。先以 batch 1 量測 Spark 的實際負載。")
        if st.form_submit_button("開始訓練" if not resume else "繼續訓練", type="primary", disabled=resume and not existing):
            submit("train", name=name, steps=steps, batch_size=batch, learning_rate=lr, validate_every=every, resume=resume)
    runs = folders("runs")
    if runs:
        selected = st.selectbox("監看實驗", runs)
        training_monitor(selected)
        active = [j for j in JOBS.list() if j["kind"] == "train" and j["payload"].get("name") == selected and j["status"] in ACTIVE]
        if active and st.button("停止這次訓練"):
            JOBS.cancel(active[0]["id"])
            st.info("已要求停止。續訓會從最後完整保存的 checkpoint 開始。")


def trajectory(run):
    path = ROOT / "runs" / run / "evaluation.example.npz"
    if not path.is_file(): return
    import matplotlib.pyplot as plt
    import numpy as np
    with np.load(path, allow_pickle=False) as d:
        images, plot = st.columns([1, 1.3])
        with images:
            st.image(d["rgb"][0, ..., :3], caption="窄角鏡頭 · 模型裁切", use_container_width=True)
            st.image(d["rgb"][0, ..., 3:], caption="廣角鏡頭 · 模型裁切", use_container_width=True)
        with plot:
            fig, ax = plt.subplots(figsize=(6, 5))
            for key, label, color in [("target_plan", "Recorded", "#172b4d"), ("teacher_plan", "Teacher", "#087f8c"), ("student_plan", "Student", "#e77924")]:
                xy = d[key][0, :, :2]
                ax.plot(xy[:, 1], xy[:, 0], label=label, color=color, linewidth=2)
            ax.set_xlabel("Right (m)"); ax.set_ylabel("Forward (m)"); ax.grid(alpha=.15); ax.legend()
            st.pyplot(fig); plt.close(fig)
            st.caption("深色：實際紀錄 · 青色：教師 · 橘色：學生。這是開環比較。")


def evaluation_page():
    st.title("評估與匯出")
    run, checkpoint = run_selector("evaluate")
    if not checkpoint: return
    on_policy = None
    if (ROOT / "runs" / run / "on_policy.pt").is_file():
        if st.checkbox("使用模擬微調權重一起評估與匯出"): on_policy = "on_policy.pt"
    batch = st.number_input("評估 batch size", 1, 1024, 1)
    if st.button("評估選定權重", type="primary"):
        submit("evaluate", name=run, checkpoint=checkpoint, batch_size=batch, on_policy=on_policy)
    report = read_optional(ROOT / "runs" / run / "evaluation.json")
    if report:
        st.subheader("最近一次評估")
        st.caption(f"Checkpoint：{report.get('checkpoint', '未記錄')} · 樣本數：{report.get('samples', 0)} · 微調：{'有' if report.get('on_policy_sha256') else '無'}")
        records = [{"模型": label, **report.get(key, {})} for label, key in [("學生", "student"), ("教師", "teacher")]]
        st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
        trajectory(run)
        with st.expander("評估來源與權重校驗碼"): st.json(report)
    st.subheader("匯出車端候選")
    with st.form("export"):
        release = st.text_input("新候選名稱", "ev6-r001")
        st.caption("匯出工具會核對目前選定的權重是否與評估一致，再執行 ONNX 數值與循環狀態比對。")
        if st.form_submit_button("驗證並匯出候選", disabled=report is None):
            submit("export", name=run, checkpoint=checkpoint, release=release, on_policy=on_policy)
    st.subheader("比較不同實驗")
    choices = st.multiselect("選擇最多三個實驗", folders("runs"), max_selections=3)
    rows = []
    for selected in choices:
        value = read_optional(ROOT / "runs" / selected / "evaluation.json")
        if value: rows.append({"實驗": selected, "Checkpoint": value.get("checkpoint"),
                               "資料切分": value.get("dataset_sha256"), **value.get("student", {})})
        else: st.info(selected + " 尚無評估報告。")
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if len({r["資料切分"] for r in rows}) > 1: st.warning("資料切分不同，這些分數不能直接當作公平排名。")


def simulation_page():
    st.title("模擬與微調")
    st.caption("使用上游世界模型與 DAgger 流程。GPU 相容性與模擬效能需在 Spark 實測。")
    live_jobs()
    a, b = st.columns(2)
    downloaded = (ROOT / "models/model.fp8_nvfp4.torchpackage").is_file()
    if a.button("下載世界模型", disabled=downloaded): submit("worldmodel-download", revision="main")
    if b.button("啟動世界模型服務", disabled=not downloaded): submit("worldmodel-server")
    st.caption("服務會持續占用 GPU。一般訓練前請到工作紀錄停止服務。")
    run, checkpoint = run_selector("simulation")
    if not checkpoint: return
    dataset = read_optional(ROOT / "dataset.json", {})
    train_ids, val_ids = dataset.get("train", []), dataset.get("val", [])
    if train_ids:
        with st.form("rl"):
            segment = st.selectbox("微調片段（訓練組）", train_ids)
            steps = st.number_input("微調步數", 1, 10000000, 10)
            st.caption("不覆蓋既有 on_policy.pt；完成後需重新評估同一組權重。")
            if st.form_submit_button("開始模擬微調"):
                submit("rl-train", name=run, checkpoint=checkpoint, segment=segment, steps=steps)
    if val_ids:
        segment = st.selectbox("互動模擬片段（驗證組）", val_ids)
        on_policy = "on_policy.pt" if (ROOT / "runs" / run / "on_policy.pt").exists() and st.checkbox("模擬使用微調權重") else None
        desktop = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        st.caption("互動模擬視窗會出現在 Spark 的圖形桌面；本版未提供瀏覽器畫面串流。")
        if st.button("開啟互動模擬", disabled=not desktop):
            submit("simulate", name=run, checkpoint=checkpoint, segment=segment, on_policy=on_policy)


def deploy_page():
    st.title("車端部署")
    st.caption("Comma 3X／four ＋ Chestnut · 先暫存與測試，再決定啟用或回復。")
    host, repo, report = device_controls("deploy")
    if not report: return
    a, b, c = st.columns(3)
    a.metric("讀取時狀態", "Offroad" if report["offroad"] else "未確認停車")
    b.metric("車端候選", len(report["candidates"])); c.metric("備份", len(report["backups"]))
    st.caption("車端 commit：" + report["commit"])
    if not report.get("target_id"):
        st.error("未讀到裝置識別，無法綁定操作對象。")
        return
    base = {"host": host, "repo": repo, "target_id": report["target_id"]}
    st.caption("每次操作都會重新核對裝置身分；編譯、啟用與回復另由車端檢查 offroad。")
    stage_tab, test_tab, activate_tab, restore_tab = st.tabs(["傳送候選", "編譯與測試", "啟用候選", "回復原模型"])
    with stage_tab:
        releases = [n for n in folders("releases") if (ROOT / "releases" / n / "manifest.json").is_file()]
        if releases:
            selected = st.selectbox("Spark 候選", releases)
            st.json(read_optional(ROOT / "releases" / selected / "manifest.json"), expanded=False)
            if st.button("傳送並在車端暫存", disabled=not report["offroad"]): submit("car-stage", **base, release=selected)
        else: st.info("Spark 尚無完整候選。先完成評估與匯出。")
    candidates = sorted(report["candidates"])
    with test_tab:
        if not candidates: st.info("車端尚無候選。")
        else:
            selected = st.selectbox("車端候選", candidates, key="compile_release")
            camera = st.selectbox("原始攝影機解析度", ["1928x1208", "1344x760"])
            st.caption("依實際 rlog 選擇，不能因兩台裝置拍到相同道路就共用參數。")
            if st.button("在 Chestnut 編譯", disabled=not report["offroad"]): submit("car-compile", **base, release=selected, camera=camera)
            with st.form("benchmark"):
                runs = st.number_input("量測次數", 20, 10000, 100)
                threshold = st.number_input("p95 門檻（ms）", .1, 49.9, 45.0)
                if st.form_submit_button("執行數值與延遲測試", disabled=not report["offroad"]):
                    submit("car-benchmark", **base, release=selected, runs=runs, max_p95_ms=threshold)
            if report["candidates"][selected].get("benchmark"):
                st.json(report["candidates"][selected]["benchmark"])
            st.warning("合成影格效能測試不涵蓋真實攝影機延遲、溫度與駕駛品質。")
    with activate_tab:
        if not candidates: st.info("車端尚無候選。")
        else:
            selected = st.selectbox("要啟用的候選", candidates, key="activate_release")
            st.write("車端會核對軟體介面、benchmark 與檔案校驗碼，並先備份原模型。請先在模型選單選回預設模型。")
            with st.form("activate"):
                closed = st.checkbox("已安排封閉場地測試，並已檢查此候選的評估結果")
                confirmation = st.text_input("輸入「裝置／候選」以確認", placeholder=host + "/" + selected)
                if st.form_submit_button("備份並啟用候選", disabled=not report["offroad"]):
                    submit("car-activate", **base, release=selected, closed_course=closed, confirmation=confirmation)
        st.caption("操作不會自動重開機。完成後請依正常方式重啟，再核對實際載入模型。")
        if st.button("核對現用模型檔案與 Chestnut 狀態"): submit("car-verify-active", **base)
    with restore_tab:
        backups = report["backups"]
        if backups:
            selected = st.selectbox("車端備份", backups, index=len(backups) - 1)
            with st.form("restore"):
                confirmation = st.text_input("輸入「裝置／備份」以確認", placeholder=host + "/" + selected)
                if st.form_submit_button("回復這份備份", disabled=not report["offroad"]):
                    submit("car-restore", **base, backup=selected, confirmation=confirmation)
        else: st.info("車端尚無本工具建立的備份。")
        if report.get("active"):
            with st.expander("最近的安裝／回復紀錄"): st.json(report["active"])


@st.fragment(run_every=3)
def job_detail(job_id):
    JOBS.reconcile()
    job = JOBS.get(job_id)
    st.subheader(LABELS.get(job["kind"], job["kind"]) + " · " + STATUS.get(job["status"], job["status"]))
    if job["error"]: st.error(job["error"])
    if job["status"] in {"failed", "interrupted"}:
        st.warning("請先查看最後的錯誤訊息。車端工作若遇到 SSH 中斷，結果可能未知，重新讀取裝置後再決定下一步。")
    with st.expander("操作參數與時間"):
        st.json({k: job[k] for k in ("id", "payload", "created", "started", "finished", "exit_code")})
    st.code(JOBS.tail(job_id), language=None)
    if job["status"] in ACTIVE:
        if job["status"] == "orphaned":
            st.warning("工作仍持有執行鎖，背景管理程序已中斷。請在 Spark 終端機核對程序，勿刪除鎖檔或重複啟動訓練。")
        elif job["cancellable"]:
            if st.button("要求停止工作", key="stop_" + job_id):
                JOBS.cancel(job_id); st.info("已送出停止要求。")
        else: st.info("車端操作執行中。為避免安裝中斷，此處不提供停止按鈕。")


def jobs_page():
    st.title("工作紀錄")
    st.caption("工作保存在 Spark；重新開啟網頁後仍可查看。失敗或中斷的工作不會自動重跑。")
    jobs = JOBS.list()
    if not jobs: st.info("尚無工作紀錄。從環境檢查或資料取得開始。")
    else:
        st.dataframe(pd.DataFrame([{"工作": LABELS.get(j["kind"], j["kind"]), "狀態": STATUS[j["status"]],
                                   "開始時間": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(j["created"])),
                                   "ID": j["id"][:8]} for j in jobs]), hide_index=True, use_container_width=True)
        choices = {j["id"]: f"{j['id'][:8]} · {LABELS.get(j['kind'], j['kind'])}" for j in jobs}
        selected = st.session_state.get("selected_job")
        selected = selected if selected in choices else next(iter(choices))
        job_id = st.selectbox("查看工作", list(choices), index=list(choices).index(selected), format_func=choices.get)
        job_detail(job_id)


with st.sidebar:
    st.title("駕駛模型\n實驗室")
    st.caption("DGX SPARK · 本地運行")
    page = st.radio("工作區", ["總覽", "資料", "訓練", "評估與匯出", "模擬與微調", "車端部署", "工作紀錄"], label_visibility="collapsed")
    st.divider()
    st.caption("資料與模型保存在 Spark。\n此介面沒有上傳私人影片到外部儀表板。")
    st.button("重新整理頁面", on_click=lambda: None)

try:
    {"總覽": home, "資料": data_page, "訓練": training_page, "評估與匯出": evaluation_page,
     "模擬與微調": simulation_page, "車端部署": deploy_page, "工作紀錄": jobs_page}[page]()
except Exception as error:
    st.error("無法完成這次畫面操作：" + str(error))
    st.caption("已啟動的背景工作不受網頁錯誤影響，可到工作紀錄確認。")
