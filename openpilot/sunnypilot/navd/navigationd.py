#!/usr/bin/env python3
"""Navigation worker; network waits never block modeld/controlsd or the UI."""
from concurrent.futures import ThreadPoolExecutor
import math
import time
import uuid

from openpilot.sunnypilot.navd.mapbox import MapboxClient
from openpilot.sunnypilot.navd.osrm import OsrmClient, DEFAULT_OSRM_URL
from openpilot.sunnypilot.navd.route import coordinate


class NavigationEngine:
  def __init__(self, client_factory=None):
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
    self.next_request_at = 0.0

  def close(self):
    self.executor.shutdown(wait=False, cancel_futures=True)

  def update(self, enabled, token, destination, gps, now, *, provider="mapbox", language="en", endpoint=DEFAULT_OSRM_URL):
    state = {"status": "off", "routeId": "", "instruction": "", "valid": False, "provider": provider}
    try:
      end = coordinate([destination["longitude"], destination["latitude"]]) if destination else None
    except (KeyError, ValueError, TypeError):
      end = None
    key = (enabled, token if provider == "mapbox" else endpoint, repr(destination), provider, language)
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
          self.error = "Route planning failed. Check the routing service and network."
          self.retry_at = now + min(300, 5 * 2 ** min(self.failures, 6))
      self.future = None
    if not enabled or end is None:
      return state
    if provider not in ("mapbox", "osm"):
      return {**state, "status": "error", "instruction": "Unknown routing provider"}
    if provider == "mapbox" and (not token or not token.startswith("pk.")):
      return {**state, "status": "error", "instruction": "Set a Mapbox public token (pk.)."}
    if gps is None:
      return {**state, "status": "waitingGps", "instruction": "Waiting for a reliable GPS fix"}
    if self.arrived:
      return {**state, "status": "arrived", "instruction": "You have arrived"}
    if self.route is None:
      if self.future is None and now >= max(self.retry_at, self.next_request_at):
        self.request_key = self.key
        self.next_request_at = now + 2.0
        self.future = self.executor.submit(self._route, provider, token, language, endpoint, gps, end)
      return {**state, "status": "error" if self.error else "routing", "instruction": self.error or "Planning route"}
    progress = self.route.update(gps["position"], now, gps["speed"], gps.get("bearing"))
    if progress is None:
      self.offroute_count += 1
      if self.offroute_count >= 3:
        self.route = None
        self.offroute_count = 0
        self.retry_at = max(self.retry_at, now + 5)
      return {**state, "status": "offRoute", "instruction": "Unable to match the current road. Replanning route."}
    self.offroute_count = 0
    if progress.pop("arrived"):
      self.arrived = True
      return {**state, "status": "arrived", "instruction": "You have arrived"}
    return {**state, **progress, "status": "active", "routeId": self.route_id, "valid": True}

  def _route(self, provider, token, language, endpoint, gps, end):
    client = (self.client_factory(token) if self.client_factory is not None else
              OsrmClient(endpoint) if provider == "osm" else MapboxClient(token, language=language))
    return client.route(gps["position"], end, gps.get("bearing"))


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
                             params.get("MapboxDestination"), gps, now,
                             provider="osm" if params.get_bool("OsmNavigation") else "mapbox",
                             language=str(params.get("LanguageSetting") or "en").removeprefix("main_"),
                             endpoint=params.get("OsmRoutingServer") or DEFAULT_OSRM_URL)
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
