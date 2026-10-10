(function () {
  "use strict";

  // This remains the builder's native solo simulation transport. The dialog is
  // only a focused presentation for the existing /simulate endpoint.
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
    try { return JSON.parse(node.textContent || "{}"); } catch (_) { return null; }
  }

  function isShared() {
    var root = document.querySelector(".dl-builder");
    return !!(root && root.dataset && root.dataset.shared === "true");
  }

  function boot() {
    if (booted) return;
    var api = window.DeckLabBuilder;
    var dialog = document.getElementById("goldfish-dialog");
    var slot = document.querySelector("[data-goldfish-sim-slot]");
    var opener = document.querySelector("[data-goldfish-open]");
    if (!api || !dialog || !slot || !opener) return;
    booted = true;

    var state = readState() || {};
    var deckId = state.id ? String(state.id) : "";
    var shared = isShared() || api.shared === true;
    var latestUrl = "/api/decks/" + encodeURIComponent(deckId) + "/simulations/latest";
    var timer = 0;
    var waited = 0;
    var stopped = true;
    var activeView = state.simulation_view || {};
    var latestRevision = String(state.revision === undefined ? "" : state.revision);
    var viewRefreshSequence = 0;

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
      runButton.textContent = "Run assembly check";
      runButton.setAttribute("aria-label", "Run assembly check");
      runButton.setAttribute("data-simulate-run", "");
      runButton.addEventListener("click", start);
      actions.appendChild(runButton);

      rerunButton = document.createElement("button");
      rerunButton.type = "button";
      rerunButton.className = "dl-button is-sm";
      rerunButton.textContent = "Re-run";
      rerunButton.setAttribute("aria-label", "Re-run assembly check");
      rerunButton.setAttribute("data-simulate-rerun", "");
      rerunButton.hidden = true;
      rerunButton.addEventListener("click", start);
      actions.appendChild(rerunButton);
    }

    var statusEl = document.createElement("p");
    statusEl.className = "dl-sim-status";
    statusEl.setAttribute("data-simulate-status", "");
    statusEl.setAttribute("role", "status");
    statusEl.setAttribute("aria-live", "polite");
    var resultEl = document.createElement("p");
    resultEl.className = "dl-sim-result";
    resultEl.setAttribute("data-simulate-result", "");
    var runMetaEl = document.createElement("p");
    runMetaEl.className = "dl-sim-note";
    var coverageEl = document.createElement("p");
    coverageEl.className = "dl-sim-coverage";
    var scopeDetails = document.createElement("details");
    scopeDetails.className = "dl-sim-scope";
    var scopeSummary = document.createElement("summary");
    scopeSummary.textContent = "What this result covers";
    scopeDetails.appendChild(scopeSummary);
    var measuresEl = document.createElement("p");
    var notMeasuresEl = document.createElement("p");
    scopeDetails.appendChild(measuresEl);
    scopeDetails.appendChild(notMeasuresEl);
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
    body.appendChild(runMetaEl);
    body.appendChild(coverageEl);
    body.appendChild(scopeDetails);
    body.appendChild(deltaEl);
    body.appendChild(unseenEl);
    body.appendChild(baselineEl);
    body.appendChild(staleEl);
    slot.appendChild(body);

    function setText(el, value) {
      el.textContent = value || "";
      el.hidden = !value;
    }

    function readiness() {
      var current = api.getState ? api.getState() : state;
      var validation = current && current.validation || {};
      var total = Number(validation.total_count);
      var saveSnapshot = api.getSaveState ? api.getSaveState() : null;
      var saveControl = document.getElementById("save-state");
      var saveLabel = saveControl && saveControl.querySelector("[data-save-label]");
      var saveText = saveLabel ? String(saveLabel.textContent || "").toLowerCase() : "";
      var saving = !!(saveSnapshot && saveSnapshot.pending) || /saving|uploading/.test(saveText);
      var failed = !!(saveSnapshot && saveSnapshot.failed) || !!(saveControl && saveControl.classList.contains("is-warn")) || /retry save|could not save/.test(saveText);
      if (shared) return { ready: false, message: "This shared deck is read-only. Copy it into your library to run the simulator." };
      if (saving) return { ready: false, message: "Saving deck changes… wait for the save to finish before running." };
      if (failed) return { ready: false, message: "Deck changes are not saved. Retry the save before running the simulator." };
      if (validation.legal !== true || total !== 100) return { ready: false, message: "A legal 100-card deck is required before the simulator can run." };
      return { ready: true, message: "Ready to run on the current saved deck." };
    }

    function syncReadiness() {
      var check = readiness();
      dialog.querySelector("[data-goldfish-readiness]").textContent = check.message;
      opener.disabled = false;
      opener.setAttribute("aria-disabled", "false");
      opener.title = shared ? "Open simulator information for this read-only deck" : "Open the solo assembly check";
      if (runButton) runButton.disabled = !check.ready || activeView.status === "queued" || activeView.status === "running";
      if (rerunButton) rerunButton.disabled = !check.ready || activeView.status === "queued" || activeView.status === "running";
      if (runButton) runButton.title = check.ready ? "Run the solo assembly check" : check.message;
    }

    function render(view) {
      view = view || {};
      activeView = view;
      var status = String(view.status || "");
      var running = status === "queued" || status === "running";
      if (rerunButton) rerunButton.hidden = shared || !view.stale;
      syncReadiness();
      if (running) {
        setText(statusEl, status === "queued" ? "Simulation queued…" : "Running the solo assembly check…");
        setText(resultEl, "");
        setText(runMetaEl, "");
        runMetaEl.hidden = true;
        setText(coverageEl, "");
        coverageEl.hidden = true;
        setText(measuresEl, "");
        setText(notMeasuresEl, "");
        scopeDetails.hidden = true;
        setText(deltaEl, "");
        setText(unseenEl, "");
        setText(baselineEl, "");
        setText(staleEl, "");
        return;
      }
      var reason = view.reason ? String(view.reason) : "";
      setText(statusEl, reason || (status === "done" ? "Assembly check complete." : ""));
      var result = view.result || null;
      setText(resultEl, result && result.text ? String(result.text) : "");
      var runMeta = [];
      if (result && result.games !== undefined) runMeta.push(String(result.games) + " simulated games");
      var turn = result && (result.objective_turn !== undefined ? result.objective_turn : result.turn);
      if (turn !== undefined && turn !== null) runMeta.push("Objective turn " + String(turn));
      setText(runMetaEl, runMeta.join(" · "));
      if (result) {
        var coverageParts = [];
        var modeled = result.modeled_cards == null ? NaN : Number(result.modeled_cards);
        var inactive = result.inert_card_count == null ? NaN : Number(result.inert_card_count);
        var unauthored = result.unauthored_card_count == null ? NaN : Number(result.unauthored_card_count);
        if (Number.isFinite(modeled)) coverageParts.push(modeled + " modeled");
        if (Number.isFinite(inactive)) coverageParts.push(inactive + " inactive in solo play");
        if (Number.isFinite(unauthored)) coverageParts.push(unauthored + " without simulator-authored rules");
        if (!coverageParts.length && result.unseen_card_count != null && Number.isFinite(Number(result.unseen_card_count))) coverageParts.push("Unavailable cards: " + Number(result.unseen_card_count));
        var coverageText = coverageParts.length ? coverageParts.join(" · ") + ". " : "Card coverage was not reported. ";
        setText(coverageEl, coverageText + "Results apply only to modeled cards; they do not verify a deck-wide win.");
        runMetaEl.hidden = !runMeta.length;
        coverageEl.hidden = false;
        var objectives = Array.isArray(result.assembly_objectives) && result.assembly_objectives.length ? " Supported objectives: " + result.assembly_objectives.map(function (name) { return String(name).replace(/_/g, " "); }).join("; ") + "." : "";
        setText(measuresEl, result.measures ? "Measures: " + String(result.measures) + objectives : objectives);
        setText(notMeasuresEl, result.does_not_measure ? "Does not measure: " + String(result.does_not_measure) : "");
        scopeDetails.hidden = !result.measures && !result.does_not_measure;
      } else {
        setText(coverageEl, "");
        runMetaEl.hidden = true;
        coverageEl.hidden = true;
        setText(measuresEl, "");
        setText(notMeasuresEl, "");
        scopeDetails.hidden = true;
      }
      var delta = view.delta || null;
      setText(deltaEl, delta && delta.text ? String(delta.text) + (delta.note ? " " + String(delta.note) : "") : "");
      var unseen = delta && delta.unseen_card_count ? delta.unseen_card_count.text : "";
      setText(unseenEl, unseen ? String(unseen) : "");
      setText(baselineEl, view.baseline_message ? String(view.baseline_message) : "");
      setText(staleEl, view.stale_message ? String(view.stale_message) : "");
    }

    function stopPoll() {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      timer = 0;
    }

    function poll(url, attempt) {
      if (stopped) return;
      if (waited >= TIMEOUT_MS) {
        render({ status: "not_simulated", reason: "The assembly check timed out. Try again when ready." });
        return;
      }
      var wait = delayForAttempt(attempt);
      timer = window.setTimeout(function () {
        waited += wait;
        if (stopped) return;
        window.fetch(url, { headers: { "Accept": "application/json" } })
          .then(function (response) { if (!response.ok) throw new Error("status"); return response.json(); })
          .then(function (result) {
            if (stopped) return;
            render(result);
            if (result && (result.status === "queued" || result.status === "running")) poll(url, attempt + 1);
          })
          .catch(function () {
            if (!stopped) render({ status: "not_simulated", reason: "Could not check the simulation status. Try again." });
          });
      }, wait);
    }

    function start() {
      var check = readiness();
      syncReadiness();
      if (!check.ready || !deckId) return;
      var current = api.getState ? api.getState() : state;
      var revision = Number(current.revision);
      var expectedRevision = Number.isFinite(revision) ? revision : null;
      viewRefreshSequence += 1;
      stopPoll();
      stopped = false;
      waited = 0;
      render({ status: "running" });
      window.fetch("/api/decks/" + encodeURIComponent(deckId) + "/simulate", {
        method: "POST",
        headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ expected_revision: expectedRevision })
      }).then(function (response) {
        return response.json().then(function (payload) { return { ok: response.ok, body: payload || {} }; });
      }).then(function (response) {
        if (stopped) return;
        if (!response.ok || !response.body.status_url) {
          var detail = response.body.detail || response.body.message || response.body.error;
          render({ status: "not_simulated", reason: detail ? String(detail) : "The assembly check could not be started. Try again." });
          return;
        }
        render({ status: response.body.status || "queued" });
        poll(response.body.status_url, 0);
      }).catch(function () {
        if (!stopped) render({ status: "not_simulated", reason: "The assembly check could not be started. Try again." });
      });
    }

    opener.addEventListener("click", function () {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "open");
      syncReadiness();
    });
    dialog.querySelector("[data-goldfish-close]").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    document.addEventListener("deck-lab:render", function (event) {
      if (event.detail && event.detail.state) {
        state = event.detail.state;
        var revision = String(state.revision === undefined ? "" : state.revision);
        if (!shared && revision && revision !== latestRevision) {
          latestRevision = revision;
          if (activeView.status === "done" && !activeView.stale) {
            activeView = Object.assign({}, activeView, {
              stale: true,
              stale_message: "This result may be out of date while the latest run status loads.",
            });
            render(activeView);
          }
          var sequence = ++viewRefreshSequence;
          window.fetch(latestUrl, { headers: { "Accept": "application/json" } }).then(function (response) {
            if (!response.ok) throw new Error("status");
            return response.json();
          }).then(function (view) {
            if (sequence !== viewRefreshSequence || activeView.status === "queued" || activeView.status === "running") return;
            render(view);
          }).catch(function () {
            if (sequence === viewRefreshSequence) latestRevision = "";
          });
        } else if (Object.prototype.hasOwnProperty.call(state, "simulation_view")) {
          render(state.simulation_view || {});
        } else syncReadiness();
      }
      syncReadiness();
    });
    document.addEventListener("deck-lab:save-state", syncReadiness);
    var saveState = document.getElementById("save-state");
    if (saveState && typeof MutationObserver !== "undefined") {
      new MutationObserver(syncReadiness).observe(saveState, { childList: true, subtree: true, characterData: true, attributes: true });
    }
    window.addEventListener("pagehide", stopPoll);

    if (!shared) render(state.simulation_view || {});
    else render(state.simulation_view || {});
    if (activeView.status === "queued" || activeView.status === "running") {
      stopped = false;
      waited = 0;
      poll(latestUrl, 0);
    }

    if (window.location.hash === "#goldfish") {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "open");
      syncReadiness();
    }
  }

  if (window.DeckLabBuilder) boot();
  document.addEventListener("deck-lab:ready", boot);
})();
