// Registers the service worker (PWA). Kept in its own file because the Content-Security-Policy blocks inline scripts.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(function () { /* PWA is optional */ });
}
