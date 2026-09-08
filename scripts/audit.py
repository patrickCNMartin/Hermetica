"""Measure the repo. Every number here comes from a file on disk or a coverage run.

    python scripts/audit.py            # print the table
    python scripts/audit.py --write    # also regenerate docs/status.md

Definitions, so the table is readable without reading this file:
  lines        physical lines in the module's .py files (blank and comment included)
  public_fns   module-level `def`/`async def` not starting with "_" (methods excluded)
  tests        test functions in tests/dev_tests whose file imports the module
  cov_pct      statements covered / statements, from one pytest --cov run
"""

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "hermetica"
TESTS = ROOT / "tests" / "dev_tests"
STATUS = ROOT / "docs" / "status.md"
MODULES = sorted(p.name for p in PKG.iterdir() if (p / "__init__.py").is_file())


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def public_functions(path):
    return sum(
        1
        for node in _parse(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )


def imported_modules(path):
    """Top-level package names imported by a test file."""
    names = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_counts():
    """module -> number of test functions in files importing it."""
    counts = dict.fromkeys(MODULES, 0)
    for path in sorted(TESTS.glob("test_*.py")):
        n = sum(
            1
            for node in ast.walk(_parse(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        for mod in imported_modules(path) & set(MODULES):
            counts[mod] += n
    return counts


def coverage():
    """One pytest run: (module -> (covered, statements), tests passed)."""
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "cov.json"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS),
            "-q",
            *[f"--cov={m}" for m in MODULES],
            f"--cov-report=json:{report}",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.exit(f"the suite is not green, refusing to report:\n{proc.stdout}")
        if not report.is_file():
            sys.exit(f"pytest produced no coverage report:\n{proc.stdout}{proc.stderr}")
        data = json.loads(report.read_text())
    totals = {m: [0, 0] for m in MODULES}
    for name, entry in data["files"].items():
        parts = pathlib.Path(name).parts
        for mod in MODULES:
            if mod in parts:
                s = entry["summary"]
                totals[mod][0] += s["covered_lines"]
                totals[mod][1] += s["num_statements"]
                break
    passed = re.search(r"(\d+) passed", proc.stdout)
    return {m: tuple(v) for m, v in totals.items()}, int(passed.group(1))


def rows():
    tests = test_counts()
    cov, passed = coverage()
    out = []
    for mod in MODULES:
        files = sorted((PKG / mod).rglob("*.py"))
        covered, statements = cov[mod]
        out.append(
            {
                "module": mod,
                "files": len(files),
                "lines": sum(
                    len(f.read_text(encoding="utf-8").splitlines()) for f in files
                ),
                "public_fns": sum(public_functions(f) for f in files),
                "tests": tests[mod],
                "covered": covered,
                "statements": statements,
                "cov_pct": round(100 * covered / statements, 1) if statements else 0.0,
            }
        )
    return out, passed


COLUMNS = [
    "module",
    "files",
    "lines",
    "public_fns",
    "tests",
    "covered",
    "statements",
    "cov_pct",
]


def table(rows, passed):
    lines = ["\t".join(COLUMNS)]
    lines += ["\t".join(str(r[c]) for c in COLUMNS) for r in rows]
    total_cov = sum(r["covered"] for r in rows)
    total_stmt = sum(r["statements"] for r in rows)
    lines.append(
        "\t".join(
            [
                "TOTAL",
                str(sum(r["files"] for r in rows)),
                str(sum(r["lines"] for r in rows)),
                str(sum(r["public_fns"] for r in rows)),
                str(sum(r["tests"] for r in rows)),
                str(total_cov),
                str(total_stmt),
                str(round(100 * total_cov / total_stmt, 1) if total_stmt else 0.0),
            ]
        )
    )
    lines.append(f"SUITE\t{passed} tests passed")
    return "\n".join(lines)


def status_md(text):
    return "\n".join(
        [
            "# Status — measured",
            "",
            "Generated by `scripts/audit.py`; run `make audit` to regenerate.",
            "Do not edit by hand — CI fails when this file differs from a fresh run.",
            "Nothing here is an estimate: every number is counted from the files or",
            "from one `pytest --cov` run over `tests/dev_tests`.",
            "",
            "| column | meaning |",
            "|---|---|",
            "| `lines` | physical lines in the module's `.py` files |",
            "| `public_fns` | module-level `def` not starting with `_` |",
            "| `tests` | test functions in files that import the module |",
            "| `cov_pct` | `covered` / `statements` × 100 |",
            "",
            "A test file that imports two modules counts its tests against both, so",
            "the `tests` column sums to more than `SUITE`, the number pytest ran.",
            "",
            "```",
            text,
            "```",
            "",
        ]
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate docs/status.md")
    args = ap.parse_args()
    text = table(*rows())
    print(text)
    if args.write:
        STATUS.write_text(status_md(text), encoding="utf-8")
        print(f"\nwrote {STATUS.relative_to(ROOT)}", file=sys.stderr)
