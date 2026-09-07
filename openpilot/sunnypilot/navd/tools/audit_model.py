#!/usr/bin/env python3
"""Inspect an ONNX file without changing camera transforms or loading a GPU.

Developer use: uv run --with onnx python -m openpilot.sunnypilot.navd.tools.audit_model MODEL.onnx
Tensor dimensions alone do not establish optical FOV or training compatibility.
"""
import argparse
import hashlib
import json
from pathlib import Path


def audit(path):
  import onnx
  model = onnx.load(path, load_external_data=False)
  inputs = {v.name: [d.dim_value or d.dim_param for d in v.type.tensor_type.shape.dim] for v in model.graph.input}
  navigation_inputs = [key for key in inputs if key.startswith('nav')]
  dependencies = {}
  for name in navigation_inputs:
    reachable = {name}
    for node in model.graph.node:  # ONNX nodes are topologically ordered
      if any(value in reachable for value in node.input):
        reachable.update(node.output)
    dependencies[name] = [v.name for v in model.graph.output if v.name in reachable]
  with open(path, 'rb') as f:
    digest = hashlib.file_digest(f, 'sha256').hexdigest()
  return {'file': Path(path).name, 'sha256': digest, 'inputs': inputs,
          'navigation_inputs': navigation_inputs,
          'navigation_output_dependencies': dependencies,
          'outputs': {v.name: [d.dim_value or d.dim_param for d in v.type.tensor_type.shape.dim] for v in model.graph.output},
          'note': '輸入尺寸不代表完整光學視野；還必須檢查相機轉換、訓練前處理與感知輸出規格。'}


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('models', nargs='+')
  args = parser.parse_args()
  print(json.dumps([audit(path) for path in args.models], ensure_ascii=False, indent=2))
