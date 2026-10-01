(function () {
  "use strict";

  var shared = window.DeckLabBuilder && window.DeckLabBuilder.shared;
  if (shared) return;

  var api = window.DeckLabBuilder;
  if (!api) return;

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

  var feedbackCache = null;
  var commentDialog = null;
  var commentTextarea = null;
  var activeCommentCardKey = null;
  var activeCommentCardName = null;
  var feedbackStatus = document.querySelector("[data-feedback-status]");
  if (!feedbackStatus) {
    feedbackStatus = document.createElement("p");
    feedbackStatus.setAttribute("data-feedback-status", "");
    feedbackStatus.setAttribute("role", "status");
    feedbackStatus.setAttribute("aria-live", "polite");
    feedbackStatus.className = "dl-feedback-status";
    var rail = document.querySelector(".dl-stats-rail");
    if (rail) rail.appendChild(feedbackStatus);
  }

  var verdictTimer = null;
  var verdictCommitted = 0;

  function deckId() {
    var state = api.getState();
    return state ? state.id : "";
  }

  function loadFeedback() {
    var id = deckId();
    if (!id) return;
    var url = "/api/decks/" + encodeURIComponent(id) + "/feedback";
    fetch(url, { headers: { "X-CSRFToken": csrfToken } })
      .then(function (r) {
        if (!r.ok) throw new Error("Failed to load feedback");
        return r.json();
      })
      .then(function (data) {
        feedbackCache = data;
        applyFeedbackToRows();
        applyDeckVerdict();
      })
      .catch(function () {});
  }

  function putCardFeedback(cardKey, cardName, vote, comment, rowEl) {
    var id = deckId();
    if (!id) return Promise.reject();
    var url =
      "/api/decks/" +
      encodeURIComponent(id) +
      "/feedback/cards/" +
      encodeURIComponent(cardKey);
    return fetch(url, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({ card_name: cardName, vote: vote, comment: comment }),
    }).then(function (r) {
      if (!r.ok) throw new Error("Save failed");
      return r.json();
    });
  }

  function putDeckFeedback(verdict, comment) {
    var id = deckId();
    if (!id) return Promise.reject();
    var url = "/api/decks/" + encodeURIComponent(id) + "/feedback/deck";
    return fetch(url, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({ verdict: verdict, comment: comment }),
    }).then(function (r) {
      if (!r.ok) throw new Error("Save failed");
      return r.json();
    });
  }

  function applyFeedbackToRows() {
    if (!feedbackCache || !feedbackCache.cards) return;
    var view = document.getElementById("table-view");
    if (!view) return;
    var rows = view.querySelectorAll("[data-entry-id]");
    rows.forEach(function (row) {
      var cardKey = row.getAttribute("data-card-key");
      if (!cardKey) {
        var eid = row.getAttribute("data-entry-id");
        if (!eid) return;
        var entry = findEntry(eid);
        if (!entry) return;
        cardKey = entry.oracle_id || "card:" + (entry.card_id || "");
        row.setAttribute("data-card-key", cardKey);
      }
      var fb = feedbackCache.cards[cardKey];
      var upBtn = row.querySelector('[data-vote="up"]');
      var downBtn = row.querySelector('[data-vote="down"]');
      var commentBtn = row.querySelector("[data-card-comment]");
      if (upBtn) {
        var upOn = !!(fb && fb.vote === "up");
        upBtn.setAttribute("aria-pressed", upOn ? "true" : "false");
        upBtn.classList.toggle("is-on", upOn);
      }
      if (downBtn) {
        var downOn = !!(fb && fb.vote === "down");
        downBtn.setAttribute("aria-pressed", downOn ? "true" : "false");
        downBtn.classList.toggle("is-on", downOn);
      }
      if (commentBtn) {
        var hasComment = !!(fb && fb.comment);
        commentBtn.classList.toggle("is-on", hasComment);
        if (hasComment) commentBtn.setAttribute("data-has-comment", "");
        else commentBtn.removeAttribute("data-has-comment");
      }
    });
  }

  function applyDeckVerdict() {
    if (!feedbackCache || !feedbackCache.deck) return;
    var sectionEl = document.querySelector('[data-ext-section="feedback"]');
    if (!sectionEl) return;
    var d = feedbackCache.deck;
    var btns = sectionEl.querySelectorAll("[data-verdict]");
    btns.forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-verdict") === (d.verdict || "") ? "true" : "false");
    });
    var ta = sectionEl.querySelector("[data-verdict-comment]");
    if (ta && ta.value !== (d.comment || "")) {
      ta.value = d.comment || "";
    }
  }

  function findEntry(entryId) {
    var state = api.getState();
    if (!state || !state.entries) return null;
    return state.entries.find(function (e) {
      return e.id === entryId;
    });
  }

  function getCardKey(entry) {
    return entry.oracle_id || "card:" + (entry.card_id || "");
  }

  function feedbackIcon(name) {
    if (window.DeckLabIcons && typeof window.DeckLabIcons.svg === "function") {
      return window.DeckLabIcons.svg(name, { size: 15 });
    }
    return null;
  }

  function ensureFeedbackGroup(row, cardKey, cardName) {
    var group = row.querySelector("[data-card-feedback]");
    if (group) return group;
    group = document.createElement("span");
    group.setAttribute("data-card-feedback", "");
    group.className = "dl-card-feedback";

    var upBtn = document.createElement("button");
    upBtn.setAttribute("data-vote", "up");
    upBtn.setAttribute("aria-pressed", "false");
    upBtn.setAttribute("aria-label", "Good pick");
    upBtn.setAttribute("data-dl-tip", "Good pick");
    upBtn.type = "button";
    upBtn.className = "dl-icon-button dl-fb-btn dl-fb-up";
    var upIcon = feedbackIcon("thumb-up");
    if (upIcon) upBtn.appendChild(upIcon);

    var downBtn = document.createElement("button");
    downBtn.setAttribute("data-vote", "down");
    downBtn.setAttribute("aria-pressed", "false");
    downBtn.setAttribute("aria-label", "Bad pick");
    downBtn.setAttribute("data-dl-tip", "Bad pick");
    downBtn.type = "button";
    downBtn.className = "dl-icon-button dl-fb-btn dl-fb-down";
    var downIcon = feedbackIcon("thumb-down");
    if (downIcon) downBtn.appendChild(downIcon);

    var commentBtn = document.createElement("button");
    commentBtn.setAttribute("data-card-comment", "");
    commentBtn.setAttribute("aria-label", "Comment on " + cardName);
    commentBtn.setAttribute("data-dl-tip", "Comment on " + cardName);
    commentBtn.type = "button";
    commentBtn.className = "dl-icon-button dl-fb-btn dl-fb-comment";
    var commentIcon = feedbackIcon("comment");
    if (commentIcon) commentBtn.appendChild(commentIcon);

    group.appendChild(upBtn);
    group.appendChild(downBtn);
    group.appendChild(commentBtn);

    var existing = row.querySelector("[data-card-feedback]");
    var actions = row.querySelector(".dl-row-actions");
    if (existing) existing.replaceWith(group);
    else if (actions) actions.appendChild(group);
    else row.appendChild(group);

    upBtn.addEventListener("click", function () {
      var current = upBtn.getAttribute("aria-pressed") === "true";
      var newVote = current ? null : "up";
      var prevFb = feedbackCache && feedbackCache.cards ? feedbackCache.cards[cardKey] : null;
      var oldComment = prevFb ? prevFb.comment : null;

      upBtn.setAttribute("aria-pressed", newVote === "up" ? "true" : "false");
      upBtn.classList.toggle("is-on", newVote === "up");
      downBtn.setAttribute("aria-pressed", "false");
      downBtn.classList.remove("is-on");

      var oldVote = prevFb ? prevFb.vote : null;
      if (!feedbackCache) feedbackCache = { cards: {}, deck: null };
      if (!feedbackCache.cards) feedbackCache.cards = {};
      feedbackCache.cards[cardKey] = { vote: newVote, comment: oldComment };

      putCardFeedback(cardKey, cardName, newVote, oldComment).then(function (data) {
        feedbackCache = data;
        applyFeedbackToRows();
        setStatus("");
      }).catch(function () {
        feedbackCache.cards[cardKey] = { vote: oldVote, comment: oldComment };
        applyFeedbackToRows();
        setStatus("Save failed, please try again");
      });
    });

    downBtn.addEventListener("click", function () {
      var current = downBtn.getAttribute("aria-pressed") === "true";
      var newVote = current ? null : "down";
      var prevFb = feedbackCache && feedbackCache.cards ? feedbackCache.cards[cardKey] : null;
      var oldComment = prevFb ? prevFb.comment : null;

      downBtn.setAttribute("aria-pressed", newVote === "down" ? "true" : "false");
      downBtn.classList.toggle("is-on", newVote === "down");
      upBtn.setAttribute("aria-pressed", "false");
      upBtn.classList.remove("is-on");

      var oldVote = prevFb ? prevFb.vote : null;
      if (!feedbackCache) feedbackCache = { cards: {}, deck: null };
      if (!feedbackCache.cards) feedbackCache.cards = {};
      feedbackCache.cards[cardKey] = { vote: newVote, comment: oldComment };

      putCardFeedback(cardKey, cardName, newVote, oldComment).then(function (data) {
        feedbackCache = data;
        applyFeedbackToRows();
        setStatus("");
      }).catch(function () {
        feedbackCache.cards[cardKey] = { vote: oldVote, comment: oldComment };
        applyFeedbackToRows();
        setStatus("Save failed, please try again");
      });
    });

    commentBtn.addEventListener("click", function () {
      activeCommentCardKey = cardKey;
      activeCommentCardName = cardName;
      var prevFb = feedbackCache && feedbackCache.cards ? feedbackCache.cards[cardKey] : null;
      if (!commentDialog) ensureCommentDialog();
      commentTextarea.value = (prevFb && prevFb.comment) || "";
      commentDialog.showModal();
      commentTextarea.focus();
    });

    return group;
  }

  function ensureCommentDialog() {
    if (commentDialog) return;
    commentDialog = document.createElement("dialog");
    commentDialog.setAttribute("data-card-comment-dialog", "");
    commentDialog.className = "dl-dialog";
    var form = document.createElement("form");
    form.method = "dialog";
    var head = document.createElement("div");
    head.className = "dl-dialog-head";
    var title = document.createElement("h2");
    title.textContent = "Comment on card";
    var closeBtn = document.createElement("button");
    closeBtn.className = "dl-icon-button";
    closeBtn.setAttribute("value", "cancel");
    closeBtn.setAttribute("aria-label", "Close comment");
    closeBtn.setAttribute("data-dl-tip", "Close comment");
    closeBtn.textContent = "\u00D7";
    closeBtn.type = "button";
    head.appendChild(title);
    head.appendChild(closeBtn);
    form.appendChild(head);
    var label = document.createElement("label");
    label.textContent = "Your notes (private to you and the Deck Lab owner)";
    commentTextarea = document.createElement("textarea");
    commentTextarea.setAttribute("data-card-comment-input", "");
    commentTextarea.className = "dl-field";
    commentTextarea.rows = 4;
    commentTextarea.maxLength = 2000;
    label.appendChild(commentTextarea);
    form.appendChild(label);
    var actions = document.createElement("div");
    actions.className = "dl-dialog-actions";
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "dl-button";
    cancelBtn.setAttribute("value", "cancel");
    cancelBtn.textContent = "Cancel";
    cancelBtn.type = "button";
    var saveBtn = document.createElement("button");
    saveBtn.className = "dl-button dl-button-primary";
    saveBtn.setAttribute("value", "save");
    saveBtn.textContent = "Save";
    saveBtn.type = "button";
    actions.appendChild(cancelBtn);
    actions.appendChild(saveBtn);
    form.appendChild(actions);
    commentDialog.appendChild(form);
    document.body.appendChild(commentDialog);

    saveBtn.addEventListener("click", function () {
      saveComment();
    });

    closeBtn.addEventListener("click", function () {
      commentDialog.close();
    });

    cancelBtn.addEventListener("click", function () {
      commentDialog.close();
    });

    commentDialog.addEventListener("close", function () {
      if (commentDialog.returnValue === "save") {
        saveComment();
      }
    });
  }

  function saveComment() {
    if (!activeCommentCardKey || !activeCommentCardName) return;
    var text = (commentTextarea.value || "").trim().slice(0, 2000) || null;
    var prevFb = feedbackCache && feedbackCache.cards ? feedbackCache.cards[activeCommentCardKey] : null;
    var oldVote = prevFb ? prevFb.vote : null;
    var oldComment = prevFb ? prevFb.comment : null;

    if (!feedbackCache) feedbackCache = { cards: {}, deck: null };
    if (!feedbackCache.cards) feedbackCache.cards = {};
    feedbackCache.cards[activeCommentCardKey] = { vote: oldVote, comment: text };
    applyFeedbackToRows();

    putCardFeedback(activeCommentCardKey, activeCommentCardName, oldVote, text).then(function (data) {
      feedbackCache = data;
      applyFeedbackToRows();
      setStatus("");
    }).catch(function () {
      feedbackCache.cards[activeCommentCardKey] = { vote: oldVote, comment: oldComment };
      applyFeedbackToRows();
      setStatus("Save failed, please try again");
    });
    commentDialog.close();
  }

  function setStatus(msg) {
    if (feedbackStatus) feedbackStatus.textContent = msg;
  }

  function buildVerdictSection() {
    var sectionEl = api.railSection("feedback", "Your verdict", { tab: "tools" });
    var verdictGroup = sectionEl.querySelector("[data-feedback-verdict-group]");
    if (verdictGroup) return sectionEl;
    verdictGroup = document.createElement("div");
    verdictGroup.setAttribute("data-feedback-verdict-group", "");
    verdictGroup.className = "dl-feedback-verdict";
    verdictGroup.setAttribute("role", "group");
    verdictGroup.setAttribute("aria-label", "Your verdict");

    var segments = document.createElement("div");
    segments.className = "dl-segments";
    var verdicts = [
      { value: "good", label: "Good" },
      { value: "mixed", label: "Mixed" },
      { value: "bad", label: "Bad" },
    ];
    verdicts.forEach(function (v) {
      var btn = document.createElement("button");
      btn.setAttribute("data-verdict", v.value);
      btn.setAttribute("aria-pressed", "false");
      btn.textContent = v.label;
      btn.type = "button";
      btn.className = "dl-fb-btn dl-fb-verdict-btn";
      btn.addEventListener("click", function () {
        var current = btn.getAttribute("aria-pressed") === "true";
        var newVerdict = current ? null : v.value;
        verdictGroup.querySelectorAll("[data-verdict]").forEach(function (b) {
          b.setAttribute("aria-pressed", b.getAttribute("data-verdict") === (newVerdict || "") ? "true" : "false");
        });
        scheduleVerdictSave(newVerdict);
      });
      segments.appendChild(btn);
    });
    verdictGroup.appendChild(segments);

    var commentLabel = document.createElement("label");
    commentLabel.className = "dl-fb-verdict-comment-label";
    var ta = document.createElement("textarea");
    ta.setAttribute("data-verdict-comment", "");
    ta.className = "dl-field";
    ta.rows = 3;
    ta.placeholder = "Add a note about this build…";
    ta.maxLength = 2000;
    commentLabel.appendChild(ta);
    verdictGroup.appendChild(commentLabel);

    var privacy = document.createElement("p");
    privacy.className = "dl-muted dl-fb-privacy";
    privacy.textContent = "Your feedback is private to you and the Deck Lab owner.";
    verdictGroup.appendChild(privacy);

    sectionEl.appendChild(verdictGroup);

    ta.addEventListener("input", function () {
      scheduleVerdictSave(null);
    });
    ta.addEventListener("blur", function () {
      if (verdictTimer) clearTimeout(verdictTimer);
      verdictTimer = null;
      commitVerdict();
    });

    return sectionEl;
  }

  function scheduleVerdictSave(verdictOverride) {
    if (verdictOverride !== undefined) verdictCommitted = 0;
    if (verdictTimer) clearTimeout(verdictTimer);
    verdictTimer = setTimeout(function () {
      verdictTimer = null;
      commitVerdict(verdictOverride);
    }, 1500);
  }

  function commitVerdict(verdictOverride) {
    var sectionEl = document.querySelector('[data-ext-section="feedback"]');
    if (!sectionEl) return;
    var activeBtn = sectionEl.querySelector('[data-verdict][aria-pressed="true"]');
    var verdict = verdictOverride !== undefined ? verdictOverride : (activeBtn ? activeBtn.getAttribute("data-verdict") : null);
    var ta = sectionEl.querySelector("[data-verdict-comment]");
    var comment = ta ? (ta.value || "").trim().slice(0, 2000) || null : null;
    var now = Date.now();
    if (now - verdictCommitted < 500) return;
    verdictCommitted = now;
    putDeckFeedback(verdict, comment).then(function (data) {
      feedbackCache = data;
      applyDeckVerdict();
      setStatus("");
    }).catch(function () {
      setStatus("Save failed, please try again");
    });
  }

  function decorateRows() {
    var view = document.getElementById("table-view");
    if (!view) return;
    var rows = view.querySelectorAll("[data-entry-id]");
    rows.forEach(function (row) {
      if (!row.classList || !row.classList.contains("dl-deck-row")) return;
      if (row.querySelector("[data-card-feedback]")) return;
      var entryId = row.getAttribute("data-entry-id");
      var entry = findEntry(entryId);
      if (!entry) return;
      if (entry.is_commander) return;
      var cardKey = getCardKey(entry);
      row.setAttribute("data-card-key", cardKey);
      ensureFeedbackGroup(row, cardKey, entry.name);
    });
  }

  function onRender() {
    decorateRows();
    applyFeedbackToRows();
    buildVerdictSection();
    applyDeckVerdict();
  }

  if (api.onRender) api.onRender(onRender);

  document.addEventListener("deck-lab:ready", function () {
    loadFeedback();
    onRender();
  });

  if (document.querySelector("#table-view")) {
    loadFeedback();
    onRender();
  }
})();
