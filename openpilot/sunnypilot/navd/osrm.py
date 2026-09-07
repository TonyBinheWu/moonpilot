"""OSM route guidance through OSRM; no learned navigation features are invented.

API: https://project-osrm.org/docs/v5.24.0/api/#route-service
The default endpoint is the OSRM demonstration service, not an offline router.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from openpilot.sunnypilot.navd.route import Route, coordinate

DEFAULT_OSRM_URL = "https://router.project-osrm.org"


class RoutingError(Exception):
  pass


def routing_endpoint(value):
  value = (value or DEFAULT_OSRM_URL).strip().rstrip('/')
  parsed = urlsplit(value)
  if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
    raise RoutingError("Use an HTTPS OSRM server URL without credentials or query parameters.")
  return value


class OsrmClient:
  def __init__(self, endpoint=DEFAULT_OSRM_URL, opener=urlopen):
    self.endpoint = routing_endpoint(endpoint)
    self.opener = opener

  def route(self, start, destination, bearing=None):
    start, destination = coordinate(start), coordinate(destination)
    query = {'geometries': 'geojson', 'overview': 'full', 'steps': 'true', 'alternatives': 'false', 'generate_hints': 'false'}
    if bearing is not None:
      query['bearings'] = f'{round(bearing) % 360},90;'
    path = f'/route/v1/driving/{start[0]},{start[1]};{destination[0]},{destination[1]}'
    request = Request(self.endpoint + path + '?' + urlencode(query), headers={'User-Agent': 'sunnypilot-navigation/1.0'})
    try:
      with self.opener(request, timeout=8) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
      if len(payload) > 16 * 1024 * 1024:
        raise RoutingError("OSM routing response is too large")
      return Route(json.loads(payload))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
      # Do not expose request URLs, locations or server error bodies to logs.
      raise RoutingError("Unable to obtain an OSM route. Check the server and network.") from None
