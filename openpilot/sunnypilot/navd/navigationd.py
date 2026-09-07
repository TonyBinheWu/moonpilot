#!/usr/bin/env python3
"""Navigation worker; network waits never block modeld/controlsd or the UI."""
from concurrent.futures import ThreadPoolExecutor
import math
import time
import uuid

from openpilot.sunnypilot.navd.mapbox import MapboxClient
from openpilot.sunnypilot.navd.route import coordinate


class NavigationEngine:
  def __init__(self, client_factory=MapboxClient):
    self.client_factory = client_factory
    self.executor = ThreadPoolExecutor(max_workers=1)
    self.future = None
    self.request_key = None
    self.key = None
    self.route = None
    self.route_id = ""
    self.retry_at = 0.0
    self.failures = 0
    self.offroute_count = 0
    self.error = ""
    self.arrived = False

  def close(self):
    self.executor.shutdown(wait=False, cancel_futures=True)

  def update(self, enabled, token, destination, gps, now):
    state = {"status": "off", "routeId": "", "instruction": "", "valid": False}
    try:
      end = coordinate([destination["longitude"], destination["latitude"]]) if destination else None
    except (KeyError, ValueError, TypeError):
      end = None
    key = (enabled, token, repr(destination))
    if key != self.key:
      self.key = key
      self.route = None
      self.route_id = ""
      self.retry_at = 0.0
      self.failures = self.offroute_count = 0
      self.error = ""
      self.arrived = False
    # Always drain completed work; a destination change invalidates its result.
    if self.future is not None and self.future.done():
      try:
        route = self.future.result()
        if self.request_key == self.key:
          self.route = route
          self.route_id = uuid.uuid4().hex
          self.failures = 0
          self.error = ""
      except Exception:
        if self.request_key == self.key:
          self.failures += 1
          self.error = "路線規劃失敗，請檢查 Mapbox 權杖與網路"
          self.retry_at = now + min(300, 5 * 2 ** min(self.failures, 6))
      self.future = None
    if not enabled or end is None:
      return state
    if not token or not token.startswith("pk."):
      return {**state, "status": "error", "instruction": "請設定 Mapbox 公開權杖（pk.）"}
    if gps is None:
      return {**state, "status": "waitingGps", "instruction": "等待可靠的 GPS 定位"}
    if self.arrived:
      return {**state, "status": "arrived", "instruction": "已抵達目的地"}
    if self.route is None:
      if self.future is None and now >= self.retry_at:
        self.request_key = self.key
        self.future = self.executor.submit(self.client_factory(token).route, gps["position"], end, gps.get("bearing"))
      return {**state, "status": "error" if self.error else "routing", "instruction": self.error or "正在規劃路線"}
    progress = self.route.update(gps["position"], now, gps["speed"], gps.get("bearing"))
    if progress is None:
      self.offroute_count += 1
      if self.offroute_count >= 3:
        self.route = None
        self.offroute_count = 0
        self.retry_at = max(self.retry_at, now + 5)
      return {**state, "status": "offRoute", "instruction": "無法確認目前路段，準備重新規劃"}
    self.offroute_count = 0
    if progress.pop("arrived"):
      self.arrived = True
      return {**state, "status": "arrived", "instruction": "已抵達目的地"}
    return {**state, **progress, "status": "active", "routeId": self.route_id, "valid": True}


def gps_sample(gps, valid, timestamp_ns, now):
  if not valid or not gps.hasFix or not 0 <= now - timestamp_ns / 1e9 <= 2.5:
    return None
  if not math.isfinite(gps.horizontalAccuracy) or not 0 < gps.horizontalAccuracy <= 20:
    return None
  try:
    position = coordinate([gps.longitude, gps.latitude])
  except ValueError:
    return None
  if not math.isfinite(gps.speed) or gps.speed < 0 or not math.isfinite(gps.bearingDeg):
    return None
  return {"position": position, "speed": gps.speed, "bearing": gps.bearingDeg % 360}


def main():
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.realtime import Ratekeeper
  params = Params()
  sm = messaging.SubMaster(["gpsLocation"])
  pm = messaging.PubMaster(["navigationStateSP"])
  engine = NavigationEngine()
  rk = Ratekeeper(1, print_delay_threshold=None)
  try:
    while True:
      sm.update(0)
      now = time.monotonic()
      gps = gps_sample(sm["gpsLocation"], sm.valid["gpsLocation"], sm.logMonoTime["gpsLocation"], now)
      result = engine.update(params.get_bool("MapboxNavigation"), params.get("MapboxToken") or "",
                             params.get("MapboxDestination"), gps, now)
      valid = result.pop("valid")
      msg = messaging.new_message("navigationStateSP")
      msg.valid = valid
      msg.navigationStateSP = result
      pm.send("navigationStateSP", msg)
      rk.keep_time()
  finally:
    engine.close()


if __name__ == "__main__":
  main()
