"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params
from concurrent.futures import ThreadPoolExecutor
import uuid

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.navd.mapbox import MapboxClient, MapboxError
from openpilot.sunnypilot.navd.route import coordinate
from openpilot.system.ui.sunnypilot.widgets.input_dialog import InputDialogSP
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, button_item_sp
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets import Widget


class NavigationLayout(Widget):
  def __init__(self):
    super().__init__()

    self._params = Params()
    self._executor = ThreadPoolExecutor(max_workers=1)
    self._search = None
    self._search_cancelled = False
    self._results = []
    self._status = "輸入地址搜尋後，請核對並選取目的地；也可直接輸入緯度、經度。"
    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  def _initialize_items(self):
    self._enabled = toggle_item_sp("Mapbox 導航", param="MapboxNavigation",
                                    description="顯示逐向指示、剩餘里程與預估時間。需網路規劃；定位不可靠時停止提供轉彎意圖。",
                                    enabled=ui_state.is_offroad)
    self._token = button_item_sp("Mapbox 公開權杖", "設定", description="使用自己的 pk. 權杖。權杖不寫入行車紀錄或 sunnylink 備份。",
                                 callback=self._edit_token, enabled=ui_state.is_offroad)
    self._destination = button_item_sp("目的地", "輸入", description=lambda: self._status,
                                       callback=self._edit_destination, enabled=lambda: ui_state.is_offroad() and self._search is None)
    self._choices = [button_item_sp(lambda i=i: self._results[i]["name"] if i < len(self._results) else "", "選取",
                                    callback=lambda i=i: self._choose(i), enabled=ui_state.is_offroad) for i in range(5)]
    self._turns = toggle_item_sp("導航轉彎提示模型（需駕駛確認）", param="NavTurnConfirmation",
                                 enabled=lambda: ui_state.is_offroad() and not self._params.get_bool("LaneTurnDesire") and
                                                 not self._params.get_bool("BlinkerPauseLateralControl"),
                                 description="預設關閉。透過模型既有轉彎意圖輸入；低於 28.8 km/h 且接近一般左／右轉時，" +
                                             "須先確認路況並向同方向輕推方向盤。每個轉彎只接受一次；不處理匝道、迴轉或自動變換車道。" +
                                             "需先關閉「Lane Turn Desire」與「Pause Lateral Control with Blinker」，避免方向燈訊號互相觸發。" +
                                             "導航不判斷路權。此功能不是完整的模型導航。")
    self._full_fov = button_item_sp("Chestnut Full-FOV Shadow Mode", "尚未提供",
                                    description="目前沒有與完整廣角輸入相容的感知模型，無法啟用。既有駕駛模型維持原本輸入；不改裁切、不假造辨識結果。",
                                    enabled=False)
    items = [self._enabled, self._token, self._destination, *self._choices,
             button_item_sp("取消導航", "取消", callback=self._cancel), self._turns, self._full_fov]
    return items

  def _edit_token(self):
    def save(result, text):
      if result == DialogResult.CONFIRM and ui_state.is_offroad():
        token = text.strip()
        if not token or token.startswith("pk."):
          self._params.put("MapboxToken", token)
          self._status = "權杖已更新" if token else "權杖已清除"
        else:
          self._status = "只接受 Mapbox 公開權杖（pk.）"
    InputDialogSP("Mapbox 公開權杖", current_text=self._params.get("MapboxToken") or "", password_mode=True, callback=save).show()

  def _edit_destination(self):
    def search(result, text):
      if result != DialogResult.CONFIRM or not ui_state.is_offroad() or not text.strip():
        return
      self._results = []
      try:
        parts = text.split(",")
        if len(parts) == 2:
          lon, lat = coordinate([float(parts[1]), float(parts[0])])
          self._results = [{"name": f"{lat:.6f}, {lon:.6f}", "latitude": lat, "longitude": lon}]
          self._status = "請核對座標，再按「選取」"
          return
      except ValueError:
        pass
      self._status = "正在搜尋地址…"
      self._search_cancelled = False
      self._search = self._executor.submit(MapboxClient(self._params.get("MapboxToken") or "").search_address, text)
    InputDialogSP("地址或緯度、經度", sub_title="例如：25.0330, 121.5654。地址搜尋不包含商家／景點搜尋。", callback=search).show()

  def _choose(self, index):
    if ui_state.is_offroad() and index < len(self._results):
      result = self._results[index]
      self._params.put("MapboxDestination", {**result, "requestId": uuid.uuid4().hex})
      self._status = "目的地：" + result["name"] + "。行車系統取得定位後規劃路線。"
      self._results = []

  def _cancel(self):
    self._params.remove("MapboxDestination")
    self._results = []
    self._status = "導航已取消"
    if self._search is not None:
      self._search_cancelled = True
      self._search.cancel()
      # A running search may finish, but cannot select/save a destination.

  def _update_state(self):
    super()._update_state()
    if self._search is not None and self._search.done():
      try:
        results = self._search.result()
        if not self._search_cancelled:
          self._results = results
          self._status = "請核對並選取目的地" if self._results else "找不到地址，請改輸入緯度、經度"
      except MapboxError as e:
        self._status = str(e)
      except Exception:
        self._status = "地址搜尋失敗"
      self._search = None
    for i, choice in enumerate(self._choices):
      choice.set_visible(i < len(self._results))

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    destination = self._params.get("MapboxDestination")
    if isinstance(destination, dict) and destination.get("name"):
      self._status = "目的地：" + str(destination['name'])
    self._scroller.show_event()
