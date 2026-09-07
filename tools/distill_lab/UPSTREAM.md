# Upstream provenance

Vendored from https://github.com/YassineYousfi/openpilot.distill at commit
`294d57a919cc384386349a22ac5c9cc02d5edb99`. Original MIT license and README are retained under `upstream/`.

Trailing whitespace and empty-file newlines are normalized without semantic changes.

Intentional changes to the vendored snapshot:

1. `localizer/logs.py`: accept real tici/tizi/mici device identities; retain external GPS and raw UBlox requirements. Require valid orientation, velocity, acceleration and angular velocity measurements. Use logged SOF/EOF midpoint instead of the comma-four-specific fixed EOF minus 8 ms approximation. This midpoint is a traceable timestamp convention, **not** a verified optical exposure-centre calibration; validate alignment on real hardware.
2. `supervised/dataset.py`: use actual sensor_name when provided by the importer; keep upstream camera mapping for existing public labels. Remove training windows whose future targets would extrapolate beyond available localization.
3. `supervised/model_for_inference.py`: set `ORT_DISABLE_TELEMETRY=1` before importing ONNX Runtime, and also disable telemetry through its API before creating sessions. This covers direct upstream simulation entry points. The lab and tests use the same settings. ORT 1.29's POSIX telemetry requires the pre-initialization environment opt-out; the API alone does not suffice.

`drive_lab/` supplies the route-level manifest, checksums, training controller, local visualization, evaluation and export. It uses upstream architecture, targets and loss rather than inventing a replacement driving model. `car.py` targets the exact source contract hashes in `target_contract.json` and never changes CAN safety limits.

For an upstream update, import it into a new research branch, review these patches and the output contract, rerun tests and hardware validation. Do not blindly replace the lockfile or point training at moving master branches.

UI package ranges are in `requirements-ui.txt`; the core training environment remains pinned by upstream/uv.lock. Save `uv pip freeze --python upstream/.venv/bin/python` with each hardware experiment when exact UI/tool reproduction is needed.
