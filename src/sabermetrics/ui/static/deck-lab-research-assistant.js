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
  heading.textContent = "Build research assistant";
  var status = document.createElement("p");
  status.className = "dl-sim-note";
  status.setAttribute("role", "status");
  var findings = document.createElement("div");
  findings.setAttribute("data-assistant-findings", "");
  var suggestions = document.createElement("div");
  suggestions.setAttribute("data-assistant-recommendations", "");
  var apply = document.createElement("button");
  apply.type = "button";
  apply.className = "dl-button is-primary is-sm";
  apply.textContent = "Apply selected replacements";
  apply.disabled = true;
  root.append(heading, status, findings, suggestions, apply);
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
  function refresh() {
    if (!state.id) return Promise.resolve();
    status.textContent = "Loading report and tournament evidence…";
    return fetch("/api/decks/" + encodeURIComponent(state.id) + "/research-assistant", {
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) throw new Error("Research assistant could not load.");
      return response.json();
    }).then(function (data) {
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
            var line = metric.summary;
            if (metric.ci95) line += " · 95% interval " + metric.ci95.low + "–" + metric.ci95.high;
            card.appendChild(el("p", metric.metric.replace(/_/g, " ") + ": " + line));
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
        suggestions.appendChild(el("p", "No swap meets the current evidence rules: a named failure signal, matching non-interaction role, same-role tournament evidence, and a known price no higher than the outgoing card."));
      }
      (data.limits || []).forEach(function (limit) { suggestions.appendChild(el("small", limit, "dl-sim-note")); });
      syncApply();
      status.textContent = "Findings and evidence loaded for revision " + data.revision + ".";
      root._recommendations = data.recommendations || [];
    }).catch(function (error) { status.textContent = error.message; });
  }
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
