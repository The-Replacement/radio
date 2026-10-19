# web/vendor

Dependencias de terceros, servidas desde acá. **Ninguna sale a internet.**

El motivo es el mismo por el que la radio no se anuncia en directorios: si
la página pide un script a un CDN, ese CDN —y quien lo opere— ve quién
entra, cuándo y desde qué IP. Comprar privacidad con un `<script src>`
externo no tiene sentido.

## hls.js 1.7.3 — `hls.light.min.js`

Apache-2.0 (ver `hls.js.LICENSE`). https://github.com/video-dev/hls.js

Para qué: Safari reproduce HLS solo; Chrome y Firefox no. hls.js es el
puente que le da HLS a Chrome, Firefox y Android. Sin él, la cámara se ve
únicamente en Safari e iOS.

Se usa la variante **light**: no trae audio alternativo, subtítulos ni
DRM/EME. La cámara es video mudo por diseño, así que nada de eso hace
falta y son ~230 KB menos (380 KB contra 608 KB).

Actualizar:

```bash
cd /tmp && curl -sSLO https://registry.npmjs.org/hls.js/-/hls.js-<VERSION>.tgz
tar xzf hls.js-<VERSION>.tgz
cp package/dist/hls.light.min.js web/vendor/
cp package/LICENSE web/vendor/hls.js.LICENSE
```

Cargado sólo si el navegador no reproduce HLS nativo (ver `web/index.html`).
