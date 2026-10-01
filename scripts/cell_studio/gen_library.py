"""Find generators and read their names and parameters WITHOUT running them.

A generator is any of:
  * a .py file with  generate(**params)  (and optionally NAME, DESCRIPTION,
    AUTHOR and PARAMS) - see generators/TEMPLATE.py;
  * a FullControl script, as people already write them: it builds `steps`
    and calls fc.transform(steps, 'gcode' or 'plot'). Its top-level number /
    true-false constants (RADIUS = 40, layers = 50) become editable parameters;
  * a .py file with the older build_steps() -> (steps, settings) convention;
  * a .gcode file from any slicer or script.

Metadata comes from reading the source with `ast`, so listing the library never
executes anybody's code. The code only runs when you press Generate, in a
separate process (gen_runner.py).
"""

import ast
import json
import os

from .home_config import REPO_ROOT

LIBRARY_ROOT = os.path.join(REPO_ROOT, "generators")
SETTINGS_FILE = os.path.join(REPO_ROOT, "config", "cell_studio.json")
GCODE_EXT = (".gcode", ".gco", ".g", ".nc")
SKIP_DIRS = {"__pycache__", ".git", ".ipynb_checkpoints", "venv", ".venv", "node_modules"}
RESERVED = {"NAME", "DESCRIPTION", "AUTHOR", "PARAMS", "TAGS"}


def _settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def extra_folders():
    return [p for p in _settings().get("generator_folders", []) if os.path.isdir(p)]


def add_folder(path):
    s = _settings()
    folders = s.get("generator_folders", [])
    if path not in folders:
        folders.append(path)
    s["generator_folders"] = folders
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)


def _pretty(stem):
    return stem.replace("_", " ").replace("-", " ").strip().capitalize()


def _kind_of(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def _norm_param(name, spec):
    """PARAMS entries may be  40,  (40, "mm"),  or  {"default": 40, "unit": "mm", ...}."""
    p = {"name": name, "unit": "", "help": "", "min": None, "max": None, "step": None, "choices": None,
         "label": _pretty(name) if name.islower() else name.replace("_", " ").title()}
    if isinstance(spec, dict):
        p.update({k: v for k, v in spec.items() if k in ("unit", "help", "min", "max", "step", "choices", "label")})
        p["default"] = spec.get("default", spec.get("value"))
    elif isinstance(spec, (tuple, list)) and spec and not isinstance(spec[0], (list, tuple, dict)):
        p["default"] = spec[0]
        if len(spec) > 1 and isinstance(spec[1], str):
            p["unit"] = spec[1]
    else:
        p["default"] = spec
    if p["choices"]:
        p["kind"] = "choice"
        if p["default"] is None:
            p["default"] = p["choices"][0]
    else:
        p["kind"] = _kind_of(p["default"])
    if p["kind"] == "str" and not isinstance(p["default"], str):
        return None
    return p


def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _simple_constant(node):
    """Number or bool literal, including -5."""
    v = _literal(node)
    if isinstance(v, bool) or (isinstance(v, (int, float)) and not isinstance(v, bool)):
        return v
    return None


def read_meta(path, root=None):
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.relpath(os.path.dirname(path), root) if root else os.path.dirname(path)
    meta = {"path": path, "name": _pretty(stem), "description": "", "author": "",
            "folder": "" if folder == "." else folder, "params": [], "style": None, "error": None}
    if path.lower().endswith(GCODE_EXT):
        meta["style"] = "gcode"
        meta["description"] = "G-code file. Widths are estimated from the extrusion amounts."
        return meta
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        meta["error"] = f"Syntax error on line {e.lineno}: {e.msg}"
        return meta
    except Exception as e:
        meta["error"] = str(e)
        return meta
    doc = ast.get_docstring(tree) or ""
    meta["description"] = doc.strip().split("\n\n")[0].replace("\n", " ") if doc else ""
    consts, params_spec, gen_fn, has_build, calls_transform, has_steps = {}, None, None, False, False, False
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "NAME":
                meta["name"] = _literal(node.value) or meta["name"]
            elif name == "DESCRIPTION":
                meta["description"] = _literal(node.value) or meta["description"]
            elif name == "AUTHOR":
                meta["author"] = _literal(node.value) or ""
            elif name == "PARAMS":
                params_spec = _literal(node.value)
            elif name == "steps":
                has_steps = True
            elif not name.startswith("_") and name not in consts:
                v = _simple_constant(node.value)
                if v is not None:
                    consts[name] = v
        elif isinstance(node, ast.FunctionDef) and node.name == "generate":
            gen_fn = node
        elif isinstance(node, ast.FunctionDef) and node.name == "build_steps":
            has_build = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "transform") or (isinstance(f, ast.Name) and f.id == "transform"):
                calls_transform = True
                break
    if gen_fn is not None:
        meta["style"] = "generate"
        if isinstance(params_spec, dict):
            meta["params"] = [p for p in (_norm_param(k, v) for k, v in params_spec.items()) if p]
        else:
            args = gen_fn.args
            defaults = args.defaults
            names = [a.arg for a in args.args][-len(defaults):] if defaults else []
            names += [a.arg for a in args.kwonlyargs]
            defaults = list(defaults) + list(args.kw_defaults)
            for n, d in zip(names, defaults):
                v = _literal(d) if d is not None else None
                p = _norm_param(n, v) if v is not None else None
                if p:
                    meta["params"].append(p)
    elif has_build:
        meta["style"] = "build_steps"
    elif calls_transform or has_steps:
        meta["style"] = "script"
        meta["params"] = [p for p in (_norm_param(k, v) for k, v in list(consts.items())[:40]) if p]
        for p in meta["params"]:
            p["const"] = True
    else:
        meta["error"] = ("No toolpath found: define generate(), or build FullControl `steps` and call "
                         "fc.transform(steps, ...). See generators/TEMPLATE.py.")
    if not meta["author"]:
        meta["author"] = meta["folder"].split(os.sep)[0] if meta["folder"] else ""
    return meta


def scan(roots=None):
    """[(root, [meta, ...]), ...] for the library folder and any added folders."""
    roots = roots or [LIBRARY_ROOT] + extra_folders()
    out = []
    for root in roots:
        items = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                low = fn.lower()
                if fn.startswith("_") or fn.upper().startswith("TEMPLATE"):
                    continue
                if low.endswith(".py") or low.endswith(GCODE_EXT):
                    items.append(read_meta(os.path.join(dirpath, fn), root))
        out.append((root, items))
    return out
