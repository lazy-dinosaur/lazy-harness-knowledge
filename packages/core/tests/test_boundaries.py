"""Dependency direction is enforced on source code: core imports neither the experiment runner nor a host."""
import ast, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def modules(folder):
    return {p.stem for p in (REPO / folder).glob('*.py')}


def imports(path):
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0: out.add(n.module.split('.')[0])
        if isinstance(n, ast.Import): out |= {a.name.split('.')[0] for a in n.names}
    return out


class Boundaries(unittest.TestCase):
    def test_core_imports_no_eval_and_no_host(self):
        forbidden = modules('packages/eval/src') | modules('hosts/pi/src')
        self.assertTrue(forbidden)
        bad = {p.name: sorted(imports(p) & forbidden) for p in (REPO / 'packages/core/src').glob('*.py') if imports(p) & forbidden}
        self.assertEqual(bad, {})

    def test_core_tests_import_no_eval(self):
        forbidden = modules('packages/eval/src')
        bad = {p.name: sorted(imports(p) & forbidden) for p in (REPO / 'packages/core/tests').glob('*.py') if imports(p) & forbidden}
        self.assertEqual(bad, {})

    def test_host_imports_no_eval(self):
        forbidden = modules('packages/eval/src')
        bad = {p.name: sorted(imports(p) & forbidden) for p in (REPO / 'hosts/pi/src').glob('*.py') if imports(p) & forbidden}
        self.assertEqual(bad, {})

    def test_no_pi_fabric_dependency(self):
        hits = [str(p.relative_to(REPO)) for p in REPO.rglob('*') if p.is_file() and p.suffix in ('.py', '.mjs', '.json', '.toml')
                and '.git' not in p.parts and p.name != 'test_boundaries.py' and 'pi-' + 'fabric' in p.read_text(errors='ignore')]
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
