"""Mapbox route parsing and bounded, heading-aware progress tracking.

Reference: sunnypilot PRs #1397/#1412. Step geometry is retained instead of
matching maneuver points to the globally nearest vertex (unsafe on loops).
"""
from bisect import bisect_right
from dataclasses import dataclass
import math

EARTH_RADIUS = 6371000.0


def coordinate(value):
  if not isinstance(value, (list, tuple)) or len(value) != 2:
    raise ValueError("座標必須是經度、緯度")
  lon, lat = map(float, value)
  if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 <= lat <= 85):
    raise ValueError("座標範圍錯誤")
  return lon, lat


def xy(point, origin):
  dlon = (point[0] - origin[0] + 180) % 360 - 180
  return (math.radians(dlon) * EARTH_RADIUS * math.cos(math.radians(origin[1])),
          math.radians(point[1] - origin[1]) * EARTH_RADIUS)


def distance(a, b):
  return math.hypot(*xy(a, b))


@dataclass(frozen=True)
class Step:
  start: float
  kind: str
  modifier: str
  instruction: str


class Route:
  def __init__(self, data):
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
      raise ValueError("找不到可用路線")
    raw = data["routes"][0]
    self.points = []
    self.cumulative = []
    self.steps = []
    for leg in raw["legs"]:
      for step in leg["steps"]:
        points = [coordinate(p) for p in step["geometry"]["coordinates"]]
        if not points:
          raise ValueError("路線缺少路段幾何")
        if self.points and distance(points[0], self.points[-1]) > 5:
          raise ValueError("路段不連續")
        start = self.cumulative[-1] if self.cumulative else 0.0
        m = step["maneuver"]
        self.steps.append(Step(start, str(m["type"]), str(m.get("modifier", "")), str(m.get("instruction", ""))[:240]))
        for point in points:
          delta = distance(self.points[-1], point) if self.points else 0.0
          if self.points and delta < 0.01:
            continue
          self.points.append(point)
          self.cumulative.append((self.cumulative[-1] if self.cumulative else 0.0) + delta)
          if len(self.points) > 100000:
            raise ValueError("路線過長")
    if len(self.points) < 2 or not self.steps or self.steps[-1].kind != "arrive":
      raise ValueError("路線資料不完整")
    self.duration = float(raw["duration"])
    if not math.isfinite(self.duration) or self.duration < 0:
      raise ValueError("路程時間錯誤")
    self.progress = 0.0
    self.last_time = None
    self.step_starts = [s.start for s in self.steps]

  def update(self, position, now, speed=0.0, bearing=None):
    position = coordinate(position)
    if not math.isfinite(now) or not math.isfinite(speed):
      return None
    dt = 1.0 if self.last_time is None else max(0.0, now - self.last_time)
    # A gap needs relocalization via a new route, not a jump to a later loop.
    if dt > 5.0:
      return None
    lower = max(0.0, self.progress - 20.0)
    upper = self.progress + max(60.0, max(0.0, speed) * dt + 30.0)
    candidates = []
    first = max(0, bisect_right(self.cumulative, lower) - 1)
    for i in range(first, len(self.points) - 1):
      start, end = self.cumulative[i:i + 2]
      if start > upper:
        break
      a = xy(self.points[i], position)
      b = xy(self.points[i + 1], position)
      dx, dy = b[0] - a[0], b[1] - a[1]
      if speed > 2 and bearing is not None:
        heading = math.degrees(math.atan2(dx, dy)) % 360
        if abs((heading - bearing + 180) % 360 - 180) > 80:
          continue
      t = min(1.0, max(0.0, -(a[0] * dx + a[1] * dy) / (dx * dx + dy * dy)))
      along = start + t * (end - start)
      if lower <= along <= upper:
        candidates.append((math.hypot(a[0] + t * dx, a[1] + t * dy), along))
    if not candidates:
      return None
    candidates.sort()
    error, along = candidates[0]
    if error > 35:
      return None
    # Near-identical matches at a crossing must not select an unrelated leg.
    if any(other_error < error + 3 and abs(other_along - along) > 25 for other_error, other_along in candidates[1:]):
      return None
    self.progress = max(self.progress, along)
    self.last_time = now
    remaining = max(0.0, self.cumulative[-1] - self.progress)
    arrived = remaining < 12 and distance(position, self.points[-1]) < 15
    current = min(len(self.steps) - 1, max(0, bisect_right(self.step_starts, self.progress + 1.0) - 1))
    upcoming = min(current + 1, len(self.steps) - 1)
    step = self.steps[upcoming]
    return {"stepIndex": upcoming, "maneuverType": step.kind, "modifier": step.modifier,
            "instruction": step.instruction, "maneuverDistance": max(0.0, step.start - self.progress),
            "distanceRemaining": remaining, "timeRemaining": self.duration * remaining / self.cumulative[-1],
            "arrived": arrived}
