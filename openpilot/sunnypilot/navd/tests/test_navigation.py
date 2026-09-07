import copy
from concurrent.futures import Future
import io
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from openpilot.sunnypilot.navd.route import Route, coordinate
from openpilot.sunnypilot.navd.mapbox import MapboxClient, MapboxError
from openpilot.sunnypilot.navd.navigationd import NavigationEngine, gps_sample
from openpilot.sunnypilot.navd.desire import NavigationTurn


def route_data():
  # About 100 m east, then 100 m north; arrival can have duplicate geometry.
  return {"code": "Ok", "routes": [{"duration": 60, "legs": [{"steps": [
    {"maneuver": {"type": "depart", "instruction": "向東"}, "geometry": {"coordinates": [[121, 25], [121.001, 25]]}},
    {"maneuver": {"type": "turn", "modifier": "left", "instruction": "左轉"},
     "geometry": {"coordinates": [[121.001, 25], [121.001, 25.001]]}},
    {"maneuver": {"type": "arrive", "instruction": "抵達"}, "geometry": {"coordinates": [[121.001, 25.001], [121.001, 25.001]]}},
  ]}]}]}


class TestRoute(unittest.TestCase):
  def test_progress_turn_and_arrival(self):
    route = Route(route_data())
    for i in range(10):
      result = route.update([121 + i * .0001, 25], i, 10, 90)
      self.assertEqual(result['modifier'], 'left')
      self.assertEqual(result['stepIndex'], 1)
    self.assertLess(result['maneuverDistance'], 12)
    for i in range(11):
      result = route.update([121.001, 25 + i * .0001], 10 + i, 10, 0)
      self.assertIsNotNone(result)
    self.assertTrue(result['arrived'])

  def test_wrong_heading_offroute_stale_and_jump(self):
    for position, now, heading in [([121, 25], 0, 270), ([121, 25.001], 0, 90), ([121.001, 25], 0, 90)]:
      with self.subTest(position=position, heading=heading):
        self.assertIsNone(Route(route_data()).update(position, now, 10, heading))
    route = Route(route_data())
    route.update([121, 25], 1, 0)
    self.assertIsNone(route.update([121, 25], 7, 0))

  def test_nan_discontinuous_and_empty(self):
    for value in [[math.nan, 25], [181, 25], [121, 91]]:
      with self.assertRaises(ValueError):
        coordinate(value)
    for mutate in ('gap', 'empty'):
      data = route_data()
      data['routes'][0]['legs'][0]['steps'][1]['geometry']['coordinates'] = [] if mutate == 'empty' else [[122, 26], [122, 26.001]]
      with self.assertRaises(ValueError):
        Route(data)

  def test_roundabout_remains_navigation_only(self):
    data = route_data()
    data['routes'][0]['legs'][0]['steps'][1]['maneuver']['type'] = 'roundabout'
    result = Route(data).update([121, 25], 0)
    self.assertEqual(result['maneuverType'], 'roundabout')


class TestClient(unittest.TestCase):
  def test_route_request_and_language(self):
    opener = Mock(return_value=io.BytesIO(json.dumps(route_data()).encode()))
    route = MapboxClient('pk.TEST', opener).route([121, 25], [121.001, 25.001], 450)
    self.assertIsInstance(route, Route)
    url = opener.call_args.args[0]
    query = parse_qs(urlparse(url).query)
    self.assertIn('121.0,25.0;121.001,25.001', url)
    self.assertEqual(query['language'], ['en'])
    self.assertEqual(query['bearings'], ['90,90;'])
    self.assertEqual(opener.call_args.kwargs['timeout'], 8)

  def test_requested_language(self):
    opener = Mock(return_value=io.BytesIO(json.dumps(route_data()).encode()))
    MapboxClient('pk.TEST', opener, language='zh-CHT').route([121, 25], [121.001, 25.001])
    self.assertEqual(parse_qs(urlparse(opener.call_args.args[0]).query)['language'], ['zh-TW'])

  def test_error_redacts_token(self):
    for error in [HTTPError('https://example/?access_token=SECRET', 401, 'SECRET', {}, None), URLError('SECRET'), ValueError('SECRET')]:
      with self.subTest(error=type(error)):
        with self.assertRaises(MapboxError) as caught:
          MapboxClient('pk.SECRET', Mock(side_effect=error)).route([121, 25], [122, 25])
        self.assertNotIn('SECRET', str(caught.exception))

  def test_malformed_response(self):
    for response in [b'not json', b'[]', b'{}', b'{"code":"NoRoute"}']:
      with self.assertRaises(MapboxError):
        MapboxClient('pk.TEST', Mock(return_value=io.BytesIO(response))).route([121, 25], [122, 25])

  def test_search_preserves_all_choices(self):
    data = {"features": [{"geometry": {"coordinates": [121, 25]}, "properties": {"full_address": '地址 A'}},
                         {"geometry": {"coordinates": [122, 26]}, "properties": {"full_address": '地址 B'}}]}
    opener = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
    results = MapboxClient('pk.TEST', opener).search_address('地址')
    self.assertEqual(len(results), 2)
    self.assertEqual(results[1]['name'], '地址 B')
    self.assertEqual(parse_qs(urlparse(opener.call_args.args[0]).query)['permanent'], ['true'])


class TestEngine(unittest.TestCase):
  def setUp(self):
    self.engine = NavigationEngine()
    self.engine.executor.shutdown()
    self.executor = Mock()
    self.engine.executor = self.executor
    self.dest = {"longitude": 121.001, "latitude": 25.001}
    self.gps = {"position": [121, 25], "speed": 0}

  def update(self, now=0, **kwargs):
    args = {'enabled': True, 'token': 'pk.TEST', 'destination': self.dest, 'gps': self.gps, 'now': now}
    args.update(kwargs)
    return self.engine.update(**args)

  def test_nonblocking_and_superseded_result(self):
    old, new = Future(), Future()
    self.executor.submit.side_effect = [old, new]
    self.assertEqual(self.update()['status'], 'routing')
    self.assertEqual(self.update(1)['status'], 'routing')
    self.assertEqual(self.executor.submit.call_count, 1)
    self.dest = {"longitude": 122, "latitude": 26}
    self.update(2)
    old.set_result(Route(route_data()))
    self.assertFalse(self.update(3)['valid'])
    self.assertEqual(self.executor.submit.call_count, 2)

  def test_cancel_and_gps_failure(self):
    future = Future()
    self.executor.submit.return_value = future
    self.update()
    future.set_result(Route(route_data()))
    self.assertTrue(self.update(1)['valid'])
    self.assertEqual(self.update(2, gps=None)['status'], 'waitingGps')
    self.assertFalse(self.update(3, destination=None)['valid'])
    self.assertIsNone(self.engine.route)

  def test_network_backoff_and_recovery(self):
    failure, success = Future(), Future()
    self.executor.submit.side_effect = [failure, success]
    self.update()
    failure.set_exception(MapboxError('SECRET'))
    status = self.update(1)
    self.assertEqual(status['status'], 'error')
    self.assertNotIn('SECRET', status['instruction'])
    self.update(2)
    self.assertEqual(self.executor.submit.call_count, 1)
    self.update(12)
    success.set_result(Route(route_data()))
    self.assertTrue(self.update(13)['valid'])

  def test_invalid_gps(self):
    sample = SimpleNamespace(hasFix=True, longitude=121, latitude=25, horizontalAccuracy=3, speed=5, bearingDeg=90)
    self.assertIsNotNone(gps_sample(sample, True, 10_000_000_000, 11))
    for key, value in [('hasFix', False), ('latitude', math.nan), ('speed', math.nan), ('horizontalAccuracy', 30)]:
      invalid = copy.copy(sample)
      setattr(invalid, key, value)
      self.assertIsNone(gps_sample(invalid, True, 10_000_000_000, 11))
    self.assertIsNone(gps_sample(sample, True, 10_000_000_000, 15))


class TestNavigationTurn(unittest.TestCase):
  def setUp(self):
    self.turn = NavigationTurn()
    self.nav = SimpleNamespace(routeId='route-a', stepIndex=1, status='active', maneuverType='turn', modifier='left', maneuverDistance=10)
    self.cs = SimpleNamespace(steeringPressed=False, steeringTorque=0, leftBlinker=False, rightBlinker=False,
                              leftBlindspot=False, rightBlindspot=False, vEgo=5, canValid=True, brakePressed=False,
                              gasPressed=False, steerFaultTemporary=False, steerFaultPermanent=False)

  def update(self, now=0, **kwargs):
    return self.turn.update(self.nav, self.cs, now, **{'enabled': True, 'fresh': True, 'lateral_active': True, **kwargs})

  def confirm(self):
    self.update()
    self.cs.steeringPressed, self.cs.steeringTorque = True, 1
    return self.update(1)

  def test_requires_nudge_and_is_single_use(self):
    self.assertEqual(self.update(), 0)
    self.assertEqual(self.confirm(), 1)
    self.assertEqual(self.update(2), 1)
    self.assertEqual(self.update(10), 0)
    self.cs.steeringPressed = False
    self.update(11)
    self.cs.steeringPressed = True
    self.assertEqual(self.update(12), 0)

  def test_cancel_and_no_resume_on_stale_pedals_manual_or_conflict(self):
    for field, value in [('brakePressed', True), ('gasPressed', True), ('rightBlinker', True), ('leftBlindspot', True),
                         ('steeringTorque', -1), ('vEgo', 15), ('canValid', False), ('steerFaultTemporary', True),
                         ('buttonEvents', [SimpleNamespace(type='cancel', pressed=True)])]:
      self.setUp()
      self.assertEqual(self.confirm(), 1)
      setattr(self.cs, field, value)
      self.assertEqual(self.update(2), 0, field)
    for argument in [{'fresh': False}, {'enabled': False}, {'lateral_active': False}, {'existing_desire': 3}, {'lane_change_active': True}]:
      self.setUp()
      self.assertEqual(self.confirm(), 1)
      self.assertEqual(self.update(2, **argument), 0)

  def test_new_route_and_unsupported_maneuvers(self):
    self.assertEqual(self.confirm(), 1)
    self.nav.routeId = 'route-b'
    self.assertEqual(self.update(2), 0)
    for kind, modifier in [('off ramp', 'right'), ('roundabout', 'left'), ('turn', 'uturn'), ('fork', 'left'), ('turn', 'slight left')]:
      self.setUp()
      self.nav.maneuverType, self.nav.modifier = kind, modifier
      self.assertEqual(self.confirm(), 0)

  def test_right_turn(self):
    self.nav.modifier = 'right'
    self.update()
    self.cs.steeringPressed, self.cs.steeringTorque = True, -1
    self.assertEqual(self.update(1), 2)

  def test_model_fallback_does_not_reissue_turn(self):
    self.assertEqual(self.confirm(), 1)
    self.turn.cancel()
    self.assertEqual(self.update(2), 0)
    self.cs.steeringPressed = False
    self.update(3)
    self.cs.steeringPressed = True
    self.assertEqual(self.update(4), 0)


if __name__ == '__main__':
  unittest.main()
