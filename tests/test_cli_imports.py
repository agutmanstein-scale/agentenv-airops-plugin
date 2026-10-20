"""A clean install has only the base dependencies, so whatever a CLI plugin module imports when agent-env loads it,
through the package's own modules, must come from them (or what they require), not from an extra."""

import ast
import sys
import tomllib
from functools import cache
from importlib.metadata import PackageNotFoundError, packages_distributions, requires
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
PKG = "agentenv_airops"
CLIS = ("agentenv_airops.cli", "agentenv_airops.occ.cli")


def test_the_cli_plugins_import_only_what_a_clean_install_has():
    base = closure([Requirement(r) for r in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "dependencies"]])
    owners = packages_distributions()
    for cli in CLIS:
        found = {name: sorted(canonicalize_name(d) for d in owners.get(name, [name])) for name in third_party(cli)}
        missing = {name: dists for name, dists in found.items() if not set(dists) & base}
        assert not missing, f"{cli} imports, through the package, modules only an extra installs: {missing}"


def test_the_walk_sees_the_imports_of_the_modules_it_reaches():
    assert {"click", "agent_env"} <= third_party("agentenv_airops.cli")
    assert {"numpy", "pandas", "highspy"} <= third_party("agentenv_airops.occ.cli")


@cache
def third_party(module: str) -> frozenset[str]:
    """The top-level names of the non-stdlib modules ``module`` imports when it loads, directly or through the
    package's own modules and their packages' __init__ files; imports inside functions run later and are left out."""
    seen, todo, out = set(), [module], set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [p for p in parents(name) if p not in seen]
        for target, is_ours in imported(name):
            if is_ours:
                todo.append(target)
            elif target not in sys.stdlib_module_names:
                out.add(target)
    return frozenset(out)


def imported(name: str) -> list[tuple[str, bool]]:
    path = source(name)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    out = []
    for node in loading(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [(a.name if a.name.startswith(PKG) else a.name.split(".")[0], a.name.startswith(PKG))
                    for a in node.names]
        elif node.level:
            base = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1])
            target = f"{base}.{node.module}" if node.module else base
            out.append((target, True))
            out += [(f"{target}.{a.name}", True) for a in node.names if exists(f"{target}.{a.name}")]
        elif node.module.startswith(PKG):
            out.append((node.module, True))
            out += [(f"{node.module}.{a.name}", True) for a in node.names if exists(f"{node.module}.{a.name}")]
        else:
            out.append((node.module.split(".")[0], False))
    return out


def loading(tree: ast.AST):
    """The import statements that run when the module loads: everywhere but inside functions."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield from loading(node)


def parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def source(name: str) -> Path:
    path = ROOT / "src" / Path(*name.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def exists(name: str) -> bool:
    return source(name).is_file()


def closure(reqs: list[Requirement]) -> set[str]:
    """The distributions a base install brings: these requirements and what they require, with no extra."""
    out, todo = set(), list(reqs)
    while todo:
        req = todo.pop()
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(req.name)
        if name in out:
            continue
        out.add(name)
        try:
            todo += [Requirement(r) for r in requires(name) or []]
        except PackageNotFoundError:
            pytest.fail(f"{name}, a base requirement, isn't installed here, so its own requirements can't be read")
    return out
