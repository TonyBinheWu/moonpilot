import unittest

from openpilot.cereal import log, custom
from openpilot.cereal.services import SERVICE_LIST


class TestNavigationSchema(unittest.TestCase):
  def test_navigation_log_roundtrip(self):
    for status in ('off', 'waitingGps', 'routing', 'active', 'offRoute', 'arrived', 'error'):
      event = log.Event.new_message()
      event.init('navigationStateSP')
      event.valid = status == 'active'
      event.navigationStateSP = {'status': status, 'routeId': 'route-1', 'instruction': '右轉', 'maneuverDistance': 10,
                                 'stepIndex': 1, 'maneuverType': 'turn', 'modifier': 'right'}
      with log.Event.from_bytes(event.to_bytes()) as reader:
        self.assertEqual(reader.navigationStateSP.status, status)
        self.assertEqual(reader.navigationStateSP.instruction, '右轉')
        self.assertEqual(reader.valid, status == 'active')
    self.assertTrue(SERVICE_LIST['navigationStateSP'].should_log)

  def test_desire_enum_contract_and_default(self):
    self.assertEqual(log.Desire.turnLeft, 1)
    self.assertEqual(log.Desire.turnRight, 2)
    self.assertEqual(custom.ModelDataV2SP.TurnDirection.turnLeft, 1)
    self.assertEqual(custom.ModelDataV2SP.TurnDirection.turnRight, 2)
    self.assertEqual(custom.ModelDataV2SP.new_message().navigationTurn, 'none')


if __name__ == '__main__':
  unittest.main()
