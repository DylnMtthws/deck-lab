(function () {
  "use strict";
  var dialog = document.getElementById("goldfish-dialog");
  var stateNode = document.getElementById("deck-document-data");
  var slot = document.querySelector("[data-goldfish-sim-slot]");
  if (!dialog || !stateNode || !slot) return;
  var state;
  try { state = JSON.parse(stateNode.textContent || "{}"); } catch (_) { return; }
  var root = document.createElement("section");
  root.className = "dl-research-assistant";
  root.setAttribute("data-research-assistant", "");
  var heading = document.createElement("h3");
  heading.className = "dl-eyebrow";
  heading.textContent = "Research Assistant · evidence mode";
  var status = document.createElement("p");
  status.className = "dl-sim-note";
  status.setAttribute("role", "status");
  var findings = document.createElement("div");
  findings.setAttribute("data-assistant-findings", "");
  var chat = document.createElement("form");
  chat.className = "dl-assistant-chat";
  var question = document.createElement("input");
  question.type = "text";
  question.maxLength = 240;
  question.placeholder = "Ask about opening, mana, commander loss, wipes, finish, or all findings";
  question.setAttribute("aria-label", "Ask about current simulation findings");
  var ask = document.createElement("button");
  ask.type = "submit";
  ask.className = "dl-button is-sm";
  ask.textContent = "Ask";
  var answer = document.createElement("p");
  answer.className = "dl-sim-note";
  answer.setAttribute("aria-live", "polite");
  chat.append(question, ask);
  var suggestions = document.createElement("div");
  suggestions.setAttribute("data-assistant-recommendations", "");
  var apply = document.createElement("button");
  apply.type = "button";
  apply.className = "dl-button is-primary is-sm";
  apply.textContent = "Apply selected replacements";
  apply.disabled = true;
  root.append(heading, status, chat, answer, findings, suggestions, apply);
  slot.appendChild(root);

  function csrf() {
    var node = document.querySelector("meta[name='csrf-token']");
    return node ? node.content || "" : "";
  }
  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = text;
    return node;
  }
  var metricLabels = {
    qualified_opening_rate: "Hands passing the modeled keep rule",
    mean_kept_cards: "Average cards kept",
    mean_kept_hand_size: "Average cards kept",
    mean_mulligans: "Average mulligans",
    mean_unused_available_mana: "Unused modeled mana at turn 5",
    mean_unused_floating_mana_turn_5: "Unused modeled mana at turn 5",
    mean_cards_remaining: "Cards remaining at turn 5",
    mean_hand_size_turn_5: "Cards in hand at turn 5",
    authored_forward_action_rate: "Runs with a modeled forward action",
    mean_first_authored_action_turn: "Average turn of first modeled action",
    engine_online_rate: "Observed engine event",
    disruption_exercised_rate: "Runs where removal occurred",
    followup_turn_reached_rate: "Runs reaching a follow-up turn after removal",
    recovery_authored_action_rate: "Recovery turns with a modeled action",
    recovery_board_rate: "Recovery turns adding a modeled permanent",
    recovery_draw_rate: "Recovery turns with modeled card draw",
    independent_engine_recovery_rate: "Independent engine restored",
    commander_dependent_hand_cards: "Cards requiring the commander",
    verified_finish_rate: "Finish proven among evaluated runs",
    positive_finish_proof_rate: "Runs with positive finish proof (lower bound)",
    finish_evaluated_rate: "Runs with verified finish evaluation",
    mean_missed_land_drops: "Average missed land drops"
  };
  function cleanNumber(value, digits) {
    var rounded = Number(value).toFixed(digits);
    if (rounded.indexOf(".") >= 0) rounded = rounded.replace(/0+$/, "").replace(/\.$/, "");
    return rounded;
  }
  function metricValue(metric) {
    if (typeof metric.value !== "number" || !Number.isFinite(metric.value)) return metric.summary || "unmeasured";
    var unit = String(metric.unit || "");
    var value;
    if (["proportion", "rate", "probability"].indexOf(unit) >= 0 && metric.value >= 0 && metric.value <= 1) {
      value = cleanNumber(metric.value * 100, 1) + "%";
    } else {
      value = cleanNumber(metric.value, 2) + (unit ? " " + unit : "");
    }
    if (Number.isInteger(metric.denominator) && metric.denominator > 0) value += " (" + metric.denominator + " games)";
    return value;
  }
  function metricInterval(metric) {
    var interval = metric.ci95;
    if (!interval || typeof interval.low !== "number" || typeof interval.high !== "number") return "";
    var unit = String(metric.unit || "");
    var rate = ["proportion", "rate", "probability"].indexOf(unit) >= 0;
    var factor = rate ? 100 : 1;
    var suffix = rate ? "%" : (unit ? " " + unit : "");
    return cleanNumber(interval.low * factor, rate ? 1 : 2) + suffix + "–" +
      cleanNumber(interval.high * factor, rate ? 1 : 2) + suffix;
  }
  function refresh() {
    if (!state.id) return Promise.resolve();
    status.textContent = "Loading report and tournament evidence…";
    return fetch("/api/decks/" + encodeURIComponent(state.id) + "/research-assistant", {
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) throw new Error("Research assistant could not load.");
      return response.json();
    }).then(function (data) {
      root._assistantData = data;
      state.revision = data.revision;
      findings.replaceChildren();
      suggestions.replaceChildren();
      if (data.diagnostics_state !== "current") {
        findings.appendChild(el("p", data.diagnostics_state === "stale"
          ? "The latest report is for an earlier deck revision. Run Goldfish again for current findings."
          : "Run Goldfish to get structured findings for this deck."));
      } else {
        findings.appendChild(el("p", "Native measurements are scenario-specific observations, not deck grades or causal card analysis."));
        (data.findings || []).forEach(function (fact) {
          var card = document.createElement("article");
          card.className = "dl-sim-diagnostic";
          card.appendChild(el("strong", fact.title + " · " + fact.status));
          (fact.metrics || []).forEach(function (metric) {
            var label = metricLabels[metric.metric] || metric.metric.replace(/_/g, " ");
            var line = metricValue(metric);
            var interval = metricInterval(metric);
            if (interval) line += " · 95% interval " + interval;
            card.appendChild(el("p", label + ": " + line));
          });
          (fact.limitations || []).forEach(function (limit) {
            card.appendChild(el("p", "Limit: " + limit, "dl-sim-note"));
          });
          findings.appendChild(card);
        });
      }
      if (data.heldout_validation) {
        var heldout = data.heldout_validation;
        var heldoutLine = "Independent-seed check: " + String(heldout.status || "unavailable") +
          (heldout.training_seed !== undefined ? " · training seed " + heldout.training_seed : "") +
          (heldout.validation_seed !== undefined ? " · validation seed " + heldout.validation_seed : "") +
          (heldout.note ? " · " + String(heldout.note) : "") +
          (heldout.reason ? " · " + String(heldout.reason) : "");
        findings.appendChild(el("p", heldoutLine, "dl-sim-note"));
      }
      suggestions.appendChild(el("h4", "Same-role tournament alternatives"));
      suggestions.appendChild(el("p", "These recorded-list alternatives are independent evidence; they are not tested improvements."));
      (data.recommendations || []).forEach(function (item, index) {
        var label = document.createElement("label");
        label.className = "dl-assistant-choice";
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = String(index);
        checkbox.setAttribute("data-assistant-choice", "");
        checkbox.addEventListener("change", syncApply);
        label.append(checkbox, document.createTextNode(" Replace " + item.from_name + " with " + item.name +
          " · " + item.lists + "/" + item.denominator + " recorded lists (" + item.window_days + " days)"));
        suggestions.appendChild(label);
      });
      if (!(data.recommendations || []).length) {
        suggestions.appendChild(el("p", "No swap meets the current evidence rules: a measured missed-land-drop observation, matching non-interaction role, same-role tournament evidence, and a known price no higher than the outgoing card."));
        (data.search_hints || []).forEach(function (hint) {
          var search = document.createElement("button");
          search.type = "button";
          search.className = "dl-button is-sm";
          search.textContent = hint.label;
          search.setAttribute("data-assistant-search", hint.query);
          search.addEventListener("click", function () {
            var open = document.querySelector("[data-add-open]");
            if (open) open.click();
            window.setTimeout(function () {
              var input = document.querySelector("[data-card-search]");
              if (!input) return;
              input.value = hint.query;
              input.dispatchEvent(new Event("input", { bubbles: true }));
              input.focus();
            }, 120);
          });
          suggestions.appendChild(search);
        });
      }
      (data.limits || []).forEach(function (limit) { suggestions.appendChild(el("small", limit, "dl-sim-note")); });
      syncApply();
      status.textContent = "Findings and evidence loaded for revision " + data.revision + ". Answers use this report and linked evidence only; no AI service is called.";
      root._recommendations = data.recommendations || [];
    }).catch(function (error) { status.textContent = error.message; });
  }
  function answerQuestion(value) {
    var data = root._assistantData || {};
    var query = String(value || "").toLowerCase();
    var all = /\b(all|everything|summary|findings)\b/.test(query);
    var keys = /\b(opening|mana|land|lands|ramp)\b/.test(query) ? ["opening", "development"] :
      /\b(commander|command zone|commander loss)\b/.test(query) ? ["commander_loss"] :
      /\b(wipe|board wipe|sweeper|recovery)\b/.test(query) ? ["creature_wipe"] :
      /\b(finish|win|winning|damage)\b/.test(query) ? ["finish"] : [];
    if (all && data.diagnostics_state === "current") keys = (data.findings || []).map(function (fact) { return fact.id; });
    if (!keys.length) {
      answer.textContent = data.diagnostics_state === "current"
        ? "I can answer from this run about opening or mana, commander loss, creature wipes, finish checks, or all findings. Other questions need evidence this assistant does not currently expose."
        : "Run Goldfish first. I can then answer about opening or mana, commander loss, creature wipes, finish checks, or all findings.";
      return;
    }
    var facts = (data.findings || []).filter(function (fact) { return keys.indexOf(fact.id) >= 0; });
    if (!facts.length) {
      answer.textContent = "The current report has no findings for that topic. This means it was not measured here, not that the deck failed.";
      return;
    }
    var lines = facts.map(function (fact) {
      var metrics = (fact.metrics || []).map(function (metric) { return metric.metric + ": " + metric.summary; });
      var caveat = fact.status === "unsupported" ? "Unsupported in this run." : "Scenario observation only; it does not establish a cause or deck quality.";
      return fact.title + " (" + fact.status + "): " + (metrics.length ? metrics.join("; ") : "no numeric measurement") + ". " + caveat;
    });
    answer.textContent = lines.join(" ");
  }
  chat.addEventListener("submit", function (event) {
    event.preventDefault();
    answerQuestion(question.value);
  });
  function syncApply() {
    var selected = root.querySelectorAll("[data-assistant-choice]:checked");
    apply.disabled = selected.length < 1 || selected.length > 3;
    root.querySelectorAll("[data-assistant-choice]:not(:checked)").forEach(function (box) {
      box.disabled = selected.length >= 3;
    });
  }
  apply.addEventListener("click", function () {
    var options = root._recommendations || [];
    var replacements = Array.from(root.querySelectorAll("[data-assistant-choice]:checked")).map(function (box) {
      var item = options[Number(box.value)];
      return { entry_id: item.entry_id, card_id: item.card_id };
    });
    if (!replacements.length || replacements.length > 3) return;
    apply.disabled = true;
    status.textContent = "Applying replacements…";
    fetch("/api/decks/" + encodeURIComponent(state.id) + "/research-assistant/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "Accept": "application/json" },
      body: JSON.stringify({ expected_revision: state.revision, replacements: replacements })
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.message || "Could not apply these replacements. Reload and review the current deck.");
        status.textContent = "Replacements applied. Reloading the updated deck; run Goldfish again to inspect it.";
        window.setTimeout(function () { window.location.reload(); }, 600);
      });
    }).catch(function (error) { status.textContent = error.message; apply.disabled = false; });
  });

  refresh();
  var run = document.querySelector("[data-simulate-run]");
  var rerun = document.querySelector("[data-simulate-rerun]");
  function watchRun() {
    var attempts = 0;
    function poll() {
      attempts += 1;
      fetch("/api/decks/" + encodeURIComponent(state.id) + "/simulations/latest", { headers: { "Accept": "application/json" } })
        .then(function (response) { return response.json(); })
        .then(function (view) {
          if (view.status === "queued" || view.status === "running") {
            if (attempts < 300) window.setTimeout(poll, 1000);
          } else refresh();
        }).catch(function () { if (attempts < 300) window.setTimeout(poll, 2000); });
    }
    window.setTimeout(poll, 1000);
  }
  if (run) run.addEventListener("click", watchRun);
  if (rerun) rerun.addEventListener("click", watchRun);
}());
