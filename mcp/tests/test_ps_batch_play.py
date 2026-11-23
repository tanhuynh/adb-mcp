import pytest

from conftest import fail, gradient, jpeg_payload, ok

# Trimmed from the real document XMP of _52_0367-1 (2026-11-22): fresh from LrC, top-level
# settings as elements, AlreadyApplied=True (Camera Raw sets it on every rendered doc),
# and a nested crs:Look whose Parameters repeat keys with different values.
XMP_FRESH = """<x:xmpmeta><rdf:RDF><rdf:Description rdf:about="">
 <xmpMM:PreservedFileName>_52_0367.CR3</xmpMM:PreservedFileName>
 <crs:Version>19.0</crs:Version><crs:WhiteBalance>Auto</crs:WhiteBalance>
 <crs:Temperature>7500</crs:Temperature><crs:Tint>0</crs:Tint>
 <crs:Exposure2012>+0.15</crs:Exposure2012><crs:Highlights2012>-40</crs:Highlights2012>
 <crs:Shadows2012>+35</crs:Shadows2012>
 <crs:HueAdjustmentYellow>-13</crs:HueAdjustmentYellow>
 <crs:SaturationAdjustmentRed>+6</crs:SaturationAdjustmentRed>
 <crs:LuminanceAdjustmentAqua>0</crs:LuminanceAdjustmentAqua>
 <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>48, 53</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
 <crs:RangeMaskMapInfo rdf:parseType="Resource"><crs:RangeMaskMapInfo rdf:parseType="Resource">
   <crs:RGBMin>0.001 0.001 0.001</crs:RGBMin></crs:RangeMaskMapInfo></crs:RangeMaskMapInfo>
 <crs:CameraProfile>Adobe Standard</crs:CameraProfile>
 <crs:Look rdf:parseType="Resource"><crs:Name>Adobe Color</crs:Name>
   <crs:Parameters rdf:parseType="Resource"><crs:Version>18.0</crs:Version>
   <crs:Exposure2012>+1.00</crs:Exposure2012><crs:CameraProfile>Camera Landscape</crs:CameraProfile>
   </crs:Parameters></crs:Look>
 <crs:HasSettings>True</crs:HasSettings><crs:AlreadyApplied>True</crs:AlreadyApplied>
</rdf:Description></rdf:RDF></x:xmpmeta>"""
XMP_ATTR = """<rdf:Description xmpMM:PreservedFileName="_52_0378.CR3" crs:WhiteBalance="Custom"
  crs:Temperature="7000" crs:Exposure2012="+0.33"/>"""


def so(name):
    return {"smartObject": {"_obj": "smartObject", "linked": False, "fileReference": name}}


def baseline_response(xmp, embedded="_52_0367.CR3"):
    return ok([so(embedded), {"XMPMetadataAsUTF8": xmp}])


class TestGetRawBaseline:
    def test_sends_smart_object_and_xmp_gets(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH))
        bp.get_raw_baseline(2)
        (cmd,) = fake.calls
        d_so, d_xmp = cmd["options"]["commands"]
        assert d_so["_target"][0] == {"_property": "smartObject"}
        assert d_so["_target"][1] == {"_ref": "layer", "_id": 2}
        assert d_xmp["_target"][0] == {"_property": "XMPMetadataAsUTF8"}

    def test_fresh_raw_document_despite_already_applied(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH))
        r = bp.get_raw_baseline(2)
        assert r["source"] == "document_xmp_at_open"
        assert r["embedded_file"] == "_52_0367.CR3"
        assert r["baseline"]["WhiteBalance"] == "Auto"
        assert r["baseline"]["Temperature"] == "7500"
        assert r["baseline"]["Highlights2012"] == "-40"

    def test_nested_look_parameters_do_not_override(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH))
        b = bp.get_raw_baseline(2)["baseline"]
        assert b["Exposure2012"] == "+0.15"          # not the Look's +1.00
        assert b["CameraProfile"] == "Adobe Standard"  # not "Camera Landscape"

    def test_hsl_keeps_only_nonzero(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH))
        assert bp.get_raw_baseline(2)["hsl"] == {
            "HueAdjustmentYellow": "-13", "SaturationAdjustmentRed": "+6"}

    def test_attribute_form(self, bp, fake):
        fake.queue(baseline_response(XMP_ATTR, embedded="_52_0378.CR3"))
        assert bp.get_raw_baseline(2)["baseline"] == {
            "WhiteBalance": "Custom", "Temperature": "7000", "Exposure2012": "+0.33"}

    def test_tiff_smart_object_is_unavailable(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH, embedded="_52_0378-Edit.tif"))
        r = bp.get_raw_baseline(2)
        assert r["source"] == "unavailable"
        assert r["embedded_file"] == "_52_0378-Edit.tif"
        assert "baseline" not in r

    def test_not_a_smart_object_is_unavailable(self, bp, fake):
        fake.queue(ok([{}, {"XMPMetadataAsUTF8": XMP_FRESH}]))
        assert bp.get_raw_baseline(2)["source"] == "unavailable"

    def test_xmp_for_a_different_raw_is_unavailable(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH, embedded="_52_0400.CR3"))
        r = bp.get_raw_baseline(2)
        assert r["source"] == "unavailable" and "_52_0367.CR3" in r["reason"]

    def test_raw_extension_case_insensitive(self, bp, fake):
        fake.queue(baseline_response(XMP_FRESH.replace("_52_0367.CR3", "dsc_1.nef"), embedded="DSC_1.NEF"))
        assert bp.get_raw_baseline(2)["source"] == "document_xmp_at_open"

    def test_masks_flag(self, bp, fake):
        xmp = XMP_FRESH.replace("<crs:HasSettings>",
              "<crs:MaskGroupBasedCorrections><rdf:Seq/></crs:MaskGroupBasedCorrections><crs:HasSettings>")
        fake.queue(baseline_response(xmp))
        assert bp.get_raw_baseline(2)["has_masks"] is True

    def test_batchplay_failure_raises(self, bp, fake):
        fake.queue(fail("no document"))
        with pytest.raises(RuntimeError, match="batchPlay failed"):
            bp.get_raw_baseline(2)


class TestCheckStale:
    def test_requests_small_layer_image(self, bp, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        bp.check_stale(2)
        (cmd,) = fake.calls
        assert cmd["action"] == "getLayerImage"
        assert cmd["options"] == {"layerId": 2, "maxSize": 512}

    def test_first_call_sets_reference(self, bp, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        assert bp.check_stale(2) == {"stale": False, "distance": None, "channel_shift": None, "wb_hint": None}

    def test_identical_render_is_not_stale(self, bp, fake):
        img = gradient()
        fake.queue(ok(jpeg_payload(img)), ok(jpeg_payload(img)), ok(jpeg_payload(img)))
        bp.check_stale(2, mark=True)
        for _ in range(2):
            r = bp.check_stale(2)
            assert r["stale"] is False
            assert r["distance"] < 0.5

    def test_exposure_change_is_stale(self, bp, fake):
        # roughly +1/6 EV in the mids: every channel up by ~8 levels
        fake.queue(ok(jpeg_payload(gradient())), ok(jpeg_payload(gradient(offset=(8, 8, 8)))))
        bp.check_stale(2, mark=True)
        r = bp.check_stale(2)
        assert r["stale"] is True
        assert all(c > 5 for c in r["channel_shift"])

    def test_white_balance_change_is_stale_and_uneven(self, bp, fake):
        # cooler: red down, blue up, green unchanged
        fake.queue(ok(jpeg_payload(gradient())), ok(jpeg_payload(gradient(offset=(-6, 0, 6)))))
        bp.check_stale(2, mark=True)
        r = bp.check_stale(2)
        assert r["stale"] is True
        red, green, blue = r["channel_shift"]
        assert red < -3 and blue > 3 and abs(green) < 1.5

    def test_exposure_is_not_flagged_as_white_balance(self, bp, fake):
        # multiplicative brightening (what exposure does): ratios stay put
        import numpy as np
        from PIL import Image
        a = gradient()
        b = Image.fromarray(np.clip(np.asarray(a, dtype=float) * 1.06, 0, 255).astype("uint8"))
        fake.queue(ok(jpeg_payload(a)), ok(jpeg_payload(b)))
        bp.check_stale(2, mark=True)
        r = bp.check_stale(2)
        assert r["stale"] is True
        assert r["wb_hint"]["likely_wb"] is False
        assert abs(r["wb_hint"]["rg_pct"]) < 0.5 and abs(r["wb_hint"]["bg_pct"]) < 0.5

    def test_small_white_balance_edit_is_caught(self, bp, fake):
        # like the measured Temp -200 K: R -0.7, G 0, B +0.7 levels
        fake.queue(ok(jpeg_payload(gradient())), ok(jpeg_payload(gradient(offset=(-1, 0, 1)))))
        bp.check_stale(2, mark=True)
        r = bp.check_stale(2)
        assert r["stale"] is True, r
        assert r["wb_hint"]["likely_wb"] is True
        assert r["wb_hint"]["rg_pct"] < 0 < r["wb_hint"]["bg_pct"]

    def test_mark_moves_reference(self, bp, fake):
        a, b = gradient(), gradient(offset=(10, 10, 10))
        fake.queue(ok(jpeg_payload(a)), ok(jpeg_payload(b)), ok(jpeg_payload(b)))
        bp.check_stale(2, mark=True)            # reference = a
        bp.check_stale(2, mark=True)            # LLM batch -> reference = b
        assert bp.check_stale(2)["stale"] is False

    def test_unmarked_check_keeps_reference(self, bp, fake):
        a, b = gradient(), gradient(offset=(10, 10, 10))
        fake.queue(ok(jpeg_payload(a)), ok(jpeg_payload(b)), ok(jpeg_payload(b)))
        bp.check_stale(2, mark=True)
        assert bp.check_stale(2)["stale"] is True
        assert bp.check_stale(2)["stale"] is True   # still compared against a

    def test_layers_tracked_separately(self, bp, fake):
        a, b = gradient(), gradient(offset=(10, 10, 10))
        fake.queue(ok(jpeg_payload(a)), ok(jpeg_payload(b)), ok(jpeg_payload(a)))
        bp.check_stale(2, mark=True)
        bp.check_stale(3, mark=True)
        assert bp.check_stale(2)["stale"] is False

    def test_same_layer_id_in_different_documents(self, bp, fake):
        a, b = gradient(), gradient(offset=(30, 30, 30))
        doc = lambda img, d: ok({**jpeg_payload(img), "documentId": d})
        fake.queue(doc(a, 65), doc(b, 142), doc(b, 142), doc(a, 65))
        bp.check_stale(2, mark=True)                 # doc 65 reference
        assert bp.check_stale(2)["distance"] is None  # doc 142: first sight, not "stale vs doc 65"
        assert bp.check_stale(2)["stale"] is False    # doc 142 vs its own reference
        assert bp.check_stale(2)["stale"] is False    # doc 65 still has its own reference

    def test_plugin_error_raises(self, bp, fake):
        fake.queue(fail("Only 8 bit image data can be encoded as jpeg"))
        with pytest.raises(RuntimeError, match="getLayerImage failed"):
            bp.check_stale(2)


def test_call_batch_play_passes_commands_through(bp, fake):
    descs = [{"_obj": "get", "_target": [{"_ref": "application"}]}]
    fake.queue(ok([{"ok": 1}]))
    assert bp.call_batch_play_command(descs) == ok([{"ok": 1}])
    assert fake.calls[0]["options"] == {"commands": descs}


@pytest.mark.parametrize("rg,bg,expected", [
    (-0.92, 1.11, True),     # measured 2026-11-22: Temp -200 K
    (0.19, -0.51, False),    # measured 2026-11-22: Exposure +0.10 (tone curve nudges B/G)
    (0.05, 0.03, False),     # unchanged ratios
    (1.2, 0.8, False),       # same direction: not a temperature move
], ids=["temp-200K", "exposure+0.10", "flat", "same-sign"])
def test_likely_wb_rule(bp, rg, bg, expected):
    assert bp._likely_wb(rg, bg) is expected


# ---- golden fixture: the real document XMP of _52_0367-1 (fresh from LrC, 2026-11-22) ----
FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


class TestRealXmp0367:
    @pytest.fixture
    def xmp(self):
        return (FIXTURES / "xmp_0367_fresh.xml").read_text(encoding="utf-8")

    def test_baseline_matches_lightroom(self, bp, fake, xmp):
        fake.queue(ok([so("_52_0367.CR3"), {"XMPMetadataAsUTF8": xmp}]))
        r = bp.get_raw_baseline(2)
        assert r["source"] == "document_xmp_at_open"      # despite crs:AlreadyApplied=True
        assert r["baseline"] == {
            "WhiteBalance": "Auto", "Temperature": "7500", "Tint": "0",
            "Exposure2012": "+0.15", "Contrast2012": "+5", "Highlights2012": "-40",
            "Shadows2012": "+35", "Whites2012": "+23", "Blacks2012": "-16",
            "Texture": "0", "Clarity2012": "0", "Dehaze": "0",
            "Vibrance": "+15", "Saturation": "+1", "CameraProfile": "Adobe Standard",
            "LensProfileEnable": "0", "ColorNoiseReduction": "0", "Sharpness": "40",
        }
        assert r["has_masks"] is False

    def test_hsl_nonzero(self, bp, fake, xmp):
        fake.queue(ok([so("_52_0367.CR3"), {"XMPMetadataAsUTF8": xmp}]))
        assert bp.get_raw_baseline(2)["hsl"] == {
            "HueAdjustmentYellow": "-13",
            "SaturationAdjustmentRed": "+6", "SaturationAdjustmentOrange": "+8",
            "SaturationAdjustmentYellow": "+21", "SaturationAdjustmentGreen": "+4",
            "LuminanceAdjustmentRed": "+19", "LuminanceAdjustmentOrange": "+19",
            "LuminanceAdjustmentYellow": "+12", "LuminanceAdjustmentGreen": "+11",
        }

    def test_look_block_is_ignored(self, bp, xmp):
        crs = bp._top_level_crs(xmp)
        assert crs["Version"] == "19.0" and crs["CameraProfile"] == "Adobe Standard"
        assert "LookTable" not in crs and "Name" not in crs     # only inside crs:Look
        assert "RGBMin" not in crs                               # only inside RangeMaskMapInfo
        assert crs["BlueHue"] == "-16" and crs["BlueSaturation"] == "+28"   # calibration
