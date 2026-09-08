from __future__ import annotations

import pyray as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.navd.model_desire import maneuver_to_desire
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


SIGNAL_PROMPT_DISTANCE = 150.0


def nav_direction(modifier: str) -> str:
  modifier = modifier.lower()
  if "left" in modifier:
    return "left"
  if "right" in modifier:
    return "right"
  return "straight"


class NavigationInstructionRenderer(Widget):
  def __init__(self) -> None:
    super().__init__()
    self.visible = False
    self.primary_text = ""
    self.distance = 0.0
    self.direction = "straight"
    self.maneuver_type = ""
    self.signal_prompt = ""
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._font_regular = gui_app.font(FontWeight.NORMAL)

  def update(self) -> None:
    sm = ui_state.sm
    self.visible = bool(ui_state.navigation_enabled and sm.alive["navInstruction"] and
                        sm.valid["navInstruction"] and sm.recv_frame["navInstruction"] >= ui_state.started_frame)
    if not self.visible:
      return

    instruction = sm["navInstruction"]
    self.primary_text = str(instruction.maneuverPrimaryText) or tr("Continue")
    self.distance = max(0.0, float(instruction.maneuverDistance))
    self.maneuver_type = str(instruction.maneuverType).strip().lower()
    self.direction = nav_direction(str(instruction.maneuverModifier))

    suggested_desire = maneuver_to_desire(self.maneuver_type, str(instruction.maneuverModifier))
    if self.distance <= SIGNAL_PROMPT_DISTANCE and suggested_desire in (log.Desire.turnLeft, log.Desire.turnRight):
      signal = tr("left") if suggested_desire == log.Desire.turnLeft else tr("right")
      self.signal_prompt = tr("Use the {} turn signal when safe").format(signal)
    else:
      self.signal_prompt = ""

  @staticmethod
  def _format_distance(distance: float, metric: bool) -> str:
    if metric:
      if distance >= 1000.0:
        return f"{distance / 1000.0:.1f} km"
      return f"{max(10, round(distance / 10) * 10):.0f} m"

    feet = distance * 3.28084
    if feet >= 528.0:
      return f"{feet / 5280.0:.1f} mi"
    return f"{max(50, round(feet / 50) * 50):.0f} ft"

  @staticmethod
  def _fit_text(font: rl.Font, text: str, size: int, max_width: float) -> str:
    if measure_text_cached(font, text, size).x <= max_width:
      return text
    while len(text) > 3 and measure_text_cached(font, text + "…", size).x > max_width:
      text = text[:-1]
    return text + "…"

  @staticmethod
  def _draw_arrow(area: rl.Rectangle, direction: str, color: rl.Color, width: float) -> None:
    center_y = area.y + area.height / 2
    center_x = area.x + area.width / 2
    head = max(16.0, area.width * 0.18)

    if direction == "left":
      start = rl.Vector2(area.x + area.width * 0.78, center_y)
      end = rl.Vector2(area.x + area.width * 0.22, center_y)
      rl.draw_line_ex(start, end, width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x + head, end.y - head), width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x + head, end.y + head), width, color)
    elif direction == "right":
      start = rl.Vector2(area.x + area.width * 0.22, center_y)
      end = rl.Vector2(area.x + area.width * 0.78, center_y)
      rl.draw_line_ex(start, end, width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x - head, end.y - head), width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x - head, end.y + head), width, color)
    else:
      start = rl.Vector2(center_x, area.y + area.height * 0.78)
      end = rl.Vector2(center_x, area.y + area.height * 0.22)
      rl.draw_line_ex(start, end, width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x - head, end.y + head), width, color)
      rl.draw_line_ex(end, rl.Vector2(end.x + head, end.y + head), width, color)

  def _render(self, rect: rl.Rectangle) -> None:
    if not self.visible:
      return

    compact = rect.width < 1600
    card_width = min(620.0 if not compact else 500.0, rect.width - (320.0 if not compact else 40.0))
    card_height = 190.0 if not compact else 132.0
    reserved_right = 260.0 if not compact else 20.0
    card_x = rect.x + rect.width - reserved_right - card_width
    card_y = rect.y + (44.0 if not compact else 18.0)
    card = rl.Rectangle(card_x, card_y, card_width, card_height)

    rl.draw_rectangle_rounded(card, 0.24, 12, rl.Color(0, 0, 0, 190))
    rl.draw_rectangle_rounded_lines_ex(card, 0.24, 12, 3, rl.Color(255, 255, 255, 55))

    arrow_width = 136.0 if not compact else 92.0
    arrow_area = rl.Rectangle(card.x + 12, card.y + 12, arrow_width, card.height - 24)
    self._draw_arrow(arrow_area, self.direction, rl.WHITE, 10.0 if not compact else 7.0)

    text_x = arrow_area.x + arrow_area.width + 18
    text_width = card.x + card.width - text_x - 22
    primary_size = 48 if not compact else 34
    distance_size = 39 if not compact else 27
    prompt_size = 28 if not compact else 21

    primary = self._fit_text(self._font_bold, self.primary_text, primary_size, text_width)
    rl.draw_text_ex(self._font_bold, primary, rl.Vector2(text_x, card.y + (24 if not compact else 14)),
                    primary_size, 0, rl.WHITE)

    distance = self._format_distance(self.distance, ui_state.is_metric)
    rl.draw_text_ex(self._font_medium, distance, rl.Vector2(text_x, card.y + (85 if not compact else 57)),
                    distance_size, 0, rl.Color(200, 220, 255, 255))

    footer = self.signal_prompt
    if footer:
      footer = self._fit_text(self._font_regular, footer, prompt_size, text_width)
      rl.draw_text_ex(self._font_regular, footer, rl.Vector2(text_x, card.y + (139 if not compact else 96)),
                      prompt_size, 0, rl.Color(190, 190, 190, 255))
