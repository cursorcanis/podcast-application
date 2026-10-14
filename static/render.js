(function () {
  "use strict";

  var POLL_MS = 5000;
  var dash = document.getElementById("render-dash");
  if (!dash) return;
  var episodeId = dash.getAttribute("data-episode-id");
  var reloadOnFinish = dash.getAttribute("data-reload-on-finish") === "true";
  var chunksById = {};

  function el(id) { return document.getElementById(id); }
  function setText(id, value) { var e = el(id); if (e) e.textContent = value; }

  function fmtSeconds(s) {
    if (s === null || s === undefined) return "—";
    s = Math.round(s);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m " + sec + "s";
    return sec + "s";
  }

  function fmtClock(epochSeconds) {
    var d = new Date(epochSeconds * 1000);
    var sameDay = d.toDateString() === new Date().toDateString();
    var t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return sameDay ? t : d.toLocaleDateString([], { weekday: "short" }) + " " + t;
  }

  function bucket(status) {
    if (status === "succeeded") return "succeeded";
    if (status === "submitted" || status === "rendering") return "in_flight";
    if (status === "failed" || status === "timed_out") return "failed";
    return "pending";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function chunkSummary(c) {
    var parts = ["Chunk " + (c.chunk_index + 1), c.speaker, c.status];
    if (c.measured_render_seconds) parts.push("took " + fmtSeconds(c.measured_render_seconds));
    if (c.attempt_count > 1) parts.push(c.attempt_count + " attempts");
    var text = (c.text || "").trim();
    if (text.length > 140) text = text.slice(0, 137) + "…";
    return parts.join(" · ") + (text ? " — “" + text + "”" : "") +
      (c.error_detail ? " — error: " + c.error_detail : "");
  }

  function renderMap(chunks) {
    var map = el("dash-map");
    if (!map) return;
    // Rebuild only when the chunk set changes (chunks can be split mid-render);
    // otherwise just update classes so hover state isn't lost every poll.
    if (map.children.length !== chunks.length) {
      map.innerHTML = "";
      chunks.forEach(function () {
        var b = document.createElement("span");
        b.className = "cell";
        b.tabIndex = 0;
        map.appendChild(b);
      });
    }
    chunksById = {};
    chunks.forEach(function (c, i) {
      var b = map.children[i];
      chunksById[i] = c;
      b.className = "cell " + bucket(c.status) + (c.attempt_count > 1 ? " retried" : "");
      b.setAttribute("data-i", i);
      b.setAttribute("aria-label", chunkSummary(c));
    });
  }

  function showTip(e) {
    var i = e.target.getAttribute && e.target.getAttribute("data-i");
    if (i === null || i === undefined) return;
    var c = chunksById[i];
    if (c) setText("dash-tip", chunkSummary(c));
  }
  var mapEl = el("dash-map");
  if (mapEl) {
    mapEl.addEventListener("mouseover", showTip);
    mapEl.addEventListener("focusin", showTip);
  }

  function renderTable(chunks) {
    var tbody = el("chunk-rows");
    if (!tbody) return;
    tbody.innerHTML = "";
    chunks.forEach(function (c) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + (c.chunk_index + 1) + "</td>" +
        "<td>" + escapeHtml(c.speaker) + "</td>" +
        "<td><i class=\"sw " + bucket(c.status) + "\"></i> " + escapeHtml(c.status) + "</td>" +
        "<td>" + c.attempt_count + "</td>" +
        "<td>" + (c.measured_render_seconds ? fmtSeconds(c.measured_render_seconds) : "—") + "</td>" +
        "<td>" + escapeHtml(c.error_detail || "") + "</td>";
      tbody.appendChild(tr);
    });
  }

  function render(body) {
    if (!body.job) {
      setText("job-status", "no render yet");
      return;
    }
    var job = body.job;
    var m = body.metrics || {};
    var counts = body.counts;
    var running = job.status === "running";

    if (body.episode && body.episode.title) setText("dash-episode-title", body.episode.title);

    var pill = el("job-status");
    if (pill) {
      pill.textContent = job.status;
      pill.className = "status-pill " + (
        running ? "running" : job.status === "succeeded" ? "ok" :
        job.status === "failed" ? "fail" : "warn");
    }

    var pct = m.percent || 0;
    setText("dash-percent", pct >= 10 || pct === 0 ? Math.round(pct) : pct.toFixed(1));
    var fill = el("dash-fill");
    if (fill) {
      fill.style.width = pct + "%";
      fill.className = "progress-fill" + (running ? " live" : "");
    }
    var bar = el("dash-progressbar");
    if (bar) bar.setAttribute("aria-valuenow", Math.round(pct));

    setText("count-total", counts.total);
    setText("count-succeeded", counts.succeeded);
    setText("count-succeeded-2", counts.succeeded);
    setText("count-in-flight", counts.in_flight);
    setText("count-pending", counts.pending);
    setText("count-failed", counts.failed);

    var stalled = running && m.current && m.current.stalled;
    var stallBox = el("dash-stalled");
    if (stallBox) stallBox.hidden = !stalled;
    if (stalled) setText("dash-stalled-time", fmtSeconds(m.current.running_seconds));

    if (stalled) {
      setText("dash-eta-text", "stalled — see the warning below");
    } else if (running && body.remaining_seconds < 1) {
      setText("dash-eta-text", "running past the estimate — finishing soon");
    } else if (running && m.eta) {
      setText("dash-eta-text", "expected to finish around " + fmtClock(m.eta));
    } else if (job.status === "succeeded") {
      setText("dash-eta-text", "render complete");
    } else {
      setText("dash-eta-text", job.status === "running" ? "estimating finish time…" : "render " + job.status);
    }

    setText("elapsed", fmtSeconds(body.elapsed_seconds));
    setText("remaining", running && !stalled && body.remaining_seconds >= 1 ? fmtSeconds(body.remaining_seconds) : "—");
    setText("projection-basis",
      body.projection_basis === "measured" ? "based on this render's speed" : "first estimate — no chunk done yet");
    setText("dash-audio", fmtSeconds(m.est_audio_done_seconds || 0) + " / " + fmtSeconds(m.est_audio_total_seconds || 0));
    setText("dash-words", (m.words_done || 0).toLocaleString() + " / " + (m.words_total || 0).toLocaleString());
    setText("dash-avg", m.avg_chunk_seconds ? fmtSeconds(m.avg_chunk_seconds) : "—");
    setText("dash-range", m.fastest_chunk_seconds
      ? "fastest " + fmtSeconds(m.fastest_chunk_seconds) + " · slowest " + fmtSeconds(m.slowest_chunk_seconds)
      : " ");
    setText("dash-retries", (m.retries || 0) + " / " + counts.failed);

    var cur = m.current;
    var curBox = el("dash-current");
    if (curBox) {
      curBox.hidden = !(running && cur);
      if (running && cur) {
        setText("dash-current-index", (cur.chunk_index + 1) + " of " + counts.total);
        setText("dash-current-speaker", cur.speaker);
        setText("dash-current-time",
          (cur.running_seconds !== null ? fmtSeconds(cur.running_seconds) + " so far" : cur.status) +
          (cur.attempt_count > 1 ? " · attempt " + cur.attempt_count : ""));
        setText("dash-current-text", "“" + cur.text_excerpt + "”");
      }
    }

    renderMap(body.chunks || []);
    renderTable(body.chunks || []);

    if (running) {
      setTimeout(poll, POLL_MS);
    } else if (reloadOnFinish) {
      // The watched render just ended — reload once so the page shows what
      // comes next (this episode's own render starting, or the finished job).
      location.reload();
    }
  }

  function poll() {
    fetch("/api/episodes/" + episodeId + "/render/status")
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { setTimeout(poll, POLL_MS); });
  }

  poll();
})();
