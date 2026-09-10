"""Source-level build configuration tests; no SCons execution or GPU validation."""
import ast
import configparser
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[4]
SCONSCRIPT = ROOT / 'openpilot/selfdrive/modeld/SConscript'


def load_config(arch, chestnut=True):
  tree = ast.parse(SCONSCRIPT.read_text())
  nodes = []
  for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'camera_configs' for t in node.targets):
      nodes.append(node)
    elif isinstance(node, ast.If):
      if isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == 'arch':
        nodes.append(node)
      elif isinstance(node.test, ast.Name) and node.test.id == 'CHESTNUT':
        nodes.append(node)
  namespace = {
    'arch': arch,
    'CHESTNUT': chestnut,
    '_ar_ox_fisheye': SimpleNamespace(width=1928, height=1208),
    '_os_fisheye': SimpleNamespace(width=1344, height=760),
    'File': lambda path: SimpleNamespace(abspath=path),
  }
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SCONSCRIPT), 'exec'), namespace)
  return namespace


class TestChestnutBuildConfig(unittest.TestCase):
  def test_both_camera_formats_on_all_build_hosts(self):
    for arch in ('comma_arm64', 'aarch64', 'x86_64', 'Darwin'):
      with self.subTest(arch=arch):
        self.assertEqual(load_config(arch)['camera_configs'], [(1928, 1208), (1344, 760)])

  def test_qcom_flags_are_preserved(self):
    config = load_config('comma_arm64')
    self.assertEqual(config['tg_backend'], 'QCOM')
    flags = dict(item.split('=', 1) for item in config['tg_flags'].split())
    self.assertEqual(flags, {'DEV': 'QCOM', 'IMAGE': '1', 'FLOAT16': '1', 'NOLOCALS': '1',
                             'JIT_BATCH_SIZE': '0', 'OPENPILOT_HACKS': '1'})

  def test_cpu_backends_are_preserved(self):
    self.assertEqual(load_config('x86_64')['tg_flags'], 'DEV=CPU:LLVM')
    self.assertEqual(load_config('Darwin')['tg_flags'], 'DEV=CPU')

  def test_upstream_chestnut_flags(self):
    flags = dict(item.split('=', 1) for item in load_config('comma_arm64')['chestnut_tg_flags'].split())
    self.assertEqual(flags, {'DEBUG': '1', 'DEV': 'USB+AMD:LLVM', 'FRAME_DEV': 'CPU', 'FLOAT16': '1',
                             'JIT_BATCH_SIZE': '0', 'GMMU': '0', 'TC_OPT': '2', 'TC_MIN_GLOBALS': '32'})

  def test_no_chestnut_configuration_without_device(self):
    config = load_config('comma_arm64', chestnut=False)
    self.assertNotIn('chestnut_tg_flags', config)
    self.assertNotIn('chestnut_lock', config)

  def test_driving_pickle_budget_accounts_for_both_formats(self):
    tree = ast.parse(SCONSCRIPT.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'estimate_pickle_max_size'
             and any(isinstance(child, ast.Name) and child.id == 'onnx_sizes_sum' for child in ast.walk(node))]
    self.assertEqual(len(calls), 1)
    argument = ast.Expression(body=calls[0].args[0])
    value = eval(compile(argument, str(SCONSCRIPT), 'eval'),
                 {'onnx_sizes_sum': 1024, 'camera_configs': load_config('comma_arm64')['camera_configs']})
    self.assertEqual(value, 2048)

  def test_existing_compile_skip_switch_is_preserved(self):
    tree = ast.parse(SCONSCRIPT.read_text())
    guards = [node for node in tree.body if isinstance(node, ast.If)
              and ast.unparse(node.test) == "not os.getenv('SKIP_TINYGRAD_COMPILE')"]
    self.assertEqual(len(guards), 1)
    self.assertTrue(any(isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                        and node.target.id == 'chestnut' for node in guards[0].body))

  def test_submodule_sources(self):
    config = configparser.ConfigParser()
    config.read(ROOT / '.gitmodules')
    self.assertEqual(config['submodule "tinygrad"']['url'], 'https://github.com/tinygrad/tinygrad.git')
    self.assertEqual(config['submodule "tinygrad"']['path'], 'tinygrad_repo')
    self.assertEqual(config['submodule "teleoprtc_repo"']['url'], 'https://github.com/commaai/teleoprtc')
    self.assertEqual(config['submodule "opendbc"']['url'], 'https://github.com/TonyBinheWu/opendbc.git')


if __name__ == '__main__':
  unittest.main()
