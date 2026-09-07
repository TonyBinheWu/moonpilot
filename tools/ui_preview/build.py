#!/usr/bin/env python3
"""Build only native UI dependencies; no Panda firmware or model compilation."""
from pathlib import Path
import os
import subprocess
import sys


def main():
  import capnproto
  import json11
  import zeromq

  root = Path(__file__).resolve().parents[2]
  generated = root / 'openpilot/cereal/gen/cpp'
  generated.mkdir(parents=True, exist_ok=True)
  bin_path = str(Path(capnproto.DIR) / 'bin')
  env = {**os.environ, 'PATH': bin_path + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
  schemas = [root / 'openpilot/cereal' / name for name in ('log.capnp', 'deprecated.capnp', 'custom.capnp')]
  schemas.append(root / 'opendbc_repo/opendbc/car/car.capnp')
  subprocess.run([bin_path + '/capnp', 'compile', '--src-prefix=' + str(root / 'openpilot/cereal'),
    '--src-prefix=' + str(root / 'opendbc_repo/opendbc/car'), '-I' + str(root / 'opendbc_repo/opendbc/car'),
    '-I' + str(root / 'openpilot/cereal'), '-o', 'c++:' + str(generated), *map(str, schemas)], env=env, check=True)
  compiler = os.environ.get('CXX', 'g++')
  command = [compiler, '-std=c++20', '-shared', '-fPIC', '-O2', '-pthread']
  command += ['-I' + str(p) for p in (root, root / 'openpilot', generated, json11.INCLUDE_DIR, zeromq.INCLUDE_DIR, capnproto.INCLUDE_DIR)]
  command += [str(root / 'openpilot/common' / f) for f in ('params_c.cc', 'params.cc', 'util.cc', 'swaglog.cc')]
  command += ['-L' + json11.LIB_DIR, '-L' + zeromq.LIB_DIR, '-ljson11', '-lzmq', '-o', str(root / 'openpilot/common/libparams_c.so')]
  subprocess.run(command, check=True)
  subprocess.run([sys.executable, '-m', 'SCons', '--minimal', '-j4'], cwd=root / 'msgq_repo', env=env, check=True)


if __name__ == '__main__':
  main()
