import unittest

import numpy as np

from openpilot.sunnypilot.navd.model_inputs import copy_nav_features, nav_input_shapes
from openpilot.sunnypilot.navd.tools.probe_taco2 import extract_features


class TestNavigationModelInputs(unittest.TestCase):
  def test_models_without_navigation_keep_existing_inputs(self):
    buffers = {'desire': np.arange(8, dtype=np.float32)}
    self.assertEqual(nav_input_shapes({'desire': (1, 25, 8)}), {})
    self.assertFalse(copy_nav_features(buffers, {}))
    with self.assertRaises(ValueError):
      copy_nav_features(buffers, {'nav_features': np.zeros((1, 64), np.float32)})
    np.testing.assert_array_equal(buffers['desire'], np.arange(8))

  def test_copy_then_missing_frame_clears_features(self):
    buffers = {'nav_features': np.zeros((1, 64), np.float32)}
    features = np.linspace(-2, 2, 64, dtype=np.float32).reshape(1, 64)
    self.assertTrue(copy_nav_features(buffers, {'nav_features': features}))
    np.testing.assert_array_equal(buffers['nav_features'], features)
    self.assertFalse(copy_nav_features(buffers, {}))
    self.assertFalse(buffers['nav_features'].any())

  def test_rejects_malformed_input_and_clears_previous_frame(self):
    invalid = [np.ones(64, np.float32), np.ones((1, 63), np.float32), np.ones((1, 64), np.int32),
               np.full((1, 64), np.nan), np.full((1, 64), np.inf), np.full((1, 64), 65536.)]
    for value in invalid:
      with self.subTest(shape=value.shape, dtype=value.dtype):
        buffers = {'nav_features': np.ones((1, 64), np.float32)}
        with self.assertRaises(ValueError):
          copy_nav_features(buffers, {'nav_features': value})
        self.assertFalse(buffers['nav_features'].any())
    for shape in [(64,), (1, 128), (1, 'features')]:
      with self.assertRaises(ValueError):
        nav_input_shapes({'nav_features': shape})

  def test_encoder_output_contract_uses_features_not_plan_or_desire(self):
    output = np.arange(228, dtype=np.float32).reshape(1, 228)
    np.testing.assert_array_equal(extract_features(output), np.arange(164, 228).reshape(1, 64))
    for invalid in [np.zeros((1, 229)), np.full((1, 228), np.nan)]:
      with self.assertRaises(ValueError):
        extract_features(invalid)
