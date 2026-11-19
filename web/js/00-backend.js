// Backend-origin override. Written at deploy time ONLY when the page and the
// stream live on different origins (e.g. Cloudflare Pages frontend + tunnel
// backend). Absent/empty, 00-config.js defaults BACKEND_URL to location.origin.
//   window.RADIO_BACKEND_URL = 'https://YOUR-BACKEND-ORIGIN';
