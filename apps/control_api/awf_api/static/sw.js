// AI Work Factory service worker: makes the dashboard installable and lets it open without a connection.
// Only the app shell (page, script, style, icons) is cached. API responses (runs, artifacts, approvals) are
// never stored on the device: they are always fetched live and need a signed-in session.
"use strict";

const VERSION = "__VERSION__";  // asset fingerprint, filled in by the server: a new release installs a new cache
const SHELL = `awf-shell-${VERSION}`;
const ASSETS = [
  "/",
  `/static/i18n.js?v=${VERSION}`,
  `/static/app.js?v=${VERSION}`,
  `/static/style.css?v=${VERSION}`,
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("awf-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/health") return;  // live data: network only

  if (request.mode === "navigate") {
    // Network first so a new release shows up at once; the cached page when offline.
    event.respondWith(fetch(request).catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(SHELL).then((cache) => cache.put(request, copy));
      }
      return response;
    })));
  }
});
