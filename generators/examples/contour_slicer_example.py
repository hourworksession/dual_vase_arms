"""Stand-in for George's slicer until his code is added: slices a mesh into perimeter loops with the panel's built-in slicer and returns them as 3D paths for the right arm. It shows the contract a slicer follows: slice(model_path, **params) returns paths, FullControl steps or G-code."""

NAME = "Contour slicer (stand-in for George's)"
AUTHOR = "Examples"
PARAMS = {
    "layer_height": {"default": 0.6, "unit": "mm", "min": 0.05, "max": 1.0},
    "line_width": {"default": 1.3, "unit": "mm", "min": 0.2, "max": 2.5},
    "walls": {"default": 1, "min": 1, "max": 6},
}


def slice(model_path, layer_height, line_width, walls):
    from slicer import slice_model, SliceSettings, WALL_OUTER, WALL_INNER
    res = slice_model(model_path, SliceSettings(layer_height=layer_height, line_width=line_width,
                                                wall_count=int(walls), infill_density=0.0,
                                                top_layers=0, bottom_layers=0))
    paths = []
    for layer in res.layers:
        for p in layer.paths:
            if p.kind in (WALL_OUTER, WALL_INNER) and len(p.points) >= 2:
                pts = [(x, y, layer.z) for x, y in p.points]
                if p.closed:
                    pts.append(pts[0])
                paths.append(pts)
    return {"paths": paths, "width": line_width, "height": layer_height}
