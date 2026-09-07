#!/usr/bin/env python3
"""Desktop-only browser transport for the real sunnypilot settings widgets.

Only this process receives browser input. No manager, Panda, controlsd, modeld,
route worker, uploader or vehicle connection is started.
"""
import argparse
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[2]


class PreviewState:
  def __init__(self):
    self.token = secrets.token_urlsafe(24)
    self.inputs = queue.Queue(maxsize=256)
    self.frame = b''
    self.sequence = 0
    self.condition = threading.Condition()
    self.ready = False
    self.width, self.height = 2160, 1080

  def publish(self, data):
    with self.condition:
      self.frame = data
      self.sequence += 1
      self.ready = True
      self.condition.notify_all()

  def input(self, data):
    kind = data.get('type')
    if kind == 'pointer':
      x, y = float(data['x']), float(data['y'])
      if not math.isfinite(x) or not math.isfinite(y) or data.get('action') not in ('down', 'move', 'up'):
        raise ValueError('Invalid pointer')
      data = {'type': kind, 'action': data['action'], 'x': min(self.width, max(0, x)), 'y': min(self.height, max(0, y))}
    elif kind == 'text':
      text = data.get('text')
      if not isinstance(text, str) or len(text) > 4096:
        raise ValueError('Invalid text')
      text.encode('utf-8')  # Reject unpaired surrogate escapes before raylib.
      data = {'type': kind, 'text': text}
    elif kind == 'key':
      if data.get('key') not in ('Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', 'Escape'):
        raise ValueError('Invalid key')
    elif kind == 'wheel':
      delta = float(data['delta'])
      if not math.isfinite(delta):
        raise ValueError('Invalid wheel')
      data = {'type': kind, 'delta': min(4, max(-4, delta))}
    else:
      raise ValueError('Invalid input')
    self.inputs.put_nowait(data)


def handler_for(state):
  class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
      pass  # Input text and tokens must not appear in access logs.

    def respond(self, status, body, content_type):
      self.send_response(status)
      self.send_header('Content-Type', content_type)
      self.send_header('Content-Length', str(len(body)))
      self.send_header('Cache-Control', 'no-store')
      self.send_header('X-Content-Type-Options', 'nosniff')
      self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; script-src 'self'; " +
                       "style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
      self.end_headers()
      self.wfile.write(body)

    def local_request(self):
      try:
        host = urlsplit('http://' + self.headers.get('Host', '')).hostname
      except ValueError:
        return False
      return host in ('127.0.0.1', 'localhost', '::1')

    def do_GET(self):
      if not self.local_request():
        self.respond(403, b'Local preview only', 'text/plain')
        return
      path = urlsplit(self.path).path
      if path == '/':
        self.respond(200, (Path(__file__).parent / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
      elif path in ('/preview.js', '/preview.css'):
        self.respond(200, (Path(__file__).parent / 'web' / path[1:]).read_bytes(),
                     'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
      elif path == '/session':
        self.respond(200, json.dumps({'token': state.token, 'ready': state.ready, 'width': state.width, 'height': state.height}).encode(), 'application/json')
      elif path == '/frame':
        try:
          after = int(parse_qs(urlsplit(self.path).query).get('after', ['-1'])[0])
        except ValueError:
          self.respond(400, b'Invalid frame', 'text/plain')
          return
        with state.condition:
          state.condition.wait_for(lambda: state.sequence > after, timeout=3)
          data, sequence = state.frame, state.sequence
        if not data:
          self.respond(503, b'UI is starting', 'text/plain')
          return
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Frame-Sequence', str(sequence))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)
      else:
        self.respond(404, b'Not found', 'text/plain')

    def do_POST(self):
      origin = self.headers.get('Origin')
      expected = 'http://' + self.headers.get('Host', '')
      if (not self.local_request() or origin not in (None, expected) or
          not secrets.compare_digest(self.headers.get('X-Preview-Token', ''), state.token)):
        self.respond(403, b'Invalid preview session', 'text/plain')
        return
      if self.path != '/input' or self.headers.get('Content-Type') != 'application/json':
        self.respond(400, b'Invalid request', 'text/plain')
        return
      try:
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 32768:
          raise ValueError
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
          raise ValueError
        state.input(data)
      except (ValueError, KeyError, TypeError, queue.Full):
        self.respond(400, b'Invalid or excessive input', 'text/plain')
        return
      self.respond(200, b'{}', 'application/json')
  return Handler


def run_ui(state, params_path, prefix, screenshot=None, panel='DEVICE', language='en'):
  # Set isolation before any openpilot imports instantiate Params or messaging.
  os.environ.update({'RAYLIB_BACKEND': 'headless', 'BIG': '1', 'SCALE': '0.6666667', 'FPS': '15',
                     'PARAMS_ROOT': params_path, 'OPENPILOT_PREFIX': prefix, 'ZMQ': '0'})
  for directory in (ROOT, ROOT / 'opendbc_repo', ROOT / 'msgq_repo', ROOT / 'tinygrad_repo'):
    sys.path.insert(0, str(directory))
  import pyray as rl
  from PIL import Image
  from openpilot.common.params import Params
  from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
  from openpilot.system.ui.lib.multilang import multilang
  from tools.ui_preview.services import install_preview_services
  install_preview_services()

  params = Params()
  if params.get('LanguageSetting') is None:
    params.put('LanguageSetting', language, block=True)
  multilang.change_language(str(params.get('LanguageSetting')))
  gui_app.init_window('sunnypilot development preview', fps=15)

  # Only settings are instantiated: account services, camera views and a live
  # vehicle are not needed to verify layouts, dialogs and parameter changes.
  from openpilot.selfdrive.ui.sunnypilot.layouts.settings.settings import SettingsLayoutSP
  from openpilot.selfdrive.ui.ui_state import ui_state
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  from opendbc.car.structs import CarParams
  ui_state.CP = CarInterface.get_non_essential_params(CAR.KIA_EV6)
  ui_state.CP_SP = CarInterface.get_non_essential_params_sp(ui_state.CP, CAR.KIA_EV6)
  ui_state.CP.safetyConfigs[0].safetyModel = CarParams.SafetyModel.noOutput
  ui_state.started = ui_state.ignition = False
  settings = SettingsLayoutSP()
  from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
  settings.set_current_panel(PanelType[panel])
  settings.set_callbacks(on_close=lambda: None)
  gui_app.push_widget(settings)

  chars, keys = deque(), deque()
  mouse = {'x': 0., 'y': 0., 'down': False, 'wheel': 0.}
  keymap = {name: getattr(rl.KeyboardKey, 'KEY_' + code) for name, code in
            [('Backspace', 'BACKSPACE'), ('Delete', 'DELETE'), ('ArrowLeft', 'LEFT'), ('ArrowRight', 'RIGHT'),
             ('Home', 'HOME'), ('End', 'END'), ('Enter', 'ENTER'), ('Escape', 'ESCAPE')]}

  def read_inputs():
    mouse['wheel'] = 0.
    while True:
      try:
        data = state.inputs.get_nowait()
      except queue.Empty:
        break
      kind = data['type']
      if kind == 'pointer':
        previous = mouse['down']
        mouse.update(x=data['x'], y=data['y'])
        if data['action'] != 'move':
          mouse['down'] = data['action'] == 'down'
        gui_app._mouse._events.append(MouseEvent(MousePos(mouse['x'], mouse['y']), 0,
          mouse['down'] and not previous, previous and not mouse['down'], mouse['down'], time.monotonic()))
      elif kind == 'text':
        chars.extend(map(ord, data['text']))
      elif kind == 'key':
        keys.append(keymap[data['key']])
      elif kind == 'wheel':
        mouse['wheel'] = data['delta']

  # Input injection is confined to this desktop process; production UI sources
  # and touch handling are not patched for the preview.
  gui_app._mouse._handle_mouse_event = read_inputs
  rl.get_mouse_position = lambda: rl.Vector2(mouse['x'], mouse['y'])
  rl.get_mouse_x = lambda: int(mouse['x'])
  rl.get_mouse_y = lambda: int(mouse['y'])
  rl.get_mouse_wheel_move = lambda: mouse['wheel']
  rl.is_mouse_button_down = lambda button: mouse['down'] if button == 0 else False
  rl.get_char_pressed = lambda: chars.popleft() if chars else 0
  rl.get_key_pressed = lambda: keys.popleft() if keys else 0
  rl.is_key_down = lambda key: False
  end_drawing = rl.end_drawing
  captures = 0

  def capture():
    nonlocal captures
    chars.clear()  # Text entered without an open input dialog is discarded.
    end_drawing()
    img = rl.load_image_from_screen()
    try:
      rl.image_format(img, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
      pixels = bytes(rl.ffi.buffer(img.data, img.width * img.height * 4))
      image = Image.frombytes('RGBA', (img.width, img.height), pixels)
      data = io.BytesIO()
      image.save(data, format='PNG', compress_level=1)
      state.publish(data.getvalue())
      captures += 1
      if screenshot and captures == 4:
        Path(screenshot).write_bytes(data.getvalue())
        gui_app.request_close()
    finally:
      rl.unload_image(img)

  rl.end_drawing = capture
  try:
    for _ in gui_app.render():
      pass
  finally:
    gui_app.close()


def source_mtimes():
  paths = [p for base in (ROOT / 'openpilot/selfdrive/ui', ROOT / 'openpilot/system/ui') for p in base.rglob('*')
           if p.suffix in ('.py', '.po')]
  return {str(p): p.stat().st_mtime_ns for p in paths}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--port', type=int, default=8080)
  parser.add_argument('--container', action='store_true', help='Bind inside the preview container; publish only to host loopback')
  parser.add_argument('--watch', action='store_true', help='Restart the UI when Python or translation files change')
  parser.add_argument('--screenshot', help='Render four frames, save a PNG and exit')
  parser.add_argument('--panel', choices=('DEVICE', 'NAVIGATION', 'STEERING', 'DISPLAY', 'MODELS'), default='DEVICE')
  parser.add_argument('--language', choices=('en', 'zh-CHT', 'zh-CHS'), default='en', help='Initial preview language')
  parser.add_argument('--state-directory', help=argparse.SUPPRESS)
  args = parser.parse_args()
  if Path('/AGNOS').exists() or Path('/TICI').exists():
    parser.error('This preview is for desktop development only. Do not run it on a vehicle device.')
  if not 1024 <= args.port <= 65535:
    parser.error('Choose a port from 1024 to 65535')
  with tempfile.TemporaryDirectory(prefix='sunnypilot-ui-preview-') as temporary:
    params_path = args.state_directory or temporary
    if args.watch:
      command = [sys.executable, str(Path(__file__).resolve()), '--port', str(args.port), '--state-directory', params_path,
                 '--panel', args.panel, '--language', args.language]
      if args.container:
        command.append('--container')
      process = subprocess.Popen(command)
      snapshot = source_mtimes()
      try:
        while process.poll() is None:
          time.sleep(1)
          updated = source_mtimes()
          if updated != snapshot:
            process.terminate()
            process.wait(timeout=10)
            process = subprocess.Popen(command)
            snapshot = updated
        raise SystemExit(process.returncode)
      except KeyboardInterrupt:
        pass
      finally:
        if process.poll() is None:
          process.terminate()
          process.wait(timeout=10)
      return
    state = PreviewState()
    server = ThreadingHTTPServer(('0.0.0.0' if args.container else '127.0.0.1', args.port), handler_for(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f'Open http://localhost:{args.port} in your browser. Preview settings are isolated.', flush=True)
    def stop(_signal, _frame):
      raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    prefix = 'preview-' + hashlib.sha256(params_path.encode()).hexdigest()[:12]
    shm_root = '/tmp' if sys.platform == 'darwin' else '/dev/shm'
    messaging_path = Path(shm_root) / ('msgq_' + prefix)
    messaging_path.mkdir(exist_ok=True)
    try:
      run_ui(state, params_path, prefix, args.screenshot, args.panel, args.language)
    finally:
      server.shutdown()
      server.server_close()
      shutil.rmtree(messaging_path, ignore_errors=True)


if __name__ == '__main__':
  main()
