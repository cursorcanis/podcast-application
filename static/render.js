(function () {
  "use strict";

  var POLL_MS = 5000;

  function el(id) { return document.getElementById(id); }

  function fmtSeconds(s) {
    s = Math.round(s);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m " + sec + "s";
    return sec + "s";
  }

  function renderChunks(chunks) {
    var tbody = el("chunk-rows");
    if (!tbody) return;
    tbody.innerHTML = "";
    chunks.forEach(function (c) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + c.chunk_index + "</td>" +
        "<td>" + c.speaker + "</td>" +
        "<td>" + c.status + "</td>" +
        "<td>" + c.attempt_count + "</td>" +
        "<td>" + (c.measured_render_seconds ? c.measured_render_seconds.toFixed(1) : "—") + "</td>" +
        "<td>" + (c.error_detail || "") + "</td>";
      tbody.appendChild(tr);
    });
  }

  function render(body) {
    if (!body.job) return;
    var statusEl = el("job-status");
    if (statusEl) statusEl.textContent = body.job.status;
    if (el("elapsed")) el("elapsed").textContent = fmtSeconds(body.elapsed_seconds);
    if (el("projected")) el("projected").textContent = fmtSeconds(body.projected_total_seconds);
    if (el("remaining")) el("remaining").textContent = fmtSeconds(body.remaining_seconds);
    if (el("projection-basis")) {
      el("projection-basis").textContent =
        body.projection_basis === "measured" ? "measured from this job's own chunks" : "initial estimate — no chunk finished yet";
    }
    if (el("count-total")) el("count-total").textContent = body.counts.total;
    if (el("count-succeeded")) el("count-succeeded").textContent = body.counts.succeeded;
    if (el("count-in-flight")) el("count-in-flight").textContent = body.counts.in_flight;
    if (el("count-pending")) el("count-pending").textContent = body.counts.pending;
    if (el("count-failed")) el("count-failed").textContent = body.counts.failed;
    renderChunks(body.chunks || []);

    if (body.job.status === "running") {
      setTimeout(poll, POLL_MS);
    } else if (statusEl) {
      // Terminal state (succeeded / failed / cancelled) reached — reload once
      // so the page shows the matching screen (e.g. the Start-render form, or
      // nothing left to poll) instead of a stale "running" shell.
      location.reload();
    }
  }

  function poll() {
    fetch("/api/episodes/" + window.PODCAST_FOUNDRY_EPISODE_ID + "/render/status")
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { setTimeout(poll, POLL_MS); });
  }

  poll();
})();
