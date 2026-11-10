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
  var publicView = emptyPublicView();
  var publicClock = null;
  var activityNode = null;
  var publicFindingRuns = new Map();
  var historicalFindingLoads = new Map();
  var publicAnswerRuns = new Map();
  var activityTimer = null;
  var activityLabel = null;
  var activityDetails = null;
  var activityHistory = null;
  var activitySignature = "";
  var activityAnnounced = "";
  var timerTick = 0;
  var connectionLost = false;
  var pollFailures = 0;
  var operationalPollStarted = 0;
  var pageAlive = true;
  var busy = false;
  var sending = false;
  var pendingSubmission = null;
  var pendingSteering = new Map();
  var sessionEpoch = 0;
  var refreshSequence = 0;
  var sessionRequestSequence = 0;
  var acceptedSessionSequence = 0;
  var workspaceNode = null;
  var workspaceCards = new Map();
  var workspaceList, workspaceBadge, workspaceRequest, workspaceReceipt, workspaceRetry;
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
  // True while the reader is at the latest message; only their own scrolling changes it.
  var followingLatest = true;
  var progressShown = "";
  var stopRequested = false;
  var deferredScroll = false;
  var deferredReply = null;
  var preview = document.createElement("div");
  preview.className = "dl-assistant-card-preview";
  preview.setAttribute("role", "dialog");
  preview.setAttribute("aria-label", "Card preview");
  preview.hidden = true;
  document.body.appendChild(preview);
  if (scrollSurface || transcript) (scrollSurface || transcript).addEventListener("scroll", function () {
    var surface = scrollSurface || transcript;
    followingLatest = surface.scrollHeight - surface.scrollTop - surface.clientHeight < 72;
    updateJumpToLatest();
  }, { passive: true });
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
  // Public-event reducer deliberately contains no DOM or network operations.
  // A head snapshot is advisory until all pages/deltas have been applied.
  function emptyPublicView() {
    return { runId: null, sequence: 0, run: null, entities: {}, staging: null, operational: {}, history: {} };
  }
  function reducePublicEvents(previous, projection) {
    if (!projection || projection.contract_version !== "assistant.events.v1") return { view: emptyPublicView(), legacy: true };
    var head = projection.run || {};
    if (!head.run_id) return { view: emptyPublicView(), legacy: true };
    var cursor = projection.cursor || {};
    if (projection.mode === "reset_required") return { view: emptyPublicView(), reset: true };
    var view = previous && previous.runId === head.run_id ? previous : emptyPublicView();
    view = Object.assign({}, view, { runId: head.run_id, operational: projection.operational || {}, history: projection.history || {} });
    if (projection.mode === "snapshot") {
      var page = projection.snapshot_page || {};
      if (!Number.isInteger(page.page_index) || !Number.isInteger(page.page_count) || page.page_index < 0 || page.page_count < 1 || page.page_count > 64 || page.page_index >= page.page_count || !Number.isSafeInteger(page.through_sequence) || page.through_sequence < 0 || !page.token) return { view: emptyPublicView(), reset: true };
      var staging = view.staging;
      if (!staging || staging.token !== page.token) {
        if (page.page_index !== 0) return { view: emptyPublicView(), reset: true };
        staging = { token: page.token, through: page.through_sequence, count: page.page_count, pages: [], bytes: 0, entities: {} };
      }
      if (staging.count !== page.page_count || staging.through !== page.through_sequence) return { view: emptyPublicView(), reset: true };
      if (page.page_index !== staging.pages.length) return { view: emptyPublicView(), reset: true };
      var bytes = new TextEncoder().encode(JSON.stringify(page)).length;
      if (bytes > 128 * 1024 || staging.bytes + bytes > 8 * 1024 * 1024) return { view: emptyPublicView(), reset: true };
      staging.bytes += bytes; staging.pages.push(page.page_index);
      var invalid = false;
      Object.keys(page.entities || {}).forEach(function (collection) {
        if (["answer_blocks", "findings", "claims", "citations", "activity", "steering", "obligations"].indexOf(collection) < 0 || !Array.isArray(page.entities[collection])) { invalid = true; return; }
        var records = staging.entities[collection] || (staging.entities[collection] = {});
        page.entities[collection].forEach(function (record) {
          if (!record || !record.id || record.run_id !== head.run_id) { invalid = true; return; }
          records[record.id] = record;
        });
      });
      if (invalid) return { view: emptyPublicView(), reset: true };
      view.staging = staging;
      if (staging.pages.length < staging.count) return { view: view, page: { token: staging.token, index: staging.pages.length } };
      view.entities = staging.entities; view.sequence = staging.through; view.staging = null;
    } else if (projection.mode === "delta") {
      if (!view.runId || !previous || previous.runId !== head.run_id || cursor.after_sequence !== view.sequence) return { view: emptyPublicView(), reset: true };
      var entities = Object.assign({}, view.entities);
      var sequence = view.sequence;
      var valid = true;
      (projection.events || []).forEach(function (event) {
        if (event.run_id !== head.run_id || event.generation !== head.generation) { valid = false; return; }
        if (event.sequence <= sequence) return; // replay is harmless
        if (event.sequence !== sequence + 1 || ["run.accepted", "run.started", "run.ended", "activity.started", "activity.finished", "finding.upserted", "finding.withdrawn", "answer.block.upserted", "claim.updated", "steering.received", "steering.applied", "steering.rejected", "stop.requested", "obligation.upserted", "obligations.refined"].indexOf(event.kind) < 0) { valid = false; return; }
        var payload = event.payload || {};
        if (payload.collection) {
          if (["answer_blocks", "findings", "claims", "citations", "activity", "steering", "obligations"].indexOf(payload.collection) < 0 || !payload.entity || !payload.entity.id || payload.entity.run_id !== head.run_id) { valid = false; return; }
          entities[payload.collection] = Object.assign({}, entities[payload.collection] || {});
          entities[payload.collection][payload.entity.id] = payload.entity;
        }
        sequence = event.sequence;
      });
      if (!valid || sequence !== cursor.through_sequence) return { view: emptyPublicView(), reset: true };
      view.entities = entities; view.sequence = sequence;
    } else return { view: emptyPublicView(), reset: true };
    if (!Number.isSafeInteger(head.last_sequence) || view.sequence > head.last_sequence) return { view: emptyPublicView(), reset: true };
    // Never announce terminal status before final artifacts reach the browser.
    if (view.sequence === head.last_sequence) view.run = head;
    return { view: view, more: view.sequence < head.last_sequence };
  }
  function expectedTarget(forStop) {
    var run = publicView.run;
    return { expected_run_id: run && run.run_id || data.turn_id || null,
      expected_generation: Number(run && run.generation || researchWorkspace().generation || 0),
      expected_revision: Number(forStop && run ? run.deck_revision : forStop && data.revision !== undefined ? data.revision : state.revision || 0) };
  }
  function elapsedLabel(run, clock, tick) {
    if (!run || !clock || (["completed", "failed", "interrupted", "stopped", "superseded"].indexOf(run.lifecycle) >= 0 && !run.ended_at)) return "Timing unavailable";
    var start = Date.parse(run.budget_started_at || run.accepted_at || "");
    var end = run.ended_at ? Date.parse(run.ended_at) : clock.server + Math.max(0, tick - clock.tick);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "Timing unavailable";
    var seconds = Math.floor((end - start) / 1000);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0") + (run.budget_started_at ? " elapsed" : " attempt elapsed");
  }
  function terminalLabel(run) {
    if (run.lifecycle === "completed") return run.legacy_fulfillment === "nonanswer" ? "No substantive answer" : run.fulfillment === "complete" ? "Answer ready" : "Answer incomplete";
    return ({ failed: "Failed", interrupted: "Interrupted", stopped: "Stopped · published findings retained", superseded: "Superseded", queued: "Queued", running: "Working on your request" })[run.lifecycle] || "Research state unavailable";
  }
  function completePublicAnswers(view) {
    var run = view && view.run;
    if (!run || ["completed", "failed", "interrupted", "stopped", "superseded"].indexOf(run.lifecycle) < 0) return [];
    var groups = new Map();
    Object.values(view.entities && view.entities.answer_blocks || {}).forEach(function (block) {
      if (!block || block.run_id !== run.run_id || typeof block.answer_id !== "string" || !block.answer_id || !Number.isSafeInteger(block.answer_version) || block.answer_version < 1) return;
      var group = groups.get(block.answer_id);
      if (!group || block.answer_version > group.version) {
        group = { id: block.answer_id, run_id: run.run_id, version: block.answer_version, hash: block.logical_text_hash, count: block.segment_count, segments: new Map(), invalid: false };
        groups.set(block.answer_id, group);
      }
      if (block.answer_version !== group.version) return;
      if (typeof block.logical_text_hash !== "string" || !/^[0-9a-f]{64}$/.test(block.logical_text_hash) || block.logical_text_hash !== group.hash || !Number.isSafeInteger(block.segment_count) || block.segment_count < 1 || block.segment_count > 100 || block.segment_count !== group.count || !Number.isSafeInteger(block.segment_index) || block.segment_index < 0 || block.segment_index >= group.count || typeof block.text !== "string" || group.segments.has(block.segment_index)) { group.invalid = true; return; }
      group.segments.set(block.segment_index, block);
    });
    var answers = [];
    groups.forEach(function (group) {
      if (group.invalid || group.segments.size !== group.count) return;
      var parts = [], reviewed = true;
      for (var index = 0; index < group.count; index += 1) {
        var block = group.segments.get(index); if (!block) return;
        parts.push(block.text); if (block.review_state !== "reviewed") reviewed = false;
      }
      var text = parts.join("");
      if (!text.trim() || new TextEncoder().encode(text).length > 1024 * 1024) return;
      answers.push({ id: group.id, run_id: group.run_id, version: group.version, hash: group.hash, text: text, reviewed: reviewed, anchor_id: "public-answer:" + group.run_id + ":" + group.id });
    });
    return answers;
  }
  function legacyAnswerMatches(message, answer) {
    return !!message && message.role === "assistant" && message.run_id === answer.run_id && typeof message.content === "string" && message.content === answer.text;
  }
  function showError(message) {
    var alert = sidebar.querySelector("[data-assistant-alert]");
    if (alert && alert.textContent !== message) alert.textContent = message;
  }
  function clearError() { var alert = sidebar.querySelector("[data-assistant-alert]"); if (alert) alert.textContent = ""; }
  function updateJumpToLatest() {
    var jump = sidebar.querySelector("[data-assistant-jump]");
    if (jump) jump.hidden = followingLatest;
  }
  function renderReadableEvidence(host, evidence) {
    if (!evidence) return;
    var entries = Array.isArray(evidence) ? evidence : [evidence];
    entries.slice(0, 12).forEach(function (source) {
      if (typeof source === "string") { host.appendChild(element("p", source)); return; }
      if (!source || typeof source !== "object") return;
      var title = source.title || source.source_kind || source.kind || source.source_type || source.card_name;
      if (title) host.appendChild(element("strong", title));
      var quote = source.relevant_excerpt || source.quote || source.excerpt || source.oracle_text || source.text;
      if (typeof quote === "string") host.appendChild(element("blockquote", quote));
      var version = source.source_version || source.version || source.source_date;
      if (version) host.appendChild(element("small", "Captured source: " + version));
      if (source.truncated || source.truncation) host.appendChild(element("small", "Excerpt is incomplete; inspect the full source before relying on omitted conditions."));
      var url = safeSourceUrl(source.source_url || source.url);
      if (url) { var link = element("a", "Open source"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; host.appendChild(link); }
      else host.appendChild(element("small", "No public source link recorded."));
    });
    var raw = element("details"); raw.appendChild(element("summary", "Raw evidence"));
    raw.appendChild(element("pre", JSON.stringify(evidence, null, 2))); host.appendChild(raw);
  }
  function safeSourceUrl(value) {
    try { var url = new URL(value); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; } catch (_) { return null; }
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
    options.headers = Object.assign({ "Accept": "application/json", "X-Assistant-Contract": "assistant.events.v1" }, options.headers || {});
    if (options.body) {
      options.headers["Content-Type"] = "application/json";
      options.headers["X-CSRFToken"] = csrf();
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var error = new Error(body.message || body.detail || body.error || "Research Assistant request failed.");
          error.retryNewSubmission = body.retry_new_submission === true;
          error.httpStatus = response.status;
          throw error;
        }
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
    settleDeferredScroll();
    try { localStorage.setItem("deck-lab-assistant-tab:" + state.id, name); } catch (_) {}
  }
  function settleDeferredScroll() {
    var surface = scrollSurface || transcript;
    if (hasWorkspaceCards()) { deferredScroll = false; deferredReply = null; return; }
    if (!deferredScroll || !surface || !surface.clientHeight) return;
    deferredScroll = false;
    scrollTranscript(surface, deferredReply === transcript.lastElementChild ? deferredReply : null, null);
    deferredReply = null;
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
  // The header button toggles the drawer on mobile and the collapsible rail on desktop,
  // so a collapsed sidebar can always be reopened from the header.
  var mobileOpen = document.querySelector("[data-assistant-open]");
  var close = sidebar.querySelector("[data-assistant-close]");
  var railToggle = sidebar.querySelector('[data-toggle-rail="left"]');
  var builder = document.querySelector(".dl-builder");
  var drawerQuery = window.matchMedia ? window.matchMedia("(max-width: 767px)") : null;
  function isDrawer() { return !!(drawerQuery && drawerQuery.matches); }
  function syncOpenButton() {
    if (!mobileOpen) return;
    var open = isDrawer() ? sidebar.classList.contains("is-open") : !(builder && builder.classList.contains("left-collapsed"));
    mobileOpen.setAttribute("aria-expanded", open ? "true" : "false");
    mobileOpen.setAttribute("aria-label", (open ? "Close" : "Open") + " Research Assistant");
  }
  function mobile(value) {
    sidebar.classList.toggle("is-open", value);
    document.body.classList.toggle("dl-assistant-drawer-open", value);
    syncOpenButton();
    if (value) settleDeferredScroll();
    if (value && tabs[0]) tabs[0].focus();
    if (!value && mobileOpen) mobileOpen.focus();
  }
  if (mobileOpen) mobileOpen.addEventListener("click", function () {
    if (isDrawer()) mobile(!sidebar.classList.contains("is-open"));
    else if (railToggle) railToggle.click();
  });
  if (close) close.addEventListener("click", function () { if (isDrawer()) mobile(false); });
  if (builder && window.MutationObserver) new MutationObserver(function () { syncOpenButton(); settleDeferredScroll(); }).observe(builder, { attributes: true, attributeFilter: ["class"] });
  if (drawerQuery && drawerQuery.addEventListener) drawerQuery.addEventListener("change", syncOpenButton);
  syncOpenButton();
  document.addEventListener("keydown", function (event) { if (event.key === "Escape" && sidebar.classList.contains("is-open")) mobile(false); });

  // Pointer and keyboard resizing share a persisted width; the grip remains reachable.
  var grip = sidebar.querySelector("[data-assistant-resize]");
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

  function researchWorkspace() { return data.research_workspace || {}; }
  function hasWorkspaceCards() { return Array.isArray(researchWorkspace().cards) && researchWorkspace().cards.length > 0; }
  function researchRunning() {
    var workspace = researchWorkspace();
    return ["queued", "running"].indexOf(workspace.job && workspace.job.state || workspace.status) >= 0;
  }
  function steerPayload(action, fields) {
    return Object.assign({ expected_revision: Number(state.revision || 0), expected_generation: Number(researchWorkspace().generation || 0),
      expected_run_id: expectedTarget(false).expected_run_id, action: action, client_request_id: window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "steer-" + Date.now() + "-" + Math.random().toString(36).slice(2) }, fields || {});
  }
  function acceptSession(next, sequence) {
    var incoming = next && next.session || next || {};
    var generation = Number(incoming.research_workspace && incoming.research_workspace.generation || 0);
    var currentGeneration = Number(researchWorkspace().generation || 0);
    if (generation < currentGeneration) return false;
    if (generation === currentGeneration) {
      var updated = Number(incoming.updated_at || 0), currentUpdated = Number(data.updated_at || 0);
      if (updated < currentUpdated) return false;
      // Server snapshot order wins when available; request order breaks ties.
      if (updated === currentUpdated && sequence < acceptedSessionSequence) return false;
    }
    sessionEpoch += 1;
    acceptedSessionSequence = Math.max(acceptedSessionSequence, sequence);
    if (publicView.runId && incoming.turn_id && publicView.runId !== incoming.turn_id) { publicView = emptyPublicView(); publicClock = null; }
    data = Object.assign({}, data, incoming);
    connectionLost = false; pollFailures = 0;
    clearError();
    render();
    return true;
  }
  function acceptMutation(next, sequence) {
    if (!acceptSession(next, sequence)) throw new Error("Research changed before this acknowledgment arrived. Your draft has been kept; review the current research before trying again.");
  }
  function steer(action, fields) {
    fields = fields || {};
    // A repeated control click after an uncertain response must replay the
    // original identity AND target, even if a poll found its replacement run.
    var semantic = {};
    Object.keys(fields).sort().forEach(function (key) {
      if (key.indexOf("expected_") !== 0) semantic[key] = fields[key];
    });
    var key = JSON.stringify([action, semantic]);
    var pending = pendingSteering.get(key);
    if (!pending) {
      if (pendingSteering.size >= 100) return Promise.reject(new Error("Resolve earlier research updates before sending more changes."));
      pending = { body: JSON.stringify(steerPayload(action, fields)), promise: null };
      pendingSteering.set(key, pending);
    }
    if (pending.promise) return pending.promise;
    var sequence = ++sessionRequestSequence;
    pending.promise = request(base + "/steer", { method: "POST", body: pending.body })
      .then(function (next) {
        acceptMutation(next, sequence);
        pendingSteering.delete(key);
        status.textContent = action === "pin" || action === "unpin" ? "Finding display updated." : action === "retry" ? "New research attempt accepted." : "Research restarted with your instruction using the remaining time and allowance.";
        progressShown = status.textContent; return refresh();
      })
      .catch(function (error) {
        if (error.retryNewSubmission || (error.httpStatus >= 400 && error.httpStatus < 500 && error.httpStatus !== 408)) pendingSteering.delete(key);
        showError(error.message); throw error;
      }).finally(function () { pending.promise = null; });
    return pending.promise;
  }
  function workspaceDisclosure(title) {
    var details = element("details"); details.appendChild(element("summary", title));
    var body = element("div"); details.appendChild(body);
    return { node: details, body: body };
  }
  function updateWorkspaceBody(part, value, build) {
    var signature = JSON.stringify(value);
    if (part.signature === signature) return;
    part.signature = signature; part.body.replaceChildren(); build(part.body);
  }
  function createWorkspaceCard(id) {
    var node = element("article", null, "dl-research-card"); node.dataset.researchCardId = id;
    var name = element("h4"); node.appendChild(name);
    var label = element("p", null, "dl-research-card-status"); node.appendChild(label);
    var printed = workspaceDisclosure("Printed card facts · deck fit unverified"); node.appendChild(printed.node);
    var claims = element("div", null, "dl-research-claims"); node.appendChild(claims);
    var history = workspaceDisclosure("Corrections and history"); node.appendChild(history.node);
    var controls = element("div", null, "dl-research-card-controls"); node.appendChild(controls);
    var record = { node: node, name: name, label: label, printed: printed, claims: claims, claimNodes: new Map(), history: history, buttons: {} };
    ["pin", "exclude", "prioritize"].forEach(function (action) {
      var button = element("button", "", "dl-button is-ghost is-sm"); button.type = "button";
      button.addEventListener("click", function () {
        button.disabled = true;
        steer(action === "pin" && record.card.pinned ? "unpin" : action, { card_id: id })
          .catch(function () {}).finally(function () { button.disabled = false; });
      });
      controls.appendChild(button); record.buttons[action] = button;
    });
    return record;
  }
  function updateWorkspaceCard(record, card, workspace) {
    record.card = card;
    if (record.name.textContent !== (card.name || card.card_id)) record.name.textContent = card.name || card.card_id;
    var labels = { retrieved: "Retrieved · fit unverified", reviewed: "Reviewed claims · see individual evidence", withdrawn: "Withdrawn · do not rely on earlier findings", superseded: "Superseded · earlier findings are out of date" };
    record.node.dataset.cardStatus = card.status || "retrieved";
    var label = (labels[card.status] || labels.retrieved) + " · version " + Number(card.version || 1) + (card.pinned ? " · pinned" : "") + (card.excluded ? " · excluded" : "");
    if (record.label.textContent !== label) record.label.textContent = label;
    var printed = card.printed || {};
    updateWorkspaceBody(record.printed, printed, function (body) {
      function faceText(face) {
        if (face.name) body.appendChild(element("strong", face.name));
        [face.mana_cost, face.type_line, face.oracle_text].forEach(function (text) { if (typeof text === "string" && text) body.appendChild(element("p", text)); });
        if (face.oracle_text_truncated) body.appendChild(element("small", "Printed text is incomplete; retrieve full text before relying on omitted rules."));
      }
      faceText(printed); (printed.card_faces || []).forEach(faceText);
      if (!printed.oracle_text && !(printed.card_faces || []).length) body.appendChild(element("small", "Printed rules text has not been retrieved."));
    });
    var ids = new Set();
    (card.claims || []).forEach(function (claim, index) {
      var id = String(claim.claim_id || "claim-" + index); ids.add(id);
      var part = record.claimNodes.get(id);
      if (!part) { part = workspaceDisclosure("Claim evidence"); record.claimNodes.set(id, part); record.claims.appendChild(part.node); }
      var claimStatus = ["supported", "unverified", "contradicted", "withdrawn", "superseded"].indexOf(claim.status) >= 0 ? claim.status : "unverified";
      var title = claimStatus.charAt(0).toUpperCase() + claimStatus.slice(1) + ": " + (claim.text || "Claim");
      if (part.node.firstChild.textContent !== title) part.node.firstChild.textContent = title;
      part.node.dataset.claimStatus = claimStatus;
      updateWorkspaceBody(part, claim, function (body) {
        body.appendChild(element("p", claimStatus === "supported" ? "Supported by supplied evidence; this does not establish a recommendation." : claimStatus === "corrected" ? "The original statement was contradicted. This is the reviewed correction; inspect its evidence." : claimStatus === "contradicted" ? "This claim conflicts with supplied evidence. Do not rely on it." : claimStatus === "withdrawn" || claimStatus === "superseded" ? "This finding is no longer current. Do not rely on its previous assessment." : "This claim has not been established."));
        if (claim.correction_applied && claim.original_text) body.appendChild(element("p", "Original statement: " + claim.original_text));
        if (claim.reason) body.appendChild(element("p", claim.reason));
        if (claim.evidence) renderReadableEvidence(body, claim.evidence);
      });
    });
    record.claimNodes.forEach(function (part, id) { if (!ids.has(id)) { part.node.remove(); record.claimNodes.delete(id); } });
    record.history.node.hidden = !(card.history || []).length;
    var latestHistory = (card.history || []).slice(-1)[0];
    record.history.node.firstChild.textContent = latestHistory ? "Corrections and history · " + (latestHistory.reason || "Version " + latestHistory.version) : "Corrections and history";
    updateWorkspaceBody(record.history, card.history || [], function (body) {
      (card.history || []).forEach(function (entry) {
        body.appendChild(element("p", "Version " + entry.version + ": " + (entry.reason || "Updated")));
        (entry.claims || []).forEach(function (claim) {
          body.appendChild(element("p", "Previous finding (" + (claim.previous_status || claim.status || "unverified") + "): " + (claim.text || "")));
          if (claim.evidence) renderReadableEvidence(body, claim.evidence);
        });
      });
    });
    record.buttons.pin.textContent = card.pinned ? "Unpin" : "Pin";
    record.buttons.pin.setAttribute("aria-pressed", card.pinned ? "true" : "false");
    record.buttons.exclude.textContent = card.excluded ? "Excluded" : "Exclude";
    record.buttons.exclude.setAttribute("aria-pressed", card.excluded ? "true" : "false");
    var prioritized = (workspace.steering && workspace.steering.prioritized_card_ids || []).map(String).indexOf(String(card.card_id)) >= 0;
    record.buttons.prioritize.textContent = prioritized ? "Prioritized" : "Prioritize";
    record.buttons.prioritize.setAttribute("aria-pressed", prioritized ? "true" : "false");
  }
  function renderWorkspace() {
    var workspace = researchWorkspace();
    var surface = scrollSurface || transcript;
    var anchor = null;
    var anchorTop = 0;
    if (workspaceList && surface && surface.clientHeight) {
      var top = surface.getBoundingClientRect().top;
      anchor = Array.prototype.find.call(workspaceList.children, function (node) { return node.getBoundingClientRect().bottom > top; });
      if (anchor) anchorTop = anchor.getBoundingClientRect().top;
    }
    if (!workspaceNode && !data.research_workspace) return;
    if (!workspaceNode) {
      workspaceNode = element("section", null, "dl-research-workspace"); workspaceNode.setAttribute("aria-label", "Research workspace");
      workspaceNode.appendChild(element("h3", "Research"));
      workspaceBadge = element("p", "", "dl-research-badge");  workspaceNode.appendChild(workspaceBadge);
      var receipt = workspaceDisclosure("Request receipt · edit request"); workspaceNode.appendChild(receipt.node);
      var requestLabel = element("label", "Captured request");
      workspaceRequest = element("textarea"); workspaceRequest.rows = 3; requestLabel.appendChild(workspaceRequest); receipt.body.appendChild(requestLabel);
      workspaceReceipt = element("p"); receipt.body.appendChild(workspaceReceipt);
      var edit = element("button", "Restart with this instruction", "dl-button is-sm"); edit.type = "button";
      edit.addEventListener("click", function () { if (!workspaceRequest.value.trim()) return; edit.disabled = true; steer("message", { message: workspaceRequest.value.trim() }).catch(function () {}).finally(function () { edit.disabled = false; }); }); receipt.body.appendChild(edit);
      workspaceRetry = element("button", "Retry research", "dl-button is-sm"); workspaceRetry.type = "button";
      workspaceRetry.addEventListener("click", function () { workspaceRetry.disabled = true; steer("retry").catch(function () {}).finally(function () { workspaceRetry.disabled = false; }); }); workspaceNode.appendChild(workspaceRetry);
      workspaceList = element("div", null, "dl-research-cards"); workspaceNode.appendChild(workspaceList);
      transcript.parentNode.insertBefore(workspaceNode, transcript);
    }
    workspaceNode.hidden = !data.research_workspace;
    if (document.activeElement !== workspaceRequest && (workspaceRequest.dataset.captured === undefined || workspaceRequest.value === workspaceRequest.dataset.captured) && workspaceRequest.dataset.captured !== String(workspace.request_text || "")) {
      workspaceRequest.value = workspace.request_text || ""; workspaceRequest.dataset.captured = String(workspace.request_text || "");
    }
    var constraints = data.constraints || {};
    var captured = Object.keys(constraints).filter(function (key) { var value = constraints[key]; return value !== null && value !== "" && value !== undefined && (!Array.isArray(value) || value.length); });
    workspaceReceipt.textContent = captured.length ? "Captured settings: " + captured.map(function (key) { return key.replace(/_/g, " ") + ": " + (Array.isArray(constraints[key]) ? constraints[key].join(", ") : constraints[key]); }).join(" · ") : "No additional settings captured.";
    var job = workspace.job || {};
    var jobState = job.state === "completed" && workspace.status === "partial" ? "partial" : job.state || workspace.status || "unknown";
    var labels = { queued: "Research queued", running: "Research running", completed: "Research completed", partial: "Research finished · some requirements or claims remain unresolved", stopped: "Research stopped · retrieved cards retained", interrupted: "Research interrupted · no active work confirmed", superseded: "Research superseded" };
    var badge = (labels[jobState] || "Research state unavailable") + (workspace.archived_omitted ? " · " + workspace.archived_omitted + " older artifacts outside the retained history limit" : "");
    if (workspaceBadge.textContent !== badge) workspaceBadge.textContent = badge;
    workspaceRetry.hidden = !job.retry_allowed;
    var seen = new Set();
    (workspace.cards || []).forEach(function (card) {
      if (!card || !card.card_id) return;
      var id = String(card.card_id); seen.add(id);
      var record = workspaceCards.get(id);
      if (!record) { record = createWorkspaceCard(id); workspaceCards.set(id, record); workspaceList.appendChild(record.node); }
      updateWorkspaceCard(record, card, workspace);
    });
    workspaceCards.forEach(function (record, id) {
      if (!seen.has(id)) { record.node.remove(); workspaceCards.delete(id); }
    });
    if (anchor && anchor.parentNode === workspaceList) {
      var delta = anchor.getBoundingClientRect().top - anchorTop;
      if (Math.abs(delta) > 1) surface.scrollTop += delta;
    }
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
    var objective = sidebar.querySelector("[data-assistant-objective]");
    if (budget && document.activeElement !== budget) budget.value = constraints.budget == null ? "" : constraints.budget;
    if (power && document.activeElement !== power) power.value = constraints.power_level || "";
    if (theme && document.activeElement !== theme) theme.value = constraints.theme || "";
    if (objective && document.activeElement !== objective) objective.value = constraints.objective || "";
    if (!protectedDirty) protectedCards();
    var provider = sidebar.querySelector("[data-assistant-provider]");
    if (provider) {
      var quota = data.cost_control || {};
      provider.hidden = false;
      provider.textContent = quota.configured ? ("Daily research remaining: $" + (quota.remaining_micros / 1000000).toFixed(2) + " · " + quota.remaining_turns + " turns. Resets " + quota.daily_reset_at + (quota.kill_switch || (data.provider && data.provider.enabled === false) ? ". Paid AI unavailable; browsing and deck editing remain available." : "")) : (quota.message || "Paid research unavailable. Browsing and deck editing remain available.");
    }
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
  function validCards(cards) {
    return Array.isArray(cards) ? cards.filter(function (card) { return card && typeof card.name === "string" && cardId(card); }) : [];
  }
  function appendCardResults(article, cards, visible) {
    // Show a few rows; the rest stay one click away instead of burying the reply.
    var valid = validCards(cards).slice(0, 12);
    if (!valid.length) return;
    var limit = visible || 4;
    var list = cardResultList(valid.slice(0, limit), "Card search results");
    article.appendChild(list);
    if (valid.length > limit) {
      var more = document.createElement("details"); more.className = "dl-assistant-card-more";
      more.appendChild(element("summary", "Show " + (valid.length - limit) + " more"));
      more.appendChild(cardResultList(valid.slice(limit), "More card search results"));
      article.appendChild(more);
    }
  }
  function cardResultList(cards, label) {
    var list = element("ul", null, "dl-assistant-card-results");
    list.setAttribute("aria-label", label);
    cards.forEach(function (card) {
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
    return list;
  }
  function appendCardTray(article, message) {
    // Card names in the reply already open previews, so the gallery starts closed.
    var cards = validCards(message.presentation.cards);
    if (!cards.length) return;
    var tray = document.createElement("details"); tray.className = "dl-assistant-card-tray";
    tray.dataset.disclosureKey = "cards:" + (message.id || "");
    var shown = Math.min(cards.length, 12);
    tray.appendChild(element("summary", "Cards in this answer · " + cards.length));
    appendCardResults(tray, cards, 12);
    if (cards.length > shown) tray.appendChild(element("small", "Showing " + shown + " of " + cards.length + ". Card names in the answer also open previews."));
    article.appendChild(tray);
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
    figure.dataset.reportId = goldfish.report_id || "";
    figure.dataset.reportFreshness = goldfish.freshness || "unknown";
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
      window.DeckLabCoverage.render(figure, coverage);
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
    var freshness = figure.dataset.reportFreshness || "unknown";
    var note = figure.querySelector(".dl-assistant-stale-note");
    if (!note) {
      note = element("p", null, "dl-assistant-stale-note");
      figure.insertBefore(note, figure.querySelector(".dl-assistant-metric-grid"));
    }
    note.hidden = freshness === "current";
    note.textContent = freshness === "stale" ? "This report measures a previous deck list." : "Report freshness has not been verified for the current deck. Recorded settings still apply.";
  }
  var freshnessRequest = 0;
  function refreshChartStaleness() {
    var sequence = ++freshnessRequest;
    sidebar.querySelectorAll(".dl-assistant-goldfish-chart").forEach(function (figure) {
      figure.dataset.reportFreshness = "unknown"; updateChartStaleness(figure);
    });
    if (!sidebar.querySelector(".dl-assistant-goldfish-chart")) return;
    request("/api/decks/" + deckId + "/simulations/latest").then(function (view) {
      if (sequence !== freshnessRequest) return;
      sidebar.querySelectorAll(".dl-assistant-goldfish-chart").forEach(function (figure) {
        figure.dataset.reportFreshness = view.id && figure.dataset.reportId === String(view.id) ? (view.freshness || "unknown") : "unknown";
        updateChartStaleness(figure);
      });
    }).catch(function () { /* Keep unknown when the authoritative check fails. */ });
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
  function appendStrategyEvidence(host, evidence) {
    if (!evidence || typeof evidence !== "object") return;
    var section = element("section", null, "dl-assistant-strategy-evidence");
    section.setAttribute("aria-label", "Strategy sources");
    section.appendChild(element("strong", "Strategy sources"));
    if (evidence.presentation_omitted_sources) section.appendChild(element("small", "Additional sources are available in Research activity."));
    if (evidence.corpus_source === "bundled_authored_guidance") section.appendChild(element("p", "No external strategy text is indexed yet. These are Deck Lab’s authored archetype notes."));
    var excerpts = Array.isArray(evidence.excerpts) ? evidence.excerpts.slice(0, 4) : [];
    if (!excerpts.length) {
      var statuses = { empty_corpus: "No strategy documents have been indexed yet.", no_matches: "No matching strategy evidence was found.", unavailable: "Strategy evidence is unavailable." };
      section.appendChild(element("p", statuses[evidence.status] || "No strategy excerpts were returned."));
    }
    excerpts.forEach(function (item) {
      if (!item || typeof item.excerpt !== "string" || !item.citation) return;
      var citation = item.citation;
      var label = String(citation.title || citation.document || "Indexed document").slice(0, 500);
      if (citation.section && !citation.title) label += " · " + String(citation.section).slice(0, 200);
      var details = document.createElement("details");
      var summary = element("summary", label);
      details.appendChild(summary);
      if (item.source_kind === "authored_guidance") details.appendChild(element("small", "Authored archetype guidance · not a retrieved primer"));
      if (item.source_kind === "external_combo_claim") details.appendChild(element("small", "Sourced combination claim · execution and prerequisites not verified"));
      if (item.source_kind === "wiki_secondary") details.appendChild(element("small", "Community wiki explanation · defer to official card and rules evidence"));
      var attribution = item.source_attribution || {};
      if (attribution.author) details.appendChild(element("small", String(attribution.author).slice(0, 500)));
      var sourceVersion = citation.version || (citation.provenance && citation.provenance.version);
      if (sourceVersion) details.appendChild(element("small", "Source version: " + String(sourceVersion).slice(0, 120)));
      if (attribution.source_date) details.appendChild(element("small", "Source date: " + String(attribution.source_date).slice(0, 100)));
      if (attribution.review_status) details.appendChild(element("small", String(attribution.review_status).replace(/_/g, " ").slice(0, 400)));
      [["Reuse license", attribution.license_url], ["Attribution and history", attribution.attribution_url]].forEach(function (entry) {
        try {
          var provenanceUrl = new URL(entry[1]);
          if ((provenanceUrl.protocol !== "https:" && provenanceUrl.protocol !== "http:") || provenanceUrl.username || provenanceUrl.password) return;
          var provenanceLink = element("a", entry[0]);
          provenanceLink.href = provenanceUrl.href; provenanceLink.target = "_blank"; provenanceLink.rel = "noopener noreferrer";
          details.appendChild(provenanceLink);
        } catch (_) {}
      });
      var url = null;
      try {
        var parsed = new URL(citation.source_url);
        if ((parsed.protocol === "https:" || parsed.protocol === "http:") && !parsed.username && !parsed.password) url = parsed.href;
      } catch (_) {}
      if (url) {
        var link = element("a", "Open source"); link.href = url;
        link.target = "_blank"; link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", "Open source: " + label);
        details.appendChild(link);
      } else if (item.source_kind !== "authored_guidance") details.appendChild(element("small", "Indexed document; no public source link recorded."));
      details.appendChild(element("blockquote", item.excerpt.slice(0, 600)));
      if (item.excerpt_truncated || item.excerpt.length > 600) details.appendChild(element("small", "Excerpt shortened; this is not the complete source."));
      if (citation.metadata_truncated) details.appendChild(element("small", "Source label shortened."));
      section.appendChild(details);
    });
    if (evidence.retrieval_mode === "lexical") section.appendChild(element("small", "Matched by text relevance; sources are evidence, not verified deck performance."));
    host.appendChild(section);
  }
  function appendStructuredPresentation(article, message) {
    var presentation = message && message.presentation;
    if (!presentation || typeof presentation !== "object") return;
    appendCardTray(article, message);
    if (presentation.goldfish) observationChart(article, presentation.goldfish);
    if (presentation.strategy_evidence) appendStrategyEvidence(article, presentation.strategy_evidence);
  }
  function toolLabel(action) {
    var names = { research_package: "Composing a research package", check_answer: "Checking card text and conditions", research_known_combinations: "Sourced combination research", replay_deck_combination: "Hypothetical interaction replay", analyze_deck_combinations: "Conditional card interactions", prepare_improvement: "Improvement research", research_memory: "Previous experiments and preferences", retrieve_strategy_evidence: "Strategy evidence", assess_deck_requirements: "Deck requirements", find_replacements: "Replacement research", test_recommended_alternatives: "Recommended alternatives experiment", inspect_deck: "Deck summary", search_cards: "Card catalog search", card_details: "Card details", latest_goldfish: "Latest Goldfish report", run_goldfish: "Goldfish run", preview_replacements: "Replacement preview", start_experiment: "Automated experiment" };
    return names[action && action.name] || "Research step";
  }
  // Pure helpers for progress and scrolling; the renderer supplies DOM measurements.
  function currentTurn(messages) {
    var list = Array.isArray(messages) ? messages : [];
    for (var index = list.length - 1; index >= 0; index -= 1) {
      if (list[index] && list[index].role === "user") return list.slice(index + 1);
    }
    return list.slice();
  }
  function isPartialFinding(message) {
    return !!(message && message.role === "tool" && message.action && message.action.type === "partial_finding");
  }
  function appendPartialFinding(group, message) {
    var action = message.action || {};
    var scope = action.data && action.data.source_scope;
    var content = typeof action.content === "string" ? action.content : messageContent(message);
    if (action.ok !== true || ["printed facts", "retrieved candidates", "preliminary fit", "research progress", "reviewed fragments"].indexOf(scope) < 0 || typeof content !== "string" || !content.trim()) return;
    var finding = element("section", null, "dl-assistant-partial-finding");
    finding.dataset.messageIds = message.id || "";
    var title = {"retrieved candidates": "Retrieved candidates", "printed facts": "Confirmed findings", "preliminary fit": "Preliminary deck fit", "research progress": "Research update", "reviewed fragments": "Checked findings"}[scope];
    finding.setAttribute("aria-label", title);
    finding.appendChild(element("h3", title));
    var labels = {"retrieved candidates": "Retrieved cards; suitability is not established by retrieval.", "printed facts": "Printed card facts; strategic fit is not established by this finding.", "preliminary fit": "Heuristic sample, not a ranked recommendation. Rules and strategic fit remain unverified.", "research progress": "Current research stage; the final answer may change after verification.", "reviewed fragments": "Checked against supplied evidence; remaining claims and request completion are still under review."};
    finding.appendChild(element("small", labels[scope]));
    var body = element("div", null, "dl-assistant-prose");
    appendMarkdown(body, content, knownCards(action.data.cards || []));
    finding.appendChild(body);
    // Keep grounded interim text visible even when Research activity is collapsed.
    group.appendChild(finding);
  }
  function toolActivity(messages) {
    // A start and its result are one call. Only finished calls count as steps.
    var finished = 0;
    var pending = [];
    (messages || []).forEach(function (message) {
      if (!message || message.role !== "tool" || isPartialFinding(message)) return;
      var action = message.action || {};
      var name = String(action.name || "");
      if (action.type === "tool_started") { pending.push(name); return; }
      var at = pending.lastIndexOf(name);
      if (at >= 0) pending.splice(at, 1);
      finished += 1;
    });
    return { finished: finished, pending: pending };
  }
  function stepCount(count) { return count + (count === 1 ? " step" : " steps") + " finished"; }
  function progressText(messages, options) {
    options = options || {};
    if (!options.chatBusy) return options.experimentsRunning ? "Automated research is running…" : "";
    if (options.stopping) return "Stopping after the current step…";
    var turn = currentTurn(messages);
    var activity = toolActivity(turn);
    var latestStage = turn.filter(function (message) {
      return isPartialFinding(message) && message.action.ok === true && message.action.data && message.action.data.source_scope === "research progress";
    }).pop();
    var parts = ["Working"];
    if (activity.pending.length) parts.push(toolLabel({ name: activity.pending[activity.pending.length - 1] }));
    else if (!activity.finished) parts = ["Working on your request…"];
    if (activity.finished) parts.push(stepCount(activity.finished));
    if (latestStage && (!activity.pending.length || (latestStage.action.data.stage === "verification" && activity.pending[activity.pending.length - 1] === "check_answer"))) {
      var stage = latestStage.action.data.stage;
      var stages = {planning: "Checking your request and deck", comparison: "Comparing costs, support and tradeoffs", verification: "Checking claims against card text"};
      if (stages[stage]) {
        parts = [stages[stage]];
        if (activity.finished) parts.push(stepCount(activity.finished));
        var stageSeconds = Number(options.now) - Number(latestStage.action.data.started_at);
        if (stageSeconds >= 30) parts.push("Still working on this stage; no new findings yet");
      }
    }
    var minutes = Math.floor((Number(options.now) - Number(options.startedAt)) / 60);
    // Minute granularity keeps the polite live region from announcing every poll.
    if (Number.isFinite(minutes) && minutes >= 1) parts.push(minutes + " min so far");
    return parts.join(" · ");
  }
  function transcriptScrollTop(view) {
    // Following: go to the latest content, but never past the top of a new reply.
    // Reading older messages: keep the same message at the same viewport offset.
    var max = Math.max(0, view.scrollHeight - view.clientHeight);
    var target = null;
    if (view.following) target = typeof view.replyTop === "number" ? Math.min(max, view.replyTop - 8) : max;
    else if (typeof view.anchorTop === "number") target = view.anchorTop - (view.anchorOffset || 0);
    return target === null ? null : Math.max(0, Math.min(max, target));
  }
  function appendSwapReviews(host, receipts) {
    if (!receipts.length) return;
    var section = element("section");
    section.appendChild(element("strong", "Strategic fit review · experiment suitability only"));
    receipts.slice(0, 8).forEach(function (receipt) {
      var card = element("div");
      var labels = { accept: "Suitable to test", reject: "Not recommended", uncertain: "Fit uncertain" };
      var pair = receipt.outgoing_name && receipt.incoming_name ? "Cut " + receipt.outgoing_name + "; add " + receipt.incoming_name : "Swap under review";
      card.appendChild(element("p", pair + " · " + (labels[receipt.decision] || "Fit uncertain")));
      if (receipt.reason) card.appendChild(element("p", receipt.reason));
      if (receipt.benefit) card.appendChild(element("p", "Potential benefit: " + receipt.benefit));
      if (Array.isArray(receipt.tradeoffs) && receipt.tradeoffs.length) card.appendChild(element("p", "Tradeoffs: " + receipt.tradeoffs.join("; ")));
      if (receipt.validation_reason) card.appendChild(element("p", receipt.validation_reason));
      if (Array.isArray(receipt.citations) && receipt.citations.length && receipt.citations_verified !== true) {
        card.appendChild(element("p", "Quoted evidence not verified"));
      }
      if (receipt.citations_verified === true && Array.isArray(receipt.citations) && receipt.citations.length) {
        var citations = element("details");
        citations.appendChild(element("summary", "Card-text evidence"));
        receipt.citations.slice(0, 8).forEach(function (citation) {
          citations.appendChild(element("p", String(citation.side || "Card") + ": “" + String(citation.quote || "") + "”"));
        });
        card.appendChild(citations);
      }
      var accounting = element("details");
      accounting.appendChild(element("summary", "Hand-card accounting"));
      var accountingRows = 0;
      ["outgoing", "incoming"].forEach(function (side) {
        var profile = receipt[side + "_card_economy"];
        if (!profile || profile.status !== "supported_from_hand_clause_accounting" || !Array.isArray(profile.branches)) return;
        profile.branches.slice(0, 4).forEach(function (branch) {
          if (!branch || !Number.isSafeInteger(branch.cards_drawn) || branch.cards_drawn < 0 ||
              !Number.isSafeInteger(branch.cards_discarded) || branch.cards_discarded < 0 ||
              !Number.isSafeInteger(branch.net_hand_delta) ||
              branch.cards_drawn - branch.cards_discarded - 1 !== branch.net_hand_delta) return;
          var name = receipt[side + "_name"] || (side === "outgoing" ? "Outgoing card" : "Incoming card");
          var condition = typeof branch.condition === "string" ? branch.condition.replace(/_/g, " ") : "specified resolution";
          accounting.appendChild(element("p", (side === "outgoing" ? "Cut " : "Add ") + name + " · " + condition + ": " + branch.cards_drawn + " drawn − " + branch.cards_discarded + " discarded − 1 spell = " + (branch.net_hand_delta > 0 ? "+" : "") + branch.net_hand_delta + " hand cards"));
          accountingRows += 1;
        });
        if (Array.isArray(profile.requirements)) {
          var requirements = profile.requirements.filter(function (item) { return typeof item === "string"; }).map(function (item) { return item.replace(/_/g, " "); });
          if (requirements.length) accounting.appendChild(element("p", "Requires: " + requirements.join("; ")));
        }
      });
      if (accountingRows) {
        accounting.appendChild(element("p", "Cast from hand and resolve with legal targets. Hand cards only; excludes board value, commander triggers and graveyard reuse."));
        card.appendChild(accounting);
      }
      section.appendChild(card);
    });
    section.appendChild(element("p", "Model review of supplied evidence; card strength and measured improvement remain unverified."));
    host.appendChild(section);
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
    if (action.name === "check_answer" && action.data && Array.isArray(action.data.claim_judgments) && action.data.claim_judgments.some(function (claim) { return claim.method !== "unreviewed_abstention" && ["supported", "contradicted", "unverified"].indexOf(claim.verdict) >= 0; })) {
      title = toolLabel(action) + (failed ? " · complete with unresolved claims" : " · complete");
    }
    if (action.name === "search_cards" && action.ok === true) {
      var cards = action.data && Array.isArray(action.data.cards) ? action.data.cards : [];
      title = "Card catalog search · " + cards.length + (cards.length === 1 ? " result" : " results");
    }
    step.appendChild(element("strong", title));
    if (failed && message.content) appendMarkdown(step, messageContent(message), cardsFrom(message));
    if (action.name === "search_cards" && action.ok === true) appendSearchResults(step, message);
    if (action.name === "retrieve_strategy_evidence" && action.ok === true) appendStrategyEvidence(step, action.data);
    if (action.ok === true && action.data && Array.isArray(action.data.review_receipts)) appendSwapReviews(step, action.data.review_receipts);
    if (action.name === "research_package" && action.ok === true && action.data) {
      var quotes = element("details");
      quotes.appendChild(element("summary", "Package card text · interaction remains an inference"));
      (action.data.hypotheses || []).forEach(function (hypothesis) {
        (hypothesis.source_citations || []).forEach(function (pair) {
          quotes.appendChild(element("p", pair.incoming + ": “" + pair.incoming_quote + "”"));
          quotes.appendChild(element("p", pair.outgoing + ": “" + pair.outgoing_quote + "”"));
        });
      });
      step.appendChild(quotes);
    }
    if (action.ok === true && action.data && Array.isArray(action.data.observations)) observationChart(step, action.data);
    if (action.ok === true && ["start_experiment", "test_recommended_alternatives"].indexOf(action.name) >= 0 && action.data && action.data.metric_summary) appendMetricSummary(step, action.data.metric_summary);
    return step;
  }
  function renderMessages() {
    if (!transcript) return;
    function latestItem() {
      var items = Array.prototype.filter.call(transcript.children, function (node) { return !node.dataset || node.dataset.assistantProcessing !== "true"; });
      return items[items.length - 1] || null;
    }
    function appendConversation(node) {
      var last = transcript.lastElementChild;
      if (last && last.dataset && last.dataset.assistantProcessing === "true") transcript.insertBefore(node, last);
      else transcript.appendChild(node);
    }
    var messages = data.messages || [];
    var workspaceMode = !!(data.research_workspace && Array.isArray(data.research_workspace.cards) && data.research_workspace.cards.length);
    var signature = JSON.stringify([workspaceMode, messages]);
    if (signature === renderedMessageSignature) return;
    var canAppend = renderedMessageSignature !== null && messages.length >= renderedMessageCount;
    var previousMessages = renderedMessageSignature ? JSON.parse(renderedMessageSignature)[1] : [];
    if (renderedMessageSignature && JSON.parse(renderedMessageSignature)[0] !== workspaceMode) canAppend = false;
    for (var index = 0; canAppend && index < renderedMessageCount; index += 1) {
      if (JSON.stringify(messages[index]) !== JSON.stringify(previousMessages[index])) canAppend = false;
    }
    var surface = scrollSurface || transcript;
    var previousIds = new Set(renderedMessageIds);
    var anchor = followingLatest ? null : transcriptAnchor(surface);
    var openKeys = new Set();
    if (!canAppend) {
      transcript.querySelectorAll("details[data-disclosure-key]").forEach(function (details) { if (details.open) openKeys.add(details.dataset.disclosureKey); });
      transcript.replaceChildren(); renderedMessageCount = 0; renderedMessageIds = [];
    }
    var newReply = null;
    var findingIds = new Set(renderedMessageIds);
    var cursor = renderedMessageCount;
    while (cursor < messages.length) {
      var message = messages[cursor];
      var role = ["user", "assistant", "tool"].indexOf(message.role) >= 0 ? message.role : "tool";
      if (role === "tool") {
        var group = latestItem();
        var activity = group && group.classList.contains("dl-assistant-activity") ? group.querySelector("details") : null;
        if (!activity) {
          group = element("article", null, "dl-assistant-message dl-assistant-activity"); group.dataset.role = "tool";
          group.dataset.messageIds = "";
          activity = document.createElement("details");
          var before = messages[cursor - 1];
          activity.dataset.disclosureKey = "activity:" + (before && before.id || "start");
          activity.appendChild(element("summary", "Research activity"));
          group.appendChild(activity); appendConversation(group);
        }
        var toolMessages = [];
        var end = cursor;
        while (end < messages.length && messages[end] && messages[end].role === "tool") { toolMessages.push(messages[end]); end += 1; }
        var finished = Number(activity.dataset.finishedCount || 0);
        toolMessages.forEach(function (item) {
          var action = item.action || {};
          if (isPartialFinding(item)) {
            if (!workspaceMode || ["printed facts", "retrieved candidates", "preliminary fit", "reviewed fragments"].indexOf(action.data && action.data.source_scope) < 0) {
              if (!item.id || !findingIds.has(item.id)) appendPartialFinding(group, item);
            } else {
              var audit = element("details"); audit.appendChild(element("summary", "Workspace finding · " + action.data.source_scope));
              audit.appendChild(element("p", action.content || item.content || "Card finding updated in Research workspace")); activity.appendChild(audit);
            }
            if (item.id) findingIds.add(item.id);
            return;
          }
          var step = renderToolStep(item);
          if (action.type === "tool_started") { step.dataset.pendingTool = String(action.name || ""); activity.appendChild(step); return; }
          // The result replaces its own "working" line; it is one step, not two.
          var waiting = Array.prototype.filter.call(activity.querySelectorAll("[data-pending-tool]"), function (node) { return node.dataset.pendingTool === String(action.name || ""); }).pop();
          if (waiting) activity.replaceChild(step, waiting); else activity.appendChild(step);
          finished += 1;
        });
        activity.dataset.finishedCount = String(finished);
        for (; cursor < end; cursor += 1) {
          var id = messages[cursor] && messages[cursor].id || null;
          renderedMessageIds.push(id); renderedMessageCount += 1;
          if (id) group.dataset.messageIds += (group.dataset.messageIds ? " " : "") + id;
        }
        continue;
      }
      var article = element("article", null, "dl-assistant-message");
      article.dataset.role = role;
      article.dataset.messageIds = message && message.id || "";
      if (role === "assistant") publicAnswerRuns.forEach(function (group) {
        group.answers.forEach(function (record) { if (legacyAnswerMatches(message, record.answer)) article.dataset.messageIds += " " + record.answer.anchor_id; });
      });
      article.setAttribute("aria-label", role === "assistant" ? "Research Assistant reply" : "Your message");
      appendReply(article, message);
      appendStructuredPresentation(article, message);
      appendConversation(article);
      if (role === "assistant" && !previousIds.has(message && message.id || null)) newReply = article;
      renderedMessageIds.push(message && message.id || null);
      renderedMessageCount += 1;
      cursor += 1;
    }
    if (newReply !== latestItem()) newReply = null;
    if (openKeys.size) transcript.querySelectorAll("details[data-disclosure-key]").forEach(function (details) { details.open = openKeys.has(details.dataset.disclosureKey); });
    renderedMessageSignature = signature;
    syncActivities();
    refreshChartStaleness();
    if (!surface.clientHeight) {
      // Hidden tab: nothing can be measured. Settle the position when chat is shown.
      if (followingLatest) { deferredScroll = true; deferredReply = newReply || (deferredReply === latestItem() ? deferredReply : null); }
      return;
    }
    if (!workspaceMode) scrollTranscript(surface, newReply, anchor);
  }
  function scrollTranscript(surface, newReply, anchor) {
    var anchorNode = anchor && Array.prototype.find.call(transcript.children, function (node) {
      return (node.dataset.messageIds || "").split(" ").some(function (id) { return anchor.ids.indexOf(id) >= 0; });
    });
    var target = transcriptScrollTop({
      following: followingLatest,
      scrollHeight: surface.scrollHeight,
      clientHeight: surface.clientHeight,
      replyTop: newReply ? contentTop(surface, newReply) : null,
      anchorTop: anchorNode ? contentTop(surface, anchorNode) : null,
      anchorOffset: anchor ? anchor.offset : 0
    });
    if (target !== null && Math.abs(target - surface.scrollTop) > 1) surface.scrollTop = target;
  }
  function contentTop(surface, node) {
    return node.getBoundingClientRect().top - surface.getBoundingClientRect().top + surface.scrollTop;
  }
  function transcriptAnchor(surface) {
    // The first message still visible at the top is the reader's place.
    var top = surface.getBoundingClientRect().top;
    var node = Array.prototype.find.call(transcript.children, function (child) { return child.getBoundingClientRect().bottom > top; });
    var ids = node ? (node.dataset.messageIds || "").split(" ").filter(Boolean) : [];
    return ids.length ? { ids: ids, offset: node.getBoundingClientRect().top - top } : null;
  }
  function syncActivities() {
    if (!transcript) return;
    var groups = transcript.querySelectorAll(".dl-assistant-activity > details");
    var conversation = Array.prototype.filter.call(transcript.children, function (node) { return !node.dataset || node.dataset.assistantProcessing !== "true"; });
    var latest = conversation[conversation.length - 1];
    Array.prototype.forEach.call(groups, function (activity) {
      var live = (data.research_workspace ? ["queued", "running"].indexOf(data.research_workspace.job && data.research_workspace.job.state || data.research_workspace.status) >= 0 : !!data.busy) && activity.parentNode === latest;
      var waiting = Array.prototype.slice.call(activity.querySelectorAll("[data-pending-tool]"));
      var finished = Number(activity.dataset.finishedCount || 0);
      var parts = ["Research activity"];
      if (finished) parts.push(stepCount(finished));
      if (waiting.length && live) parts.push("now: " + toolLabel({ name: waiting[waiting.length - 1].dataset.pendingTool }));
      else if (waiting.length) {
        parts.push(waiting.length + " did not finish");
        waiting.forEach(function (step) {
          var line = step.firstChild; var text = toolLabel({ name: step.dataset.pendingTool }) + " · did not finish";
          if (line && line.textContent !== text) line.textContent = text;
        });
      }
      var summary = activity.querySelector("summary");
      // Unchanged text is not rewritten, so the live log does not repeat itself every poll.
      if (summary.textContent !== parts.join(" · ")) summary.textContent = parts.join(" · ");
    });
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
        .catch(function (error) { showError(error.message); button.disabled = false; });
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
  function appendSwapOutcome(host, outcome) {
    var panel = element("div", null, "dl-assistant-swap-outcome");
    var objectives = {
      qualified_opening_rate: "Modeled opening-policy proxy",
      mean_missed_land_drops: "Fewer missed land drops",
      positive_finish_proof_rate: "Verified solo finishes",
      engine_online_rate: "Engine development",
      recovery_draw_rate: "Draw recovery after commander loss",
      recovery_board_rate: "Board recovery after creature wipe"
    };
    var verdicts = { improved: "Improved for selected objective", regressed: "Regressed for selected objective", indeterminate: "Improvement undetermined", incomparable: "Results cannot be compared", stale: "Results are stale" };
    if (!outcome || typeof outcome !== "object") {
      panel.appendChild(element("strong", "Improvement undetermined"));
      panel.appendChild(element("p", "Objective evaluation is not available yet."));
      host.appendChild(panel); return;
    }
    panel.appendChild(element("strong", outcome.display_label || verdicts[outcome.verdict] || "Improvement undetermined"));
    panel.appendChild(element("p", "Objective: " + (objectives[outcome.objective] || outcome.objective || "Not selected")));
    if (outcome.reason) panel.appendChild(element("p", outcome.reason));
    var evidence = outcome.evidence || {};
    if (evidence.scope) panel.appendChild(element("p", "Test scope: " + String(evidence.scope).replace(/_/g, " ")));
    if (typeof evidence.delta === "number" && Number.isFinite(evidence.delta)) {
      panel.appendChild(element("p", "Raw metric change (candidate − baseline): " + evidence.delta.toFixed(3)));
    }
    var interval = evidence.difference_interval;
    if (interval && Number.isFinite(interval.low) && Number.isFinite(interval.high)) {
      panel.appendChild(element("p", "Interval toward improvement (positive is better): " + interval.low.toFixed(3) + " to " + interval.high.toFixed(3)));
    }
    if (evidence.direction === -1) panel.appendChild(element("p", "Lower values are better for this metric; the improvement interval reverses the raw change direction."));
    if (outcome.training_evidence && outcome.training_evidence.reason) {
      panel.appendChild(element("p", "Training evidence: " + outcome.training_evidence.reason));
    }
    panel.appendChild(element("p", "Solo evidence for this objective. Overall deck strength and other tradeoffs remain unverified."));
    if (outcome.note) panel.appendChild(element("p", outcome.note));
    host.appendChild(panel);
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
        appendSwapOutcome(section, alternative.outcome);
        if (alternative.validation) {
          appendComparisonFigure(section, alternative.validation.comparison, "Held-out");
          section.appendChild(element("p", "Independent validation · " + (alternative.validation.status || "descriptive") +
            (alternative.validation.reason ? " · " + alternative.validation.reason : "")));
        }
        if (alternative.comparison) appendComparisonFigure(section, alternative.comparison, "Training");
        var fullEvidence = element("details"); fullEvidence.dataset.experimentDetails = String(experiment.id) + "|" + String(alternative.id);
        fullEvidence.appendChild(element("summary", "Full experiment evidence"));
        fullEvidence.appendChild(element("pre", readableReport({
          outcome: alternative.outcome,
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
            .catch(function (error) { showError(error.message); adopt.disabled = false; });
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
  function publicRecords(collection) { return Object.values(publicView.entities[collection] || {}); }
  function renderPublicFindings(viewOverride) {
    var view = viewOverride || publicView;
    var run = view.run;
    var findings = Object.values(view.entities.findings || {});
    if (run && findings.length) {
      var group = publicFindingRuns.get(run.run_id);
      if (!group) {
        var section = element("section", null, "dl-assistant-grounded-findings"); section.dataset.assistantProcessing = "true"; section.dataset.messageIds = "public-findings:" + run.run_id;
        var heading = element("h3", "Retrieved findings · final answer still separate"); section.appendChild(heading);
        group = { node: section, heading: heading, records: new Map(), generation: run.generation }; publicFindingRuns.set(run.run_id, group);
        if (run.lifecycle === "superseded") heading.textContent = "Superseded findings · research restarted";
        else if (viewOverride) heading.textContent = "Earlier retrieved findings · inspect their source limitations";
        var later = Array.from(publicFindingRuns.values()).sort(function(a,b){return a.generation-b.generation;}).find(function(g){return g.generation > run.generation && g.node.parentNode === transcript;});
        if (later) transcript.insertBefore(section,later.node);
      }
      findings.forEach(function (finding) {
        var record = group.records.get(finding.id);
        var signature = JSON.stringify(finding);
        if (record && record.signature === signature) return;
        if (!record) { record = {node: element("article", null, "dl-assistant-partial-finding")}; group.records.set(finding.id, record); group.node.appendChild(record.node); }
        var opened = record.node.querySelector && record.node.querySelector("details"); var wasOpen = !!(opened && opened.open);
        record.node.replaceChildren(); record.signature = signature;
        record.node.appendChild(element("strong", finding.type === "unranked_candidate" ? "Retrieved candidate · unranked" : finding.type === "rule_excerpt" ? "Retrieved rule excerpt" : "Captured printed fact"));
        var retiredStatus = ["withdrawn", "superseded"].indexOf(finding.status) >= 0 ? finding.status : ["withdrawn", "superseded"].indexOf(finding.review_state) >= 0 ? finding.review_state : null;
        record.node.dataset.findingStatus = retiredStatus || "current";
        if (retiredStatus) record.node.appendChild(element("p", "This finding is " + retiredStatus + "; do not rely on its earlier assessment."));
        record.node.appendChild(element("p", finding.text || "Finding unavailable"));
        record.node.appendChild(element("small", "Deck revision " + (finding.deck_revision === undefined ? run.deck_revision : finding.deck_revision) + " · source retrieval is not strategic approval."));
        (finding.limitations || []).forEach(function (text) { record.node.appendChild(element("small", text)); });
        var sources = element("details"); sources.open = wasOpen; sources.appendChild(element("summary", "Sources and limitations"));
        renderReadableEvidence(sources, finding.sources || []); record.node.appendChild(sources);
      });
    }
    if (run && run.steered_from_run_id && publicFindingRuns.has(run.steered_from_run_id)) publicFindingRuns.get(run.steered_from_run_id).heading.textContent = "Superseded findings · research restarted";
    while (publicFindingRuns.size > 8) { var key = Array.from(publicFindingRuns.keys()).filter(function(id){return id!==publicView.runId;}).sort(function(a,b){return (publicFindingRuns.get(a).generation||0)-(publicFindingRuns.get(b).generation||0);})[0]; var retired = publicFindingRuns.get(key); if (retired.node.parentNode) retired.node.remove(); publicFindingRuns.delete(key); }
    Array.from(publicFindingRuns.values()).sort(function(a,b){return (a.generation||0)-(b.generation||0);}).forEach(function (group) {
      if (group.node.parentNode !== transcript) transcript.appendChild(group.node);
    });
  }
  function renderPublicAnswers() {
    if (!transcript) return;
    var run = publicView.run;
    var answers = completePublicAnswers(publicView);
    var surface = scrollSurface || transcript;
    var anchor = surface && !followingLatest ? transcriptAnchor(surface) : null;
    var changed = false, newReply = null;
    if (run && publicAnswerRuns.has(run.run_id)) {
      var present = new Set(answers.map(function (answer) { return answer.id; }));
      var previous = publicAnswerRuns.get(run.run_id);
      previous.answers.forEach(function (record, id) {
        if (!present.has(id)) { if (record.node.parentNode) record.node.remove(); previous.answers.delete(id); changed = true; }
      });
    }
    if (run && answers.length) {
      var group = publicAnswerRuns.get(run.run_id);
      if (!group) { group = { run_id: run.run_id, answers: new Map() }; publicAnswerRuns.set(run.run_id, group); }
      answers.forEach(function (answer) {
        var record = group.answers.get(answer.id);
        if (!record) {
          var article = element("article", null, "dl-assistant-message");
          article.dataset.role = "assistant"; article.dataset.assistantProcessing = "true";
          article.dataset.messageIds = answer.anchor_id;
          article.setAttribute("aria-label", "Research Assistant reply");
          record = {node: article, signature: null, answer: answer}; group.answers.set(answer.id, record);
        }
        var signature = JSON.stringify(answer);
        if (record.signature !== signature) {
          record.node.replaceChildren(); record.answer = answer; record.signature = signature;
          var body = element("div", null, "dl-assistant-prose");
          appendMarkdown(body, answer.text, []); record.node.appendChild(body);
          record.node.appendChild(element("small", answer.reviewed ? "Review receipts available; inspect claim verdicts and sources." : "Final answer · claim review incomplete or unavailable."));
          record.node.appendChild(element("small", "Deck revision " + run.deck_revision + "."));
          changed = true;
        }
      });
    }
    publicAnswerRuns.forEach(function (group) {
      group.answers.forEach(function (record) {
        var inLegacy = (data.messages || []).some(function (message) { return legacyAnswerMatches(message, record.answer); });
        if (inLegacy) {
          if (record.node.parentNode === transcript) { record.node.remove(); changed = true; }
        } else if (record.node.parentNode !== transcript) { transcript.appendChild(record.node); changed = true; newReply = record.node; }
        record.node.dataset.superseded = run && run.steered_from_run_id === group.run_id ? "true" : "false";
      });
    });
    while (publicAnswerRuns.size > 8) {
      var key = publicAnswerRuns.keys().next().value;
      publicAnswerRuns.get(key).answers.forEach(function (record) { if (record.node.parentNode) record.node.remove(); });
      publicAnswerRuns.delete(key); changed = true;
    }
    if (changed && surface && surface.clientHeight) scrollTranscript(surface, newReply, anchor);
  }
  function renderActivity() {
    var run = publicView.run;
    if (!run && !data.turn_id) return;
    if (!activityNode) {
      activityNode = element("details", null, "dl-assistant-processing");
      activityNode.dataset.assistantProcessing = "true"; activityNode.setAttribute("aria-live", "off"); activityNode.open = true;
      var summary = element("summary");
      var dot = element("span", "", "dl-assistant-processing-dot"); dot.setAttribute("aria-hidden", "true"); summary.appendChild(dot);
      activityLabel = element("span"); summary.appendChild(activityLabel);
      activityTimer = element("span", "", "dl-assistant-elapsed"); activityTimer.setAttribute("aria-hidden", "true"); summary.appendChild(activityTimer);
      activityNode.appendChild(summary);
      activityDetails = element("div", null, "dl-assistant-processing-details"); activityNode.appendChild(activityDetails);
      activityHistory = element("ol"); activityDetails.appendChild(activityHistory);
      activityNode.addEventListener("toggle", updateActivityTimer);
    }
    if (activityNode.parentNode !== transcript || transcript.lastElementChild !== activityNode) transcript.appendChild(activityNode);
    var lifecycle = run ? run.lifecycle : researchWorkspace().job && researchWorkspace().job.state || (data.busy ? "running" : "completed");
    var active = ["queued", "running"].indexOf(lifecycle) >= 0;
    var label = run ? terminalLabel(run) : active ? (lifecycle === "queued" ? "Queued" : "Working on your request") : "Research ended · review details below";
    var operations = publicRecords("activity");
    var current = operations.filter(function (operation) { return operation.state === "started"; }).pop();
    if (lifecycle === "running" && current && typeof current.label === "string") label = current.label;
    if (active && !current && lifecycle !== "queued") label = progressText(data.messages, { chatBusy: true }).replace(/ · \d+ steps? finished/g, "") || label;
    if (stopRequested && active) label = "Requesting stop…";
    if (connectionLost) label = active ? "Connection lost; research may still be running" : "Updates unavailable; last confirmed: " + label;
    var suffix = run ? " · evidence review " + ({ reviewed: "complete; inspect verdicts", partial: "partial", unavailable: "unavailable", not_started: "not started" }[run.evidence_status] || "unknown") : "";
    if (run && run.fulfillment !== "complete" && !active) suffix += " · request fulfillment " + run.fulfillment;
    var announcement = label + (active ? "" : suffix);
    if (activityLabel.textContent !== announcement) activityLabel.textContent = announcement;
    activityNode.dataset.active = active && !connectionLost ? "true" : "false";
    if (!active && activityNode.dataset.lifecycle !== lifecycle) activityNode.open = false;
    activityNode.dataset.lifecycle = lifecycle;
    if (activityNode.dataset.runId !== String(run && run.run_id || data.turn_id)) {
      activityNode.open = active; activityNode.dataset.runId = String(run && run.run_id || data.turn_id);
    }
    var details = [];
    if (publicView.history_load_error) details.push("Earlier findings could not be loaded. Reconnect to retry; current research is unaffected.");
    if (publicView.history && publicView.history.compacted) details.push("Earlier activity unavailable · " + Number(publicView.history.compacted_event_count || 0) + " event records compacted. Retained findings remain available.");
    if (run && run.steered_from_run_id) details.push("Restarted from earlier research using the same deadline and allowance. Earlier findings remain superseded.");
    if (run && run.lifecycle === "stopped") details.push("An in-flight request may finish in the background and will be discarded.");
    if (publicView.operational.cost_pending) details.push("Cost pending; unknown usage remains reserved.");
    if (publicView.operational.background_work_pending) details.push("Previously started work may still occupy a worker.");
    operations.forEach(function (operation) { details.push((operation.label || "Research activity") + " · " + (operation.state || "unknown")); });
    if (!operations.length) {
      currentTurn(data.messages || []).filter(function (message) { return message.role === "tool"; }).forEach(function (message) { details.push(toolLabel(message.action || {}) + (message.action && message.action.type === "tool_started" ? " · started" : " · result recorded")); });
    }
    var signature = JSON.stringify(details);
    if (signature !== activitySignature) { activityHistory.replaceChildren(); details.forEach(function (text) { activityHistory.appendChild(element("li", text)); }); activitySignature = signature; }
    if (announcement !== activityAnnounced) {
      showProgress(announcement); activityAnnounced = announcement;
    }
    var reconnect = sidebar.querySelector("[data-assistant-reconnect]");
    if (reconnect) { reconnect.hidden = !connectionLost && !publicView.history_load_error && !publicView.operational.background_work_pending && !publicView.operational.cost_pending; reconnect.textContent = connectionLost ? "Reconnect" : "Refresh pending status"; }
    updateActivityTimer();
    if (active && !timerTick && pageAlive) timerTick = window.setInterval(updateActivityTimer, 1000);
    if (!active && timerTick) { window.clearInterval(timerTick); timerTick = 0; }
    updateJumpToLatest();
  }
  function updateActivityTimer() {
    if (!activityTimer) return;
    activityTimer.hidden = !activityNode.open;
    if (!activityNode.open) return;
    var text = elapsedLabel(publicView.run, publicClock, performance.now());
    if (activityTimer.textContent !== text) activityTimer.textContent = text;
  }
  function needsOperationalPoll() {
    if (!publicView.run || ["queued", "running"].indexOf(publicView.run.lifecycle) >= 0 || (!publicView.operational.background_work_pending && !publicView.operational.cost_pending)) { operationalPollStarted = 0; return false; }
    if (!operationalPollStarted) operationalPollStarted = performance.now();
    return !document.hidden && performance.now() - operationalPollStarted < 60000;
  }
  function render() {
    renderSettings(); renderWorkspace(); renderMessages(); renderProposals(); renderExperiments();
    var welcome = sidebar.querySelector("[data-assistant-welcome]");
    if (welcome) welcome.hidden = (data.messages || []).length > 0;
    var experimentsRunning = (data.experiments || []).some(function (item) { return ["queued", "running"].indexOf(item.status) >= 0; });
    busy = (data.research_workspace ? researchRunning() : !!data.busy) || experimentsRunning;
    var send = sidebar.querySelector("[data-assistant-send]"); if (send) { send.disabled = sending; send.textContent = busy ? "Restart with this instruction" : "Send"; }
    var stop = sidebar.querySelector("[data-assistant-stop]"); if (stop) stop.hidden = !busy;
    syncActivities();
    if (!busy) stopRequested = false;
    var helper = sidebar.querySelector("[data-assistant-steering-help]"); if (helper) helper.hidden = !busy;
    renderPublicFindings(); renderPublicAnswers(); renderActivity();
    if (busy || needsOperationalPoll()) schedulePoll();
    else if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = 0; }
  }
  function showProgress(text) {
    // Progress owns the status line only while it is the latest thing shown there;
    // errors and action results written by other handlers are left alone.
    var owned = !status.textContent || status.textContent === progressShown;
    if (text) { if (owned) { if (status.textContent !== text) status.textContent = text; progressShown = text; } }
    else if (progressShown) { if (status.textContent === progressShown) status.textContent = ""; progressShown = ""; }
  }
  function publicUrl(view, page, historicalRunId) {
    var query = new URLSearchParams({ contract: "assistant.events.v1" });
    if (historicalRunId) query.set("run_id",historicalRunId);
    else query.set("follow_current","1");
    if (page) { query.set("snapshot_token", page.token); query.set("page_index", page.index); }
    else if (view.runId && !view.staging) { if (!historicalRunId) query.set("run_id", view.runId); query.set("after_sequence", view.sequence); }
    return base + "?" + query.toString();
  }
  function readPublicReply(next, view, depth, historicalRunId) {
    var projection = next && (next.public_events || next.session && next.session.public_events);
    if (!projection) return Promise.resolve({ next: next, view: emptyPublicView(), clock: null });
    if (historicalRunId && (!projection.run || projection.run.run_id !== historicalRunId)) throw new Error("Historical research identity changed.");
    var result = reducePublicEvents(view, projection);
    if (depth >= 128) throw new Error("Research updates could not be synchronized. Reconnect to refresh state.");
    if (result.legacy) return Promise.resolve({ next: next, view: result.view, clock: null });
    if (result.reset) {
      if (depth > 1 && !view.runId) throw new Error("Research updates unavailable. Reconnect to refresh state.");
      return request(publicUrl(emptyPublicView(),null,historicalRunId)).then(function (reply) { return readPublicReply(reply, emptyPublicView(), depth + 1,historicalRunId); });
    }
    if (result.page || result.more) return request(publicUrl(result.view, result.page,historicalRunId)).then(function (reply) { return readPublicReply(reply, result.view, depth + 1,historicalRunId); });
    var time = Date.parse(projection.server_time || "");
    return Promise.resolve({ next: next, view: result.view, clock: Number.isFinite(time) ? { server: time, tick: performance.now() } : null });
  }
  function loadPreviousFindings() {
    if (!publicView.run) return;
    var ids = [publicView.run.steered_from_run_id].concat((data.messages||[]).filter(function(m){return m.role==="user";}).reverse().map(function(m){return m.run_id;}));
    ids = Array.from(new Set(ids.filter(function(id){return id && id !== publicView.runId;}))).slice(0,8);
    var chain = Promise.resolve();
    ids.forEach(function(id){
      if (historicalFindingLoads.has(id)) return;
      historicalFindingLoads.set(id,true);
      chain = chain.then(function(){return request(publicUrl(emptyPublicView(),null,id));})
        .then(function(reply){return readPublicReply(reply,emptyPublicView(),0,id);})
        .then(function(result){
          if (!result.view.run || result.view.run.run_id !== id) throw new Error("Historical research identity changed.");
          var surface=scrollSurface||transcript, anchor=followingLatest?null:transcriptAnchor(surface);
          renderPublicFindings(result.view); renderActivity();
          if (anchor && surface.clientHeight) scrollTranscript(surface,null,anchor);
        }).catch(function(){publicView.history_load_error=true; renderActivity();});
    });
    while (historicalFindingLoads.size>16) historicalFindingLoads.delete(historicalFindingLoads.keys().next().value);
  }
  function refresh() {
    var epoch = sessionEpoch;
    var sequence = ++refreshSequence;
    var requestSequence = ++sessionRequestSequence;
    return request(publicUrl(publicView)).then(function (next) { return readPublicReply(next, publicView, 0); }).then(function (result) {
      if (epoch === sessionEpoch && sequence === refreshSequence) {
        publicView = result.view; publicClock = result.clock;
        acceptSession(result.next, requestSequence);
        loadPreviousFindings();
      }
    }).catch(function () {
      if (epoch !== sessionEpoch || sequence !== refreshSequence) return;
      connectionLost = true; pollFailures += 1;
      showError("Updates unavailable. The last confirmed research state is retained. Reconnect refreshes state without starting another attempt.");
      renderActivity(); if (busy || needsOperationalPoll()) schedulePoll();
    });
  }
  function schedulePoll() {
    if (pollTimer || !pageAlive) return;
    var delay = pollFailures ? [2000, 4000, 8000, 15000][Math.min(pollFailures - 1, 3)] : !busy ? 5000 : 1800;
    pollTimer = window.setTimeout(function () { pollTimer = 0; refresh(); }, delay);
  }

  sidebar.querySelectorAll("[data-assistant-mode]").forEach(function (button) {
    button.addEventListener("click", function () {
      var mode = button.dataset.assistantMode;
      var sequence = ++sessionRequestSequence;
      request(base + "/settings", { method: "POST", body: JSON.stringify(Object.assign(expectedTarget(false), { mode: mode, constraints: data.constraints || {} })) })
        .then(function (next) { acceptMutation(next, sequence); })
        .catch(function (error) { showError(error.message); });
    });
  });
  var constraintToggle = sidebar.querySelector("[data-assistant-constraints-toggle]");
  if (constraintToggle) constraintToggle.addEventListener("click", function () {
    var form = sidebar.querySelector("[data-assistant-constraints]"); form.hidden = !form.hidden;
    constraintToggle.setAttribute("aria-expanded", form.hidden ? "false" : "true");
    if (!form.hidden) { sidebar.querySelector("[data-assistant-scroll]").scrollTop = 0; form.querySelector("select, input").focus({ preventScroll: true }); }
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
      objective: sidebar.querySelector("[data-assistant-objective]").value,
      power_level: sidebar.querySelector("[data-assistant-power]").value.trim(),
      theme: sidebar.querySelector("[data-assistant-theme]").value.trim(),
      protected_cards: Array.prototype.slice.call(sidebar.querySelectorAll("[data-protected-card]:checked")).map(function (box) { return box.value; })
    };
    var sequence = ++sessionRequestSequence;
    request(base + "/settings", { method: "POST", body: JSON.stringify(Object.assign(expectedTarget(false), { mode: data.mode || "suggest", constraints: constraints })) })
      .then(function (next) { acceptMutation(next, sequence); protectedDirty = false; renderSettings(); if (statusNode) statusNode.textContent = "Constraints saved."; })
      .catch(function (error) { if (statusNode) statusNode.textContent = error.message; });
  });
  var composer = sidebar.querySelector("[data-assistant-composer]");
  if (composer) composer.addEventListener("submit", function (event) {
    event.preventDefault();
    var input = sidebar.querySelector("[data-assistant-message]");
    var message = input.value.trim(); if (!message || sending) return;
    sending = true;
    status.textContent = "Sending message…"; progressShown = status.textContent;
    if (!hasWorkspaceCards()) followingLatest = true;
    var send = sidebar.querySelector("[data-assistant-send]"); if (send) send.disabled = true;
    var sequence = ++sessionRequestSequence;
    var signature = JSON.stringify([message, state.revision, data.mode, data.constraints]);
    if (!pendingSubmission || pendingSubmission.signature !== signature) pendingSubmission = {
      signature: signature, kind: busy ? "steer" : "message", target: expectedTarget(false), id: window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "message-" + Date.now() + "-" + Math.random().toString(36).slice(2)
    };
    var operation = pendingSubmission.kind === "steer" ? steer("message", Object.assign({}, pendingSubmission.target, { message: message, client_request_id: pendingSubmission.id })) : request(base + "/messages", { method: "POST", body: JSON.stringify(Object.assign({}, pendingSubmission.target, { message: message, client_request_id: pendingSubmission.id })) }).then(function (next) { acceptMutation(next, sequence); return refresh(); });
    operation.then(function () { pendingSubmission = null; if (input.value.trim() === message) input.value = ""; })
      .catch(function (error) { if (error.retryNewSubmission) pendingSubmission = null; showError(error.message); })
      .finally(function () { sending = false; if (send) send.disabled = false; });
  });
  var stop = sidebar.querySelector("[data-assistant-stop]");
  if (stop) stop.addEventListener("click", function () {
    stop.disabled = true; stopRequested = true; renderActivity();
    request(base + "/stop", { method: "POST", body: JSON.stringify(expectedTarget(true)) }).then(function () { stopRequested = true; status.textContent = "Stop requested; an in-flight operation may finish."; progressShown = status.textContent; return refresh(); })
      .catch(function (error) { stopRequested = false; showError(error.message); renderActivity(); return refresh(); }).finally(function () { stop.disabled = false; });
  });
  var input = sidebar.querySelector("[data-assistant-message]");
  if (input) {
    input.addEventListener("keydown", function (event) {
      var touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !touch) { event.preventDefault(); if (composer && composer.requestSubmit) composer.requestSubmit(); }
    });
    input.addEventListener("input", function () { var counter = sidebar.querySelector("[data-assistant-count]"); if (counter) { counter.hidden = input.value.length < 1800; counter.textContent = input.value.length + " / 2,000"; } });
  }
  var jump = sidebar.querySelector("[data-assistant-jump]");
  if (jump) jump.addEventListener("click", function () { followingLatest = true; var surface = scrollSurface || transcript; surface.scrollTop = surface.scrollHeight; updateJumpToLatest(); });
  var reconnect = sidebar.querySelector("[data-assistant-reconnect]");
  if (reconnect) reconnect.addEventListener("click", function () { pollFailures = 0; historicalFindingLoads.clear(); publicView.history_load_error=false; refresh(); });
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
  window.addEventListener("pagehide", function () { pageAlive = false; if (pollTimer) window.clearTimeout(pollTimer); pollTimer = 0; if (timerTick) window.clearInterval(timerTick); timerTick = 0; });
  window.addEventListener("pageshow", function (event) { pageAlive = true; if (event.persisted) refresh(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
}());
