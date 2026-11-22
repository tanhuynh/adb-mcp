from PIL import Image

from conftest import fail, gradient, jpeg_payload, ok


class TestGetLayerImage:
    def test_default_max_size(self, ps, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        ps.get_layer_image(2)
        assert fake.calls[0]["options"] == {"layerId": 2, "maxSize": 1024}

    def test_custom_max_size(self, ps, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        ps.get_layer_image(2, max_size=512)
        assert fake.calls[0]["options"]["maxSize"] == 512

    def test_returns_mcp_image(self, ps, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        img = ps.get_layer_image(2)
        assert isinstance(img, ps.Image)
        assert img.data[:2] == b"\xff\xd8"      # JPEG SOI marker

    def test_failure_passes_response_through(self, ps, fake):
        fake.queue(fail())
        assert ps.get_layer_image(2) == fail()


class TestGetDocumentImage:
    def test_sends_max_size(self, ps, fake):
        fake.queue(ok(jpeg_payload(gradient())), ok(jpeg_payload(gradient())))
        ps.get_document_image()
        ps.get_document_image(max_size=640)
        assert [c["options"] for c in fake.calls] == [{"maxSize": 1024}, {"maxSize": 640}]

    def test_returns_mcp_image(self, ps, fake):
        fake.queue(ok(jpeg_payload(gradient())))
        assert isinstance(ps.get_document_image(), ps.Image)


class TestSaveDocumentImageAsPng:
    def test_writes_png_from_nested_jpeg(self, ps, fake, tmp_path):
        out = tmp_path / "preview.png"
        fake.queue(ok(jpeg_payload(gradient(w=2048, h=1366))))
        r = ps.save_document_image_as_png(str(out))
        assert r["status"] == "success"
        assert (r["width"], r["height"]) == (2048, 1366)
        assert r["size_bytes"] == out.stat().st_size
        with Image.open(out) as im:
            assert im.format == "PNG" and im.size == (2048, 1366)

    def test_default_and_custom_max_size(self, ps, fake, tmp_path):
        fake.queue(ok(jpeg_payload(gradient())), ok(jpeg_payload(gradient())))
        ps.save_document_image_as_png(str(tmp_path / "a.png"))
        ps.save_document_image_as_png(str(tmp_path / "b.png"), max_size=800)
        assert [c["options"] for c in fake.calls] == [{"maxSize": 2048}, {"maxSize": 800}]

    def test_plugin_failure(self, ps, fake, tmp_path):
        fake.queue(fail("Only 8 bit image data can be encoded as jpeg"))
        r = ps.save_document_image_as_png(str(tmp_path / "x.png"))
        assert r["status"] == "error" and "getDocumentImage failed" in r["error"]
        assert not (tmp_path / "x.png").exists()

    def test_non_jpeg_payload(self, ps, fake, tmp_path):
        fake.queue(ok({"dataUrl": "data:image/png;base64,AAAA"}))
        r = ps.save_document_image_as_png(str(tmp_path / "x.png"))
        assert r == {"status": "error", "error": "No JPEG image data received"}
