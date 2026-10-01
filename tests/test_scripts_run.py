"""Every script in the repo must run, not just parse.

`calibrate.py` shipped for months calling `argparse.ArgumentParser()` with no `argparse` import
anywhere in the file. It compiles, it imports, pytest passes, and it dies with
`NameError: name 'argparse' is not defined` the instant the user runs it - which is exactly the
script you need to set the app up.

Nothing in the test suite touches these entry points, so nothing noticed. These checks are the
cheap guard: a script that cannot even build its own parser is not finished.
"""
from __future__ import annotations

import ast
import builtins
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(
    [p for p in ROOT.glob("*.py")] + [p for p in (ROOT / "tools").glob("*.py")]
    + [p for p in (ROOT / "kinesis").glob("*.py")]
)
# builtins is a module, not a namespace: dir() on it does not give the builtin names. Getting this
# wrong makes the test report print/len/Exception as "undefined", which is how a useful check turns
# into noise nobody reads.
_BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}


def _undefined_names(path: Path) -> list:
    """Names the module reads but never binds. Static, so no import is needed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = set(_BUILTINS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(read - bound)


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_module_reads_a_name_it_never_binds(path):
    missing = _undefined_names(path)
    assert not missing, (
        f"{path.name} uses {missing} without importing or defining it. It compiles and imports "
        f"fine, then dies with NameError the first time someone runs it."
    )


# The scripts a user actually launches to get the app working. `--help` is the cheapest way to prove
# a script can build its parser, needs no camera, and touches nothing.
ENTRY_POINTS = ["calibrate.py", "calibrate_gaze.py", "kinesis.py", "tools/check_gaze.py",
                "tools/deploy_check.py"]


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_an_entry_point_answers_help(script):
    """`--help` must exit 0. A crash here is the bug that shipped in calibrate.py."""
    path = ROOT / script
    if not path.is_file():
        pytest.skip(f"{script} not present")
    done = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True,
                          timeout=90, cwd=str(ROOT))
    assert done.returncode == 0, (
        f"{script} --help exited {done.returncode}\n{done.stderr[-800:]}"
    )
    assert "usage" in (done.stdout + done.stderr).lower(), f"{script} --help printed no usage"
