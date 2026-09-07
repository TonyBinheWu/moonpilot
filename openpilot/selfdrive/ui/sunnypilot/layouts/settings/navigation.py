"""Navigation setup shared by the device UI and the desktop browser preview."""
from concurrent.futures import ThreadPoolExecutor
import uuid

from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.navd.mapbox import MapboxClient, MapboxError
from openpilot.sunnypilot.navd.osrm import DEFAULT_OSRM_URL, RoutingError, routing_endpoint
from openpilot.sunnypilot.navd.route import coordinate
from openpilot.system.ui.lib.multilang import tr, tr_noop, multilang
from openpilot.system.ui.sunnypilot.widgets.input_dialog import InputDialogSP
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, button_item_sp
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.scroller_tici import Scroller


class NavigationLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._executor = ThreadPoolExecutor(max_workers=1)
    self._search = None
    self._search_cancelled = False
    self._results = []
    self._status = tr_noop("Search for an address and confirm the destination, or enter latitude, longitude.")
    self._status_values = {}
    self._scroller = Scroller(self._initialize_items(), line_separator=True, spacing=0)

  def _set_status(self, text, **values):
    self._status, self._status_values = text, values

  def _initialize_items(self):
    self._enabled = toggle_item_sp(lambda: tr("Navigation"), param="MapboxNavigation", enabled=ui_state.is_offroad,
      description=lambda: tr("Show directions, remaining distance and arrival time. Route planning needs a network connection."))
    self._osm = toggle_item_sp(lambda: tr("Use OpenStreetMap routes (OSRM)"), param="OsmNavigation", enabled=ui_state.is_offroad,
      description=lambda: tr("Use OSM routes without a Mapbox token. Enter destination coordinates, or use Mapbox for address search. " +
                             "Your location and destination are sent to the selected routing server."))
    self._server = button_item_sp(lambda: tr("OSRM routing server"), lambda: tr("Set"), callback=self._edit_server,
      enabled=ui_state.is_offroad,
      description=lambda: tr("The default is a public demonstration server. For regular use, configure your own HTTPS OSRM endpoint. " +
                             "Map data: OpenStreetMap contributors. Report map errors at https://www.openstreetmap.org."))
    self._token = button_item_sp(lambda: tr("Mapbox public token"), lambda: tr("Set"), callback=self._edit_token,
      enabled=ui_state.is_offroad,
      description=lambda: tr("Use your own pk. token. Tokens are excluded from drive logs and sunnylink backups."))
    self._destination = button_item_sp(lambda: tr("Destination"), lambda: tr("Enter"),
      description=lambda: tr(self._status).format(**self._status_values), callback=self._edit_destination,
      enabled=lambda: ui_state.is_offroad() and self._search is None)
    self._choices = [button_item_sp(lambda i=i: self._results[i]["name"] if i < len(self._results) else "", lambda: tr("Select"),
      callback=lambda i=i: self._choose(i), enabled=ui_state.is_offroad) for i in range(5)]
    self._turns = toggle_item_sp(lambda: tr("Driver-confirmed navigation turns"), param="NavTurnConfirmation",
      enabled=lambda: ui_state.is_offroad() and not self._params.get_bool("Mads") and not self._params.get_bool("LaneTurnDesire") and
                      not self._params.get_bool("BlinkerPauseLateralControl"),
      description=lambda: tr("Near a normal left or right turn below 28.8 km/h, check the road and nudge the wheel toward the turn. " +
                             "This supplies the existing turn-intent input to either driving model. Disable MADS, Lane Turn Desire and " +
                             "Pause Lateral Control with Blinker first. Ramps, U-turns and automatic lane changes are not supported. " +
                             "The route does not determine right of way or provide a learned map input."))
    self._full_fov = button_item_sp("Chestnut Full-FOV Shadow Mode", lambda: tr("Unavailable"), enabled=False,
      description=lambda: tr("A compatible full wide-angle perception model is not available."))
    return [self._enabled, self._osm, self._server, self._token, self._destination, *self._choices,
            button_item_sp(lambda: tr("Cancel navigation"), lambda: tr("Cancel"), callback=self._cancel), self._turns, self._full_fov]

  def _edit_server(self):
    def save(result, text):
      if result == DialogResult.CONFIRM and ui_state.is_offroad():
        try:
          self._params.put("OsmRoutingServer", routing_endpoint(text))
          self._set_status(tr_noop("Routing server updated"))
        except RoutingError as e:
          self._set_status(str(e))
    InputDialogSP(tr("OSRM routing server"), current_text=self._params.get("OsmRoutingServer") or DEFAULT_OSRM_URL,
                  callback=save).show()

  def _edit_token(self):
    def save(result, text):
      if result == DialogResult.CONFIRM and ui_state.is_offroad():
        token = text.strip()
        if not token or token.startswith("pk."):
          self._params.put("MapboxToken", token)
          self._set_status(tr_noop("Token updated") if token else tr_noop("Token cleared"))
        else:
          self._set_status(tr_noop("Only Mapbox public tokens (pk.) are accepted."))
    InputDialogSP(tr("Mapbox public token"), current_text=self._params.get("MapboxToken") or "", password_mode=True, callback=save).show()

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
          self._set_status(tr_noop("Check the coordinates, then select the destination."))
          return
      except ValueError:
        pass
      if not self._params.get("MapboxToken"):
        self._set_status(tr_noop("Enter latitude, longitude. Address search requires a Mapbox token."))
        return
      self._set_status(tr_noop("Searching for an address..."))
      self._search_cancelled = False
      self._search = self._executor.submit(
        MapboxClient(self._params.get("MapboxToken") or "", language=multilang.language).search_address, text)
    InputDialogSP(tr("Address or latitude, longitude"),
                  sub_title=tr("For example: 25.0330, 121.5654. Address search does not include businesses or points of interest."),
                  callback=search).show()

  def _choose(self, index):
    if ui_state.is_offroad() and index < len(self._results):
      result = self._results[index]
      self._params.put("MapboxDestination", {**result, "requestId": uuid.uuid4().hex})
      self._set_status(tr_noop("Destination: {name}. Routing starts when a reliable position is available."), name=result["name"])
      self._results = []

  def _cancel(self):
    self._params.remove("MapboxDestination")
    self._results = []
    self._set_status(tr_noop("Navigation cancelled"))
    if self._search is not None:
      self._search_cancelled = True
      self._search.cancel()

  def _update_state(self):
    super()._update_state()
    if self._search is not None and self._search.done():
      try:
        results = self._search.result()
        if not self._search_cancelled:
          self._results = results
          self._set_status(tr_noop("Check and select the destination.") if self._results else
                           tr_noop("No address found. Enter latitude, longitude instead."))
      except MapboxError as e:
        self._set_status(str(e))
      except Exception:
        self._set_status(tr_noop("Address search failed"))
      self._search = None
    for i, choice in enumerate(self._choices):
      choice.set_visible(i < len(self._results))
    self._server.set_visible(self._params.get_bool("OsmNavigation"))

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    destination = self._params.get("MapboxDestination")
    if isinstance(destination, dict) and destination.get("name"):
      self._set_status(tr_noop("Destination: {name}"), name=str(destination['name']))
    self._scroller.show_event()
