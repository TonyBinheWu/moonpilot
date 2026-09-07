"""Reproducible acquisition, training and candidate deployment for openpilot.distill."""
import os

# ORT 1.29 initializes POSIX telemetry before the Python session API is called.
# The process-level opt-out must be present before importing its native library.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
