/* Shared helpers: JSON fetch, alerts, time formatting, debounce. */
(function (global) {
  "use strict";

  async function api(path, options) {
    options = options || {};
    const init = {
      method: options.method || "GET",
      headers: Object.assign(
        { Accept: "application/json" },
        options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
        options.headers || {}
      ),
      // Cookies are HttpOnly; they must ride along on every call.
      credentials: "same-origin",
    };
    if (options.body !== undefined) {
      init.body = options.body instanceof FormData ? options.body : JSON.stringify(options.body);
    }

    let response;
    try {
      response = await fetch(path, init);
    } catch (err) {
      throw new ApiError("Network unreachable. Check your connection.", 0);
    }

    if (response.status === 401) {
      // Session gone: bounce to the login page that matches this area.
      const target = global.location.pathname.startsWith("/admin") ? "/admin/login" : "/login";
      if (global.location.pathname !== target) global.location.href = target;
      throw new ApiError("Your session has expired. Please sign in again.", 401);
    }

    const type = response.headers.get("content-type") || "";
    const payload = type.includes("application/json")
      ? await response.json().catch(function () { return null; })
      : await response.text();

    if (!response.ok) {
      const detail =
        (payload && payload.detail) ||
        (typeof payload === "string" && payload) ||
        "Request failed (" + response.status + ")";
      throw new ApiError(detail, response.status, payload);
    }
    return payload;
  }

  function ApiError(message, status, payload) {
    this.name = "ApiError";
    this.message = message;
    this.status = status;
    this.payload = payload;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function showAlert(el, message, kind) {
    if (!el) return;
    el.textContent = message;
    el.className = "alert " + (kind || "err");
    el.hidden = false;
  }

  function hideAlert(el) {
    if (el) el.hidden = true;
  }

  /** Seconds -> HH:MM:SS (or MM:SS under an hour). */
  function formatDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = function (n) { return String(n).padStart(2, "0"); };
    return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : pad(m) + ":" + pad(sec);
  }

  /** Trailing-edge debounce; the returned function exposes .flush(). */
  function debounce(fn, wait) {
    let timer = null;
    let lastArgs = null;
    function wrapped() {
      lastArgs = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        const args = lastArgs;
        lastArgs = null;
        fn.apply(null, args);
      }, wait);
    }
    wrapped.flush = function () {
      if (timer) {
        clearTimeout(timer);
        timer = null;
        const args = lastArgs;
        lastArgs = null;
        if (args) fn.apply(null, args);
      }
    };
    wrapped.pending = function () { return timer !== null; };
    return wrapped;
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      }
    );
  }

  function relativeTime(iso) {
    if (!iso) return "never";
    const delta = (Date.now() - new Date(iso).getTime()) / 1000;
    if (delta < 5) return "just now";
    if (delta < 60) return Math.floor(delta) + "s ago";
    if (delta < 3600) return Math.floor(delta / 60) + "m ago";
    return Math.floor(delta / 3600) + "h ago";
  }

  global.Portal = {
    api: api,
    ApiError: ApiError,
    showAlert: showAlert,
    hideAlert: hideAlert,
    formatDuration: formatDuration,
    debounce: debounce,
    escapeHtml: escapeHtml,
    relativeTime: relativeTime,
  };
})(window);
