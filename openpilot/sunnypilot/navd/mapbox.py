"""Bounded HTTPS client. Tokens and request URLs must never enter logs."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from openpilot.sunnypilot.navd.route import coordinate, Route


class MapboxError(Exception):
  pass


class MapboxClient:
  def __init__(self, token, opener=urlopen, language="en"):
    self.token = token.strip()
    self.opener = opener
    self.language = {"zh-CHT": "zh-TW", "zh-CHS": "zh-CN", "pt-BR": "pt"}.get(language, language)
    if self.language not in ("en", "zh-TW", "zh-CN", "de", "es", "fr", "ja", "ko", "pt", "th", "tr", "uk"):
      self.language = "en"

  def get(self, path, query):
    if not self.token.startswith("pk."):
      raise MapboxError("Set a Mapbox public token (pk.).")
    query = {**query, "access_token": self.token}
    try:
      with self.opener("https://api.mapbox.com/" + path + "?" + urlencode(query), timeout=8) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
      if len(payload) > 16 * 1024 * 1024:
        raise MapboxError("Mapbox response is too large")
      result = json.loads(payload)
      if not isinstance(result, dict):
        raise ValueError
      return result
    except HTTPError as e:
      raise MapboxError("Invalid Mapbox token or permissions" if e.code in (401, 403) else
                        "Mapbox rate limit reached. Try again later." if e.code == 429 else "Mapbox routing is temporarily unavailable") from None
    except (URLError, TimeoutError, OSError, ValueError):
      raise MapboxError("Unable to reach Mapbox. Check your network.") from None

  def search_address(self, text, proximity=None):
    language = {"zh-TW": "zh-Hant", "zh-CN": "zh-Hans"}.get(self.language, self.language)
    query = {"q": text.strip(), "limit": 5, "language": language, "autocomplete": "false", "permanent": "true"}
    if proximity is not None:
      query["proximity"] = ",".join(map(str, coordinate(proximity)))
    data = self.get("search/geocode/v6/forward", query)
    results = []
    try:
      for feature in data.get("features", []):
        lon, lat = coordinate(feature["geometry"]["coordinates"])
        props = feature["properties"]
        results.append({"longitude": lon, "latitude": lat, "name": str(props.get("full_address", props.get("name", text)))[:200]})
    except (ValueError, KeyError, TypeError):
      raise MapboxError("Incomplete address search response") from None
    return results

  def route(self, start, destination, bearing=None):
    start, destination = coordinate(start), coordinate(destination)
    query = {"geometries": "geojson", "overview": "full", "steps": "true", "alternatives": "false", "language": self.language}
    if bearing is not None:
      query["bearings"] = f"{round(bearing) % 360},90;"
    path = f"directions/v5/mapbox/driving/{start[0]},{start[1]};{destination[0]},{destination[1]}"
    data = self.get(path, query)
    try:
      return Route(data)
    except (KeyError, IndexError, ValueError, TypeError):
      raise MapboxError("No complete driving route found") from None
