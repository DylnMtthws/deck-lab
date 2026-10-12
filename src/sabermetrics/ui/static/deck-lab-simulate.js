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
      runButton.textContent = "Run Goldfish";
      runButton.setAttribute("aria-label", "Run Goldfish simulator");
      runButton.setAttribute("data-simulate-run", "");
      runButton.addEventListener("click", start);
      actions.appendChild(runButton);

      rerunButton = document.createElement("button");
      rerunButton.type = "button";
      rerunButton.className = "dl-button is-sm";
      rerunButton.textContent = "Run Goldfish again";
      rerunButton.setAttribute("aria-label", "Run Goldfish simulator again");
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
    var diagnosticsEl = document.createElement("section");
    diagnosticsEl.className = "dl-sim-diagnostics";
    diagnosticsEl.setAttribute("data-simulate-diagnostics", "");
    var missingDiagnosticsEl = document.createElement("p");
    missingDiagnosticsEl.className = "dl-sim-diagnostic-note";
    missingDiagnosticsEl.setAttribute("data-simulate-diagnostics-missing", "");
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
    body.appendChild(diagnosticsEl);
    body.appendChild(missingDiagnosticsEl);
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

    var diagnosticTests = [
      ["opening", "Opening hand"],
      ["development", "Development through the early turns"],
      ["commander_loss", "Commander loss"],
      ["creature_wipe", "Creature wipe recovery"],
      ["finish", "Finish"],
    ];
    var metricLabels = {
      qualified_opening_rate: "Hands passing the modeled keep rule",
      mean_kept_cards: "Average cards kept",
      mean_unused_available_mana: "Unused modeled mana capacity at turn 5",
      mean_cards_remaining: "Cards remaining at turn 5",
      authored_forward_action_rate: "Played an action with modeled effects",
      mean_first_authored_action_turn: "First modeled action turn",
      engine_online_rate: "Deck engine online",
      disruption_exercised_rate: "Runs where removal occurred",
      followup_turn_reached_rate: "Follow-up turn observed after removal",
      recovery_authored_action_rate: "Played a modeled action on recovery turn",
      recovery_board_rate: "Added a modeled nonland permanent",
      recovery_draw_rate: "Drew cards from a modeled effect",
      independent_engine_recovery_rate: "Independent engine restored",
      commander_dependent_hand_cards: "Cards requiring the commander",
      verified_finish_rate: "Finish proven among evaluated runs (conditional)",
      positive_finish_proof_rate: "Proven finish lower bound",
      finish_evaluated_rate: "Runs with verified finish evaluation",
      kept_hands: "Kept hands",
      mulligans: "Mulligans",
      keep_rate: "Kept opening hands",
      mean_mulligans: "Average mulligans",
      mean_kept_hand_size: "Average cards kept",
      productive_action_by_turn_3: "Productive action by turn 3",
      mean_missed_land_drops: "Average missed land drops",
      mean_hand_size_turn_5: "Cards in hand at turn 5",
      mean_unused_floating_mana_turn_5: "Unused mana at turn 5",
      engine_active_by_turn: "Engine active by turn",
      supported_damage_engine_by_turn: "Damage engine active by turn",
      commander_recovered: "Recovered after commander loss",
      recovery_by_turn: "Recovery by turn",
      useful_recovery_action: "Useful recovery action",
      win_conversion: "Board can convert to a win",
      lethal_combat: "Lethal combat available",
      verified_combo: "Verified combo available",
    };

    function humanLabel(value) {
      var key = String(value || "");
      if (metricLabels[key]) return metricLabels[key];
      return key.replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
    }

    function numberText(value) {
      if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
      return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
    }

    function formatDiagnosticMetric(metric) {
      if (metric === null || metric === undefined) return "Not measured";
      if (typeof metric !== "object" || Array.isArray(metric)) return numberText(metric);
      if (!Object.prototype.hasOwnProperty.call(metric, "value") || metric.value === null || metric.value === undefined) {
        var observed = metric.numerator !== undefined || metric.denominator !== undefined
          ? " · " + (metric.numerator == null ? "?" : numberText(metric.numerator)) + "/" + (metric.denominator == null ? "?" : numberText(metric.denominator)) + " observed"
          : "";
        return "Not measured" + observed;
      }
      var value = metric.value;
      var unit = String(metric.unit || "");
      var shown = numberText(value);
      if (typeof value === "number" && (unit === "probability" || unit === "fraction" || unit === "proportion")) shown = numberText(value * 100) + "%";
      else if (typeof value === "number" && unit === "percent") shown = numberText(value) + "%";
      else if (unit) shown += " " + unit;
      if (metric.numerator !== undefined || metric.denominator !== undefined) {
        shown += " · " + (metric.numerator == null ? "?" : numberText(metric.numerator)) + "/" + (metric.denominator == null ? "?" : numberText(metric.denominator));
      }
      var ci = metric.ci95;
      var low = Array.isArray(ci) ? ci[0] : ci && ci.low;
      var high = Array.isArray(ci) ? ci[1] : ci && ci.high;
      if (typeof low === "number" && typeof high === "number") {
        if (unit === "probability" || unit === "fraction" || unit === "proportion") {
          shown += " · 95% interval " + numberText(low * 100) + "–" + numberText(high * 100) + "%";
        } else shown += " · 95% interval " + numberText(low) + "–" + numberText(high) + (unit ? " " + unit : "");
      }
      return shown;
    }

    function renderHeldout(validation) {
      if (!validation || typeof validation !== "object") return;
      diagnosticsEl.hidden = false;
      var section = document.createElement("section");
      section.setAttribute("data-simulate-heldout", "");
      var heading = document.createElement("h3");
      heading.textContent = "Independent held-out comparison";
      section.appendChild(heading);
      function paragraph(text) {
        var line = document.createElement("p");
        line.textContent = text;
        section.appendChild(line);
      }
      paragraph("Status: " + String(validation.status || "unavailable") +
        (validation.training_seed !== undefined ? " · training seed " + validation.training_seed : "") +
        (validation.validation_seed !== undefined ? " · validation seed " + validation.validation_seed : ""));
      if (validation.note) paragraph(String(validation.note));
      if (validation.reason) paragraph(String(validation.reason));
      var comparison = validation.comparison || {};
      if (comparison.reason) paragraph("Comparison suppressed: " + String(comparison.reason));
      var before = validation.baseline_report || {};
      var after = validation.candidate_report || {};
      var keys = Array.from(new Set(Object.keys(before.tests || {}).concat(Object.keys(after.tests || {}))));
      keys.forEach(function (testKey) {
        var oldTest = (before.tests || {})[testKey] || {};
        var newTest = (after.tests || {})[testKey] || {};
        var metrics = Array.from(new Set(Object.keys(oldTest.metrics || {}).concat(Object.keys(newTest.metrics || {}))));
        metrics.forEach(function (metricKey) {
          var change = ((comparison.metric_deltas || {})[testKey] || {})[metricKey] || {};
          paragraph(humanLabel(testKey) + " · " + humanLabel(metricKey) +
            " · baseline: " + formatDiagnosticMetric((oldTest.metrics || {})[metricKey]) +
            " · candidate: " + formatDiagnosticMetric((newTest.metrics || {})[metricKey]) +
            " · " + (change.comparable === true ? "descriptive change " + numberText(change.delta) + " " + String(change.unit || "") : "Not compared: " + String(change.reason || comparison.reason || "Metric is unmeasured or settings differ.")) +
            " · status " + String(oldTest.status || "unknown") + " → " + String(newTest.status || "unknown") +
            (change.unvalidated_reason ? " · Not validated: " + String(change.unvalidated_reason) : ""));
        });
      });
      diagnosticsEl.appendChild(section);
    }

    function renderDiagnostics(diagnostics) {
      diagnosticsEl.textContent = "";
      if (!diagnostics || typeof diagnostics !== "object" || !diagnostics.tests || typeof diagnostics.tests !== "object") {
        diagnosticsEl.hidden = true;
        return false;
      }
      diagnosticsEl.hidden = false;
      var heading = document.createElement("h3");
      heading.textContent = "Five-test diagnostics";
      diagnosticsEl.appendChild(heading);
      var settings = diagnostics.settings;
      if (settings && typeof settings === "object") {
        var settingsLine = document.createElement("p");
        settingsLine.className = "dl-sim-diagnostic-settings";
        var parts = [];
        if (settings.games != null) parts.push(numberText(settings.games) + " games");
        if (settings.seed != null) parts.push("seed " + String(settings.seed));
        if (settings.checkpoints && typeof settings.checkpoints === "object") parts.push("turns " + Object.keys(settings.checkpoints).map(function (key) { return humanLabel(key) + " " + settings.checkpoints[key]; }).join(", "));
        if (settings.engine_version) parts.push("engine " + String(settings.engine_version));
        if (settings.settings_fingerprint) parts.push("settings " + String(settings.settings_fingerprint));
        settingsLine.textContent = parts.length ? parts.join(" · ") : "Run settings not reported.";
        diagnosticsEl.appendChild(settingsLine);
      }
      var coverage = diagnostics.coverage;
      if (coverage && typeof coverage === "object") {
        var coverageLine = document.createElement("p");
        coverageLine.className = "dl-sim-diagnostic-coverage";
        var coverageParts = [];
        if (coverage.modeled_cards != null) coverageParts.push(numberText(coverage.modeled_cards) + " modeled");
        if (coverage.inert_cards != null) coverageParts.push(numberText(coverage.inert_cards) + " inactive in solo play");
        if (coverage.unauthored_cards != null) coverageParts.push(numberText(coverage.unauthored_cards) + " without simulator-authored rules");
        coverageLine.textContent = (coverageParts.length ? coverageParts.join(" · ") : "Coverage counts not reported") + ". These results describe only the reported simulation scope, not a multiplayer win-rate forecast.";
        diagnosticsEl.appendChild(coverageLine);
      }
      diagnosticTests.forEach(function (entry) {
        var key = entry[0];
        var report = diagnostics.tests[key];
        var card = document.createElement("article");
        card.className = "dl-sim-diagnostic-card";
        card.setAttribute("data-diagnostic-test", key);
        var title = document.createElement("h4");
        title.textContent = entry[1];
        card.appendChild(title);
        var status = report && ["supported", "partial", "unsupported"].indexOf(report.status) >= 0 ? report.status : "unsupported";
        var statusLine = document.createElement("p");
        statusLine.className = "dl-sim-diagnostic-status is-" + status;
        statusLine.textContent = status === "supported" ? "Measured" : status === "partial" ? "Partially measured" : "Unsupported / not measured";
        card.appendChild(statusLine);
        var metrics = report && report.metrics && typeof report.metrics === "object" ? report.metrics : {};
        var metricKeys = Object.keys(metrics);
        if (key === "finish") {
          metricKeys = metricKeys.filter(function (name) { return name !== "verified_finish_rate" || !metrics.positive_finish_proof_rate; });
          metricKeys.sort(function (left, right) { return left === "positive_finish_proof_rate" ? -1 : right === "positive_finish_proof_rate" ? 1 : 0; });
          var unknownFinish = document.createElement("p");
          unknownFinish.textContent = "A positive finish proof is a lower bound across all trials. The remaining trials are unverified; absence of proof is not a verified failure or a win-rate estimate.";
          card.appendChild(unknownFinish);
        }
        if (!metricKeys.length) {
          var missing = document.createElement("p");
          missing.className = "dl-sim-diagnostic-metric is-unknown";
          missing.textContent = "No metrics reported; unknown is not zero.";
          card.appendChild(missing);
        }
        metricKeys.forEach(function (metricKey) {
          var line = document.createElement("p");
          line.className = "dl-sim-diagnostic-metric";
          line.textContent = humanLabel(metricKey) + ": " + (metricKey === "positive_finish_proof_rate" && metrics[metricKey].value === 0
            ? "No proof observed; other outcomes unknown · " + formatDiagnosticMetric(metrics[metricKey])
            : formatDiagnosticMetric(metrics[metricKey])) + (status === "partial" ? " · authored subset only" : "");
          card.appendChild(line);
          var metric = metrics[metricKey];
          if (metric && typeof metric.value === "number" && Number.isFinite(metric.value) && metric.unit === "proportion" && metric.value >= 0 && metric.value <= 1 && status !== "unsupported") {
            var meter = document.createElement("meter");
            meter.className = "dl-sim-diagnostic-meter";
            meter.setAttribute("min", "0");
            meter.setAttribute("max", "1");
            meter.setAttribute("value", String(metric.value));
            meter.setAttribute("aria-label", humanLabel(metricKey));
            meter.textContent = numberText(metric.value * 100) + "%";
            card.appendChild(meter);
          }
        });
        var evidenceDetails = document.createElement("details");
        var evidenceSummary = document.createElement("summary");
        evidenceSummary.textContent = "Coverage, assumptions and evidence";
        evidenceDetails.appendChild(evidenceSummary);
        ["limitations", "evidence"].forEach(function (field) {
          var values = Array.isArray(report && report[field]) ? report[field] : [];
          values.forEach(function (value) {
            var note = document.createElement("p");
            note.className = "dl-sim-diagnostic-note";
            note.textContent = (field === "limitations" ? "Limitation: " : "Evidence: ") + String(value);
            evidenceDetails.appendChild(note);
          });
        });
        card.appendChild(evidenceDetails);
        diagnosticsEl.appendChild(card);
      });
      var comparison = diagnostics.comparison;
      if (comparison && typeof comparison === "object") {
        var comparisonLine = document.createElement("p");
        comparisonLine.className = "dl-sim-diagnostic-comparison";
        comparisonLine.textContent = comparison.comparable === true
          ? "Comparable run settings retained" + (comparison.settings_fingerprint ? " · " + String(comparison.settings_fingerprint) : "")
          : "No comparable diagnostics baseline" + (comparison.reason ? ": " + String(comparison.reason) : "");
        diagnosticsEl.appendChild(comparisonLine);
        if (comparison.note) {
          var comparisonNote = document.createElement("p");
          comparisonNote.textContent = String(comparison.note);
          diagnosticsEl.appendChild(comparisonNote);
        }
        (Array.isArray(comparison.history) ? comparison.history : []).forEach(function (prior) {
          var historySection = document.createElement("details");
          var historyTitle = document.createElement("summary");
          historyTitle.textContent = "Compare with revision " + String(prior.revision == null ? "unknown" : prior.revision) + (prior.comparable ? "" : " — settings differ or unmeasured");
          historySection.appendChild(historyTitle);
          if (prior.reason) {
            var reason = document.createElement("p");
            reason.textContent = String(prior.reason);
            historySection.appendChild(reason);
          }
          var deltas = prior.metric_deltas || {};
          Object.keys(deltas).forEach(function (testKey) {
            Object.keys(deltas[testKey] || {}).forEach(function (metricKey) {
              var change = deltas[testKey][metricKey];
              var line = document.createElement("p");
              line.textContent = humanLabel(testKey) + " · " + humanLabel(metricKey) + ": " + (change.comparable === true
                ? numberText(change.before) + " → " + numberText(change.after) + " " + String(change.unit || "") + " (change " + numberText(change.delta) + ")"
                : "Not compared" + (change.reason ? ": " + String(change.reason) : ""));
              historySection.appendChild(line);
            });
          });
          diagnosticsEl.appendChild(historySection);
        });
        if (comparison.history_truncated) {
          var truncated = document.createElement("p");
          truncated.textContent = "Showing the five most recent prior runs.";
          diagnosticsEl.appendChild(truncated);
        }
      }
      return true;
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
      opener.title = shared ? "Open Goldfish information for this read-only deck" : "Open the Goldfish simulator";
      if (runButton) runButton.disabled = !check.ready || activeView.status === "queued" || activeView.status === "running";
      if (rerunButton) rerunButton.disabled = !check.ready || activeView.status === "queued" || activeView.status === "running";
      if (runButton) runButton.title = check.ready ? "Run the Goldfish simulator" : check.message;
    }

    function render(view) {
      view = view || {};
      activeView = view;
      var status = String(view.status || "");
      var running = status === "queued" || status === "running";
      if (rerunButton) rerunButton.hidden = shared || !view.stale;
      syncReadiness();
      if (running) {
        setText(statusEl, status === "queued" ? "Simulation queued…" : "Running Goldfish…");
        setText(resultEl, "");
        diagnosticsEl.textContent = "";
        diagnosticsEl.hidden = true;
        setText(missingDiagnosticsEl, "");
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
      setText(statusEl, reason || (status === "done" ? "Goldfish run complete." : ""));
      var result = view.result || null;
      setText(resultEl, result && result.text ? String(result.text) : "");
      var hasDiagnostics = renderDiagnostics(result && result.diagnostics);
      renderHeldout(result && result.heldout_validation);
      setText(missingDiagnosticsEl, result && !hasDiagnostics && status === "done"
        ? "Five-test diagnostics are not included in this run; this is the existing solo assembly check only."
        : "");
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
