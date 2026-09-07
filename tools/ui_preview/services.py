"""In-memory Wi-Fi service for desktop UI interaction; never changes host Wi-Fi."""
from openpilot.system.ui.lib import wifi_manager as wifi


class PreviewWifiManager:
  def __init__(self):
    self.networks = [wifi.Network('Preview Wi-Fi', 90, wifi.SecurityType.WPA2, False)]
    self.wifi_state = wifi.WifiState()
    self.ipv4_address = ''
    self.current_network_metered = wifi.MeteredType.NO
    self.tethering_password = 'preview-only'
    self._saved = set()
    self._tethering = False
    self._callbacks = {}

  @property
  def connected_ssid(self):
    return self.wifi_state.ssid

  @property
  def connecting_to_ssid(self):
    return None

  def add_callbacks(self, **callbacks):
    for name, callback in callbacks.items():
      if callback is not None:
        self._callbacks.setdefault(name, []).append(callback)

  def _emit(self, name, *args):
    for callback in self._callbacks.get(name, []):
      callback(*args)

  def set_active(self, active):
    if active:
      self._emit('networks_updated', self.networks)

  def process_callbacks(self):
    pass

  def _update_networks(self, **kwargs):
    pass

  def _request_scan(self):
    pass

  def connect_to_network(self, ssid, password='', hidden=False):
    self._saved.add(ssid)
    self.wifi_state = wifi.WifiState(ssid, wifi.ConnectStatus.CONNECTED)
    self.ipv4_address = '192.0.2.1'
    self._emit('activated')

  def activate_connection(self, ssid):
    self.connect_to_network(ssid)

  def forget_connection(self, ssid):
    self._saved.discard(ssid)
    self.wifi_state = wifi.WifiState()
    self.ipv4_address = ''
    self._emit('forgotten', ssid)
    self._emit('disconnected')

  def is_connection_saved(self, ssid):
    return ssid in self._saved

  def is_tethering_active(self):
    return self._tethering

  def set_tethering_active(self, active):
    self._tethering = active

  def set_tethering_password(self, password):
    self.tethering_password = password

  def set_current_network_metered(self, metered):
    self.current_network_metered = metered

  def set_ipv4_forward(self, enabled):
    pass


def install_preview_services():
  wifi.WifiManager = PreviewWifiManager
