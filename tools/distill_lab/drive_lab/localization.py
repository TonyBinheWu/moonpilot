"""Device-preserving localization and explicit logged-ECEF fallback."""
from collections import defaultdict
from pathlib import Path
import numpy as np

from .common import read_json, write_json, digest


def validate_times(values, name, max_gap=0.12):
    values = np.asarray(values)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all() or np.any(np.diff(values) <= 0):
        raise ValueError(f"{name} 時間戳必須有限且嚴格遞增")
    if np.max(np.diff(values)) > max_gap:
        raise ValueError(f"{name} 出現超過 {max_gap} 秒的缺口")


def check_labels(frame_info, labels):
    for camera in ("fcamera", "ecamera"):
        if camera + "/t" in frame_info:
            t = frame_info[camera + "/t"]
            validate_times(t, camera, 0.08)
            if abs(float(np.median(np.diff(t))) - 0.05) > 0.003:
                raise ValueError("資料載入器按 20Hz 取樣；其他幀率需另做時間重取樣")
            if len(t) != int(frame_info[camera + "/frame_count"].item()):
                raise ValueError("影格數與時間戳數不同")
    if "ecamera/t" in frame_info:
        f, e = frame_info["fcamera/t"], frame_info["ecamera/t"]
        if len(f) != len(e) or np.max(np.abs(f - e)) > 0.025:
            raise ValueError("雙攝影機未逐幀同步；拒絕以相同 index 配對")
    t = labels["frame_t"]
    validate_times(t, "localizer/frame_t", 0.08)
    if len(t) != len(frame_info["fcamera/t"]) or np.max(np.abs(t - frame_info["fcamera/t"])) > 0.001:
        raise ValueError("標籤與影片時間戳不同")
    states = labels["frame_states"]
    if states.shape != (len(t), 43) or not np.isfinite(states).all():
        raise ValueError("標籤應為有限的 N×43 狀態矩陣")
    if np.max(np.abs(np.linalg.norm(states[:, 3:7], axis=1) - 1)) > 0.01:
        raise ValueError("ECEF 姿態四元數未正規化")
    for key in ("rpy", "wide_from_device_euler"):
        if labels[key].shape != (3,) or not np.isfinite(labels[key]).all():
            raise ValueError("缺少有效攝影機外參")
    speed = np.linalg.norm(states[:, 7:10], axis=1)
    if np.max(speed) > 80:
        raise ValueError("定位速度超出 80 m/s，請檢查單位或紀錄錯誤")
    return {"frames": len(t), "duration_s": float(t[-1] - t[0]),
            "max_speed_mps": float(speed.max()), "finite": True}


def logged_ecef(streams, frame_info):
    """Use actual logged filter measurements; never invent GPS targets."""
    from localizer.math import euler_to_rot, rot_to_quat, nlerp_quaternions, interpolate
    samples = []
    for event in streams["liveLocationKalman"]:
        p = event.liveLocationKalman
        fields = ("positionECEF", "orientationECEF", "velocityECEF", "angularVelocityDevice", "accelerationDevice")
        if (event.valid and str(p.status) == "valid" and p.inputsOK and p.gpsOK and p.sensorsOK
                and all(getattr(p, n).valid and len(getattr(p, n).value) == 3 for n in fields)):
            samples.append((event.logMonoTime * 1e-9, *[list(getattr(p, n).value) for n in fields]))
    if len(samples) < 10:
        raise ValueError("沒有足夠有效的 liveLocationKalman ECEF 狀態；不能用影片代替定位標籤")
    t = np.array([s[0] for s in samples])
    validate_times(t, "liveLocationKalman")
    ft = frame_info["fcamera/t"]
    if ft[0] < t[0] or ft[-1] > t[-1]:
        raise ValueError("定位未涵蓋整段影片；請改用完整片段，不能外插標籤")
    states = np.zeros((len(t), 43), dtype=np.float64)
    for dest, idx in ((slice(0, 3), 1), (slice(7, 10), 3), (slice(10, 13), 4), (slice(19, 22), 5)):
        states[:, dest] = np.array([s[idx] for s in samples])
    states[:, 3:7] = rot_to_quat(euler_to_rot(np.array([s[2] for s in samples])))
    calibration = [e.extrinsicsCalibration for e in streams["extrinsicsCalibration"] if e.valid]
    if not calibration or any(str(c.calStatus) != "calibrated" for c in calibration):
        raise ValueError("缺少整段穩定的校正資料")
    if any(b.validBlocks < a.validBlocks for a, b in zip(calibration, calibration[1:])):
        raise ValueError("片段中校正曾重設")
    rpy = np.array(calibration[-1].rpyCalib, dtype=np.float64)
    wide = np.array(calibration[-1].wideFromDeviceEuler, dtype=np.float64)
    states[:, [18, 22, 29]] = 1
    states[:, 33:36] = wide
    states[:, 36:43] = states[:, :7]
    frames = interpolate(ft, t, states)
    frames[:, 3:7] = nlerp_quaternions(ft, t, states[:, 3:7])
    frames[:, 36:43] = frames[:, :7]
    return {"t": t, "states": states, "frame_t": ft, "frame_states": frames,
            "rpy": rpy, "wide_from_device_euler": wide}


def prepare(segment, method="raw-gnss"):
    from openpilot.tools.lib.logreader import LogReader
    from openpilot.common.transformations.camera import DEVICE_CAMERAS
    from safetensors.numpy import save_file
    from localizer.logs import read_log, _frame_info, FRAME_INFO_METADATA
    from localizer.localizer import localize
    segment = Path(segment)
    logs = [p for p in (segment / "rlog.zst", segment / "rlog.bz2", segment / "rlog") if p.exists()]
    if len(logs) != 1:
        raise ValueError("需要一個完整 rlog")
    events = list(LogReader(str(logs[0]), sort_by_time=True, only_union_types=True))
    streams = defaultdict(list)
    for e in events:
        streams[e.which()].append(e)
    init = [e.initData for e in streams["initData"] if e.valid and e.initData.version]
    devices = {str(i.deviceType) for i in init}
    if len(devices) != 1 or not devices <= {"tici", "tizi", "mici"}:
        raise ValueError(f"不支援／混合裝置：{devices}")
    device = next(iter(devices))
    sensors = {str(e.narrowRoadCameraState.sensor) for e in streams["narrowRoadCameraState"] if e.valid}
    sensors.discard("unknown")
    if len(sensors) != 1:
        raise ValueError("缺少唯一的真實攝影機 sensor；不能只由裝置名稱猜測")
    sensor = next(iter(sensors))
    cameras = DEVICE_CAMERAS[(device, sensor)]
    if method == "raw-gnss":
        loc_input, frame_info = read_log(events, segment)
        if loc_input.frame_t[0] < loc_input.pose_t[0] or loc_input.frame_t[-1] > loc_input.pose_t[-1]:
            raise ValueError("deviceMotion 未涵蓋整段影片；請改用完整片段")
        labels = localize(loc_input)
    else:
        frame_info = _frame_info(streams, init[0].deviceType.raw, segment)
        labels = logged_ecef(streams, frame_info)
    frame_info["sensor_name"] = np.frombuffer(sensor.encode("ascii"), dtype=np.uint8).copy()
    for name, camera in (("fcamera", cameras.narrow_road), ("ecamera", cameras.wide_road)):
        resolution = (int(frame_info[name + "/width"].item()), int(frame_info[name + "/height"].item()))
        if resolution != camera.size:
            raise ValueError(f"{name}: 解析度 {resolution} 不符 {device}/{sensor} 的 {camera.size}；不可直接縮放掩蓋")
    report = check_labels(frame_info, labels)
    controls = [e.carControl for e in streams["carControl"] if e.valid]
    assisted = sum(bool(c.latActive or c.longActive) for c in controls) / len(controls) if controls else None
    report.update({"device": device, "sensor": sensor, "label_method": method,
                   "timestamp_method": "mean(timestampSof,timestampEof)",
                   "timestamp_note": "影格擷取區間中點；不是已驗證的光學曝光中心，需實車同步測試",
                   "assisted_fraction": assisted,
                   "label_quality": "offline_gnss_fusion" if method == "raw-gnss" else "online_filter_research_only"})
    save_file(labels, segment / "localizer.safetensors", metadata={"schema_version": "1"})
    save_file(frame_info, segment / "frame_info.safetensors", metadata=FRAME_INFO_METADATA)
    provenance = read_json(segment / "source.json")
    provenance.update(report)
    provenance["labels_sha256"] = digest(segment / "localizer.safetensors")
    write_json(segment / "source.json", provenance)
    write_json(segment / "quality.json", report)
    return report
