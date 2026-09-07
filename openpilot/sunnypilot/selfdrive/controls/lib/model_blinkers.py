from opendbc.car.hyundai.values import HyundaiFlags
from opendbc.sunnypilot.car.hyundai.blinkers import supports_model_blinkers


def hkg_model_blinkers(CP, CS, lateral_active, fresh, lane_state, lane_direction, turn_direction):
  if not (supports_model_blinkers(CP) and CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS and
          lateral_active and fresh and CS.canValid):
    return False, False
  if CS.brakePressed or CS.gasPressed or (CS.leftBlinker and CS.rightBlinker):
    return False, False
  # Signal only an accepted maneuver, not every model probability/pre-lane-change.
  left = (lane_state == 2 and lane_direction == 1) or turn_direction == 1
  right = (lane_state == 2 and lane_direction == 2) or turn_direction == 2
  if left == right:
    return False, False
  if (left and (CS.rightBlinker or CS.leftBlindspot)) or (right and (CS.leftBlinker or CS.rightBlindspot)):
    return False, False
  if CS.steeringPressed and ((left and CS.steeringTorque < 0) or (right and CS.steeringTorque > 0)):
    return False, False
  return left, right
