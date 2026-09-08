from __future__ import annotations

from types import SimpleNamespace
from typing import cast
import unittest

import requests

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navd import RouteEngine, limit_route_points, mapbox_language


class FakeParams:
  def __init__(self):
    self.values = {}

  def get(self, key, *args, **kwargs):
    return self.values.get(key)

  def put(self, key, value, *args, **kwargs):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


class FakePM:
  def __init__(self):
    self.messages = {}

  def send(self, service, message):
    self.messages[service] = message


class FailingSession:
  def __init__(self):
    self.calls = 0

  def get(self, *args, **kwargs):
    self.calls += 1
    raise requests.ConnectionError("offline")


class NetworkSM:
  def __init__(self, network_type):
    self.alive = {"deviceState": True}
    self.valid = {"deviceState": True}
    self.device_state = SimpleNamespace(networkType=network_type)

  def __getitem__(self, service):
    assert service == "deviceState"
    return self.device_state


def route_response():
  return {
    "routes": [{
      "legs": [{
        "annotation": {"maxspeed": [{"speed": 50, "unit": "km/h"}]},
        "steps": [{
          "distance": 100.0,
          "duration": 12.0,
          "duration_typical": 14.0,
          "geometry": {"coordinates": [[114.1000, 22.3000], [114.1010, 22.3000]]},
          "maneuver": {"type": "turn", "modifier": "left", "instruction": "Turn left"},
          "bannerInstructions": [],
        }, {
          "distance": 80.0,
          "duration": 10.0,
          "geometry": {"coordinates": [[114.1010, 22.3000], [114.1010, 22.3010]]},
          "maneuver": {"type": "arrive", "modifier": "straight", "instruction": "Arrive"},
          "bannerInstructions": [],
        }],
      }],
    }],
  }


def make_engine(session=None, params=None, pm=None):
  params = params or FakeParams()
  return RouteEngine(object(), pm or FakePM(), params=cast(Params, params), cache_params=cast(Params, params),
                     session=cast(requests.Session, session or FailingSession()))


class TestRouteEngine(unittest.TestCase):
  def test_mapbox_language_aliases_match_supported_instruction_codes(self):
    assert mapbox_language("main_zh-CHT") == "zh-TW"
    assert mapbox_language("zh-CHS") == "zh-CN"
    assert mapbox_language("th") == "th-TH"
    assert mapbox_language("en") == "en"
    assert mapbox_language(None) is None

  def test_route_message_point_limit_preserves_endpoints(self):
    points = [{"latitude": float(index), "longitude": float(index)} for index in range(10)]
    limited = limit_route_points(points, max_points=4)
    assert limited == [points[0], points[3], points[6], points[9]]

  def test_failed_refresh_retains_loaded_route(self):
    session = FailingSession()
    engine = make_engine(session)
    assert engine._load_route_response(route_response())
    original_route = engine.route
    original_geometry = engine.route_geometry
    engine.last_position = Coordinate(22.3000, 114.1000)

    assert not engine.calculate_route(Coordinate(22.3010, 114.1010), "pk.test-token-that-is-long-enough")
    assert engine.route is original_route
    assert engine.route_geometry is original_geometry
    assert engine.step_idx == 0

  def test_offline_reroute_does_not_discard_loaded_route_or_call_mapbox(self):
    session = FailingSession()
    params = FakeParams()
    destination = Coordinate(22.3010, 114.1010)
    params.values["NavDestination"] = destination.as_dict()
    engine = make_engine(session, params=params)
    engine.nav_destination = destination
    assert engine._load_route_response(route_response())
    original_route = engine.route
    engine.last_position = Coordinate(22.4000, 114.2000)  # intentionally off route
    engine.gps_ok = True

    engine.recompute_route(network_available=False)

    assert session.calls == 0
    assert engine.route is original_route
    assert engine.step_idx == 0

  def test_network_gate_accepts_only_wifi_and_cellular(self):
    online_types = (log.DeviceState.NetworkType.wifi, log.DeviceState.NetworkType.cell2G,
                    log.DeviceState.NetworkType.cell3G, log.DeviceState.NetworkType.cell4G,
                    log.DeviceState.NetworkType.cell5G)
    for network_type in online_types:
      with self.subTest(network_type=network_type):
        engine = RouteEngine(NetworkSM(network_type), FakePM(), params=cast(Params, FakeParams()),
                             cache_params=cast(Params, FakeParams()))
        assert engine.network_available()

    for network_type in (log.DeviceState.NetworkType.none, log.DeviceState.NetworkType.ethernet):
      with self.subTest(network_type=network_type):
        engine = RouteEngine(NetworkSM(network_type), FakePM(), params=cast(Params, FakeParams()),
                             cache_params=cast(Params, FakeParams()))
        assert not engine.network_available()

  def test_route_response_rejects_empty_data_without_overwriting_route(self):
    engine = make_engine()
    assert engine._load_route_response(route_response())
    original_route = engine.route

    assert not engine._load_route_response({"routes": []})
    assert engine.route is original_route

  def test_loaded_route_produces_local_turn_instruction(self):
    pm = FakePM()
    engine = make_engine(pm=pm)
    assert engine._load_route_response(route_response())
    engine.last_position = Coordinate(22.3000, 114.1000)
    engine.localizer_valid = True

    engine.send_instruction()

    message = pm.messages["navInstruction"]
    assert message.valid
    assert message.navInstruction.maneuverType == "turn"
    assert message.navInstruction.maneuverModifier == "left"
    assert message.navInstruction.maneuverDistance == 100.0
    assert message.navInstruction.distanceRemaining == 180.0
    assert len(message.navInstruction.allManeuvers) == 1

  def test_missing_typical_duration_falls_back_to_route_duration(self):
    response = route_response()
    response["routes"][0]["legs"][0]["steps"][0]["duration_typical"] = None
    pm = FakePM()
    engine = make_engine(pm=pm)
    assert engine._load_route_response(response)
    engine.last_position = Coordinate(22.3000, 114.1000)

    engine.send_instruction()

    assert pm.messages["navInstruction"].navInstruction.timeRemainingTypical == 22.0

  def test_cached_route_restores_for_same_destination(self):
    params = FakeParams()
    destination = Coordinate(22.3010, 114.1010)
    params.values["NavDestination"] = destination.as_dict()
    engine = make_engine(params=params)
    assert engine._load_route_response(route_response())
    engine._cache_route(destination, route_response())

    restored_pm = FakePM()
    restored = make_engine(params=params, pm=restored_pm)

    assert restored.step_idx == 0
    assert restored.route is not None
    assert restored.nav_destination == destination
    assert restored_pm.messages["navRoute"].valid
