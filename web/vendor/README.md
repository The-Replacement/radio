# web/vendor

Third-party dependencies, served from here. **None of them go out to the
internet.**

The reason is the same one the radio is not announced in directories: if the
page asks a CDN for a script, that CDN —and whoever runs it— sees who comes in,
when and from which IP. Buying privacy with an external `<script src>` makes no
sense.

## hls.js 1.7.3 — `hls.light.min.js`

Apache-2.0 (see `hls.js.LICENSE`). https://github.com/video-dev/hls.js

What it is for: Safari plays HLS only; Chrome and Firefox do not. hls.js is the
bridge that gives HLS to Chrome, Firefox and Android. Without it, the camera is
seen only in Safari and iOS.

The **light** variant is used: it carries no alternate audio, no subtitles and
no DRM/EME. The camera is muted video by design, so none of that is needed and
it is ~230 KB less (380 KB versus 608 KB).

Actualizar:

```bash
cd /tmp && curl -sSLO https://registry.npmjs.org/hls.js/-/hls.js-<VERSION>.tgz
tar xzf hls.js-<VERSION>.tgz
cp package/dist/hls.light.min.js web/vendor/
cp package/LICENSE web/vendor/hls.js.LICENSE
```

Loaded **always**: regardless of whether the browser supports native HLS or
not. Chrome 154 answers `"maybe"` to `canPlayType` and leaves the stream ~10s
behind with no knob (measured). So everybody uses hls.js + MSE: one single,
predictable path. Safari's native implementation is not used either — same MSE,
same player, no per-platform fork.
