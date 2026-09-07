"""Native glyph/paste checks and local browser transport tests (no vehicle)."""
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from tools.ui_preview.preview import PreviewState, handler_for, ROOT


class TestTransport(unittest.TestCase):
  def setUp(self):
    self.state = PreviewState()
    self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.state))
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    self.thread.start()
    self.base = f'http://127.0.0.1:{self.server.server_port}'
    self.opener = build_opener(ProxyHandler({}))

  def tearDown(self):
    self.server.shutdown()
    self.server.server_close()
    self.thread.join(timeout=2)

  def request(self, data, **headers):
    request = Request(self.base + '/input', data=json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json', **headers})
    try:
      return self.opener.open(request, timeout=3).status
    except HTTPError as e:
      return e.code

  def test_authentication_origin_and_input_bounds(self):
    data = {'type': 'text', 'text': 'test'}
    self.assertEqual(self.request(data), 403)
    headers = {'X-Preview-Token': self.state.token}
    self.assertEqual(self.request(data, **headers, Origin='https://unrelated.example'), 403)
    self.assertEqual(self.request(data, **headers, Host='unrelated.example'), 403)
    self.assertEqual(self.request({'type': 'text', 'text': 'x' * 4097}, **headers), 400)
    self.assertEqual(self.request({'type': 'pointer', 'action': 'down', 'x': float('nan'), 'y': 0}, **headers), 400)
    self.assertEqual(self.request(data, **headers), 200)
    self.assertEqual(self.state.inputs.get_nowait(), data)
    self.assertTrue(self.state.inputs.empty())


class TestNativeUi(unittest.TestCase):
  def run_child(self, command, **kwargs):
    result = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True, text=True, timeout=30, **kwargs)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_glyph_coverage_language_switch_and_paste(self):
    code = '''
from collections import deque
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import multilang, tr
from openpilot.system.ui.widgets.inputbox import InputBox
from openpilot.system.ui.lib.text_measure import measure_text_cached
multilang.change_language('en')
gui_app.init_window('font test')
try:
  for language in ('en', 'zh-CHT', 'en'):
    multilang.change_language(language)
    for text in (tr('Use OpenStreetMap routes (OSRM)'), '台北市 忠孝東路', '左轉 90 m', '⚙ Navigation'):
      font = gui_app.font_for_text(gui_app.font(), text)
      assert gui_app._covers_text(font, frozenset(map(ord, text))), (language, text)
      assert measure_text_cached(gui_app.font(), text, 48).x > 0
  assert tr('Use OpenStreetMap routes (OSRM)') == 'Use OpenStreetMap routes (OSRM)'
  box = InputBox(max_text_size=128)
  pasted = 'pk.' + 'a' * 100 + '台北市'
  chars = deque(map(ord, pasted))
  rl.get_char_pressed = lambda: chars.popleft() if chars else 0
  box._handle_keyboard_input()
  assert box.text == pasted
finally:
  gui_app.close()
'''
    self.run_child(['-c', code], env={**os.environ, 'RAYLIB_BACKEND': 'headless', 'BIG': '1', 'SCALE': '0.5'})

  def test_render_and_http_toggle_persist_across_restart(self):
    with socket.socket() as sock:
      sock.bind(('127.0.0.1', 0))
      port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory() as tmp:
      state_dir = str(Path(tmp) / 'params')
      args = ['tools/ui_preview/preview.py', '--port', str(port), '--panel', 'NAVIGATION', '--state-directory', state_dir]
      with (Path(tmp) / 'ui.log').open('w+') as output:
        process = subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=output, stderr=output)
        try:
          deadline = time.monotonic() + 20
          while time.monotonic() < deadline:
            try:
              with opener.open(base + '/session', timeout=1) as response:
                session = json.load(response)
              if session['ready']:
                break
            except OSError:
              pass
            if process.poll() is not None:
              output.seek(0)
              self.fail(output.read())
            time.sleep(.1)
          else:
            self.fail('UI did not start')
          with opener.open(base + '/frame', timeout=2) as response:
            self.assertTrue(response.read().startswith(b'\x89PNG'))
          # First row in the real Navigation settings page: master toggle.
          for action in ('down', 'up'):
            request = Request(base + '/input', data=json.dumps({'type': 'pointer', 'action': action, 'x': 670, 'y': 125}).encode(),
                              headers={'Content-Type': 'application/json', 'X-Preview-Token': session['token']})
            with opener.open(request, timeout=2) as response:
              self.assertEqual(response.status, 200)
            time.sleep(.2)
          prefix = 'preview-' + hashlib.sha256(state_dir.encode()).hexdigest()[:12]
          value = Path(state_dir) / prefix / 'MapboxNavigation'
          deadline = time.monotonic() + 3
          while time.monotonic() < deadline and (not value.exists() or value.read_bytes() != b'1'):
            time.sleep(.1)
          self.assertEqual(value.read_bytes(), b'1')
        finally:
          process.terminate()
          process.wait(timeout=10)
      self.run_child([*args, '--screenshot', str(Path(tmp) / 'restart.png')])
      self.assertEqual(value.read_bytes(), b'1')


if __name__ == '__main__':
  unittest.main()
