"""Bounded HTTPS client. Tokens and request URLs must never enter logs."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from openpilot.sunnypilot.navd.route import coordinate, Route


class MapboxError(Exception):
  pass


class MapboxClient:
  def __init__(self, token, opener=urlopen):
    self.token = token.strip()
    self.opener = opener

  def get(self, path, query):
    if not self.token.startswith("pk."):
      raise MapboxError("請設定 Mapbox 公開權杖（pk.）")
    query = {**query, "access_token": self.token}
    try:
      with self.opener("https://api.mapbox.com/" + path + "?" + urlencode(query), timeout=8) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
      if len(payload) > 16 * 1024 * 1024:
        raise MapboxError("Mapbox 回應過大")
      result = json.loads(payload)
      if not isinstance(result, dict):
        raise ValueError
      return result
    except HTTPError as e:
      raise MapboxError("Mapbox 權杖或權限錯誤" if e.code in (401, 403) else
                        "Mapbox 請求受限，稍後重試" if e.code == 429 else "Mapbox 暫時無法規劃路線") from None
    except (URLError, TimeoutError, OSError, ValueError):
      raise MapboxError("無法取得 Mapbox 資料，請檢查網路") from None

  def search_address(self, text, proximity=None):
    query = {"q": text.strip(), "limit": 5, "language": "zh-Hant", "autocomplete": "false", "permanent": "true"}
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
      raise MapboxError("地址搜尋回應不完整") from None
    return results

  def route(self, start, destination, bearing=None):
    start, destination = coordinate(start), coordinate(destination)
    query = {"geometries": "geojson", "overview": "full", "steps": "true", "alternatives": "false", "language": "zh-TW"}
    if bearing is not None:
      query["bearings"] = f"{round(bearing) % 360},90;"
    path = f"directions/v5/mapbox/driving/{start[0]},{start[1]};{destination[0]},{destination[1]}"
    data = self.get(path, query)
    try:
      return Route(data)
    except (KeyError, IndexError, ValueError, TypeError):
      raise MapboxError("找不到完整的可行駛路線") from None
