from pathlib import Path
import os
import sys

# Tests create local inference sessions and require no telemetry collection.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
try:
    import onnxruntime
    onnxruntime.disable_telemetry_events()
except ModuleNotFoundError:
    pass  # The export tests already skip when this optional test dependency is absent.

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "upstream"))
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(LAB.parents[1]))
