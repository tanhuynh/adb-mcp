// Unit tests for the UXP command handlers, run in plain Node with Photoshop stubbed.
// Run:  node --test uxp/ps/tests/*.test.js
const { test, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");

// ---- stub `photoshop` and `uxp` before the handlers are required ----------------
const calls = { getPixels: [], encode: [], batchPlay: [], disposed: 0 };
const state = { encodeThrows: false, batchPlayResult: [] };

const fakeImageData = (opts) => ({
    width: opts.targetSize?.width ?? 8192,
    height: Math.round((opts.targetSize?.width ?? 8192) * 5464 / 8192),
    colorSpace: "RGB",
    components: 3,
    dispose: () => { calls.disposed += 1; },
});

const photoshop = {
    app: { activeDocument: { layers: [{ id: 2, name: "_52_0378" }, { id: 5, name: "grp", layers: [{ id: 7 }] }] } },
    constants: {},
    core: { executeAsModal: async (cb) => cb({}) },
    action: { batchPlay: async (descs) => { calls.batchPlay.push(descs); return state.batchPlayResult; } },
    imaging: {
        getPixels: async (opts) => { calls.getPixels.push(opts); return { imageData: fakeImageData(opts) }; },
        encodeImageData: async (o) => {
            calls.encode.push(o);
            if (state.encodeThrows) throw new Error("Only 8 bit image data can be encoded as jpeg");
            return "QUJD";
        },
    },
};
const uxp = { storage: { localFileSystem: {} } };

const origLoad = Module._load;
Module._load = function (request, ...rest) {
    if (request === "photoshop") return photoshop;
    if (request === "uxp") return uxp;
    return origLoad.call(this, request, ...rest);
};
const core = require("../commands/core.js").commandHandlers;
const layers = require("../commands/layers.js").commandHandlers;

beforeEach(() => {
    calls.getPixels.length = 0; calls.encode.length = 0; calls.batchPlay.length = 0; calls.disposed = 0;
    state.encodeThrows = false; state.batchPlayResult = [];
});

const PREVIEW_OPTS = { componentSize: 8, colorProfile: "sRGB IEC61966-2.1", applyAlpha: true };

// ---- getDocumentImage ----------------------------------------------------------
test("getDocumentImage: 8-bit sRGB preview at default 1024", async () => {
    const r = await core.getDocumentImage({ options: {} });
    assert.equal(calls.getPixels.length, 1);
    assert.deepEqual(calls.getPixels[0], { ...PREVIEW_OPTS, targetSize: { width: 1024 } });
    assert.equal(r.dataUrl, "data:image/jpeg;base64,QUJD");
    assert.equal(r.width, 1024);
});

test("getDocumentImage: works when options are missing", async () => {
    await core.getDocumentImage({});
    assert.deepEqual(calls.getPixels[0].targetSize, { width: 1024 });
});

test("getDocumentImage: honours maxSize", async () => {
    await core.getDocumentImage({ options: { maxSize: 640 } });
    assert.deepEqual(calls.getPixels[0].targetSize, { width: 640 });
});

test("getDocumentImage: disposes pixels on success and on encode failure", async () => {
    await core.getDocumentImage({ options: {} });
    assert.equal(calls.disposed, 1);
    state.encodeThrows = true;
    await assert.rejects(core.getDocumentImage({ options: {} }), /8 bit/);
    assert.equal(calls.disposed, 2);
});

// ---- getLayerImage -------------------------------------------------------------
test("getLayerImage: targets the layer with preview options", async () => {
    const r = await layers.getLayerImage({ options: { layerId: 2 } });
    assert.deepEqual(calls.getPixels[0], { ...PREVIEW_OPTS, layerID: 2, targetSize: { width: 1024 } });
    assert.equal(r.format, "jpeg");
});

test("getLayerImage: honours maxSize and finds nested layers", async () => {
    await layers.getLayerImage({ options: { layerId: 7, maxSize: 512 } });
    assert.equal(calls.getPixels[0].layerID, 7);
    assert.deepEqual(calls.getPixels[0].targetSize, { width: 512 });
});

test("getLayerImage: unknown layer names the right function", async () => {
    await assert.rejects(layers.getLayerImage({ options: { layerId: 99 } }), /^Error: getLayerImage : Could not find layerId : 99/);
    assert.equal(calls.getPixels.length, 0);
});

test("getLayerImage: disposes pixels when encoding fails", async () => {
    state.encodeThrows = true;
    await assert.rejects(layers.getLayerImage({ options: { layerId: 2 } }), /8 bit/);
    assert.equal(calls.disposed, 1);
});

// ---- executeBatchPlayCommand ---------------------------------------------------
test("executeBatchPlayCommand: returns every result, not just the first", async () => {
    const descs = [{ _obj: "set" }, { _obj: "get" }];
    state.batchPlayResult = [{ _obj: "set" }, { XMPMetadataAsUTF8: "<x/>" }];
    const r = await core.executeBatchPlayCommand({ options: { commands: descs } });
    assert.deepEqual(calls.batchPlay[0], descs);
    assert.deepEqual(r, state.batchPlayResult);
});
