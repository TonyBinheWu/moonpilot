"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import custom

from openpilot.common.constants import CV
from openpilot.common.params import Params

TurnDirection = custom.ModelDataV2SP.TurnDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
MAP_TURN_INTENT_SPEED_LIMIT_MAX = 70 * CV.KPH_TO_MS
MAP_TURN_INTENT_SPEED_MAX = 75 * CV.KPH_TO_MS

HIGHWAY_ROAD_NAME_HINTS = (
  "highway", "freeway", "motorway", "expressway", "hwy", "fwy", "expwy",
  "高速", "國道", "快速道路", "快速路", "高架", "交流道", "interchange",
)

CITY_ROAD_NAME_HINTS = (
  "road", "rd", "street", "st", "avenue", "ave", "boulevard", "blvd", "lane", "ln",
  "路", "街", "巷", "弄", "大道", "段", "橋", "隧道",
)


class LaneTurnController:
  def __init__(self, desire_helper):
    self.DH = desire_helper
    self.turn_direction = TurnDirection.none
    self.params = Params()
    self.lane_turn_value = float(self.params.get("LaneTurnValue", return_default=True)) * CV.MPH_TO_MS
    self.param_read_counter = 0
    self.enabled = self.params.get_bool("LaneTurnDesire")
    self.map_speed_limit = 0.0
    self.road_name = ""
    self.map_turn_context_active = False

  @staticmethod
  def _has_any_hint(value: str, hints: tuple[str, ...]) -> bool:
    road = value.lower()
    return any(hint in road for hint in hints)

  def read_params(self):
    self.enabled = self.params.get_bool("LaneTurnDesire")
    value = float(self.params.get("LaneTurnValue", return_default=True)) * CV.MPH_TO_MS
    self.lane_turn_value = min(float(LANE_CHANGE_SPEED_MIN), value)
    self.map_speed_limit = float(self.params.get("MapSpeedLimit", return_default=True) or 0.0)
    self.road_name = self.params.get("RoadName", return_default=True) or ""

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def _map_turn_context(self, v_ego: float) -> bool:
    if v_ego < self.lane_turn_value:
      return True

    if v_ego > MAP_TURN_INTENT_SPEED_MAX:
      return False

    if self.road_name and self._has_any_hint(self.road_name, HIGHWAY_ROAD_NAME_HINTS):
      return False

    if 0.0 < self.map_speed_limit <= MAP_TURN_INTENT_SPEED_LIMIT_MAX:
      return True

    if self.road_name and self._has_any_hint(self.road_name, CITY_ROAD_NAME_HINTS):
      return True

    return False

  def update_lane_turn(self, blindspot_left: bool, blindspot_right: bool, left_blinker: bool, right_blinker: bool, v_ego: float) -> None:
    self.map_turn_context_active = self._map_turn_context(v_ego)
    turn_context_active = self.enabled or self.map_turn_context_active

    if left_blinker and not right_blinker and turn_context_active and not blindspot_left:
      self.turn_direction = TurnDirection.turnLeft
    elif right_blinker and not left_blinker and turn_context_active and not blindspot_right:
      self.turn_direction = TurnDirection.turnRight
    else:
      self.turn_direction = TurnDirection.none

  def get_turn_direction(self):
    if not (self.enabled or self.map_turn_context_active):
      return TurnDirection.none
    return self.turn_direction
