from concurrent.futures import Future
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from openpilot.sunnypilot.navd.osrm import OsrmClient, RoutingError, routing_endpoint
from openpilot.sunnypilot.navd.navigationd import NavigationEngine, gps_sample
from openpilot.sunnypilot.navd.desire import NavigationTurn
from openpilot.sunnypilot.navd.tests.test_navigation import route_data
from openpilot.system.qcomgpsd.accuracy import horizontal_accuracy


class TestOsm(unittest.TestCase):
  def setUp(self):
    self.data = copy.deepcopy(route_data())
    for step in self.data['routes'][0]['legs'][0]['steps']:
      step['maneuver'].pop('instruction')  # OSRM has maneuver metadata, not Mapbox instructions.

  def client(self):
    self.opener = Mock(return_value=io.BytesIO(json.dumps(self.data).encode()))
    return OsrmClient(opener=self.opener)

  def test_tokenless_route_to_confirmed_turn_and_cancel(self):
    route = self.client().route([121, 25], [121.001, 25.001], 90)
    query = parse_qs(urlsplit(self.opener.call_args.args[0].full_url).query)
    self.assertNotIn('access_token', query)
    self.assertEqual(query['steps'], ['true'])
    progress = None
    for i in range(10):
      progress = route.update([121 + i * .0001, 25], i, 5, 90)
    nav = SimpleNamespace(**{**progress, 'routeId': 'fixture', 'status': 'active'})
    cs = SimpleNamespace(vEgo=5, steeringPressed=False, steeringTorque=0, canValid=True,
      leftBlinker=False, rightBlinker=False, leftBlindspot=False, rightBlindspot=False,
      brakePressed=False, gasPressed=False, steerFaultTemporary=False, steerFaultPermanent=False, buttonEvents=[])
    turn = NavigationTurn()
    def update(now):
      return turn.update(nav, cs, now, enabled=True, fresh=True, lateral_active=True)
    self.assertEqual(update(9), 0)
    cs.steeringPressed, cs.steeringTorque = True, 1
    self.assertEqual(update(10), 1)
    cs.buttonEvents = [SimpleNamespace(type='cancel', pressed=True)]
    self.assertEqual(update(11), 0)
    cs.buttonEvents = []
    self.assertEqual(update(12), 0)

  def test_invalid_endpoints_and_server_responses(self):
    for endpoint in ['http://example.org', 'https://user:secret@example.org', 'https://example.org?key=secret', 'file:///tmp/x']:
      with self.subTest(endpoint=endpoint), self.assertRaises(RoutingError):
        routing_endpoint(endpoint)
    for payload in [b'[]', b'{}', b'{"code":"NoRoute"}', b'invalid', b' ' * (16 * 1024 * 1024 + 1)]:
      with self.assertRaises(RoutingError):
        OsrmClient(opener=Mock(return_value=io.BytesIO(payload))).route([121, 25], [121, 26])
    with self.assertRaises(RoutingError) as caught:
      OsrmClient(opener=Mock(side_effect=URLError('PRIVATE LOCATION'))).route([121, 25], [121, 26])
    self.assertNotIn('PRIVATE', str(caught.exception))

  def test_provider_switch_invalidates_old_route_and_throttles(self):
    engine = NavigationEngine()
    engine.executor.shutdown()
    engine.executor = Mock()
    old, new = Future(), Future()
    engine.executor.submit.side_effect = [old, new]
    args = {'enabled': True, 'token': 'pk.TEST', 'destination': {'longitude': 121.001, 'latitude': 25.001},
            'gps': {'position': [121, 25], 'speed': 0}}
    engine.update(**args, now=0)
    old.set_result(self.client().route([121, 25], [121.001, 25.001]))
    args['token'] = ''
    self.assertFalse(engine.update(**args, now=.5, provider='osm')['valid'])
    self.assertEqual(engine.executor.submit.call_count, 1)
    engine.update(**args, now=2, provider='osm')
    new.set_result(self.client().route([121, 25], [121.001, 25.001]))
    state = engine.update(**args, now=3, provider='osm')
    self.assertTrue(state['valid'])
    self.assertEqual(state['provider'], 'osm')
    self.assertFalse(engine.update(**args, now=4, provider='unknown')['valid'])


class TestGpsAccuracy(unittest.TestCase):
  def test_modem_metres_and_consumer_gate(self):
    report = {'u_HorizontalReliability': 3, 'u_EllipseConfidence': 95,
              'q_FltEllipseSemimajorAxis': 4.0, 'q_FltEllipseSemiminorAxis': 2.0}
    accuracy = horizontal_accuracy(report)
    self.assertEqual(accuracy, 4.0)
    sample = SimpleNamespace(hasFix=True, longitude=121, latitude=25, speed=5, bearingDeg=90, horizontalAccuracy=accuracy)
    self.assertIsNotNone(gps_sample(sample, True, 10_000_000_000, 11))
    for key, value in [('u_HorizontalReliability', 2), ('u_HorizontalReliability', 255),
                       ('u_EllipseConfidence', 0), ('u_EllipseConfidence', 67), ('u_EllipseConfidence', 101),
                       ('q_FltEllipseSemimajorAxis', float('nan')), ('q_FltEllipseSemiminorAxis', 0)]:
      with self.subTest(key=key):
        sample.horizontalAccuracy = horizontal_accuracy({**report, key: value})
        self.assertEqual(sample.horizontalAccuracy, 0)
        self.assertIsNone(gps_sample(sample, True, 10_000_000_000, 11))
    sample.horizontalAccuracy = 21
    self.assertIsNone(gps_sample(sample, True, 10_000_000_000, 11))


if __name__ == '__main__':
  unittest.main()
