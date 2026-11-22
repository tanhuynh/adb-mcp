import pytest

from conftest import fail, gradient, jpeg_payload, ok

# Document XMP as seen on 2026-11-21: fresh from LrC (attribute form) ...
XMP_ATTR = """<x:xmpmeta><rdf:RDF><rdf:Description
  crs:Version="19.0" crs:WhiteBalance="Custom" crs:Temperature="7000" crs:Tint="0"
  crs:Exposure2012="+0.33" crs:Highlights2012="-40" crs:CameraProfile="Adobe Standard"
  crs:HueAdjustmentYellow="-13" crs:SaturationAdjustmentRed="+6" crs:LuminanceAdjustmentRed="0">
  <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
</rdf:Description></rdf:RDF></x:xmpmeta>"""
# ... element form (what `strings` found in the saved TIFF) ...
XMP_ELEM = """<rdf:Description><crs:WhiteBalance>Custom</crs:WhiteBalance>
<crs:Temperature>7000</crs:Temperature><crs:Exposure2012>+0.33</crs:Exposure2012>
</rdf:Description>"""
# ... and a saved/reopened TIFF.
XMP_SAVED = """<rdf:Description crs:Version="19.0" crs:ProcessVersion="15.4"
  crs:WhiteBalance="As Shot" crs:Exposure2012="0" crs:AlreadyApplied="True"/>"""


def xmp_response(xmp, as_list=True):
    body = {"XMPMetadataAsUTF8": xmp}
    return ok([body] if as_list else body)


class TestGetRawBaseline:
    def test_sends_targeted_xmp_get(self, bp, fake):
        fake.queue(xmp_response(XMP_ATTR))
        bp.get_raw_baseline()
        (cmd,) = fake.calls
        assert cmd["action"] == "executeBatchPlayCommand"
        (desc,) = cmd["options"]["commands"]
        assert desc["_obj"] == "get"
        assert desc["_target"][0] == {"_property": "XMPMetadataAsUTF8"}
        assert desc["_target"][1]["_ref"] == "document"

    def test_attribute_form(self, bp, fake):
        fake.queue(xmp_response(XMP_ATTR))
        r = bp.get_raw_baseline()
        assert r["source"] == "document_xmp_at_open"
        assert r["baseline"]["Temperature"] == "7000"
        assert r["baseline"]["Exposure2012"] == "+0.33"
        assert r["baseline"]["Highlights2012"] == "-40"
        assert r["baseline"]["CameraProfile"] == "Adobe Standard"
        assert r["has_masks"] is False

    def test_hsl_keeps_only_nonzero(self, bp, fake):
        fake.queue(xmp_response(XMP_ATTR))
        hsl = bp.get_raw_baseline()["hsl"]
        assert hsl == {"HueAdjustmentYellow": "-13", "SaturationAdjustmentRed": "+6"}

    def test_element_form(self, bp, fake):
        fake.queue(xmp_response(XMP_ELEM))
        b = bp.get_raw_baseline()["baseline"]
        assert b == {"WhiteBalance": "Custom", "Temperature": "7000", "Exposure2012": "+0.33"}

    def test_nested_element_does_not_leak(self, bp, fake):
        fake.queue(xmp_response(XMP_ATTR))
        r = bp.get_raw_baseline()
        assert "ToneCurvePV2012" not in r["baseline"]

    @pytest.mark.parametrize("as_list", [True, False], ids=["return-o", "return-o0"])
    def test_handles_both_plugin_return_shapes(self, bp, fake, as_list):
        fake.queue(xmp_response(XMP_ATTR, as_list=as_list))
        assert bp.get_raw_baseline()["baseline"]["Temperature"] == "7000"

    def test_saved_file_is_unavailable(self, bp, fake):
        fake.queue(xmp_response(XMP_SAVED))
        r = bp.get_raw_baseline()
        assert r["source"] == "unavailable"
        assert "baseline" not in r

    def test_masks_flag(self, bp, fake):
        fake.queue(xmp_response(XMP_ATTR.replace("</rdf:Description>",
                   "<crs:MaskGroupBasedCorrections><rdf:Seq/></crs:MaskGroupBasedCorrections></rdf:Description>")))
        assert bp.get_raw_baseline()["has_masks"] is True

    def test_batchplay_failure_raises(self, bp, fake):
        fake.queue(fail("no document"))
        with pytest.raises(RuntimeError, match="batchPlay failed"):
            bp.get_raw_baseline()


class TestCheckStale:
    def test_requests_small_layer_image(self, bp, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        bp.check_stale(2)
        (cmd,) = fake.calls
        assert cmd["action"] == "getLayerImage"
        assert cmd["options"] == {"layerId": 2, "maxSize": 512}

    def test_first_call_sets_reference(self, bp, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        assert bp.check_stale(2) == {"stale": False, "distance": None, "channel_shift": None}

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

    def test_plugin_error_raises(self, bp, fake):
        fake.queue(fail("Only 8 bit image data can be encoded as jpeg"))
        with pytest.raises(RuntimeError, match="getLayerImage failed"):
            bp.check_stale(2)


def test_call_batch_play_passes_commands_through(bp, fake):
    descs = [{"_obj": "get", "_target": [{"_ref": "application"}]}]
    fake.queue(ok([{"ok": 1}]))
    assert bp.call_batch_play_command(descs) == ok([{"ok": 1}])
    assert fake.calls[0]["options"] == {"commands": descs}
