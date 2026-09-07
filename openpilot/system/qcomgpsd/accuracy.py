"""Position uncertainty from the modem's horizontal error ellipse, in meters."""
import math


def horizontal_accuracy(report: dict) -> float:
  # HDOP/VDOP are dimensionless and must not be passed off as meter accuracy.
  axes = (report['q_FltEllipseSemimajorAxis'], report['q_FltEllipseSemiminorAxis'])
  # Reject unknown reliability codes and low-confidence ellipses rather than
  # treating a small radius at low confidence as accurate positioning.
  if report['u_HorizontalReliability'] not in (3, 4) or not 68 <= report['u_EllipseConfidence'] <= 100:
    return 0.0
  return max(axes) if all(math.isfinite(v) and v > 0 for v in axes) else 0.0
