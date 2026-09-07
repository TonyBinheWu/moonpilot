from types import SimpleNamespace
import unittest

from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.values import CAR
from opendbc.sunnypilot.car.hyundai.blinkers import configure_model_blinkers
from openpilot.sunnypilot.selfdrive.controls.lib.model_blinkers import hkg_model_blinkers


class TestBlinkerIntent(unittest.TestCase):
  def setUp(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[CanBus(None, fingerprint).CAM][0x50] = 16
    self.cp = CarInterface.get_params(CAR.KIA_EV6, fingerprint, [], False, False, False)
    configure_model_blinkers(self.cp, True)
    self.cs = SimpleNamespace(canValid=True, brakePressed=False, gasPressed=False, leftBlinker=False, rightBlinker=False,
                              leftBlindspot=False, rightBlindspot=False, steeringPressed=False, steeringTorque=0)

  def call(self, **kwargs):
    arguments = {'lateral_active': True, 'fresh': True, 'lane_state': 2, 'lane_direction': 1, 'turn_direction': 0}
    return hkg_model_blinkers(self.cp, self.cs, **{**arguments, **kwargs})

  def test_only_accepted_maneuvers(self):
    self.assertEqual(self.call(), (True, False))
    self.assertEqual(self.call(lane_direction=2), (False, True))
    self.assertEqual(self.call(lane_state=1), (False, False))
    self.assertEqual(self.call(lane_state=0, turn_direction=2), (False, True))
    self.assertEqual(self.call(turn_direction=2), (False, False))

  def test_inactive_stale_and_disabled(self):
    for argument in [{'fresh': False}, {'lateral_active': False}]:
      self.assertEqual(self.call(**argument), (False, False))
    configure_model_blinkers(self.cp, False)
    self.assertEqual(self.call(), (False, False))

  def test_driver_priority_and_blindspot(self):
    for field in ['rightBlinker', 'leftBlindspot', 'brakePressed', 'gasPressed']:
      self.setUp()
      setattr(self.cs, field, True)
      self.assertEqual(self.call(), (False, False))
    self.setUp()
    self.cs.leftBlinker = self.cs.rightBlinker = True
    self.assertEqual(self.call(), (False, False))
    self.setUp()
    self.cs.steeringPressed, self.cs.steeringTorque = True, -1
    self.assertEqual(self.call(), (False, False))


if __name__ == '__main__':
  unittest.main()
