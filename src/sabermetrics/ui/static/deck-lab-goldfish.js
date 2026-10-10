(function () {
  "use strict";

  var root = document.querySelector("[data-goldfish-deck]");
  if (!root) return;

  var deckId = String(root.dataset.goldfishDeck || "");
  var endpoint = "/api/decks/" + encodeURIComponent(deckId) + "/diagnostics";
  var tests = [
    ["opening", "Opening hand"],
    ["turns", "Turns"],
    ["commander", "Commander removed"],
    ["wipe", "After a wipe"],
    ["finish", "Finish"]
  ];
  var state = { history: [], selectedId: "", revision: root.dataset.revision || "", timeout: 0, elapsed: 0, pollStarted: 0 };
  var status = document.querySelector("[data-gf-status]");
  var alertBox = document.querySelector("[data-gf-alert]");
  var historyList = document.querySelector("[data-gf-history]");
  var parentSelect = document.querySelector("[data-gf-run-form] [name='parent_id']");

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function setStatus(message) {
    if (status) status.textContent = message || "";
  }

  function showError(message) {
    alertBox.textContent = message || "Something went wrong while loading diagnostics.";
    alertBox.hidden = false;
  }

  function clearError() {
    alertBox.textContent = "";
    alertBox.hidden = true;
  }

  function request(url, options) {
    return fetch(url, options || {}).then(function (response) {
      return response.text().then(function (body) {
        var data = {};
        try { data = body ? JSON.parse(body) : {}; } catch (_) { data = {}; }
        if (!response.ok) throw new Error(data.error || data.message || "Request failed (" + response.status + ").");
        return data;
      });
    });
  }

  function humanize(value) { return String(value || "Metric").replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }); }
  function percent(value) { return typeof value === "number" && Number.isFinite(value) ? (value * 100).toFixed(1).replace(/\.0$/, "") + "%" : "Not measured"; }
  function formatValue(value, key) {
    if (value === null || value === undefined || value === "") return "Not measured";
    if (typeof value === "number" && key && /probability/i.test(key)) return percent(value);
    if (typeof value === "number") return Number.isFinite(value) ? String(value) : "Not measured";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map(function (item) { return formatValue(item, key); }).join(", ") || "Not measured";
    if (typeof value === "object") {
      if (typeof value.probability === "number") {
        var outcome = value.successes !== undefined && value.trials !== undefined ? value.successes + "/" + value.trials + " · " : "";
        outcome += percent(value.probability);
        if (Array.isArray(value.ci95)) outcome += " · 95% CI " + percent(value.ci95[0]) + "–" + percent(value.ci95[1]);
        return outcome;
      }
      return Object.keys(value).map(function (name) { return humanize(name) + ": " + formatValue(value[name], name); }).join(" · ") || "Not measured";
    }
    return String(value);
  }

  function metricRows(metrics) {
    var rows = [];
    function visit(value, path, depth) {
      if (depth > 4) return;
      if (value === undefined || value === null) { rows.push({ key: path, value: null }); return; }
      if (Array.isArray(value)) {
        if (value.every(function (item) { return typeof item === "number" && Number.isFinite(item); }) || value.every(function (item) { return item && typeof item === "object" && item.turn !== undefined; })) {
          rows.push({ key: path, value: value, curve: true });
        } else {
          rows.push({ key: path, value: value });
        }
      } else if (typeof value === "object") {
        if (typeof value.probability === "number" || (value.successes !== undefined && value.trials !== undefined)) rows.push({ key: path, value: value });
        else Object.keys(value).forEach(function (key) { visit(value[key], path ? path + " · " + key : key, depth + 1); });
      } else {
        rows.push({ key: path, value: value });
      }
    }
    visit(metrics || {}, "", 0);
    return rows;
  }

  function renderCurve(row) {
    var wrap = node("div", "gf-curve");
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", humanize(row.key) + " by turn");
    var values = row.value.map(function (point) { return typeof point === "number" ? point : point.probability; });
    var max = Math.max.apply(Math, values.filter(function (value) { return typeof value === "number" && Number.isFinite(value); }).concat([1]));
    row.value.forEach(function (point, index) {
      var value = typeof point === "number" ? point : point.probability;
      var column = node("div", "gf-curve-column");
      var amount = node("span", "gf-curve-value", typeof value === "number" ? percent(value) : "Not measured");
      var bar = node("span", "gf-curve-bar");
      if (typeof value === "number" && Number.isFinite(value)) bar.style.height = Math.max(3, (value / max) * 100) + "%";
      bar.setAttribute("aria-hidden", "true");
      var label = node("span", "gf-curve-label", String(point && point.turn !== undefined ? point.turn : index + 1));
      column.appendChild(amount);
      column.appendChild(bar);
      column.appendChild(label);
      wrap.appendChild(column);
    });
    return wrap;
  }

  function renderMetrics(container, metrics) {
    var rows = metricRows(metrics);
    if (!rows.length) {
      container.appendChild(node("p", "gf-muted", "No metrics were included; no score is inferred."));
      return;
    }
    var table = node("table", "gf-metrics");
    var caption = node("caption", "gf-visually-hidden", "Measured metrics");
    table.appendChild(caption);
    var body = node("tbody");
    rows.forEach(function (row) {
      var tr = node("tr");
      tr.appendChild(node("th", "", humanize(row.key) || "Metric"));
      var valueCell = node("td");
      if (row.curve) {
        valueCell.appendChild(renderCurve(row));
        var fallback = node("details", "gf-curve-fallback");
        fallback.appendChild(node("summary", "", "View values"));
        var curveTable = node("table", "gf-metrics gf-curve-table");
        var curveBody = node("tbody");
        row.value.forEach(function (point, index) {
          var curveRow = node("tr");
          curveRow.appendChild(node("th", "", "Turn " + formatValue(point && point.turn !== undefined ? point.turn : index + 1)));
          curveRow.appendChild(node("td", "", formatValue(point)));
          curveBody.appendChild(curveRow);
        });
        curveTable.appendChild(curveBody);
        fallback.appendChild(curveTable);
        valueCell.appendChild(fallback);
      } else {
        valueCell.textContent = formatValue(row.value, row.key);
      }
      tr.appendChild(valueCell);
      body.appendChild(tr);
    });
    table.appendChild(body);
    container.appendChild(table);
  }

  function renderListDetails(parent, label, items) {
    if (!items || !items.length) return;
    var details = node("details", "gf-detail-list");
    details.appendChild(node("summary", "", label + " (" + items.length + ")"));
    var list = node("ul");
    items.forEach(function (item) {
      var li = node("li");
      if (item && typeof item === "object") {
        li.appendChild(node("strong", "", item.name || item.title || item.test || "Detail"));
        li.appendChild(node("span", "", item.reason || item.text || item.description || formatValue(item)));
      } else li.textContent = formatValue(item);
      list.appendChild(li);
    });
    details.appendChild(list);
    parent.appendChild(details);
  }

  function renderTestCard(key, title, report) {
    var card = document.querySelector("[data-test='" + key + "']");
    card.replaceChildren();
    card.appendChild(node("p", "gf-eyebrow", "Test " + (tests.findIndex(function (entry) { return entry[0] === key; }) + 1)));
    card.appendChild(node("h3", "", title));
    var data = report || {};
    var resultStatus = data.status || (report ? "unsupported" : "unreported");
    var statusLabel = resultStatus === "measured" ? "Measured" : resultStatus === "partial" ? "Partial coverage" : resultStatus === "unreported" ? "Not reported" : "Unsupported / not measured";
    var statusEl = node("p", "gf-test-status gf-status-" + resultStatus, statusLabel);
    card.appendChild(statusEl);
    if (resultStatus === "unreported") {
      card.appendChild(node("p", "gf-placeholder", "No test result was included for this run. Performance is unknown."));
    } else if (resultStatus === "unsupported") {
      card.appendChild(node("p", "gf-placeholder", "This test has no measured result for the current run. Unknown performance is not treated as zero."));
    }
    var metrics = node("div", "gf-test-metrics");
    renderMetrics(metrics, data.metrics || {});
    card.appendChild(metrics);
    renderListDetails(card, "Limitations", data.limitations);
    renderListDetails(card, "Run traces", data.traces);
  }

  function renderCoverage(result) {
    var coverage = result && result.coverage || {};
    var summary = document.querySelector("[data-gf-coverage-summary]");
    var detail = document.querySelector("[data-gf-coverage-details]");
    summary.textContent = coverage.modeled_cards === undefined ? "Coverage was not reported for this run." : formatValue(coverage.modeled_cards) + " of " + formatValue(coverage.total_cards) + " card copies fully modeled.";
    detail.replaceChildren();
    var unsupported = coverage.unsupported_cards || [];
    if (unsupported.length) {
      detail.appendChild(node("p", "gf-unsupported-callout", unsupported.length + " unsupported card" + (unsupported.length === 1 ? "" : "s") + " affect confidence in this report."));
      var ul = node("ul", "gf-unsupported-list");
      unsupported.forEach(function (card) {
        var li = node("li");
        li.appendChild(node("strong", "", card.name || "Unknown card"));
        li.appendChild(node("span", "", card.reason || "No reason provided."));
        ul.appendChild(li);
      });
      detail.appendChild(ul);
    } else detail.appendChild(node("p", "gf-muted", "No unsupported cards were listed."));
    renderListDetails(detail, "Assumptions", coverage.assumptions);
  }

  function renderRecommendations(view) {
    var section = document.querySelector("[data-gf-recommendations]");
    var listRoot = document.querySelector("[data-gf-recommendation-list]");
    listRoot.replaceChildren();
    var list = view && view.recommendations || [];
    section.hidden = !list.length;
    list.forEach(function (item) {
      var article = node("article", "gf-recommendation");
      article.appendChild(node("p", "gf-eyebrow", item.test || "Recommendation"));
      article.appendChild(node("h3", "", item.title || "Review this test result"));
      if (item.reason) article.appendChild(node("p", "", item.reason));
      if (item.next_step) article.appendChild(node("p", "gf-recommendation-next", item.next_step));
      listRoot.appendChild(article);
    });
  }

  function renderComparisons(view) {
    var section = document.querySelector("[data-gf-comparison]");
    var body = document.querySelector("[data-gf-comparison-body]");
    var label = document.querySelector("[data-gf-comparison-label]");
    body.replaceChildren();
    var comparisons = view && view.comparisons || [];
    section.hidden = !comparisons.length;
    if (!comparisons.length) return;
    label.textContent = "Changes shown by the evaluator";
    comparisons.forEach(function (comparison) {
      var article = node("article", "gf-comparison-item");
      article.appendChild(node("h3", "", comparison.label || "Comparison"));
      if (comparison.note) article.appendChild(node("p", "gf-muted", comparison.note));
      if (comparison.deltas && comparison.deltas.length) {
        var table = node("table", "gf-delta-table");
        var head = node("thead");
        var headRow = node("tr");
        ["Test", "Metric", "Before", "After", "Change"].forEach(function (value) { headRow.appendChild(node("th", "", value)); });
        head.appendChild(headRow);
        table.appendChild(head);
        var tbody = node("tbody");
        comparison.deltas.forEach(function (delta) {
          var row = node("tr");
          [delta.test, delta.metric, delta.before, delta.after, delta.delta].forEach(function (value) { row.appendChild(node("td", "", formatValue(value))); });
          tbody.appendChild(row);
        });
        table.appendChild(tbody);
        article.appendChild(table);
      } else article.appendChild(node("p", "gf-muted", "No comparable numeric changes were reported."));
      body.appendChild(article);
    });
  }

  function renderView(view) {
    var meta = document.querySelector("[data-gf-run-meta]");
    if (!view) {
      meta.textContent = "No completed run selected yet.";
      renderCoverage(null);
      tests.forEach(function (test) { renderTestCard(test[0], test[1], null); });
      renderRecommendations(null);
      renderComparisons(null);
      return;
    }
    var settings = view.settings || {};
    meta.textContent = "Run " + (view.id || "—") + " · " + (view.status || "unknown") + " · revision " + formatValue(view.deck_revision) + " · " + formatValue(settings.games) + " runs · seed " + formatValue(settings.seed) + (view.result && view.result.engine_version ? " · engine " + view.result.engine_version : "");
    var stale = view.stale ? " This result is stale because the deck changed." : "";
    var unsupportedCount = (((view.result || {}).coverage || {}).unsupported_cards || []).length;
    alertBox.textContent = (view.status === "failed" && view.error ? "Run failed: " + view.error : "") + stale + (unsupportedCount ? " " + unsupportedCount + " unsupported card" + (unsupportedCount === 1 ? "" : "s") + " limit this run's conclusions." : "");
    alertBox.hidden = !alertBox.textContent;
    renderCoverage(view.result);
    var reports = view.result && view.result.tests || {};
    tests.forEach(function (test) { renderTestCard(test[0], test[1], reports[test[0]]); });
    renderRecommendations(view);
    renderComparisons(view);
  }

  function renderHistory() {
    historyList.replaceChildren();
    var empty = document.querySelector("[data-gf-history-empty]");
    empty.hidden = state.history.length > 0;
    parentSelect.replaceChildren();
    parentSelect.appendChild(new Option("Automatic (latest run)", ""));
    state.history.forEach(function (view, index) {
      var option = new Option("Run " + (index + 1) + " · " + (view.status || "unknown") + (view.hypothesis ? " · " + view.hypothesis : ""), view.id);
      if (view.status !== "done") option.disabled = true;
      parentSelect.appendChild(option);
      var item = node("li", "gf-history-item");
      var button = node("button", "gf-history-select", "");
      button.type = "button";
      button.setAttribute("aria-pressed", view.id === state.selectedId ? "true" : "false");
      button.dataset.runId = view.id || "";
      button.appendChild(node("span", "gf-history-status gf-history-" + (view.status || "unknown"), view.status || "Unknown"));
      button.appendChild(node("strong", "", view.hypothesis || ((view.settings || {}).plan_id || "Baseline run")));
      button.appendChild(node("span", "gf-history-detail", "Revision " + formatValue(view.deck_revision) + " · " + formatValue((view.settings || {}).games) + " runs" + (view.stale ? " · stale" : "")));
      button.addEventListener("click", function () { state.selectedId = String(view.id || ""); renderHistory(); renderView(view); });
      item.appendChild(button);
      historyList.appendChild(item);
    });
  }

  function selectedView() {
    return state.history.find(function (view) { return String(view.id) === state.selectedId; }) || null;
  }

  function applyHistory(data, preferLatest) {
    state.history = Array.isArray(data.history) ? data.history : [];
    if (data.revision !== undefined) state.revision = String(data.revision);
    if (preferLatest || !state.selectedId || !state.history.some(function (view) { return String(view.id) === state.selectedId; })) {
      state.selectedId = state.history.length ? String(state.history[0].id || "") : "";
    }
    renderHistory();
    renderView(selectedView());
  }

  function loadHistory(preferLatest) {
    clearError();
    return request(endpoint).then(function (data) { applyHistory(data, preferLatest); }).catch(function (error) { showError(error.message); });
  }

  function schedulePoll() {
    if (state.timeout) window.clearTimeout(state.timeout);
    if (Date.now() - state.pollStarted > 5 * 60 * 1000) {
      state.timeout = 0;
      setStatus("This run is still pending after five minutes. Refresh to check again.");
      return;
    }
    state.timeout = window.setTimeout(function () {
      request(endpoint).then(function (data) {
        applyHistory(data, false);
        var active = state.history.find(function (view) { return String(view.id) === state.selectedId; });
        if (active && (active.status === "queued" || active.status === "running")) schedulePoll();
        else { setStatus(active && active.status === "failed" ? "Run failed. Review the error and retry." : "Run finished."); }
      }).catch(function (error) {
        showError(error.message + " You can refresh to retry.");
        setStatus("Could not check run status.");
      });
    }, state.elapsed < 30000 ? 1800 : 3500);
    state.elapsed += state.elapsed < 30000 ? 1800 : 3500;
  }

  parentSelect.addEventListener("change", function () {
    var parent = state.history.find(function (view) { return String(view.id) === parentSelect.value; });
    if (!parent || !parent.settings) return;
    var form = document.querySelector("[data-gf-run-form]");
    form.elements.games.value = parent.settings.games;
    form.elements.seed.value = parent.settings.seed;
    form.elements.plan_id.value = parent.settings.plan_id;
  });
  document.querySelector("[data-gf-fresh]").addEventListener("click", function () {
    var random = new Uint32Array(1);
    window.crypto.getRandomValues(random);
    var form = document.querySelector("[data-gf-run-form]");
    form.elements.seed.value = random[0];
    parentSelect.value = "";
    setStatus("Fresh trials selected. Keep the list unchanged to confirm a result with a new shuffle seed.");
  });

  document.querySelector("[data-gf-run-form]").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var submit = form.querySelector("[data-gf-submit]");
    var csrf = document.querySelector("meta[name='csrf-token']");
    var payload = {
      expected_revision: Number(state.revision),
      plan_id: form.elements.plan_id.value,
      games: Number(form.elements.games.value),
      seed: Number(form.elements.seed.value),
      parent_id: form.elements.parent_id.value || null,
      hypothesis: form.elements.hypothesis.value.trim()
    };
    clearError();
    submit.disabled = true;
    setStatus("Queueing diagnostics…");
    request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.content : "" },
      body: JSON.stringify(payload)
    }).then(function (data) {
      if (state.timeout) window.clearTimeout(state.timeout);
      state.pollStarted = Date.now();
      state.elapsed = 0;
      setStatus("Diagnostics queued. Checking for results…");
      if (data.history) applyHistory(data, true);
      else if (data.id) {
        state.selectedId = String(data.id);
        loadHistory(true).then(schedulePoll);
        return;
      }
      schedulePoll();
    }).catch(function (error) {
      showError(error.message);
      setStatus("Run could not be queued.");
    }).finally(function () { submit.disabled = false; });
  });

  document.querySelector("[data-gf-refresh]").addEventListener("click", function () {
    setStatus("Refreshing history…");
    loadHistory(true).then(function () { setStatus("History refreshed."); });
  });
  window.addEventListener("pagehide", function () { if (state.timeout) window.clearTimeout(state.timeout); });
  loadHistory(true);
})();
