"""Run one generator in its own process and write its toolpath as JSON.

    python gen_runner.py <generator file> <job.json> <result.json>

job.json: {"params": {...}, "defaults": {"line_width", "layer_height", "filament_diameter"}}

Why a separate process: generators are other people's Python. Here a slow loop
can be cancelled, a crash or a stray plt.show()/input() cannot freeze the panel,
and nothing the script does can reach the arms (it never sees the controllers).
It does run with the same rights as the panel, so only add code you trust.

FullControl's fc.transform is intercepted: a script that ends with
fc.transform(steps, 'gcode') or (..., 'plot') hands its steps to us instead of
writing files or opening a browser.
"""

import ast
import contextlib
import importlib.util
import inspect
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))        # scripts/ (for slicer etc. if a generator wants them)
os.environ.setdefault("MPLBACKEND", "Agg")       # plt.show() does nothing

sys.path.insert(0, HERE)
import toolpath as tpmod                          # noqa: E402  (this folder; no Qt needed here)


def _rewrite_constants(src, path, values):
    """Replace top-level `NAME = <literal>` values for script-style generators."""
    tree = ast.parse(src, filename=path)
    done = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in values and node.targets[0].id not in done):
            node.value = ast.copy_location(ast.Constant(values[node.targets[0].id]), node.value)
            done.add(node.targets[0].id)
    return compile(tree, path, "exec")


def run(path, job):
    params = job.get("params", {}) or {}
    defaults = job.get("defaults", {}) or {}
    if path.lower().endswith((".gcode", ".gco", ".g", ".nc")):
        with open(path, encoding="utf-8", errors="replace") as f:
            return tpmod.from_gcode(f.read(), defaults.get("layer_height", 0.2),
                                    defaults.get("filament_diameter", 1.75))

    captured = []
    fc_transform = None
    try:
        import fullcontrol as fc
        fc_transform = fc.transform

        def intercept(steps, result_type="gcode", controls=None, show_tips=True, **kw):
            captured.append((steps, controls))
            if result_type == "gcode":       # keep the script's own outputs correct (it may save the file)
                return fc_transform(steps, result_type, controls, show_tips=False, **kw)
            return None                      # 'plot': never open a browser window

        fc.transform = intercept
    except ImportError:
        fc = None

    folder = os.path.dirname(os.path.abspath(path))
    sys.path.insert(0, folder)
    os.chdir(folder)
    sys.argv = [path]
    with open(path, encoding="utf-8") as f:
        src = f.read()
    consts = job.get("constants") or {}
    code = _rewrite_constants(src, path, consts) if consts else compile(src, path, "exec")
    module = type(sys)("cell_generator")
    module.__file__ = path
    sys.modules["cell_generator"] = module
    try:
        exec(code, module.__dict__)
    except ModuleNotFoundError as e:
        if e.name == "fullcontrol":
            raise tpmod.GeneratorError("This generator needs FullControl, which is not installed on this "
                                       "computer:\n  pip install git+https://github.com/FullControlXYZ/fullcontrol")
        raise

    gen = module.__dict__.get("generate")
    if callable(gen):
        sig = inspect.signature(gen)
        if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            kwargs = dict(params)
        else:
            kwargs = {k: v for k, v in params.items() if k in sig.parameters}
        result = gen(**kwargs)
        if result is None and captured:
            steps, ctl = captured[-1]
            return tpmod.from_fullcontrol(steps, ctl, fc_transform)
        return tpmod.adapt(result, defaults, fc_transform)
    build = module.__dict__.get("build_steps")
    if callable(build):
        return tpmod.adapt(build(), defaults, fc_transform)
    if captured:
        steps, ctl = captured[-1]
        return tpmod.from_fullcontrol(steps, ctl, fc_transform)
    steps = module.__dict__.get("steps")
    if isinstance(steps, list) and steps:
        return tpmod.adapt(steps, defaults, fc_transform)
    raise tpmod.GeneratorError("The script ran but produced no toolpath. Define generate(), or build "
                               "FullControl `steps` and call fc.transform(steps, 'gcode').")


def _user_traceback(path):
    """Traceback lines from the generator's own files, not from this runner."""
    tb = traceback.format_exc().splitlines()
    keep, skip = [], False
    for line in tb:
        if line.strip().startswith('File "') and os.path.abspath(__file__) in line:
            skip = True
            continue
        if line.strip().startswith('File "'):
            skip = False
        if not skip:
            keep.append(line)
    return "\n".join(keep[-40:])


def main():
    path, job_file, out_file = sys.argv[1:4]
    with open(job_file) as f:
        job = json.load(f)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            tp = run(os.path.abspath(path), job)
        result = {"ok": True, **tp}
    except tpmod.GeneratorError as e:
        result = {"ok": False, "error": str(e)}
    except SystemExit as e:
        result = {"ok": False, "error": f"The script called exit({e.code}) before producing a toolpath."}
    except BaseException as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": _user_traceback(path)}
    result["output"] = buf.getvalue()[-20000:]
    with open(out_file, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
