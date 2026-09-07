"""Execute synthetic policies through real tinygrad queues/JIT (no vehicle model).

Check both recurrent layouts, float16 casting and packed frame offsets. These
tests establish input transport, not learned navigation behavior.
"""
from types import SimpleNamespace
import unittest

import numpy as np
from tinygrad import Tensor, dtypes
from tinygrad.engine.jit import TinyJit

from openpilot.selfdrive.modeld import compile_modeld as stock
from openpilot.sunnypilot.modeld_v2 import compile_modeld as legacy
from openpilot.sunnypilot.navd.model_inputs import copy_nav_features


class ProbePolicy:
  def __init__(self, shapes):
    self.graph_inputs = {name: SimpleNamespace(dtype=dtypes.float16) for name in shapes}

  def __call__(self, inputs):
    parts = [inputs[name].flatten() for name in ('traffic_convention', 'action_t')]
    parts += [inputs[name].flatten()[-1:] for name in ('img', 'big_img', 'features_buffer', 'desire_pulse')]
    if 'nav_features' in inputs:
      parts.insert(0, inputs['nav_features'].flatten())
    return {'output': Tensor.cat(*parts).reshape(1, -1)}


class TestNavCompiler(unittest.TestCase):
  def check_transport(self, runner_kind, spatial, navigation):
    shapes = {'img': (1, 12, 2, 2), 'big_img': (1, 12, 2, 2), 'features_buffer': (1, 2, 3, 4) if spatial else (1, 2, 4),
              'desire_pulse': (1, 3, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2)}
    if navigation:
      shapes['nav_features'] = (1, 64)
    policy = ProbePolicy(shapes)
    if runner_kind == 'stock':
      queues, buffers, frames = stock.make_input_queues(shapes, 1, device='CPU', frame_copy_size=24)
      frames['img'][:] = 7
      frames['big_img'][:] = 9
      def warp(tfm, big_tfm, frame, big_frame):
        return frame.cat(big_frame).reshape(2, 6, 2, 2)
      metadata = {'input_shapes': shapes}
      run = stock.make_run_model(warp, stock.make_run_policy(policy, metadata, 1), metadata, 24)
      args = {key: queues[key] for key in stock.MODELD_INPUTS}
    else:
      queues, buffers = legacy.make_supercombo_input_queues(shapes, 1, device='CPU')
      run = legacy.make_run_policy(None, [policy], None, 1, shapes)
      args = {key: queues[key] for key in legacy.POLICY_INPUTS}
      args['warped'] = Tensor(np.stack([np.full((6, 2, 2), 7, np.uint8), np.full((6, 2, 2), 9, np.uint8)]), device='CPU').realize()
    buffers['traffic_convention'][:] = [1, 0]
    buffers['action_t'][:] = [.25, .5]
    buffers['prev_feat'][:] = 3
    buffers['desire'][-1] = 1
    jit = TinyJit(run)
    # Includes warmup, capture, changing values under JIT, then missing input.
    for step in range(5):
      value = np.full((1, 64), step + 1, np.float32)
      copy_nav_features(buffers, {'nav_features': value} if navigation and step < 4 else {})
      result = jit(**args)
      result = result[0] if runner_kind == 'stock' else result
      expected = [1, 0, .25, .5, 7, 9, 3, 1]
      if navigation:
        expected = ([step + 1] * 64 if step < 4 else [0] * 64) + expected
      np.testing.assert_array_equal(result.numpy().flatten(), expected)

  def test_stock_small_and_big_layout_with_and_without_navigation(self):
    for spatial in (False, True):
      for navigation in (False, True):
        with self.subTest(spatial=spatial, navigation=navigation):
          self.check_transport('stock', spatial, navigation)

  def test_legacy_small_and_big_layout_with_and_without_navigation(self):
    for spatial in (False, True):
      for navigation in (False, True):
        with self.subTest(spatial=spatial, navigation=navigation):
          self.check_transport('legacy', spatial, navigation)

  def test_bad_shape_rejected_by_both_compilers(self):
    shapes = {'desire_pulse': (1, 3, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2),
              'features_buffer': (1, 2, 4), 'nav_features': (1, 128)}
    for compiler in (stock, legacy):
      with self.assertRaises(ValueError):
        compiler.get_policy_npy_shapes(shapes)
