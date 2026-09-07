"""Driver-confirmed low-speed turn pulses, not a route-conditioned model.

Both model runners use their existing turn-desire inputs. No synthetic carState
blinkers, highway lane changes, direct curvature commands, or longitudinal input.
"""
import math


class NavigationTurn:
  def __init__(self):
    self.key = None
    self.used = False
    self.until = 0.0
    self.pressed = True  # a held wheel at startup is not a new confirmation

  def cancel(self):
    self.used = True
    self.until = 0.0
    self.pressed = True

  def update(self, nav, cs, now, *, enabled, fresh, lateral_active, existing_desire=0, lane_change_active=False):
    rising = cs.steeringPressed and not self.pressed
    self.pressed = cs.steeringPressed
    key = (nav.routeId, nav.stepIndex)
    if key != self.key:
      self.key = key
      self.used = False
      self.until = 0.0
      rising = False  # require a nudge after this maneuver was shown
    left = nav.modifier == "left"
    turn = 1 if left else 2  # log.Desire.turnLeft/turnRight
    opposite_signal = cs.rightBlinker if left else cs.leftBlinker
    blindspot = cs.leftBlindspot if left else cs.rightBlindspot
    opposite_torque = cs.steeringPressed and (cs.steeringTorque < 0 if left else cs.steeringTorque > 0)
    allowed = (enabled and fresh and lateral_active and nav.status == "active" and bool(nav.routeId) and
               nav.maneuverType == "turn" and nav.modifier in ("left", "right") and
               math.isfinite(nav.maneuverDistance) and math.isfinite(cs.vEgo) and math.isfinite(cs.steeringTorque) and
               0.3 < cs.vEgo < 8.0 and 0 <= nav.maneuverDistance <= min(30.0, max(8.0, cs.vEgo * 3.0)) and
               cs.canValid and not (cs.brakePressed or cs.gasPressed or cs.steerFaultTemporary or cs.steerFaultPermanent) and
               not (opposite_signal or blindspot or opposite_torque or lane_change_active) and existing_desire == 0)
    if not allowed:
      self.until = 0.0
      return 0
    correct_torque = cs.steeringTorque > 0 if left else cs.steeringTorque < 0
    if rising and correct_torque and not self.used:
      self.used = True
      self.until = now + 8.0
    return turn if now < self.until else 0
