(function () {
  "use strict";
  var sidebar = document.querySelector("[data-assistant-sidebar]");
  var stateNode = document.getElementById("deck-document-data");
  if (!sidebar || !stateNode) return;
  var state;
  try { state = JSON.parse(stateNode.textContent || "{}"); } catch (_) { return; }
  if (!state.id) return;
  var deckId = encodeURIComponent(state.id);
  var base = "/api/decks/" + deckId + "/assistant";
  var transcript = sidebar.querySelector("[data-assistant-transcript]");
  var experimentsNode = sidebar.querySelector("[data-assistant-experiments]");
  var proposalsNode = sidebar.querySelector("[data-assistant-proposals]");
  var status = sidebar.querySelector("[data-assistant-status]");
  var busy = false;
  var protectedDirty = false;
  var pollTimer = 0;
  var data = { mode: "suggest", constraints: {}, messages: [], experiments: [], provider: {} };
  proposalsNode.addEventListener("change", function () {
    var checked = proposalsNode.querySelectorAll("[data-replacement]:checked");
    var button = proposalsNode.querySelector("[data-assistant-apply]");
    if (button) button.disabled = checked.length < 1 || checked.length > 3;
    proposalsNode.querySelectorAll("[data-replacement]:not(:checked)").forEach(function (box) { box.disabled = checked.length >= 3; });
  });
  function csrf() {
    var node = document.querySelector("meta[name='csrf-token']");
    return node ? node.content || node.getAttribute("content") || "" : "";
  }
  function element(tag, text, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function request(url, options) {
    options = options || {};
    options.headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    if (options.body) {
      options.headers["Content-Type"] = "application/json";
      options.headers["X-CSRFToken"] = csrf();
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.message || body.detail || body.error || "Research Assistant request failed.");
        return body;
      });
    });
  }
  function showTab(name, focus) {
    sidebar.querySelectorAll("[data-assistant-tab]").forEach(function (tab) {
      var on = tab.dataset.assistantTab === name;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      var pane = sidebar.querySelector('[data-assistant-pane="' + tab.dataset.assistantTab + '"]');
      if (pane) pane.hidden = !on;
      if (on && focus) tab.focus();
    });
    try { localStorage.setItem("deck-lab-assistant-tab:" + state.id, name); } catch (_) {}
  }
  var tabs = Array.prototype.slice.call(sidebar.querySelectorAll("[data-assistant-tab]"));
  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () { showTab(tab.dataset.assistantTab, false); });
    tab.addEventListener("keydown", function (event) {
      var next;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
      else if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs[tabs.length - 1];
      else return;
      event.preventDefault(); showTab(next.dataset.assistantTab, true);
    });
  });
  try {
    var savedTab = localStorage.getItem("deck-lab-assistant-tab:" + state.id);
    if (tabs.some(function (tab) { return tab.dataset.assistantTab === savedTab; })) showTab(savedTab, false);
  } catch (_) {}
  var mobileOpen = document.querySelector("[data-assistant-open]");
  var close = sidebar.querySelector("[data-assistant-close]");
  function mobile(value) {
    sidebar.classList.toggle("is-open", value);
    if (mobileOpen) mobileOpen.setAttribute("aria-expanded", value ? "true" : "false");
    document.body.classList.toggle("dl-assistant-drawer-open", value);
    if (value && tabs[0]) tabs[0].focus();
    if (!value && mobileOpen) mobileOpen.focus();
  }
  if (mobileOpen) mobileOpen.addEventListener("click", function () { mobile(true); });
  if (close) close.addEventListener("click", function () {
    if (window.matchMedia && window.matchMedia("(max-width: 767px)").matches) mobile(false);
  });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape" && sidebar.classList.contains("is-open")) mobile(false); });

  // Pointer and keyboard resizing share a persisted width; the grip remains reachable.
  var grip = sidebar.querySelector("[data-assistant-resize]");
  var builder = document.querySelector(".dl-builder");
  var widthKey = "deck-lab-assistant-width:" + state.id;
  var width = 360;
  try { width = Math.max(280, Math.min(520, Number(localStorage.getItem(widthKey)) || width)); } catch (_) {}
  function setWidth(value) {
    width = Math.max(280, Math.min(520, Number(value) || 360));
    if (builder) builder.style.setProperty("--assistant-sidebar-width", width + "px");
    if (grip) grip.setAttribute("aria-valuenow", String(Math.round(width)));
    try { localStorage.setItem(widthKey, String(width)); } catch (_) {}
  }
  setWidth(width);
  if (grip) {
    grip.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      event.preventDefault(); grip.setPointerCapture(event.pointerId);
      function move(next) { setWidth(next.clientX - sidebar.getBoundingClientRect().left); }
      function up() { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); }
      grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up);
    });
    grip.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault(); setWidth(width + (event.key === "ArrowRight" ? 16 : -16));
    });
  }

  function protectedCards() {
    var host = sidebar.querySelector("[data-assistant-protected]");
    if (!host) return;
    var selected = new Set((data.constraints && data.constraints.protected_cards || []).map(String));
    host.replaceChildren();
    var seen = new Set();
    (state.entries || []).filter(function (entry) { return entry && entry.card_id; }).forEach(function (entry) {
      var id = String(entry.card_id);
      if (seen.has(id)) return;
      seen.add(id);
      var name = entry.name || id;
      var box = document.createElement("input"); box.type = "checkbox"; box.value = id;
      box.checked = selected.has(id); box.dataset.protectedCard = "";
      var label = element("label"); label.append(box, document.createTextNode(name)); host.appendChild(label);
    });
  }
  function renderSettings() {
    sidebar.querySelectorAll("[data-assistant-mode]").forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.assistantMode === data.mode ? "true" : "false");
    });
    var constraints = data.constraints || {};
    var budget = sidebar.querySelector("[data-assistant-budget]");
    var power = sidebar.querySelector("[data-assistant-power]");
    var theme = sidebar.querySelector("[data-assistant-theme]");
    if (budget && document.activeElement !== budget) budget.value = constraints.budget == null ? "" : constraints.budget;
    if (power && document.activeElement !== power) power.value = constraints.power_level || "";
    if (theme && document.activeElement !== theme) theme.value = constraints.theme || "";
    if (!protectedDirty) protectedCards();
    var provider = sidebar.querySelector("[data-assistant-provider]");
    if (provider) provider.textContent = data.provider && data.provider.enabled
      ? "Assistant provider enabled. Responses may take a moment."
      : "Assistant provider is unavailable; messages and settings remain saved.";
    var stop = sidebar.querySelector("[data-assistant-stop]");
    if (stop) stop.hidden = !(data.busy || (data.experiments || []).some(function (item) { return ["queued", "running"].indexOf(item.status) >= 0; }));
  }
  function messageContent(message) {
    var content = message && message.content;
    if (typeof content === "string") return content;
    try { return JSON.stringify(content, null, 2); } catch (_) { return String(content || ""); }
  }
  function renderMessages() {
    if (!transcript) return;
    var nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48;
    transcript.replaceChildren();
    (data.messages || []).forEach(function (message) {
      var role = ["user", "assistant", "tool"].indexOf(message.role) >= 0 ? message.role : "tool";
      transcript.appendChild(element("article", messageContent(message), "dl-assistant-message"));
      transcript.lastElementChild.dataset.role = role;
    });
    if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
  }
  function addReplacementChoices(host, replacements, context) {
    if (!Array.isArray(replacements) || !replacements.length) return;
    replacements.forEach(function (swap) {
      var outId = swap.outgoing_entry_id || swap.entry_id;
      var inId = swap.incoming_card_id || swap.card_id;
      if (!outId || !inId) return;
      var out = (state.entries || []).find(function (entry) { return String(entry.id) === String(outId); });
      var label = element("label", null, "dl-assistant-choice");
      var box = document.createElement("input"); box.type = "checkbox";
      box.dataset.replacement = ""; box.value = JSON.stringify({ outgoing_entry_id: String(outId), incoming_card_id: String(inId) });
      label.append(box, document.createTextNode("Replace " + (swap.outgoing_name || out && out.name || outId) + " with " + (swap.incoming_name || swap.name || inId)));
      host.appendChild(label);
    });
  }
  function renderProposals() {
    var selected = new Set(Array.prototype.slice.call(proposalsNode.querySelectorAll("[data-replacement]:checked")).map(function (box) { return box.value; }));
    proposalsNode.replaceChildren();
    (data.messages || []).forEach(function (message) {
      (message.proposals || []).forEach(function (proposal) {
        var card = element("article", null, "dl-assistant-proposal");
        card.appendChild(element("h3", proposal.name || "Suggested change"));
        if (proposal.rationale) card.appendChild(element("p", proposal.rationale));
        addReplacementChoices(card, proposal.replacements, proposal);
        card.querySelectorAll("[data-replacement]").forEach(function (box) { box.checked = selected.has(box.value); });
        proposalsNode.appendChild(card);
      });
    });
    if (!proposalsNode.querySelector("[data-replacement]")) return;
    var button = element("button", "Apply selected swaps", "dl-button is-primary is-sm");
    button.type = "button"; button.dataset.assistantApply = "";
    button.addEventListener("click", function () {
      var replacements = Array.prototype.slice.call(proposalsNode.querySelectorAll("[data-replacement]:checked")).map(function (box) { return JSON.parse(box.value); });
      if (!replacements.length || replacements.length > 3) { status.textContent = "Select one to three suggested swaps."; return; }
      button.disabled = true; status.textContent = "Applying selected swaps…";
      request(base + "/apply", { method: "POST", body: JSON.stringify({ replacements: replacements, expected_revision: Number(state.revision || 0) }) })
        .then(function (result) { if (result && result.revision !== undefined) state.revision = result.revision; status.textContent = "Selected swaps applied. Review the updated deck, then rerun Goldfish."; window.location.reload(); })
        .catch(function (error) { status.textContent = error.message; button.disabled = false; });
    });
    proposalsNode.appendChild(button);
    button.disabled = true;
  }
  function readableReport(report) {
    if (!report) return "No report supplied.";
    return typeof report === "string" ? report : JSON.stringify(report, null, 2);
  }
  function renderExperiments() {
    experimentsNode.replaceChildren();
    (data.experiments || []).forEach(function (experiment) {
      var card = element("article", null, "dl-assistant-experiment");
      card.appendChild(element("h3", "Automated research · " + (experiment.status || "unknown")));
      if (experiment.error) card.appendChild(element("p", experiment.error));
      (experiment.alternatives || []).forEach(function (alternative) {
        var section = element("section");
        section.appendChild(element("strong", alternative.name || "Alternative"));
        if (alternative.rationale) section.appendChild(element("p", alternative.rationale));
        if (alternative.validation) {
          section.appendChild(element("p", "Independent validation: " + (alternative.validation.status || "descriptive") +
            (alternative.validation.note ? " · " + alternative.validation.note : "") +
            (alternative.validation.reason ? " · " + alternative.validation.reason : "")));
          var validationDetails = element("details");
          validationDetails.appendChild(element("summary", "Validation details"));
          validationDetails.appendChild(element("pre", readableReport(alternative.validation)));
          section.appendChild(validationDetails);
        }
        if (alternative.comparison) {
          var comparisonDetails = element("details");
          comparisonDetails.appendChild(element("summary", "Metric comparison details"));
          comparisonDetails.appendChild(element("pre", readableReport(alternative.comparison)));
          section.appendChild(comparisonDetails);
        }
        if (alternative.training_report) section.appendChild(element("details") );
        if (alternative.training_report) {
          var details = section.lastElementChild; details.appendChild(element("summary", "Training report details"));
          details.appendChild(element("pre", readableReport(alternative.training_report)));
        }
        if (alternative.validation_report) {
          var validation = element("details"); validation.appendChild(element("summary", "Validation report details"));
          validation.appendChild(element("pre", readableReport(alternative.validation_report))); section.appendChild(validation);
        }
        var adopt = element("button", "Adopt this alternative", "dl-button is-sm"); adopt.type = "button";
        adopt.disabled = alternative.status === "unavailable" || experiment.status === "failed";
        adopt.addEventListener("click", function () {
          adopt.disabled = true; status.textContent = "Adopting selected alternative…";
          request(base + "/experiments/" + encodeURIComponent(experiment.id) + "/adopt", {
            method: "POST", body: JSON.stringify({ alternative_id: alternative.id, expected_revision: Number(state.revision || 0) })
          }).then(function () { window.location.reload(); }).catch(function (error) { status.textContent = error.message; adopt.disabled = false; });
        });
        section.appendChild(adopt); card.appendChild(section);
      });
      if (!experiment.alternatives || !experiment.alternatives.length) card.appendChild(element("p", "No alternatives are available yet."));
      experimentsNode.appendChild(card);
    });
  }
  function render() {
    renderSettings(); renderMessages(); renderProposals(); renderExperiments();
    busy = !!data.busy || (data.experiments || []).some(function (item) { return ["queued", "running"].indexOf(item.status) >= 0; });
    var send = sidebar.querySelector("[data-assistant-send]"); if (send) send.disabled = busy;
    var stop = sidebar.querySelector("[data-assistant-stop]"); if (stop) stop.hidden = !busy;
    if (busy) schedulePoll();
  }
  function refresh() {
    return request(base).then(function (next) { data = Object.assign({}, data, next || {}); render(); }).catch(function (error) { status.textContent = error.message; });
  }
  function schedulePoll() { if (pollTimer) return; pollTimer = window.setTimeout(function () { pollTimer = 0; refresh(); }, 1800); }

  sidebar.querySelectorAll("[data-assistant-mode]").forEach(function (button) {
    button.addEventListener("click", function () {
      var mode = button.dataset.assistantMode;
      request(base + "/settings", { method: "POST", body: JSON.stringify({ mode: mode, constraints: data.constraints || {} }) })
        .then(function (next) { data = Object.assign({}, data, next || {}, { mode: mode }); renderSettings(); })
        .catch(function (error) { status.textContent = error.message; });
    });
  });
  var constraintToggle = sidebar.querySelector("[data-assistant-constraints-toggle]");
  if (constraintToggle) constraintToggle.addEventListener("click", function () {
    var form = sidebar.querySelector("[data-assistant-constraints]"); form.hidden = !form.hidden;
    constraintToggle.setAttribute("aria-expanded", form.hidden ? "false" : "true");
  });
  var constraintsForm = sidebar.querySelector("[data-assistant-constraints]");
  var protectedList = sidebar.querySelector("[data-assistant-protected]");
  if (protectedList) protectedList.addEventListener("change", function () { protectedDirty = true; });
  if (constraintsForm) constraintsForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var statusNode = sidebar.querySelector("[data-assistant-settings-status]");
    var budgetText = sidebar.querySelector("[data-assistant-budget]").value.trim();
    var constraints = {
      budget: budgetText ? Number(budgetText) : null,
      power_level: sidebar.querySelector("[data-assistant-power]").value.trim(),
      theme: sidebar.querySelector("[data-assistant-theme]").value.trim(),
      protected_cards: Array.prototype.slice.call(sidebar.querySelectorAll("[data-protected-card]:checked")).map(function (box) { return box.value; })
    };
    request(base + "/settings", { method: "POST", body: JSON.stringify({ mode: data.mode || "suggest", constraints: constraints }) })
      .then(function (next) { data = Object.assign({}, data, next || {}, { constraints: constraints }); protectedDirty = false; renderSettings(); if (statusNode) statusNode.textContent = "Constraints saved."; })
      .catch(function (error) { if (statusNode) statusNode.textContent = error.message; });
  });
  var composer = sidebar.querySelector("[data-assistant-composer]");
  if (composer) composer.addEventListener("submit", function (event) {
    event.preventDefault();
    var input = sidebar.querySelector("[data-assistant-message]");
    var message = input.value.trim(); if (!message || busy) return;
    status.textContent = "Sending message…";
    request(base + "/messages", { method: "POST", body: JSON.stringify({ message: message, expected_revision: Number(state.revision || 0) }) })
      .then(function () { input.value = ""; status.textContent = "Message sent."; return refresh(); })
      .catch(function (error) { status.textContent = error.message; });
  });
  var stop = sidebar.querySelector("[data-assistant-stop]");
  if (stop) stop.addEventListener("click", function () {
    stop.disabled = true;
    request(base + "/stop", { method: "POST", body: JSON.stringify({}) }).then(function () { status.textContent = "Stop requested."; return refresh(); })
      .catch(function (error) { status.textContent = error.message; }).finally(function () { stop.disabled = false; });
  });
  var latestSim = document.querySelector("[data-simulate-rerun]");
  if (latestSim) latestSim.addEventListener("click", function () { showTab("goldfish", false); });
  document.addEventListener("deck-lab:render", function (event) {
    if (!event.detail || !event.detail.state) return;
    state = event.detail.state;
    protectedCards();
  });
  if (window.location.hash === "#goldfish") showTab("goldfish", false);
  refresh();
  window.addEventListener("pagehide", function () { if (pollTimer) window.clearTimeout(pollTimer); });
}());
