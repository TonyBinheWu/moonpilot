from __future__ import annotations

import math
from typing import Any

import requests

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.navd.helpers import (Coordinate, coordinate_from_param,
                                               distance_along_geometry, maxspeed_to_ms,
                                               minimum_distance, parse_banner_instructions)

REROUTE_DISTANCE = 25.0
MANEUVER_TRANSITION_THRESHOLD = 10.0
CACHE_VERSION = 1
NAV_ROUTE_MAX_POINTS = 4096

ONLINE_NETWORK_TYPES = {
  log.DeviceState.NetworkType.wifi,
  log.DeviceState.NetworkType.cell2G,
  log.DeviceState.NetworkType.cell3G,
  log.DeviceState.NetworkType.cell4G,
  log.DeviceState.NetworkType.cell5G,
}

MAPBOX_LANGUAGE_ALIASES = {
  "zh-CHS": "zh-CN",
  "zh-CHT": "zh-TW",
  "th": "th-TH",
}


def mapbox_language(language_setting: str | None) -> str | None:
  if not language_setting:
    return None
  language = language_setting.removeprefix("main_")
  return MAPBOX_LANGUAGE_ALIASES.get(language, language)


def limit_route_points(points: list[dict[str, float]], max_points: int = NAV_ROUTE_MAX_POINTS) -> list[dict[str, float]]:
  if max_points <= 0:
    return []
  if max_points == 1:
    return points[:1]
  if len(points) <= max_points:
    return points

  last_index = len(points) - 1
  indices = [round(index * last_index / (max_points - 1)) for index in range(max_points)]
  return [points[index] for index in indices]


class RouteEngine:
  def __init__(self, sm, pm, params: Params | None = None, cache_params: Params | None = None,
               session: requests.Session | None = None) -> None:
    self.sm = sm
    self.pm = pm
    self.params = params or Params()
    self.cache_params = cache_params or Params("/dev/shm/params")
    self.session = session or requests.Session()

    self.last_position = coordinate_from_param("LastGPSPositionLLK", self.params)
    self.last_bearing: float | None = None
    self.gps_ok = False
    self.localizer_valid = False

    self.nav_destination: Coordinate | None = None
    self.step_idx: int | None = None
    self.route: list[dict[str, Any]] | None = None
    self.route_geometry: list[list[Coordinate]] | None = None

    self.recompute_backoff = 0
    self.recompute_countdown = 0
    self._network_available_last = False

    destination = coordinate_from_param("NavDestination", self.params)
    if destination is not None:
      self.nav_destination = destination
      self._restore_cached_route(destination)

  def update(self) -> None:
    self.sm.update(0)
    self.update_location()

    network_available = self.network_available()
    if network_available and not self._network_available_last:
      self.recompute_countdown = 0
    self._network_available_last = network_available

    self.recompute_route(network_available)
    self.send_instruction()

  def update_location(self) -> None:
    location = self.sm["liveLocationKalman"]
    location_valid = (location.status == log.LiveLocationKalman.Status.valid and
                      location.positionGeodetic.valid and len(location.positionGeodetic.value) >= 2)
    self.localizer_valid = bool(location_valid)
    self.gps_ok = bool(location_valid and location.gpsOK)

    if location_valid:
      self.last_position = Coordinate(float(location.positionGeodetic.value[0]), float(location.positionGeodetic.value[1]))
      orientation = location.calibratedOrientationNED
      if orientation.valid and len(orientation.value) >= 3:
        self.last_bearing = math.degrees(float(orientation.value[2]))

  def network_available(self) -> bool:
    return bool(self.sm.alive["deviceState"] and self.sm.valid["deviceState"] and
                self.sm["deviceState"].networkType in ONLINE_NETWORK_TYPES)

  def recompute_route(self, network_available: bool) -> None:
    new_destination = coordinate_from_param("NavDestination", self.params)
    if new_destination is None:
      if self.route is not None or self.nav_destination is not None:
        self.clear_route(clear_cache=True)
        self.send_route()
      return

    destination_changed = self.nav_destination is None or new_destination.distance_to(self.nav_destination) >= 1.0
    if destination_changed:
      cloudlog.warning("navd: new destination received")
      self.nav_destination = new_destination
      self.route = None
      self.route_geometry = None
      self.step_idx = None
      self.recompute_backoff = 0
      self.recompute_countdown = 0
      self._restore_cached_route(new_destination)

    if self.last_position is None:
      return

    should_recompute = self.should_recompute()

    # Keep following an already loaded route through tunnels or a network outage.
    if not self.gps_ok and self.step_idx is not None:
      return
    if not should_recompute:
      return

    if not network_available:
      return

    token = (self.params.get("MapboxPublicKey") or "").strip()
    if not token.startswith("pk."):
      return

    if self.recompute_countdown == 0:
      self.recompute_countdown = 2 ** self.recompute_backoff
      self.recompute_backoff = min(6, self.recompute_backoff + 1)
      self.calculate_route(new_destination, token)
    else:
      self.recompute_countdown = max(0, self.recompute_countdown - 1)

  def calculate_route(self, destination: Coordinate, token: str) -> bool:
    if self.last_position is None:
      return False

    cloudlog.warning("navd: calculating route")
    language = mapbox_language(self.params.get("LanguageSetting"))

    query: dict[str, str] = {
      "access_token": token,
      "annotations": "maxspeed",
      "geometries": "geojson",
      "overview": "full",
      "steps": "true",
      "banner_instructions": "true",
      "alternatives": "false",
    }
    if language:
      query["language"] = language
    if self.last_bearing is not None:
      query["bearings"] = f"{(self.last_bearing + 360.0) % 360.0:.0f},90;"

    coordinates = ";".join((
      f"{self.last_position.longitude},{self.last_position.latitude}",
      f"{destination.longitude},{destination.latitude}",
    ))
    url = f"https://api.mapbox.com/directions/v5/mapbox/driving-traffic/{coordinates}"

    try:
      response = self.session.get(url, params=query, timeout=10)
      response.raise_for_status()
      route_response = response.json()
    except (requests.RequestException, ValueError) as error:
      # Do not erase a usable route when connectivity disappears or a refresh fails.
      cloudlog.warning(f"navd: route request failed ({type(error).__name__}); retaining loaded route")
      return False

    if not self._load_route_response(route_response):
      cloudlog.warning("navd: Mapbox returned no usable route; retaining loaded route")
      return False

    self.nav_destination = destination
    self.recompute_backoff = 0
    self.recompute_countdown = 0
    self._cache_route(destination, route_response)
    self.send_route()
    return True

  def _load_route_response(self, route_response: dict[str, Any]) -> bool:
    try:
      leg = route_response["routes"][0]["legs"][0]
      route = leg["steps"]
    except (KeyError, IndexError, TypeError):
      return False
    if not route:
      return False

    maxspeeds = leg.get("annotation", {}).get("maxspeed", []) or []
    geometry: list[list[Coordinate]] = []
    maxspeed_idx = 0

    try:
      for step in route:
        path: list[Coordinate] = []
        for item in step["geometry"]["coordinates"]:
          coordinate = Coordinate.from_mapbox_tuple(item)
          if maxspeed_idx < len(maxspeeds):
            maxspeed = maxspeeds[maxspeed_idx]
            if isinstance(maxspeed, dict) and not (maxspeed.get("unknown") or maxspeed.get("none")):
              try:
                coordinate.annotations["maxspeed"] = maxspeed_to_ms(maxspeed)
              except (KeyError, TypeError, ValueError):
                pass
          path.append(coordinate)
          maxspeed_idx += 1

        if not path:
          return False
        geometry.append(path)
        maxspeed_idx = max(0, maxspeed_idx - 1)
    except (IndexError, KeyError, TypeError, ValueError):
      return False

    self.route = route
    self.route_geometry = geometry
    self.step_idx = 0
    return True

  def _cache_route(self, destination: Coordinate, route_response: dict[str, Any]) -> None:
    try:
      self.cache_params.put("NavRouteCache", {
        "version": CACHE_VERSION,
        "destination": destination.as_dict(),
        "response": route_response,
      }, block=True)
    except (RuntimeError, TypeError, ValueError):
      cloudlog.exception("navd: failed to cache route")

  def _restore_cached_route(self, destination: Coordinate) -> bool:
    try:
      cache = self.cache_params.get("NavRouteCache")
      if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION:
        return False
      cached_destination_data = cache.get("destination")
      if not isinstance(cached_destination_data, dict):
        return False
      cached_destination = Coordinate(float(cached_destination_data["latitude"]), float(cached_destination_data["longitude"]))
      if destination.distance_to(cached_destination) >= 1.0:
        return False
      response = cache.get("response")
      if not isinstance(response, dict) or not self._load_route_response(response):
        return False
      cloudlog.warning("navd: restored loaded route from cache")
      self.send_route()
      return True
    except (KeyError, RuntimeError, TypeError, ValueError):
      cloudlog.exception("navd: failed to restore cached route")
      return False

  def send_instruction(self) -> None:
    from openpilot.cereal import messaging

    msg = messaging.new_message("navInstruction")
    if (self.step_idx is None or self.route is None or self.route_geometry is None or
        self.last_position is None or self.step_idx >= len(self.route)):
      msg.valid = False
      self.pm.send("navInstruction", msg)
      return

    last_position = self.last_position
    step = self.route[self.step_idx]
    geometry = self.route_geometry[self.step_idx]
    along_geometry = distance_along_geometry(geometry, last_position)
    maneuver_distance = float(step.get("distance", 0.0)) - along_geometry

    instruction = parse_banner_instructions(step.get("bannerInstructions") or [], maneuver_distance) or {}
    maneuver = step.get("maneuver", {}) or {}
    instruction.setdefault("maneuverPrimaryText", str(maneuver.get("instruction") or step.get("name") or ""))
    instruction.setdefault("maneuverSecondaryText", "")
    instruction.setdefault("maneuverType", str(maneuver.get("type") or ""))
    instruction.setdefault("maneuverModifier", str(maneuver.get("modifier") or ""))

    nav_instruction = msg.navInstruction
    nav_instruction.maneuverDistance = maneuver_distance
    for field, value in instruction.items():
      setattr(nav_instruction, field, value)

    remaining = max(0.0, 1.0 - along_geometry / max(float(step.get("distance", 0.0)), 1.0))
    total_distance = float(step.get("distance", 0.0)) * remaining
    step_duration = float(step.get("duration", 0.0))
    step_duration_typical = step.get("duration_typical")
    total_time = step_duration * remaining
    total_time_typical = float(step_duration if step_duration_typical is None else step_duration_typical) * remaining
    all_maneuvers = []

    for index in range(self.step_idx + 1, len(self.route)):
      future_step = self.route[index]
      total_distance += float(future_step.get("distance", 0.0))
      future_duration = float(future_step.get("duration", 0.0))
      future_duration_typical = future_step.get("duration_typical")
      total_time += future_duration
      total_time_typical += float(future_duration if future_duration_typical is None else future_duration_typical)
      future_maneuver = future_step.get("maneuver", {}) or {}
      all_maneuvers.append({
        "distance": float(future_step.get("distance", 0.0)),
        "type": str(future_maneuver.get("type", "")),
        "modifier": str(future_maneuver.get("modifier", "")),
      })

    nav_instruction.distanceRemaining = total_distance
    nav_instruction.timeRemaining = total_time
    nav_instruction.timeRemainingTypical = total_time_typical
    nav_instruction.allManeuvers = all_maneuvers

    closest_idx, closest = min(enumerate(geometry), key=lambda pair: pair[1].distance_to(last_position))
    if closest_idx > 0 and along_geometry < distance_along_geometry(geometry, geometry[closest_idx]):
      closest = geometry[closest_idx - 1]
    if "maxspeed" in closest.annotations and self.localizer_valid:
      nav_instruction.speedLimit = closest.annotations["maxspeed"]

    speed_limit_sign = step.get("speedLimitSign")
    if speed_limit_sign == "mutcd":
      nav_instruction.speedLimitSign = log.NavInstruction.SpeedLimitSign.mutcd
    elif speed_limit_sign == "vienna":
      nav_instruction.speedLimitSign = log.NavInstruction.SpeedLimitSign.vienna

    msg.valid = True
    self.pm.send("navInstruction", msg)

    if maneuver_distance < -MANEUVER_TRANSITION_THRESHOLD:
      if self.step_idx + 1 < len(self.route):
        self.step_idx += 1
        self.recompute_backoff = 0
        self.recompute_countdown = 0
      else:
        cloudlog.warning("navd: destination reached")
        self.params.remove("NavDestination")
        self.clear_route(clear_cache=True)
        self.send_route()

  def send_route(self) -> None:
    from openpilot.cereal import messaging

    coordinates = []
    if self.route_geometry is not None:
      for path in self.route_geometry:
        coordinates.extend(coordinate.as_dict() for coordinate in path)
    coordinates = limit_route_points(coordinates)

    msg = messaging.new_message("navRoute")
    msg.valid = bool(coordinates)
    msg.navRoute.coordinates = coordinates
    self.pm.send("navRoute", msg)

  def clear_route(self, clear_cache: bool = False) -> None:
    self.route = None
    self.route_geometry = None
    self.step_idx = None
    self.nav_destination = None
    self.recompute_backoff = 0
    self.recompute_countdown = 0
    if clear_cache:
      self.cache_params.remove("NavRouteCache")

  def should_recompute(self) -> bool:
    if self.step_idx is None or self.route is None or self.route_geometry is None:
      return True
    if self.step_idx == len(self.route) - 1 or self.last_position is None:
      return False

    path = self.route_geometry[self.step_idx]
    if len(path) < 2:
      return True
    minimum = REROUTE_DISTANCE + 1.0
    for first, second in zip(path, path[1:], strict=False):
      if first.distance_to(second) >= 1.0:
        minimum = min(minimum, minimum_distance(first, second, self.last_position))
    return minimum > REROUTE_DISTANCE


def main(sm=None, pm=None) -> None:
  from openpilot.cereal import messaging

  if sm is None:
    sm = messaging.SubMaster(["liveLocationKalman", "deviceState"])
  if pm is None:
    pm = messaging.PubMaster(["navInstruction", "navRoute"])

  route_engine = RouteEngine(sm, pm)
  ratekeeper = Ratekeeper(1.0)
  while True:
    route_engine.update()
    ratekeeper.keep_time()


if __name__ == "__main__":
  main()
