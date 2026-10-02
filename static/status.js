(function () {
  "use strict";

  var POLL_MS = 10000;

  function el(id) { return document.getElementById(id); }

  function renderComfyUI(body) {
    var pill = el("comfyui-status");
    var detail = el("comfyui-detail");
    var checked = el("checked-at");
    if (checked) { checked.textContent = body.checked_at || "—"; }

    if (body.reachable) {
      pill.textContent = "REACHABLE — ComfyUI " + (body.version || "unknown");
      pill.className = "status-pill ok";
      detail.textContent = "";
    } else {
      pill.textContent = "UNREACHABLE";
      pill.className = "status-pill bad";
      var lines = ["Error: " + (body.error_detail || "no response"), ""];
      lines.push("Documented fallback order:");
      (body.fallback_order || []).forEach(function (f, i) {
        lines.push((i + 1) + ". " + f);
      });
      detail.textContent = lines.join("\n");
    }
  }

  function poll() {
    fetch("/api/comfyui/status")
      .then(function (r) { return r.json(); })
      .then(renderComfyUI)
      .catch(function (err) {
        el("comfyui-status").textContent = "PROBE FAILED";
        el("comfyui-status").className = "status-pill bad";
        el("comfyui-detail").textContent =
          "The app itself could not reach /api/comfyui/status: " + err;
      });
  }

  poll();
  setInterval(poll, POLL_MS);
})();