"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import log

from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL


class AutoLaneChangeMode:
  OFF = -1
  NUDGE = 0  # default
  NUDGELESS = 1
  HALF_SECOND = 2
  ONE_SECOND = 3
  TWO_SECONDS = 4
  THREE_SECONDS = 5


AUTO_LANE_CHANGE_TIMER = {
  AutoLaneChangeMode.OFF: 0.0,            # Off
  AutoLaneChangeMode.NUDGE: 0.0,          # Nudge
  AutoLaneChangeMode.NUDGELESS: 0.05,     # Nudgeless
  AutoLaneChangeMode.HALF_SECOND: 0.5,    # 0.5-second delay
  AutoLaneChangeMode.ONE_SECOND: 1.0,     # 1-second delay
  AutoLaneChangeMode.TWO_SECONDS: 2.0,    # 2-second delay
  AutoLaneChangeMode.THREE_SECONDS: 3.0,  # 3-second delay
}

ONE_SECOND_DELAY = -1
MODEL_HIGHWAY_AUTO_LANE_CHANGE_SPEED_MIN = 80 * CV.KPH_TO_MS
MODEL_HIGHWAY_AUTO_LANE_CHANGE_CLEAR_DELAY = 1.0


class AutoLaneChangeController:
  def __init__(self, desire_helper, bsm_available: bool = False):
    self.DH = desire_helper
    self.params = Params()
    self.bsm_available = bsm_available

    self.lane_change_wait_timer = 0.0
    self.param_read_counter = 0
    self.lane_change_delay = 0.0
    self.model_highway_auto_lane_change_ready_timer = 0.0

    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    self.lane_change_bsm_delay = False
    self.model_highway_auto_lane_change_enabled = False

    self.prev_brake_pressed = False
    self.auto_lane_change_allowed = False
    self.prev_lane_change = False

    self.read_params()

  def reset(self) -> None:
    # Auto reset if parent state indicates we should
    if self.DH.lane_change_state == log.LaneChangeState.off and \
       self.DH.lane_change_direction == log.LaneChangeDirection.none:
      self.lane_change_wait_timer = 0.0
      self.model_highway_auto_lane_change_ready_timer = 0.0
      self.prev_brake_pressed = False
      self.prev_lane_change = False

  def read_params(self) -> None:
    self.lane_change_bsm_delay = self.params.get_bool("AutoLaneChangeBsmDelay")
    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    self.model_highway_auto_lane_change_enabled = self.params.get_bool("ModelHighwayAutoLaneChange")

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def update_lane_change_timers(self, blindspot_detected: bool, v_ego: float) -> None:
    self.lane_change_delay = AUTO_LANE_CHANGE_TIMER.get(self.lane_change_set_timer,
                                                        AUTO_LANE_CHANGE_TIMER[AutoLaneChangeMode.NUDGE])

    self.lane_change_wait_timer += DT_MDL

    if blindspot_detected or v_ego < MODEL_HIGHWAY_AUTO_LANE_CHANGE_SPEED_MIN:
      self.model_highway_auto_lane_change_ready_timer = 0.0
    else:
      self.model_highway_auto_lane_change_ready_timer += DT_MDL

    if self.lane_change_bsm_delay and blindspot_detected and self.lane_change_delay > 0:
      if self.lane_change_delay == AUTO_LANE_CHANGE_TIMER[AutoLaneChangeMode.NUDGELESS]:
        self.lane_change_wait_timer = ONE_SECOND_DELAY
      else:
        self.lane_change_wait_timer = self.lane_change_delay + ONE_SECOND_DELAY

  def model_highway_auto_lane_change_allowed(self) -> bool:
    return self.model_highway_auto_lane_change_enabled and \
           self.bsm_available and \
           self.model_highway_auto_lane_change_ready_timer > MODEL_HIGHWAY_AUTO_LANE_CHANGE_CLEAR_DELAY

  def update_allowed(self) -> bool:
    # Auto lane change allowed if:
    # 1. The legacy timer requested an automatic lane change, or the highway model toggle is active
    # 2. Brake wasn't previously pressed
    # 3. This is not immediately following another lane change
    # 4. Any mode-specific wait timer has elapsed

    if self.prev_brake_pressed:
      return False

    if self.prev_lane_change:
      return False

    if self.model_highway_auto_lane_change_allowed():
      return True

    if self.lane_change_set_timer in (AutoLaneChangeMode.OFF, AutoLaneChangeMode.NUDGE):
      return False

    return bool(self.lane_change_wait_timer > self.lane_change_delay)

  def update_lane_change(self, blindspot_detected: bool, brake_pressed: bool, v_ego: float) -> None:
    if brake_pressed and not self.prev_brake_pressed:
      self.prev_brake_pressed = brake_pressed

    self.update_lane_change_timers(blindspot_detected, v_ego)

    self.auto_lane_change_allowed = self.update_allowed()

  def update_state(self):
    if self.DH.lane_change_state == log.LaneChangeState.laneChangeStarting:
      self.prev_lane_change = True

    self.reset()
