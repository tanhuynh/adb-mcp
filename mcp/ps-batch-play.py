# MIT License
#
# Copyright (c) 2025 Mike Chambers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from mcp.server.fastmcp import FastMCP, Image
from core import init, sendCommand, createCommand
from fonts import list_all_fonts_postscript
import numpy as np
import base64
import socket_client
import sys
import os

import io, re
from PIL import Image as PILImage

FONT_LIMIT = 1000 #max number of font names to return to AI

mcp_name = "Adobe Photoshop Batch Play MCP Server"
mcp = FastMCP(mcp_name, log_level="ERROR")
print(f"{mcp_name} running on stdio", file=sys.stderr)

APPLICATION = "photoshop"
PROXY_URL = 'http://localhost:3001'
PROXY_TIMEOUT = 20

socket_client.configure(
    app=APPLICATION, 
    url=PROXY_URL,
    timeout=PROXY_TIMEOUT
)

init(APPLICATION, socket_client)

@mcp.tool()
def call_batch_play_command(commands: list):
    """
    Executes arbitrary Photoshop batchPlay commands via MCP.

    Args:
        commands (str): A raw JSON string representing a list of batchPlay descriptors.
            This should be the exact JSON string you would pass to `batchPlay()` in a UXP plugin.

    Returns:
        Any: The result returned from Photoshop after executing the batchPlay command(s).

    Example:
        >>> commands = '''
        ... [
        ...     {
        ...         "_obj": "exportDocumentAs",
        ...         "exportAs": {
        ...             "_obj": "exportAsPNG",
        ...             "interlaced": false,
        ...             "transparency": true,
        ...             "metadata": 1
        ...         },
        ...         "documentID": 1234,
        ...         "saveFile": {
        ...             "_path": "/Users/yourname/Downloads/export.png",
        ...             "_kind": "local"
        ...         },
        ...         "overwrite": true
        ...     }
        ... ]
        ... '''
        >>> result = call_batch_play_command(commands)
        >>> print(result)
        # Output from Photoshop will be returned as-is (usually a list of response descriptors)
    """

    if not commands:
        raise ValueError("commands cannot be empty.")

    command = createCommand(
        "executeBatchPlayCommand",
        {
            "commands": commands
        }
    )

    return sendCommand(command)


KEEP = ("WhiteBalance", "Temperature", "Tint", "Exposure2012", "Contrast2012",
        "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012", "Texture",
        "Clarity2012", "Dehaze", "Vibrance", "Saturation", "CameraProfile",
        "Look", "LensProfileEnable", "ColorNoiseReduction", "Sharpness")
HSL = ("HueAdjustment", "SaturationAdjustment", "LuminanceAdjustment")
# Camera Raw writes crs: settings both as attributes and as simple elements
_ATTR = re.compile(r'crs:(\w+)="([^"]*)"')
_ELEM = re.compile(r'<crs:(\w+)>([^<]*)</crs:\1>')
THUMB = (64, 64)          # downsampled RGB thumbnail compared between calls
STALE_MAD = 0.25         # mean abs diff (0-255). Measured 2026-11-22: unchanged/undo = 0.00,
                         # Temp -200 K < 1.5 (missed at 1.5), Exposure +0.10 = 3.24
WB_PCT = 0.5             # both |R/G| and |B/G| must move this much (%), in opposite directions.
                         # Measured: Temp -200 K = -0.92/+1.11 (WB); Exposure +0.10 = +0.19/-0.51 (not WB)


def _likely_wb(rg_pct: float, bg_pct: float) -> bool:
    return rg_pct * bg_pct < 0 and min(abs(rg_pct), abs(bg_pct)) > WB_PCT
_last_thumb: dict[int, np.ndarray] = {}


def _bp(descs: list):
    r = sendCommand(createCommand("executeBatchPlayCommand", {"commands": descs}))
    if r.get("status") != "SUCCESS":
        raise RuntimeError(f"batchPlay failed: {r}")
    return r["response"]


@mcp.tool()
def get_raw_baseline() -> dict:
    """Raw develop settings captured when the active document was opened from Lightroom Classic.
    Frozen snapshot: raw edits made later in Photoshop are NOT reflected; use check_stale.
    Returns source='unavailable' for a saved/reopened file (crs AlreadyApplied=True)."""
    res = _bp([{"_obj": "get", "_target": [
        {"_property": "XMPMetadataAsUTF8"},
        {"_ref": "document", "_enum": "ordinal", "_value": "targetEnum"}]}])
    res = res[0] if isinstance(res, list) else res
    xmp = res["XMPMetadataAsUTF8"]
    crs = dict(_ATTR.findall(xmp)) | dict(_ELEM.findall(xmp))
    if crs.get("AlreadyApplied") == "True":
        return {"source": "unavailable",
                "reason": "AlreadyApplied=True: document crs describes the saved TIFF, not the raw",
                "hint": "baseline = the source CR3's Lightroom settings; ask the user"}
    return {"source": "document_xmp_at_open",
            "baseline": {k: crs[k] for k in KEEP if k in crs},
            "hsl": {k: v for k, v in crs.items() if k.startswith(HSL) and v not in ("0", "+0")},
            "has_masks": "MaskGroupBasedCorrections" in xmp}


def _thumb(b64: str) -> np.ndarray:
    with PILImage.open(io.BytesIO(base64.b64decode(b64))) as im:
        return np.asarray(im.convert("RGB").resize(THUMB, PILImage.BILINEAR), dtype=np.float32)


@mcp.tool()
def check_stale(layer_id: int, mark: bool = False) -> dict:
    """Compare the layer render with the thumbnail stored after the LLM's last batch.
    Call with mark=True at session start and right after each LLM batch.
    stale=True with no LLM change means the user edited something (e.g. the raw in
    Camera Raw): ask what changed. Unchanged renders give distance 0.0 exactly.

    channel_shift: mean R/G/B change in levels. It is uneven even for pure exposure
    edits (a warm image gains more red), so do NOT infer white balance from it.
    wb_hint: change in the R/G and B/G ratios (%). Exposure leaves both near 0;
    a white-balance edit moves them in opposite directions (cooler: rg_pct < 0,
    bg_pct > 0). Camera Raw exposure can nudge one ratio slightly (tone curve,
    highlight roll-off), so likely_wb=True only when BOTH ratios move beyond WB_PCT
    in opposite directions."""
    r = sendCommand(createCommand("getLayerImage", {"layerId": layer_id, "maxSize": 512}))
    if r.get("status") != "SUCCESS":
        raise RuntimeError(f"getLayerImage failed: {r}")
    t = _thumb(r["response"]["dataUrl"].split(",", 1)[1])
    prev = _last_thumb.get(layer_id)
    if prev is None or prev.shape != t.shape:
        out = {"stale": False, "distance": None, "channel_shift": None, "wb_hint": None}
    else:
        diff = t - prev
        mad = float(np.abs(diff).mean())
        m0 = prev.reshape(-1, 3).mean(axis=0) + 1e-6
        m1 = t.reshape(-1, 3).mean(axis=0) + 1e-6
        rg = 100 * ((m1[0] / m1[1]) / (m0[0] / m0[1]) - 1)
        bg = 100 * ((m1[2] / m1[1]) / (m0[2] / m0[1]) - 1)
        out = {"stale": mad > STALE_MAD, "distance": round(mad, 2),
               "channel_shift": [round(float(c), 2) for c in diff.reshape(-1, 3).mean(axis=0)],
               "wb_hint": {"rg_pct": round(float(rg), 2), "bg_pct": round(float(bg), 2),
                           "likely_wb": _likely_wb(float(rg), float(bg))}}
    if mark or prev is None:
        _last_thumb[layer_id] = t
    return out


@mcp.resource("config://get_instructions")
def get_instructions() -> str:
    """Read this first! Returns information and instructions on how to use Photoshop and this API"""

    return f"""
    You are a photoshop expert who is creative and loves to help other people learn to use Photoshop and create. You are well versed in composition, design and color theory, and try to follow that theory when making decisions.

    Unless otherwise specified, all commands act on the currently active document in Photoshop

    Rules to follow:

    1. Think deeply about how to solve the task
    2. Always check your work
    3. You can view the current visible photoshop file by calling get_document_image
    4. Pay attention to font size (dont make it too big)
    5. Always use alignment (align_content()) to position your text.
    6. Read the info for the API calls to make sure you understand the requirements and arguments
    7. When you make a selection, clear it once you no longer need it

    Here are some general tips for when working with Photoshop.

    In general, layers are created from bottom up, so keep that in mind as you figure out the order or operations. If you want you have lower layers show through higher ones you must either change the opacity of the higher layers and / or blend modes.

    When using fonts there are a couple of things to keep in mind. First, the font origin is the bottom left of the font, not the top right.

    Suggestions for sizes:
    Paragraph text : 8 to 12 pts
    Headings : 14 - 20 pts
    Single Word Large : 20 to 25pt

    Pay attention to what layer names are needed for. Sometimes the specify the name of a newly created layer and sometimes they specify the name of the layer that the action should be performed on.

    As a general rule, you should not flatten files unless asked to do so, or its necessary to apply an effect or look.

    When generating an image, you do not need to first create a pixel layer. A layer will automatically be created when you generate the image.

    Colors are defined via a dict with red, green and blue properties with values between 0 and 255
    {{"red":255, "green":0, "blue":0}}

    Bounds is defined as a dict with top, left, bottom and right properties
    {{"top": 0, "left": 0, "bottom": 250, "right": 300}}

    Valid options for API calls:

    alignment_modes: {", ".join(alignment_modes)}

    justification_modes: {", ".join(justification_modes)}

    blend_modes: {", ".join(blend_modes)}

    anchor_positions: {", ".join(anchor_positions)}

    interpolation_methods: {", ".join(interpolation_methods)}

    fonts: {", ".join(font_names[:FONT_LIMIT])}
    """

font_names = list_all_fonts_postscript()

interpolation_methods = [
   "AUTOMATIC",
   "BICUBIC",
   "BICUBICSHARPER",
   "BICUBICSMOOTHER",
   "BILINEAR",
   "NEARESTNEIGHBOR"
]

anchor_positions = [
   "BOTTOMCENTER",
   "BOTTOMLEFT", 
   "BOTTOMRIGHT", 
   "MIDDLECENTER", 
   "MIDDLELEFT", 
   "MIDDLERIGHT", 
   "TOPCENTER", 
   "TOPLEFT", 
   "TOPRIGHT"
]

justification_modes = [
    "CENTER",
    "CENTERJUSTIFIED",
    "FULLYJUSTIFIED",
    "LEFT",
    "LEFTJUSTIFIED",
    "RIGHT",
    "RIGHTJUSTIFIED"
]

alignment_modes = [
    "LEFT",
    "CENTER_HORIZONTAL",
    "RIGHT",
    "TOP",
    "CENTER_VERTICAL",
    "BOTTOM"
]

blend_modes = [
    "COLOR",
    "COLORBURN",
    "COLORDODGE",
    "DARKEN",
    "DARKERCOLOR",
    "DIFFERENCE",
    "DISSOLVE",
    "DIVIDE",
    "EXCLUSION",
    "HARDLIGHT",
    "HARDMIX",
    "HUE",
    "LIGHTEN",
    "LIGHTERCOLOR",
    "LINEARBURN",
    "LINEARDODGE",
    "LINEARLIGHT",
    "LUMINOSITY",
    "MULTIPLY",
    "NORMAL",
    "OVERLAY",
    "PASSTHROUGH",
    "PINLIGHT",
    "SATURATION",
    "SCREEN",
    "SOFTLIGHT",
    "SUBTRACT",
    "VIVIDLIGHT"
]

if __name__ == "__main__":
    mcp.run()

