"""Layer dependency rules from AGENTS.md section 4, enforced by parsing imports with ``ast``."""
import ast
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT_LAYERS = {"mcd", "ioc", "sdc", "sched", "rte", "app", "sim", "web", "config"}
STDLIB = set(__import__("sys").stdlib_module_names)

# layer -> project layers it may import (itself is always allowed)
ALLOWED = {
    "mcd": set(),
    "ioc": {"mcd"},
    "sdc": {"mcd"},
    "sched": {"mcd"},
    "rte": {"ioc", "sdc", "sched"},
    "app": {"rte"},
    "sim": {"rte", "sdc"},
    "web": {"rte", "app", "sim"},
}
NO_DJANGO = {"mcd", "ioc", "sdc", "sched", "rte", "app", "sim"}


def imported_modules(path):
    """Yield (top-level module, line) of every absolute import; relative imports stay in the layer."""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0], node.lineno


def layer_files(layer, skip_tests=True):
    base = os.path.join(ROOT, layer)
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "static", "templates", "migrations")]
        for name in files:
            if name.endswith(".py") and not (skip_tests and name == "test.py"):
                yield os.path.join(folder, name)


class LayerRuleTests(unittest.TestCase):
    def test_every_layer_follows_the_dependency_rules(self):
        violations = []
        checked = 0
        for layer, allowed in ALLOWED.items():
            for path in layer_files(layer):
                checked += 1
                for module, line in imported_modules(path):
                    if module in PROJECT_LAYERS and module != layer and module not in allowed:
                        violations.append("%s:%d imports %s" % (os.path.relpath(path, ROOT), line, module))
        self.assertGreater(checked, 10)
        self.assertEqual(violations, [])

    def test_only_web_and_config_use_django(self):
        offenders = []
        for layer in NO_DJANGO:
            for path in layer_files(layer, skip_tests=False):
                for module, line in imported_modules(path):
                    if module == "django":
                        offenders.append("%s:%d" % (os.path.relpath(path, ROOT), line))
        self.assertEqual(offenders, [])

    def test_app_imports_only_rte_and_the_standard_library(self):
        checked = 0
        for path in layer_files("app", skip_tests=False):
            checked += 1
            for module, line in imported_modules(path):
                ok = module in STDLIB or module in ("rte", "app")
                self.assertTrue(ok, "%s:%d imports %s" % (os.path.relpath(path, ROOT), line, module))
        self.assertGreaterEqual(checked, 6)

    def test_app_uses_the_rte_not_the_bsw_functions(self):
        for path in layer_files("app"):
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            for prefix in ("PCS_", "IOC_", "US_", "USB_", "TIM_", "SCHED_"):
                self.assertNotIn(prefix, text, "%s uses %s" % (os.path.relpath(path, ROOT), prefix))

    def test_the_checker_detects_a_broken_rule(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write("from mcd import pcs\nimport django\n")
        try:
            found = {module for module, _ in imported_modules(handle.name)}
        finally:
            os.unlink(handle.name)
        self.assertEqual(found, {"mcd", "django"})
        self.assertNotIn("mcd", ALLOWED["app"])


if __name__ == "__main__":
    unittest.main()
