from types import SimpleNamespace
from typing import cast
import unittest

from openpilot.cereal import log
from openpilot.common.parameterized import parameterized
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.model_desire import NavigationDesireController, activation_distance, maneuver_to_desire


class FakeParams:
  def __init__(self, navigation: bool = True, model_intent: bool = True):
    self.values = {
      "NavigationEnabled": navigation,
      "NavigationModelIntent": model_intent,
    }

  def get_bool(self, key: str) -> bool:
    return self.values[key]


def instruction(maneuver_type: str = "turn", modifier: str = "left", distance: float = 20.0):
  return SimpleNamespace(maneuverType=maneuver_type, maneuverModifier=modifier, maneuverDistance=distance)


def car_state(left: bool = False, right: bool = False):
  return SimpleNamespace(leftBlinker=left, rightBlinker=right)


class TestNavigationDesireController(unittest.TestCase):
  @parameterized.expand([
    ("turn", "left", log.Desire.turnLeft),
    ("fork", "slight right", log.Desire.turnRight),
    ("off ramp", "sharp left", log.Desire.turnLeft),
    ("roundabout", "right", log.Desire.none),
    ("merge", "left", log.Desire.none),
    ("turn", "uturn", log.Desire.none),
    ("turn", "left uturn", log.Desire.none),
    ("turn", "left or right", log.Desire.none),
  ])
  def test_maneuver_mapping(self, maneuver_type, modifier, expected):
    assert maneuver_to_desire(maneuver_type, modifier) == expected

  def test_activation_distance_scales_and_is_bounded(self):
    assert activation_distance(0.0) == 25.0
    assert activation_distance(15.0) == 60.0
    assert activation_distance(40.0) == 90.0

  def test_requires_both_switches_and_live_instruction(self):
    controller = NavigationDesireController(cast(Params, FakeParams(model_intent=False)))
    assert controller.update(instruction(), car_state(), 10.0, True, True) == log.Desire.none

    controller = NavigationDesireController(cast(Params, FakeParams()))
    assert controller.update(instruction(), car_state(), 10.0, False, True) == log.Desire.none
    assert controller.update(instruction(), car_state(), 10.0, True, False) == log.Desire.none

  def test_only_activates_inside_speed_scaled_window(self):
    controller = NavigationDesireController(cast(Params, FakeParams()))
    assert controller.update(instruction(distance=61.0), car_state(), 15.0, True, True) == log.Desire.none
    assert controller.update(instruction(distance=60.0), car_state(), 15.0, True, True) == log.Desire.turnLeft
    assert controller.update(instruction(distance=-0.1), car_state(), 15.0, True, True) == log.Desire.none

  def test_opposite_manual_signal_cancels_navigation_intent(self):
    controller = NavigationDesireController(cast(Params, FakeParams()))
    assert controller.update(instruction(modifier="left"), car_state(right=True), 10.0, True, True) == log.Desire.none
    assert controller.update(instruction(modifier="left"), car_state(left=True), 10.0, True, True) == log.Desire.turnLeft
    assert controller.update(instruction(modifier="left"), car_state(left=True, right=True), 10.0, True, True) == log.Desire.none
