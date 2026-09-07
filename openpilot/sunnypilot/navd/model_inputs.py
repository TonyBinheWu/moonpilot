"""Optional taco2 input plumbing. This does not add navigation weights to a model.

The onroad loop currently has no compatible nav encoder/renderer and supplies no
nav_features. Nonzero features are only supplied by offline experiments.
"""
import numpy as np

NAV_FEATURE_SHAPE = (1, 64)


def nav_input_shapes(input_shapes: dict) -> dict:
  if 'nav_features' not in input_shapes:
    return {}
  if tuple(input_shapes['nav_features']) != NAV_FEATURE_SHAPE:
    raise ValueError('taco2 nav_features requires shape (1, 64); a different encoder needs its own contract')
  return {'nav_features': NAV_FEATURE_SHAPE}


def copy_nav_features(buffers: dict, inputs: dict) -> bool:
  """Copy an explicit input, clearing the previous frame on missing/invalid data.

  Reject supplying features to a model without this input instead of silently
  claiming the model used them. Shape matching does not establish compatibility
  with the encoder's learned representation.
  """
  target = buffers.get('nav_features')
  value = inputs.get('nav_features')
  if target is None:
    if value is not None:
      raise ValueError('This driving model has no nav_features input')
    return False
  target.fill(0)
  if target.shape != NAV_FEATURE_SHAPE:
    raise ValueError('Unsupported nav_features buffer shape')
  if value is None:
    return False
  value = np.asarray(value)
  if value.shape != NAV_FEATURE_SHAPE or value.dtype.kind != 'f' or not np.isfinite(value).all():
    raise ValueError('nav_features must contain 64 finite floating-point values with shape (1, 64)')
  if np.any(np.abs(value) > np.finfo(np.float16).max):
    raise ValueError('nav_features exceeds the taco2 float16 input range')
  np.copyto(target, value, casting='same_kind')
  return True
