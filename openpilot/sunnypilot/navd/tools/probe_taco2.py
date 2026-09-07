#!/usr/bin/env python3
"""Offline-only taco2 encoder / driving-input experiment. Never publishes controls.

Supply the unmodified taco2 navmodel.onnx and one or more driving ONNX files.
Synthetic rasters are numerical smoke tests, NOT compatible Mapbox rendering or
evidence that a driving model follows a route. No files are downloaded or modified.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.sunnypilot.navd.model_inputs import NAV_FEATURE_SHAPE
from openpilot.sunnypilot.navd.tools.audit_model import audit

TACO2_COMMIT = 'a8c957e9d98cb3c334ab60c09e731c262605ae56'
ENCODER_SHA256 = 'f851f19b0a9e2299639f18856e4879ee863918227b7cef6a07eb6b94a273a9aa'
FEATURE_START = 33 * 2 * 2 + 32  # plan mean/std XY, then desire prediction (nav.h)


def make_session(path):
  import onnxruntime as ort
  options = ort.SessionOptions()
  options.intra_op_num_threads = 2
  options.inter_op_num_threads = 1
  return ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])


def synthetic_rasters():
  y, x = np.indices((256, 256))
  trunk = (np.abs(x - 128) <= 3) & (y >= 128)
  masks = {'blank': np.zeros((256, 256), dtype=bool), 'straight': np.abs(x - 128) <= 3,
           'left': trunk | ((np.abs(y - 128) <= 3) & (x <= 128)),
           'right': trunk | ((np.abs(y - 128) <= 3) & (x >= 128))}
  return {key: value.astype(np.float32).reshape(1, 1, 256, 256) for key, value in masks.items()}


def extract_features(output):
  output = np.asarray(output)
  if output.shape != (1, 228) or not np.isfinite(output).all():
    raise ValueError('Unexpected taco2 navmodel output; expected finite (1, 228)')
  return output[:, FEATURE_START:FEATURE_START + 64].astype(np.float32)


def encode(encoder):
  with open(encoder, 'rb') as f:
    if hashlib.file_digest(f, 'sha256').hexdigest() != ENCODER_SHA256:
      raise ValueError('Encoder SHA-256 differs from pinned taco2; do not assume the same feature layout')
  session = make_session(encoder)
  features = {name: extract_features(session.run(None, {'input_img': raster})[0])
              for name, raster in synthetic_rasters().items()}
  summary = {name: {'shape': list(value.shape), 'finite': bool(np.isfinite(value).all()),
                    'l2_norm': float(np.linalg.norm(value)),
                    'max_abs_delta_from_blank': float(np.max(np.abs(value - features['blank'])))}
             for name, value in features.items()}
  return features, summary


def probe_driving(path, features):
  report = audit(path)
  if 'nav_features' not in report['inputs']:
    return report | {'status': 'unsupported_no_nav_features', 'inference_run': False}
  if tuple(report['inputs']['nav_features']) != NAV_FEATURE_SHAPE:
    return report | {'status': 'unsupported_feature_shape', 'inference_run': False}
  if not report['navigation_output_dependencies']['nav_features']:
    return report | {'status': 'unused_nav_features_input', 'inference_run': False}

  session = make_session(path)
  dtypes = {'tensor(float)': np.float32, 'tensor(float16)': np.float16}
  inputs = {}
  for spec in session.get_inputs():
    if spec.type not in dtypes or any(not isinstance(d, int) or d <= 0 for d in spec.shape):
      raise ValueError(f'Unsupported input specification: {spec.name}, {spec.type}, {spec.shape}')
    inputs[spec.name] = np.zeros(spec.shape, dtype=dtypes[spec.type])
  if inputs.get('traffic_convention', np.empty(0)).shape == (1, 2):
    inputs['traffic_convention'][0, 0] = 1
  if inputs.get('driving_style', np.empty(0)).shape == (1, 12):
    inputs['driving_style'][0, [0, 5]] = 1  # taco2 modeld.cc defaults
  # All non-navigation inputs are identical; temporal state is not fed back.
  outputs = {}
  for name, value in {'zero_features': np.zeros(NAV_FEATURE_SHAPE, np.float32), **features}.items():
    inputs['nav_features'][:] = value
    result = session.run(None, inputs)
    if any(not np.isfinite(out).all() for out in result):
      raise ValueError(f'Non-finite driving output for {name}')
    outputs[name] = [out.astype(np.float32) for out in result]
  output_names = [spec.name for spec in session.get_outputs()]
  deltas = {name: {key: float(np.max(np.abs(out - baseline)))
                   for key, out, baseline in zip(output_names, result, outputs['zero_features'], strict=True)}
            for name, result in outputs.items()}
  return report | {'status': 'offline_numerical_probe_only', 'inference_run': True,
                   'max_abs_output_delta_from_zero_features': deltas,
                   'note_navigation': '數值差異不證明會導航；目前相機與歷史輸入為零，不能評估實際駕駛表現。'}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--encoder', type=Path, required=True)
  parser.add_argument('--driving', type=Path, action='append', required=True)
  args = parser.parse_args()
  features, summary = encode(args.encoder)
  report = {'taco2_commit': TACO2_COMMIT, 'encoder_sha256': ENCODER_SHA256,
            'experiment': 'offline synthetic raster smoke test; no onroad producer or model weight modification',
            'encoder_results': summary,
            'driving_results': [probe_driving(path, features) for path in args.driving]}
  print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
  main()
