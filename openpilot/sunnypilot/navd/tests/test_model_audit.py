from pathlib import Path
import tempfile
import unittest

import onnx
from onnx import TensorProto, helper

from openpilot.sunnypilot.navd.tools.audit_model import audit
from openpilot.sunnypilot.navd.tools.probe_taco2 import probe_driving


class TestModelAudit(unittest.TestCase):
  def test_unused_input_does_not_claim_navigation_support(self):
    for connected in (False, True):
      with self.subTest(connected=connected), tempfile.TemporaryDirectory() as tmp:
        inputs = [helper.make_tensor_value_info(name, TensorProto.FLOAT, [1, 64]) for name in ('x', 'nav_features')]
        output = helper.make_tensor_value_info('out', TensorProto.FLOAT, [1, 64])
        node = helper.make_node('Identity', ['nav_features' if connected else 'x'], ['out'])
        model = helper.make_model(helper.make_graph([node], 'probe', inputs, [output]))
        path = Path(tmp) / 'probe.onnx'
        onnx.save(model, path)
        self.assertEqual(audit(path)['navigation_output_dependencies'], {'nav_features': ['out'] if connected else []})
        if not connected:
          report = probe_driving(path, {})
          self.assertEqual(report['status'], 'unused_nav_features_input')
          self.assertFalse(report['inference_run'])
