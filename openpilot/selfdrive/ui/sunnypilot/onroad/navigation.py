import time

import pyray as rl
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget


class NavigationBanner(Widget):
  def _render(self, rect):
    sm = ui_state.sm
    if sm.recv_frame["navigationStateSP"] < ui_state.started_frame:
      return
    nav = sm['navigationStateSP']
    age = time.monotonic() - sm.logMonoTime['navigationStateSP'] / 1e9
    if not 0 <= age < 2.5 or nav.status == "off":
      return
    title = tr(nav.instruction)
    attribution = "© OpenStreetMap" if nav.provider == "osm" else "© Mapbox © OpenStreetMap"
    if not title and nav.status == "active":
      # OSRM supplies maneuver types instead of localized instruction text.
      title = {("turn", "left"): tr("Turn left"), ("turn", "right"): tr("Turn right"),
               ("arrive", ""): tr("You have arrived")}.get((nav.maneuverType, nav.modifier), tr("Follow the route"))
    subtitle = ""
    if nav.status == "active" and sm.valid['navigationStateSP']:
      distance = f"{nav.maneuverDistance / 1000:.1f} km" if nav.maneuverDistance >= 1000 else f"{nav.maneuverDistance:.0f} m"
      title = f"{distance}  {title}"
      subtitle = tr("{distance:.1f} km remaining | About {minutes} min").format(
        distance=nav.distanceRemaining / 1000, minutes=max(1, round(nav.timeRemaining / 60))) + " | " + attribution
      speed = sm['carState'].vEgo
      if (ui_state.params.get_bool('NavTurnConfirmation') and not ui_state.params.get_bool('Mads') and
          not ui_state.params.get_bool('LaneTurnDesire') and not ui_state.params.get_bool('BlinkerPauseLateralControl') and
          nav.maneuverType == 'turn' and nav.modifier in ('left', 'right') and
          0.3 < speed < 8 and 0 <= nav.maneuverDistance <= min(30, max(8, speed * 3))):
        subtitle = tr("Check the road, then nudge the wheel toward the turn") + " | " + attribution
    font = gui_app.font(FontWeight.SEMI_BOLD)
    box = rl.Rectangle(rect.x + rect.width * .23, rect.y + 75, rect.width * .60, 135)
    rl.draw_rectangle_rounded(box, .15, 8, rl.Color(0, 0, 0, 170))
    while len(title) > 2 and measure_text_cached(font, title, 38).x > box.width - 36:
      title = title[:-2] + "…"
    rl.draw_text_ex(font, title, rl.Vector2(box.x + 18, box.y + 18), 38, 0, rl.WHITE)
    if subtitle:
      rl.draw_text_ex(font, subtitle, rl.Vector2(box.x + 18, box.y + 82), 24, 0, rl.LIGHTGRAY)
