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
  var scrollSurface = sidebar.querySelector("[data-assistant-scroll]");
  var experimentsNode = sidebar.querySelector("[data-assistant-experiments]");
  var proposalsNode = sidebar.querySelector("[data-assistant-proposals]");
  var status = sidebar.querySelector("[data-assistant-status]");
  var busy = false;
  var protectedDirty = false;
  var pollTimer = 0;
  var data = { mode: "suggest", constraints: {}, messages: [], experiments: [], provider: {} };
  var previewCardId = null;
  var previewOpener = null;
  var previewPinned = false;
  var suppressPreviewFocusId = null;
  var previewTimer = 0;
  var renderedMessageSignature = null;
  var renderedExperimentSignature = null;
  var renderedProposalSignature = null;
  var renderedMessageCount = 0;
  var renderedMessageIds = [];
  var preview = document.createElement("div");
  preview.className = "dl-assistant-card-preview";
  preview.setAttribute("role", "dialog");
  preview.setAttribute("aria-label", "Card preview");
  preview.hidden = true;
  document.body.appendChild(preview);
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
  function cardId(card) { return card && (card.id || card.card_id || card.incoming_card_id); }
  function knownCards(extra) {
    var byId = Object.create(null);
    function add(card) {
      var id = cardId(card);
      if (id && card && typeof card.name === "string") byId[String(id)] = Object.assign({}, byId[String(id)] || {}, card, { id: String(id) });
    }
    (state.entries || []).forEach(add);
    (data.messages || []).forEach(function (message) {
      (message.presentation && message.presentation.cards || []).forEach(add);
      var action = message.action || {};
      (action.data && action.data.cards || []).forEach(add);
    });
    (extra || []).forEach(add);
    return Object.keys(byId).map(function (id) { return byId[id]; })
      .sort(function (a, b) { return b.name.length - a.name.length; });
  }
  function showPreview(card, opener, toggle) {
    if (!card || !opener) return;
    window.clearTimeout(previewTimer);
    if (previewOpener && previewOpener !== opener) previewOpener.setAttribute("aria-expanded", "false");
    if (toggle && !preview.hidden && previewCardId === String(card.id)) { previewPinned = true; preview.classList.add("is-pinned"); var closeButton = preview.querySelector("button"); if (closeButton) closeButton.focus(); return; }
    previewCardId = String(card.id);
    previewOpener = opener;
    previewPinned = !!toggle;
    preview.classList.toggle("is-pinned", previewPinned);
    preview.replaceChildren();
    var image = card.image_uri;
    var safeImage = false;
    try { safeImage = typeof image === "string" && new URL(image, window.location.href).protocol === "https:"; } catch (_) {}
    if (safeImage) {
      var img = document.createElement("img"); img.src = image; img.alt = ""; img.loading = "lazy";
      img.addEventListener("error", function () { img.hidden = true; });
      img.addEventListener("load", positionPreview);
      preview.appendChild(img);
    }
    preview.appendChild(element("strong", card.name));
    var meta = [card.mana_cost, card.type_line].filter(function (value) { return typeof value === "string" && value; }).join(" · ");
    if (meta) preview.appendChild(element("small", meta));
    if (typeof card.oracle_text === "string" && card.oracle_text.trim()) preview.appendChild(element("p", card.oracle_text.trim()));
    var close = element("button", "Close preview", "dl-button is-ghost is-sm"); close.type = "button";
    close.addEventListener("click", function () { hidePreview(true); }); preview.appendChild(close);
    var ask = element("button", "Ask about this card", "dl-button is-sm"); ask.type = "button";
    ask.addEventListener("click", function () {
      var input = sidebar.querySelector("[data-assistant-message]");
      if (input) { input.value = "What role could " + card.name + " fill in this deck?"; showTab("chat", false); input.focus(); }
      hidePreview();
    }); preview.appendChild(ask);
    preview.hidden = false;
    positionPreview();
    opener.setAttribute("aria-expanded", "true");
    if (toggle && close.focus) close.focus();
  }
  function positionPreview() {
    if (preview.hidden || !previewOpener) return;
    var rect = previewOpener.getBoundingClientRect();
    var width = Math.min(300, window.innerWidth - 24);
    preview.style.width = width + "px";
    preview.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + "px";
    preview.style.top = Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - preview.getBoundingClientRect().height - 8)) + "px";
  }
  window.addEventListener("resize", positionPreview);
  function hidePreview(returnFocus) {
    window.clearTimeout(previewTimer);
    preview.hidden = true; previewCardId = null; previewPinned = false; preview.classList.remove("is-pinned");
    sidebar.querySelectorAll("[data-known-card-id][aria-expanded='true']").forEach(function (button) { button.setAttribute("aria-expanded", "false"); });
    if (returnFocus && previewOpener && previewOpener.focus) { suppressPreviewFocusId = String(previewOpener.dataset.knownCardId || ""); previewOpener.focus(); }
    previewOpener = null;
  }
  preview.addEventListener("mouseenter", function () { window.clearTimeout(previewTimer); });
  preview.addEventListener("mouseleave", function () { if (!previewPinned) previewTimer = window.setTimeout(hidePreview, 120); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !preview.hidden) { event.preventDefault(); event.stopImmediatePropagation(); hidePreview(true); }
  });
  document.addEventListener("pointerdown", function (event) {
    var target = event.target;
    if (!preview.hidden && !preview.contains(target) && !(target.closest && target.closest("[data-known-card-id]"))) hidePreview();
  });
  sidebar.addEventListener("pointerover", function (event) {
    var button = event.target.closest && event.target.closest("[data-known-card-id]");
    if (!button || button.contains(event.relatedTarget) || previewPinned) return;
    var card = knownCards().find(function (item) { return String(item.id) === button.dataset.knownCardId; });
    if (card) showPreview(card, button, false);
  });
  sidebar.addEventListener("focusin", function (event) {
    var button = event.target.closest && event.target.closest("[data-known-card-id]");
    if (!button) return;
    if (suppressPreviewFocusId === button.dataset.knownCardId) { suppressPreviewFocusId = null; return; }
    var card = knownCards().find(function (item) { return String(item.id) === button.dataset.knownCardId; });
    if (card) showPreview(card, button, false);
  });
  sidebar.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-known-card-id]");
    if (!button) return;
    event.preventDefault();
    var card = knownCards().find(function (item) { return String(item.id) === button.dataset.knownCardId; });
    if (card) showPreview(card, button, true);
  });
  sidebar.addEventListener("keydown", function (event) {
    var button = event.target.closest && event.target.closest("[data-known-card-id]");
    if (button && event.key === "Tab" && !event.shiftKey && !preview.hidden) {
      event.preventDefault();
      var close = preview.querySelector("button"); if (close) close.focus();
    }
  });
  sidebar.addEventListener("pointerout", function (event) {
    var button = event.target.closest && event.target.closest("[data-known-card-id]");
    var focusedHere = button && document.activeElement === button;
    if (button && !previewPinned && !focusedHere && !button.contains(event.relatedTarget) && !preview.contains(event.relatedTarget)) previewTimer = window.setTimeout(hidePreview, 140);
  });
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
  function ensureDeckSaved() {
    var api = window.DeckLabBuilder;
    var save = api && typeof api.getSaveState === "function" ? api.getSaveState() : null;
    if (save && (save.pending || save.failed)) {
      throw new Error("Save the current deck edits before adopting or applying assistant changes.");
    }
  }
  function acceptUpdatedDocument(payload) {
    var updated = payload && payload.document && Array.isArray(payload.document.entries)
      ? payload.document : payload;
    var api = window.DeckLabBuilder;
    if (!api || typeof api.replaceDocument !== "function" || !api.replaceDocument(updated)) {
      throw new Error("The change was saved, but the live deck has pending edits. Reload to sync it before continuing.");
    }
    state = api.getState();
    return refresh();
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
  sidebar.querySelectorAll("[data-assistant-starter]").forEach(function (button) {
    button.addEventListener("click", function () {
      var input = sidebar.querySelector("[data-assistant-message]");
      if (!input) return;
      input.value = button.dataset.assistantStarter || "";
      showTab("chat", false); input.focus();
    });
  });
  sidebar.querySelectorAll("[data-assistant-open-tab]").forEach(function (button) {
    button.addEventListener("click", function () { showTab(button.dataset.assistantOpenTab, false); });
  });
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
      function up() { grip.removeEventListener("pointermove", move); ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) { grip.removeEventListener(name, up); }); }
      grip.addEventListener("pointermove", move); ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) { grip.addEventListener(name, up); });
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
  function cardMatchAt(text, offset, cards) {
    var lower = text.toLocaleLowerCase();
    var best = null;
    cards.forEach(function (card) {
      var name = card.name.toLocaleLowerCase();
      var at = lower.indexOf(name, offset);
      while (at >= 0) {
        var before = at > 0 ? lower.charAt(at - 1) : "";
        var after = lower.charAt(at + name.length);
        var word = /[a-z0-9]/i;
        if ((!word.test(name.charAt(0)) || !word.test(before)) && (!word.test(name.charAt(name.length - 1)) || !word.test(after))) break;
        at = lower.indexOf(name, at + 1);
      }
      if (at >= 0 && (!best || at < best.at)) best = { at: at, end: at + card.name.length, card: card };
    });
    return best;
  }
  function appendInline(parent, text, cards, allowBold) {
    var offset = 0;
    while (offset < text.length) {
      var bold = allowBold !== false && text.slice(offset).match(/^\*\*([^*\n]+)\*\*/);
      if (bold) {
        var strong = document.createElement("strong");
        appendInline(strong, bold[1], cards, false);
        parent.appendChild(strong); offset += bold[0].length; continue;
      }
      var match = cardMatchAt(text, offset, cards);
      if (match && match.at === offset) {
        var link = element("button", text.slice(match.at, match.end), "dl-assistant-card-link");
        link.type = "button"; link.dataset.knownCardId = String(match.card.id);
        link.setAttribute("aria-label", "Preview " + match.card.name); link.setAttribute("aria-expanded", "false");
        parent.appendChild(link); offset = match.end; continue;
      }
      var nextBold = allowBold !== false ? text.indexOf("**", offset) : -1;
      var nextCard = match ? match.at : -1;
      var end = text.length;
      if (nextBold >= 0) end = Math.min(end, nextBold);
      if (nextCard >= 0) end = Math.min(end, nextCard);
      if (end <= offset) end = offset + 1;
      parent.appendChild(document.createTextNode(text.slice(offset, end)));
      offset = end;
    }
  }
  function appendMarkdown(parent, text, cards) {
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    var list = null;
    function paragraph(text) {
      // Legacy replies may contain no breaks. Preserve every sentence verbatim,
      // but provide reading pauses; never invent headings or bullet semantics.
      var chunks = [text];
      if (text.length > 420 && typeof Intl.Segmenter === "function") {
        var sentences = Array.from(new Intl.Segmenter("en", { granularity: "sentence" }).segment(text), function (part) { return part.segment; });
        chunks = []; var chunk = "";
        sentences.forEach(function (sentence) {
          if (chunk.length >= 240) { chunks.push(chunk); chunk = ""; }
          chunk += sentence;
        });
        if (chunk) chunks.push(chunk);
      }
      chunks.forEach(function (chunk) { var p = element("p"); appendInline(p, chunk, cards, true); parent.appendChild(p); });
    }
    lines.forEach(function (line) {
      var item = line.match(/^\s*(?:([-*•])|([0-9]+)[.)])\s+(.+)$/);
      var heading = line.match(/^#{1,6}\s+(.+)$/);
      if (item) {
        var tag = item[2] ? "OL" : "UL";
        if (!list || list.tagName !== tag) {
          list = document.createElement(tag.toLowerCase());
          if (item[2]) list.start = Number(item[2]);
          parent.appendChild(list);
        }
        var li = document.createElement("li"); appendInline(li, item[3], cards, true); list.appendChild(li);
      } else {
        list = null;
        if (heading) { var h = element("h3"); appendInline(h, heading[1], cards, true); parent.appendChild(h); }
        else if (line.trim()) paragraph(line.trim());
      }
    });
  }
  function appendReply(parent, message) {
    var body = element("div", null, "dl-assistant-prose");
    var reply = message.presentation && message.presentation.reply;
    if (reply && typeof reply.lead === "string" && Array.isArray(reply.findings) && (reply.lead.trim() || reply.findings.length || reply.next_action)) {
      appendMarkdown(body, reply.lead, cardsFrom(message));
      if (reply.findings.length) {
        var list = element("ul");
        reply.findings.forEach(function (finding) { var li = element("li"); appendInline(li, String(finding), cardsFrom(message), true); list.appendChild(li); });
        body.appendChild(list);
      }
      if (reply.next_action) {
        var action = element("div", null, "dl-assistant-next-action");
        action.appendChild(element("h3", "Next step"));
        appendMarkdown(action, reply.next_action, cardsFrom(message)); body.appendChild(action);
      }
    } else appendMarkdown(body, messageContent(message), cardsFrom(message));
    parent.appendChild(body);
  }
  function cardsFrom(message) {
    return knownCards(message && message.presentation && message.presentation.cards || []);
  }
  function appendCardResults(article, cards) {
    if (!Array.isArray(cards)) return;
    var list = element("ul", null, "dl-assistant-card-results");
    list.setAttribute("aria-label", "Card search results");
    cards.slice(0, 12).forEach(function (card) {
      if (!card || typeof card.name !== "string" || !cardId(card)) return;
      var row = element("li", null, "dl-assistant-card-result");
      var link = element("button", null, "dl-assistant-card-link"); link.type = "button";
      link.dataset.knownCardId = String(cardId(card)); link.setAttribute("aria-label", "Preview " + card.name); link.setAttribute("aria-expanded", "false");
      var image = card.image_uri;
      try {
        if (typeof image === "string" && new URL(image, window.location.href).protocol === "https:") {
          var thumb = document.createElement("img"); thumb.src = image; thumb.alt = ""; thumb.loading = "lazy"; thumb.addEventListener("error", function () { thumb.hidden = true; }); link.appendChild(thumb);
        }
      } catch (_) {}
      link.appendChild(element("span", card.name));
      row.appendChild(link);
      var details = [card.mana_cost, card.type_line].filter(function (value) { return typeof value === "string" && value.trim(); }).join(" · ");
      if (details) row.appendChild(element("small", details));
      list.appendChild(row);
    });
    if (list.children.length) article.appendChild(list);
  }
  function observationChart(host, goldfish) {
    if (!goldfish || typeof goldfish !== "object") return;
    var tests = [
      ["opening", "Opening", "qualified_opening_rate"],
      ["development", "Engine activity", "engine_online_rate"],
      ["commander_loss", "Commander recovery", "recovery_draw_rate"],
      ["creature_wipe", "Wipe recovery", "recovery_board_rate"],
      ["finish", "Finish proof · lower bound", "positive_finish_proof_rate"]
    ];
    var observations = Array.isArray(goldfish.observations) ? goldfish.observations : [];
    if (!observations.length && !(goldfish.test_statuses || []).length) return;
    var statuses = Object.create(null);
    (goldfish.test_statuses || []).forEach(function (item) { statuses[item.test] = item; });
    var figure = element("figure", null, "dl-assistant-goldfish-chart");
    figure.dataset.reportRevision = goldfish.report_revision == null ? "" : String(goldfish.report_revision);
    figure.dataset.reportStale = goldfish.stale ? "true" : "false";
    var reportLine = "Native Goldfish observations · " + (goldfish.report_status || "status unknown") + " · not a multiplayer win-rate forecast";
    if (goldfish.report_revision != null) reportLine += " · revision " + goldfish.report_revision;
    figure.appendChild(element("figcaption", reportLine));
    updateChartStaleness(figure);
    if (goldfish.report_deck_sha256) figure.appendChild(element("small", "Deck fingerprint: " + String(goldfish.report_deck_sha256).slice(0, 16) + "…"));
    if ((goldfish.test_statuses || []).some(function (item) { return item.status === "partial"; })) {
      figure.appendChild(element("p", "Partial observations use only the authored subset of cards."));
    }
    var grid = element("div", null, "dl-assistant-metric-grid");
    tests.forEach(function (test) {
      var metric = observations.find(function (item) { return item.test === test[0] && item.metric === test[2]; });
      var testStatus = statuses[test[0]] || {};
      var card = element("section", null, "dl-assistant-metric");
      card.appendChild(element("strong", test[1]));
      if (!metric || testStatus.status === "unsupported" || typeof metric.value !== "number" || !Number.isFinite(metric.value)) {
        var why = (metric && (metric.reason || metric.status)) || (testStatus.status === "unsupported" ? "unsupported / not measured" : goldfish.report_reason || "not reported");
        if (test[0] === "finish") why = "positive finish proof not reported; conditional proof is omitted";
        card.appendChild(element("span", "Unknown · " + why));
      } else {
        var isRate = ["proportion", "rate", "probability"].indexOf(String(metric.unit || "")) >= 0 && metric.value >= 0 && metric.value <= 1;
        var value = isRate ? (metric.value * 100).toFixed(1).replace(/\.0$/, "") + "%" : formatScalar(metric.value, metric.unit);
        var sample = metric.denominator != null ? metric.denominator : (metric.n || metric.sample_size);
        var numerator = metric.numerator != null ? metric.numerator : null;
        var sampleLabel = numerator != null && sample != null ? " · " + numerator + "/" + sample : sample != null ? " · n=" + sample : "";
        if (test[0] === "finish" && metric.value === 0) {
          card.appendChild(element("span", "No proof observed; other outcomes unknown" + sampleLabel));
        } else {
          card.appendChild(element("span", value + sampleLabel));
        }
        if (isRate && metric.ci95 && typeof metric.ci95.low === "number" && typeof metric.ci95.high === "number") card.appendChild(element("small", "95% interval · " + (metric.ci95.low * 100).toFixed(1) + "%–" + (metric.ci95.high * 100).toFixed(1) + "%"));
        if (isRate && testStatus.status !== "unsupported") {
          var track = element("div", null, "dl-assistant-meter"); track.setAttribute("role", "img"); track.setAttribute("aria-label", test[1] + " observed rate " + value);
          var fill = element("span"); fill.style.width = Math.max(0, Math.min(100, metric.value * 100)) + "%"; track.appendChild(fill); card.appendChild(track);
        }
      }
      grid.appendChild(card);
    });
    figure.appendChild(grid);
    var coverage = goldfish.coverage;
    if (coverage && typeof coverage === "object") {
      figure.appendChild(element("p", "Model coverage · " + ["modeled_cards", "total_cards", "inert_cards", "unauthored_cards"].map(function (key) { return key.replace(/_cards$/, "") + ": " + (coverage[key] == null ? "unknown" : coverage[key]); }).join(" · ")));
    }
    if (goldfish.report_note) figure.appendChild(element("small", goldfish.report_note));
    var notes = element("details"); notes.appendChild(element("summary", "Test coverage and limitations"));
    if (goldfish.coverage_note) notes.appendChild(element("p", goldfish.coverage_note));
    ((coverage && coverage.assumptions) || []).forEach(function (assumption) { notes.appendChild(element("p", assumption)); });
    (goldfish.test_statuses || []).forEach(function (item) { notes.appendChild(element("p", String(item.test || "Test").replace(/_/g, " ") + " · " + (item.status || "unknown") + ((item.limitations || []).length ? " · " + item.limitations.join("; ") : ""))); });
    figure.appendChild(notes);
    host.appendChild(figure);
  }
  function updateChartStaleness(figure) {
    var reportRevision = figure.dataset.reportRevision;
    var revisionMismatch = reportRevision !== "" && state.revision != null && Number(reportRevision) !== Number(state.revision);
    var stale = figure.dataset.reportStale === "true" || revisionMismatch;
    var note = figure.querySelector(".dl-assistant-stale-note");
    if (stale && !note) {
      note = element("p", null, "dl-assistant-stale-note");
      figure.insertBefore(note, figure.querySelector(".dl-assistant-metric-grid"));
    }
    if (note) {
      note.hidden = !stale;
      note.textContent = "This report is stale for the current deck revision" + (revisionMismatch ? " (report revision " + reportRevision + "; current revision " + state.revision + ")." : ".");
    }
  }
  function refreshChartStaleness() {
    sidebar.querySelectorAll(".dl-assistant-goldfish-chart").forEach(updateChartStaleness);
  }
  function appendSearchResults(article, message) {
    var action = message && message.action;
    if (!action || action.name !== "search_cards" || action.ok !== true) return;
    var cards = action.data && Array.isArray(action.data.cards) ? action.data.cards : null;
    if (!cards) return;
    if (!cards.length) {
      article.appendChild(element("p", "No matching cards were found."));
      return;
    }
    appendCardResults(article, cards);
  }
  function appendStructuredPresentation(article, message) {
    var presentation = message && message.presentation;
    if (!presentation || typeof presentation !== "object") return;
    appendCardResults(article, presentation.cards);
    if (presentation.goldfish) observationChart(article, presentation.goldfish);
  }
  function toolLabel(action) {
    var names = { inspect_deck: "Deck summary", search_cards: "Card catalog search", card_details: "Card details", latest_goldfish: "Latest Goldfish report", run_goldfish: "Goldfish run", preview_replacements: "Replacement preview", start_experiment: "Automated experiment" };
    return names[action && action.name] || "Assistant tool";
  }
  function renderToolStep(message) {
    var action = message.action || {};
    var step = element("div", null, "dl-assistant-tool-step");
    if (action.type === "tool_started") {
      step.appendChild(element("span", toolLabel(action) + " · working"));
      return step;
    }
    var failed = action.ok === false;
    var title = toolLabel(action) + (failed ? " · unavailable" : " · complete");
    if (action.name === "search_cards" && action.ok === true) {
      var cards = action.data && Array.isArray(action.data.cards) ? action.data.cards : [];
      title = "Card catalog search · " + cards.length + (cards.length === 1 ? " result" : " results");
    }
    step.appendChild(element("strong", title));
    if (failed && message.content) appendMarkdown(step, messageContent(message), cardsFrom(message));
    if (action.name === "search_cards" && action.ok === true) appendSearchResults(step, message);
    if (action.ok === true && action.data && Array.isArray(action.data.observations)) observationChart(step, action.data);
    if (action.ok === true && action.name === "start_experiment" && action.data && action.data.metric_summary) appendMetricSummary(step, action.data.metric_summary);
    return step;
  }
  function renderMessages() {
    if (!transcript) return;
    var messages = data.messages || [];
    var signature = JSON.stringify(messages);
    if (signature === renderedMessageSignature) return;
    var canAppend = renderedMessageSignature !== null && messages.length >= renderedMessageCount;
    for (var index = 0; canAppend && index < renderedMessageCount; index += 1) {
      if ((messages[index] && messages[index].id) !== (renderedMessageIds[index] || null)) canAppend = false;
    }
    var surface = scrollSurface || transcript;
    var nearBottom = surface.scrollHeight - surface.scrollTop - surface.clientHeight < 72;
    if (!canAppend) { transcript.replaceChildren(); renderedMessageCount = 0; renderedMessageIds = []; }
    var cursor = renderedMessageCount;
    while (cursor < messages.length) {
      var message = messages[cursor];
      var role = ["user", "assistant", "tool"].indexOf(message.role) >= 0 ? message.role : "tool";
      if (role === "tool") {
        var group = transcript.lastElementChild;
        var activity = group && group.classList.contains("dl-assistant-activity") ? group.querySelector("details") : null;
        if (!activity) {
          group = element("article", null, "dl-assistant-message dl-assistant-activity"); group.dataset.role = "tool";
          activity = document.createElement("details");
          activity.appendChild(element("summary", "Research activity · 0 steps"));
          group.appendChild(activity); transcript.appendChild(group);
        }
        var toolMessages = [];
        var end = cursor;
        while (end < messages.length && messages[end] && messages[end].role === "tool") { toolMessages.push(messages[end]); end += 1; }
        var count = Number(activity.dataset.stepCount || 0);
        var pending = Number(activity.dataset.pendingCount || 0);
        toolMessages.forEach(function (item) {
          activity.appendChild(renderToolStep(item)); count += 1;
          if (item.action && item.action.type === "tool_started") pending += 1;
          else if (item.action && item.action.type === "tool_result") pending = Math.max(0, pending - 1);
        });
        activity.dataset.stepCount = String(count); activity.dataset.pendingCount = String(pending);
        activity.querySelector("summary").textContent = "Research activity · " + count + (count === 1 ? " step" : " steps") + (pending ? " · working" : "");
        for (; cursor < end; cursor += 1) { renderedMessageIds.push(messages[cursor] && messages[cursor].id || null); renderedMessageCount += 1; }
        continue;
      }
      var article = element("article", null, "dl-assistant-message");
      article.dataset.role = role;
      article.setAttribute("aria-label", role === "assistant" ? "Research Assistant reply" : "Your message");
      appendReply(article, message);
      appendStructuredPresentation(article, message);
      transcript.appendChild(article);
      renderedMessageIds.push(message && message.id || null);
      renderedMessageCount += 1;
      cursor += 1;
    }
    renderedMessageSignature = signature;
    if (nearBottom) surface.scrollTop = surface.scrollHeight;
  }
  function appendMetricSummary(host, rows) {
    if (!Array.isArray(rows) || !rows.length) return;
    var figure = element("figure", null, "dl-assistant-experiment-chart");
    figure.appendChild(element("figcaption", "Measured training observations · descriptive only"));
    rows.slice(0, 5).forEach(function (row) {
      var item = element("div", null, "dl-assistant-experiment-metric");
      var label = [row.alternative_name, row.test, row.metric].filter(function (value) { return typeof value === "string" && value; }).join(" · ");
      item.appendChild(element("strong", label || "Observation"));
      var isRate = /(?:rate|probability)$/.test(String(row.metric || "")) || ["proportion", "rate", "probability"].indexOf(String(row.unit || "")) >= 0;
      function print(value) {
        if (typeof value !== "number" || !Number.isFinite(value)) return "unknown";
        return isRate && value >= 0 && value <= 1 ? (value * 100).toFixed(1).replace(/\.0$/, "") + "%" : formatScalar(value, "");
      }
      function printDelta(value) {
        if (typeof value !== "number" || !Number.isFinite(value)) return "unknown";
        if (isRate) return (value > 0 ? "+" : "") + (value * 100).toFixed(1).replace(/\.0$/, "") + " percentage points";
        return formatDelta(value, row.unit || "");
      }
      if (row.comparable && typeof row.before === "number" && typeof row.after === "number") {
        item.appendChild(element("span", print(row.before) + " → " + print(row.after) + (typeof row.delta === "number" ? " · Δ " + printDelta(row.delta) : "")));
        if (isRate) {
          [row.before, row.after].forEach(function (value, index) {
            var track = element("div", null, "dl-assistant-meter"); track.setAttribute("aria-hidden", "true");
            var fill = document.createElement("span"); fill.style.width = Math.max(0, Math.min(100, value * 100)) + "%"; track.appendChild(fill); item.appendChild(track);
          });
        }
      } else item.appendChild(element("span", "Unknown / suppressed" + (row.reason ? " · " + row.reason : "")));
      figure.appendChild(item);
    });
    host.appendChild(figure);
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
    var proposalData = (data.messages || []).map(function (message) { return message.proposals || []; });
    var signature = JSON.stringify(proposalData);
    if (signature === renderedProposalSignature) return;
    renderedProposalSignature = signature;
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
      try { ensureDeckSaved(); } catch (error) { status.textContent = error.message; button.disabled = false; return; }
      request(base + "/apply", { method: "POST", body: JSON.stringify({ replacements: replacements, expected_revision: Number(state.revision || 0) }) })
        .then(function (result) { status.textContent = "Selected swaps applied. Review the updated deck, then rerun Goldfish."; return acceptUpdatedDocument(result); })
        .catch(function (error) { status.textContent = error.message; button.disabled = false; });
    });
    proposalsNode.appendChild(button);
    button.disabled = true;
  }
  function readableReport(report) {
    if (!report) return "No report supplied.";
    return typeof report === "string" ? report : JSON.stringify(report, null, 2);
  }
  function formatScalar(value, unit) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "unknown";
    var rate = ["proportion", "rate", "probability"].indexOf(String(unit || "")) >= 0 && value >= 0 && value <= 1;
    var number = (rate ? value * 100 : value).toFixed(rate ? 1 : 2);
    if (number.indexOf(".") >= 0) number = number.replace(/0+$/, "").replace(/\.$/, "");
    return number + (rate ? "%" : (unit ? " " + unit : ""));
  }
  function formatDelta(value, unit) {
    var rate = ["proportion", "rate", "probability"].indexOf(String(unit || "")) >= 0 && Math.abs(value) <= 1;
    var number = (rate ? value * 100 : value).toFixed(rate ? 1 : 2);
    if (number.indexOf(".") >= 0) number = number.replace(/0+$/, "").replace(/\.$/, "");
    return (value > 0 ? "+" : "") + number + (rate ? " percentage points" : (unit ? " " + unit : ""));
  }
  function appendComparisonFigure(host, comparison, label) {
    var figure = element("figure", null, "dl-assistant-experiment-chart");
    figure.appendChild(element("figcaption", label + " observations · descriptive only; no overall winner is inferred"));
    var metrics = [];
    Object.keys(comparison && comparison.metric_deltas || {}).forEach(function (test) {
      Object.keys(comparison.metric_deltas[test] || {}).forEach(function (name) {
        metrics.push({ test: test, name: name, value: comparison.metric_deltas[test][name] });
      });
    });
    if (!comparison || comparison.comparable === false) figure.appendChild(element("p", "Unavailable or suppressed" + (comparison && comparison.reason ? " · " + comparison.reason : "")));
    metrics.slice(0, 5).forEach(function (item) {
      var metric = item.value || {};
      var row = element("div", null, "dl-assistant-experiment-metric");
      row.appendChild(element("strong", item.test.replace(/_/g, " ") + " · " + item.name.replace(/_/g, " ")));
      if (metric.comparable && typeof metric.before === "number" && typeof metric.after === "number") {
        row.appendChild(element("span", formatScalar(metric.before, metric.unit) + " → " + formatScalar(metric.after, metric.unit) + (typeof metric.delta === "number" ? " · Δ " + formatDelta(metric.delta, metric.unit) : "")));
        if (metric.validated === false) row.appendChild(element("small", "Descriptive only · " + (metric.unvalidated_reason || "validation requirements were not met")));
        if (metric.unit === "proportion" && metric.before >= 0 && metric.before <= 1 && metric.after >= 0 && metric.after <= 1) {
          [metric.before, metric.after].forEach(function (value) {
            var track = element("div", null, "dl-assistant-meter"); track.setAttribute("aria-hidden", "true");
            var fill = document.createElement("span"); fill.style.width = Math.max(0, Math.min(100, value * 100)) + "%"; track.appendChild(fill); row.appendChild(track);
          });
        }
      } else row.appendChild(element("span", "Unknown / suppressed" + (metric.reason ? " · " + metric.reason : "")));
      figure.appendChild(row);
    });
    if (!metrics.length) figure.appendChild(element("p", "No comparable scalar values were supplied."));
    host.appendChild(figure);
  }
  function renderExperiments() {
    var signature = JSON.stringify(data.experiments || []);
    if (signature === renderedExperimentSignature) return;
    var opened = Array.prototype.map.call(experimentsNode.querySelectorAll("details"), function (details, index) { return details.open ? index : -1; }).filter(function (index) { return index >= 0; });
    var focused = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.experimentFocus;
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
          appendComparisonFigure(section, alternative.validation.comparison, "Held-out");
          section.appendChild(element("p", "Independent validation · " + (alternative.validation.status || "descriptive") +
            (alternative.validation.reason ? " · " + alternative.validation.reason : "")));
        }
        if (alternative.comparison) appendComparisonFigure(section, alternative.comparison, "Training");
        var fullEvidence = element("details"); fullEvidence.dataset.experimentDetails = String(experiment.id) + "|" + String(alternative.id);
        fullEvidence.appendChild(element("summary", "Full experiment evidence"));
        fullEvidence.appendChild(element("pre", readableReport({
          training_report: alternative.training_report,
          comparison: alternative.comparison,
          validation: alternative.validation,
          validation_report: alternative.validation_report
        })));
        section.appendChild(fullEvidence);
        var adopt = element("button", "Adopt this alternative", "dl-button is-sm"); adopt.type = "button"; adopt.dataset.experimentFocus = String(experiment.id) + "|" + String(alternative.id);
        adopt.disabled = alternative.status === "unavailable" || experiment.status === "failed";
        adopt.addEventListener("click", function () {
          adopt.disabled = true; status.textContent = "Adopting selected alternative…";
          try { ensureDeckSaved(); } catch (error) { status.textContent = error.message; adopt.disabled = false; return; }
          request(base + "/experiments/" + encodeURIComponent(experiment.id) + "/adopt", {
            method: "POST", body: JSON.stringify({ alternative_id: alternative.id, expected_revision: Number(state.revision || 0) })
          }).then(function (updated) { status.textContent = "Alternative adopted. The update is in the deck history and can be undone."; return acceptUpdatedDocument(updated); })
            .catch(function (error) { status.textContent = error.message; adopt.disabled = false; });
        });
        section.appendChild(adopt); card.appendChild(section);
      });
      if (!experiment.alternatives || !experiment.alternatives.length) card.appendChild(element("p", "No alternatives are available yet."));
      experimentsNode.appendChild(card);
    });
    Array.prototype.forEach.call(experimentsNode.querySelectorAll("details"), function (details, index) { details.open = opened.indexOf(index) >= 0; });
    if (focused) Array.prototype.find.call(experimentsNode.querySelectorAll("[data-experiment-focus]"), function (button) { return button.dataset.experimentFocus === focused; })?.focus();
    renderedExperimentSignature = signature;
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
    refreshChartStaleness();
  });
  if (window.location.hash === "#goldfish") showTab("goldfish", false);
  refresh();
  window.addEventListener("pagehide", function () { if (pollTimer) window.clearTimeout(pollTimer); });
}());
