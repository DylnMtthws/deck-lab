(function () {
  "use strict";

  // Same backoff as deck-lab-generate.js delayForAttempt (T10).
  var TIMEOUT_MS = 5 * 60 * 1000;
  var booted = false;

  function delayForAttempt(attempt) {
    if (attempt <= 0) return 1000;
    if (attempt === 1) return 2000;
    return 4000;
  }

  function csrfToken() {
    var meta = document.querySelector("meta[name='csrf-token']");
    return meta ? (meta.content || meta.getAttribute("content") || "") : "";
  }

  function readState() {
    var node = document.getElementById("deck-document-data");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "{}");
    } catch (error) {
      return null;
    }
  }

  function isShared() {
    var root = document.querySelector(".dl-builder");
    return !!(root && root.dataset && root.dataset.shared === "true");
  }

  function textOf(view, key) {
    if (!view || !view[key]) return "";
    return String(view[key]);
  }

  function boot() {
    if (booted) return;
    var api = window.DeckLabBuilder;
    if (!api || typeof api.railSection !== "function") return;
    var section = api.railSection("simulation", "Goldfish simulation", { tab: "deck" });
    if (!section) return;
    booted = true;

    var state = readState() || {};
    var deckId = state.id ? String(state.id) : "";
    var shared = isShared();
    var latestUrl = "/api/decks/" + encodeURIComponent(deckId) + "/simulations/latest";
    var timer = 0;
    var waited = 0;
    var stopped = true;

    var body = document.createElement("div");
    body.className = "dl-sim";

    var actions = document.createElement("div");
    actions.className = "dl-sim-actions";
    var runButton = null;
    var rerunButton = null;
    if (!shared) {
      runButton = document.createElement("button");
      runButton.type = "button";
      runButton.className = "dl-button is-primary is-sm";
      runButton.textContent = "Run simulation";
      runButton.setAttribute("aria-label", "Run simulation");
      runButton.setAttribute("data-simulate-run", "");
      runButton.addEventListener("click", start);
      actions.appendChild(runButton);

      rerunButton = document.createElement("button");
      rerunButton.type = "button";
      rerunButton.className = "dl-button is-sm";
      rerunButton.textContent = "Re-run";
      rerunButton.setAttribute("aria-label", "Re-run");
      rerunButton.setAttribute("data-simulate-rerun", "");
      rerunButton.hidden = true;
      rerunButton.addEventListener("click", start);
      actions.appendChild(rerunButton);
    }

    var statusEl = document.createElement("p");
    statusEl.className = "dl-sim-status";
    statusEl.setAttribute("role", "status");
    statusEl.setAttribute("data-simulate-status", "");

    var resultEl = document.createElement("p");
    resultEl.className = "dl-sim-result";
    resultEl.setAttribute("data-simulate-result", "");

    var deltaEl = document.createElement("p");
    deltaEl.className = "dl-sim-delta";
    deltaEl.setAttribute("data-simulate-delta", "");

    var unseenEl = document.createElement("p");
    unseenEl.className = "dl-sim-unseen";
    unseenEl.setAttribute("data-simulate-unseen", "");

    var baselineEl = document.createElement("p");
    baselineEl.className = "dl-sim-note";
    baselineEl.setAttribute("data-simulate-baseline", "");

    var staleEl = document.createElement("p");
    staleEl.className = "dl-sim-note";
    staleEl.setAttribute("data-simulate-stale", "");

    body.appendChild(actions);
    body.appendChild(statusEl);
    body.appendChild(resultEl);
    body.appendChild(deltaEl);
    body.appendChild(unseenEl);
    body.appendChild(baselineEl);
    body.appendChild(staleEl);
    section.appendChild(body);

    function setText(el, value) {
      el.textContent = value || "";
      el.hidden = !value;
    }

    function render(view) {
      view = view || {};
      var status = String(view.status || "");
      var running = status === "queued" || status === "running";
      if (rerunButton) rerunButton.hidden = shared || !view.stale;
      if (running) {
        setText(statusEl, "Running");
        setText(resultEl, "");
        setText(deltaEl, "");
        setText(unseenEl, "");
        setText(baselineEl, "");
        setText(staleEl, "");
        return;
      }
      var reason = textOf(view, "reason");
      setText(statusEl, reason || (status === "done" ? "Done" : ""));
      var result = view.result || null;
      setText(resultEl, result && result.text ? String(result.text) : "");
      var delta = view.delta || null;
      setText(deltaEl, delta && delta.text ? String(delta.text) : "");
      var unseen = delta && delta.unseen_card_count ? delta.unseen_card_count.text : "";
      setText(unseenEl, unseen ? String(unseen) : "");
      setText(baselineEl, textOf(view, "baseline_message"));
      setText(staleEl, textOf(view, "stale_message"));
    }

    function stopPoll() {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      timer = 0;
    }

    function poll(url, attempt) {
      if (stopped) return;
      if (waited >= TIMEOUT_MS) {
        render({
          status: "not_simulated",
          reason: "Not simulated: the simulation timed out"
        });
        return;
      }
      var wait = delayForAttempt(attempt);
      timer = window.setTimeout(function () {
        waited += wait;
        if (stopped) return;
        window.fetch(url, { headers: { "Accept": "application/json" } })
          .then(function (response) {
            if (!response.ok) throw new Error("status");
            return response.json();
          })
          .then(function (body) {
            if (stopped) return;
            render(body);
            if (body && (body.status === "queued" || body.status === "running")) {
              poll(url, attempt + 1);
            }
          })
          .catch(function () {
            if (stopped) return;
            render({
              status: "not_simulated",
              reason: "Not simulated: the status request failed"
            });
          });
      }, wait);
    }

    function start() {
      stopPoll();
      stopped = false;
      waited = 0;
      render({ status: "running" });
      window.fetch("/api/decks/" + encodeURIComponent(deckId) + "/simulate", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken()
        },
        body: "{}"
      }).then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, body: payload || {} };
        });
      }).then(function (result) {
        if (stopped) return;
        if (!result.ok || !result.body.status_url) {
          render({
            status: "not_simulated",
            reason: "Not simulated: the simulation could not be started"
          });
          return;
        }
        render({ status: result.body.status || "queued" });
        poll(result.body.status_url, 0);
      }).catch(function () {
        if (stopped) return;
        render({
          status: "not_simulated",
          reason: "Not simulated: the simulation could not be started"
        });
      });
    }

    render(state.simulation_view || {});
    var initial = state.simulation_view || {};
    if (initial.status === "queued" || initial.status === "running") {
      stopped = false;
      waited = 0;
      poll(latestUrl, 0);
    }
  }

  if (window.DeckLabBuilder) boot();
  document.addEventListener("deck-lab:ready", boot);
})();
