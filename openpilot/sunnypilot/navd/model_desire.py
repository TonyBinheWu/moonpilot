from __future__ import annotations

import math

from openpilot.cereal import log
from openpilot.common.params import Params


SUPPORTED_MANEUVER_TYPES = {
  "turn",
  "fork",
  "off ramp",
  "on ramp",
  "end of road",
}


def maneuver_to_desire(maneuver_type: str, maneuver_modifier: str) -> log.Desire:
  """Map an unambiguous navigation maneuver to an existing model desire."""
  if maneuver_type.strip().lower() not in SUPPORTED_MANEUVER_TYPES:
    return log.Desire.none

  modifier = maneuver_modifier.strip().lower()
  if "uturn" in modifier or ("left" in modifier and "right" in modifier):
    return log.Desire.none
  if "left" in modifier:
    return log.Desire.turnLeft
  if "right" in modifier:
    return log.Desire.turnRight
  return log.Desire.none


def activation_distance(v_ego: float) -> float:
  """Keep turn intent close to the maneuver while allowing more lead time at speed."""
  return max(25.0, min(90.0, max(0.0, v_ego) * 4.0))


class NavigationDesireController:
  PARAM_REFRESH_FRAMES = 20

  def __init__(self, params: Params | None = None) -> None:
    self.params = params or Params()
    self._frame = 0
    self.navigation_enabled = False
    self.model_intent_enabled = False
    self._refresh_params()

  def _refresh_params(self) -> None:
    self.navigation_enabled = self.params.get_bool("NavigationEnabled")
    self.model_intent_enabled = self.params.get_bool("NavigationModelIntent")

  def update(self, nav_instruction, car_state, v_ego: float, nav_alive: bool, nav_valid: bool) -> log.Desire:
    self._frame += 1
    if self._frame % self.PARAM_REFRESH_FRAMES == 0:
      self._refresh_params()

    if not (self.navigation_enabled and self.model_intent_enabled and nav_alive and nav_valid):
      return log.Desire.none

    try:
      distance = float(nav_instruction.maneuverDistance)
    except (AttributeError, TypeError, ValueError):
      return log.Desire.none

    if not math.isfinite(distance) or distance < 0.0 or distance > activation_distance(v_ego):
      return log.Desire.none

    desire = maneuver_to_desire(str(nav_instruction.maneuverType), str(nav_instruction.maneuverModifier))
    if desire == log.Desire.none:
      return desire

    # Hazard lights and a deliberate opposite turn signal cancel navigation intent.
    left_blinker = bool(getattr(car_state, "leftBlinker", False))
    right_blinker = bool(getattr(car_state, "rightBlinker", False))
    if left_blinker and right_blinker:
      return log.Desire.none
    if left_blinker != right_blinker:
      if left_blinker and desire != log.Desire.turnLeft:
        return log.Desire.none
      if right_blinker and desire != log.Desire.turnRight:
        return log.Desire.none

    return desire
