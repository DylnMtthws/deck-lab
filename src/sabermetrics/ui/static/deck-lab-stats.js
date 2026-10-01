(function () {
  "use strict";

  var PRIVATE_ZONE_NAMES = {
    sideboard: 1, notes: 1, note: 1, maybeboard: 1, maybe: 1, draft: 1, considering: 1
  };

  function isLibraryZoneName(name) {
    return !Object.prototype.hasOwnProperty.call(PRIVATE_ZONE_NAMES, String(name || "Unsorted").trim().toLowerCase());
  }

  function isLibraryEntry(entry, zones) {
    if (entry.is_commander) return false;
    var zone = zones.find(function (z) { return z.id === entry.zone_id; });
    var name = zone ? zone.name : "Unsorted";
    return isLibraryZoneName(name);
  }

  function libraryEntries(state) {
    return state.entries.filter(function (entry) { return isLibraryEntry(entry, state.zones); });
  }

  function bucketLabel(bucketIndex) {
    return bucketIndex === 5 ? "5+" : String(bucketIndex);
  }

  // ---------------------------------------------------------------------------
  // Curve filter
  // ---------------------------------------------------------------------------

  var _activeCurveBin = null;

  function curveBucket(entry) {
    if (/Land/i.test(entry.type_line || "")) return -1;
    return Math.min(5, Math.floor(Math.max(0, Number(entry.mana_value || 0))));
  }

  function _curveFilterFn(bucketIndex) {
    return function (entry) {
      return curveBucket(entry) === bucketIndex;
    };
  }

  function handleCurveClick(binElement, bucketIndex) {
    return function () {
      var label = "Mana value " + bucketLabel(bucketIndex);
      if (_activeCurveBin === bucketIndex) {
        _activeCurveBin = null;
        window.DeckLabBuilder.setEntryFilter(null);
      } else {
        _activeCurveBin = bucketIndex;
        window.DeckLabBuilder.setEntryFilter(_curveFilterFn(bucketIndex), label);
      }
    };
  }

  function reapplyActiveCurveClass() {
    document.querySelectorAll(".dl-curve-bin").forEach(function (bin) {
      var bucket = parseInt(bin.getAttribute("data-mana-bucket"), 10);
      bin.classList.toggle("is-active", !isNaN(bucket) && bucket === _activeCurveBin);
    });
  }

  // ---------------------------------------------------------------------------
  // Pips vs sources
  // ---------------------------------------------------------------------------

  function countPips(manaCost) {
    var raw = String(manaCost || "");
    var counts = { W: 0, U: 0, B: 0, R: 0, G: 0 };
    var matches = raw.match(/\{([^}]+)\}/g) || [];
    matches.forEach(function (symbol) {
      var inner = symbol.slice(1, -1);
      var letters = inner.replace(/[^WUBRG]/g, "");
      letters.split("").forEach(function (ch) {
        if (counts[ch] !== undefined) counts[ch]++;
      });
    });
    return counts;
  }

  function commanderColorIdentity(state) {
    var ids = new Set();
    state.entries.filter(function (e) { return !!e.is_commander; }).forEach(function (e) {
      (e.color_identity || []).forEach(function (c) { ids.add(c); });
    });
    if (ids.size === 0) { ids.add("W"); ids.add("U"); ids.add("B"); ids.add("R"); ids.add("G"); }
    return ids;
  }

  function getAddClauseColor(oracleText) {
    var text = String(oracleText || "");
    var addMatch = text.match(/Add\s+\{([^}]+)\}/);
    if (!addMatch) return null;
    var inner = addMatch[1];
    if (inner === "C") return null;
    if (inner === "any color" || inner === "mana of any") return "ANY";
    var colors = [];
    var chars = inner.split(/[/,]/);
    chars.forEach(function (ch) {
      ch = ch.trim();
      if (ch === "W") colors.push("W");
      else if (ch === "U") colors.push("U");
      else if (ch === "B") colors.push("B");
      else if (ch === "R") colors.push("R");
      else if (ch === "G") colors.push("G");
    });
    if (colors.length > 1) return "ANY";
    return colors[0] || null;
  }

  function isManaProducer(entry) {
    var type = String(entry.type_line || "");
    var isArtifactCreature = /Artifact/i.test(type) || /Creature/i.test(type);
    return isArtifactCreature && /\bAdd\s+\{/.test(String(entry.oracle_text || ""));
  }

  function pipsAndSources(state) {
    var libEntries = libraryEntries(state);
    var totalColoredPips = 0;
    var pipCounts = { W: 0, U: 0, B: 0, R: 0, G: 0 };
    var sourceCounts = { W: 0, U: 0, B: 0, R: 0, G: 0 };

    libEntries.forEach(function (entry) {
      var qty = Number(entry.quantity || 0);
      if (/Land/i.test(entry.type_line || "")) return;
      var pips = countPips(entry.mana_cost);
      Object.keys(pipCounts).forEach(function (c) {
        pipCounts[c] += pips[c] * qty;
      });
      totalColoredPips += (pips.W + pips.U + pips.B + pips.R + pips.G) * qty;
    });

    libEntries.forEach(function (entry) {
      var qty = Number(entry.quantity || 0);
      var type = String(entry.type_line || "");
      if (/Land/i.test(type)) {
        var color = getAddClauseColor(entry.oracle_text);
        if (!color) return;
        if (color === "ANY") {
          Object.keys(sourceCounts).forEach(function (c) { sourceCounts[c] += qty; });
        } else {
          sourceCounts[color] += qty;
        }
      } else if (isManaProducer(entry)) {
        var color = getAddClauseColor(entry.oracle_text);
        if (!color) return;
        if (color === "ANY") {
          Object.keys(sourceCounts).forEach(function (c) { sourceCounts[c] += qty; });
        } else {
          sourceCounts[color] += qty;
        }
      }
    });

    var result = {};
    Object.keys(pipCounts).forEach(function (c) {
      result[c] = {
        pips: pipCounts[c],
        pipShare: totalColoredPips > 0 ? Math.round(pipCounts[c] / totalColoredPips * 100) : 0,
        sources: sourceCounts[c],
      };
    });
    return result;
  }

  var PIP_COLORS = { W: "#ede7cf", U: "#5aa6f0", B: "#6b5b8c", R: "#e06a5e", G: "#57d39b" };

  function renderPipsSection(commanderIds, pipsData) {
    var section = window.DeckLabBuilder.railSection("pips", "Color requirements", { tab: "deck" });
    section.replaceChildren();
    var heading = document.createElement("h2");
    heading.className = "dl-eyebrow";
    heading.textContent = "Color requirements";
    section.appendChild(heading);
    var sorted = Array.from(commanderIds).sort();
    sorted.forEach(function (color) {
      var data = pipsData[color] || { pips: 0, pipShare: 0, sources: 0 };
      var row = document.createElement("div");
      row.className = "dl-pip-row";
      row.setAttribute("data-pips-color", color);
      var mana = document.createElement("i");
      mana.className = "mana mana-" + color;
      mana.textContent = color;
      row.appendChild(mana);
      var bar = document.createElement("span");
      bar.className = "dl-pip-bar";
      var fill = document.createElement("i");
      fill.style.width = Math.max(0, Math.min(100, data.pipShare)) + "%";
      fill.style.background = PIP_COLORS[color] || "var(--muted)";
      bar.appendChild(fill);
      row.appendChild(bar);
      var stat = document.createElement("span");
      stat.className = "dl-pip-stat";
      stat.appendChild(document.createTextNode(data.pipShare + "% · "));
      var count = document.createElement("b");
      count.textContent = String(data.sources);
      stat.appendChild(count);
      stat.appendChild(document.createTextNode(" src"));
      row.appendChild(stat);
      section.appendChild(row);
    });
    var rule = document.createElement("p");
    rule.className = "dl-muted";
    rule.textContent = "Pips needed vs. lands and dorks that make each color.";
    section.appendChild(rule);
  }

  // ---------------------------------------------------------------------------
  // Odds calculator
  // ---------------------------------------------------------------------------

  function hypergeomAtLeast(N, K, n, k) {
    if (k <= 0) return 1.0;
    if (k > n || K < k) return 0.0;
    // Compute P(X >= k) = 1 - P(X <= k-1) using cumulative product
    // P(X = i) = C(K, i) * C(N-K, n-i) / C(N, n)
    var prob = 0;
    for (var i = k; i <= Math.min(n, K); i++) {
      if (N - K < n - i) continue;
      prob += hypergeomExact(N, K, n, i);
    }
    return Math.min(1.0, Math.max(0.0, prob));
  }

  function hypergeomExact(N, K, n, k) {
    if (k < 0 || k > K || k > n || n - k > N - K) return 0.0;
    // Compute C(K, k) * C(N-K, n-k) / C(N, n) using iterative product to avoid overflow
    var result = 1.0;
    var i;
    // C(K, k)
    for (i = 1; i <= k; i++) {
      result *= (K - k + i) / i;
    }
    // C(N-K, n-k)
    for (i = 1; i <= n - k; i++) {
      result *= (N - K - (n - k) + i) / i;
    }
    // Divide by C(N, n)
    for (i = 1; i <= n; i++) {
      result /= (N - n + i) / i;
    }
    return result;
  }

  function roleLabelMap() {
    var map = {};
    if (window.DeckLabBuilder) {
      var builder = window.DeckLabBuilder;
      if (builder._roleOptions) {
        builder._roleOptions.forEach(function (opt) {
          map[opt[0]] = opt[1];
        });
      }
    }
    if (!map.ramp) {
      var options = [
        ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"],
        ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"],
        ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
        ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
        ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
      ];
      options.forEach(function (opt) { map[opt[0]] = opt[1]; });
    }
    return map;
  }

  // ---------------------------------------------------------------------------
  // Persisted user state for odds and sample hand
  // ---------------------------------------------------------------------------

  var _oddsCategory = "__lands__";
  var _oddsSeen = 7;
  var _oddsNeed = 1;
  var _hand = [];

  function renderOddsSection(state) {
    var section = window.DeckLabBuilder.railSection("odds", "Draw odds", { tab: "tools" });
    section.replaceChildren();
    var heading = document.createElement("h2");
    heading.className = "dl-eyebrow";
    heading.textContent = "Draw odds";
    section.appendChild(heading);

    var libEntries = libraryEntries(state);
    var N = libEntries.reduce(function (n, e) { return n + Number(e.quantity || 0); }, 0);

    // Category select
    var select = document.createElement("select");
    select.className = "dl-select";
    select.setAttribute("data-odds-category", "");
    select.setAttribute("aria-label", "Card category");

    // Lands option
    var landOpt = document.createElement("option");
    landOpt.value = "__lands__";
    landOpt.textContent = "Lands";
    select.appendChild(landOpt);

    // Role options
    var roles = roleLabelMap();
    var usedRoles = {};
    libEntries.forEach(function (e) {
      if (e.role && roles[e.role]) usedRoles[e.role] = roles[e.role];
    });
    Object.keys(usedRoles).sort().forEach(function (key) {
      var opt = document.createElement("option");
      opt.value = key;
      opt.textContent = usedRoles[key];
      select.appendChild(opt);
    });

    // Restore previous category selection, falling back to Lands if no longer present
    var categoryExists = false;
    for (var ci = 0; ci < select.children.length; ci++) {
      if (select.children[ci].value === _oddsCategory) {
        categoryExists = true;
        break;
      }
    }
    select.value = categoryExists ? _oddsCategory : "__lands__";
    if (!categoryExists) _oddsCategory = "__lands__";

    // Clamp seen to N
    if (_oddsSeen > N) _oddsSeen = N;

    var catCount = function () {
      var cat = select.value;
      var count = 0;
      libEntries.forEach(function (e) {
        var qty = Number(e.quantity || 0);
        if (cat === "__lands__") {
          if (/Land/i.test(e.type_line || "")) count += qty;
        } else if (e.role === cat) {
          count += qty;
        }
      });
      return count;
    };

    // Seen input
    var seenInput = document.createElement("input");
    seenInput.type = "number";
    seenInput.className = "dl-field dl-inline-field";
    seenInput.setAttribute("data-odds-seen", "");
    seenInput.value = String(_oddsSeen);
    seenInput.min = "1";
    seenInput.max = String(N);
    seenInput.setAttribute("aria-label", "Cards drawn");

    // Need input
    var needInput = document.createElement("input");
    needInput.type = "number";
    needInput.className = "dl-field dl-inline-field";
    needInput.setAttribute("data-odds-need", "");
    needInput.value = String(_oddsNeed);
    needInput.min = "0";
    needInput.setAttribute("aria-label", "Minimum cards of this category needed");

    // Result output
    var resultOut = document.createElement("div");
    resultOut.className = "dl-odds-result";
    resultOut.setAttribute("data-odds-result", "");
    var detailOut = document.createElement("p");
    detailOut.className = "dl-muted dl-odds-detail";
    detailOut.setAttribute("data-odds-detail", "");

    function updateOdds() {
      var n = parseInt(seenInput.value, 10) || 7;
      var k = parseInt(needInput.value, 10) || 0;
      var K = catCount();
      var prob = hypergeomAtLeast(N, K, n, k);
      var categoryLabel = "";
      for (var i_ = 0; i_ < select.children.length; i_++) {
        if (select.children[i_].value === select.value) {
          categoryLabel = select.children[i_].textContent;
          break;
        }
      }
      // Persist current values
      _oddsCategory = select.value;
      _oddsSeen = n;
      _oddsNeed = k;
      var pretty = categoryLabel ? categoryLabel.toLowerCase() : "cards";
      resultOut.textContent = (prob * 100).toFixed(1) + "%";
      detailOut.textContent = K + " " + pretty + " in " + N + " cards";
    }

    select.addEventListener("change", updateOdds);
    seenInput.addEventListener("input", updateOdds);
    needInput.addEventListener("input", updateOdds);

    var card = document.createElement("div");
    card.className = "dl-odds-card";
    card.appendChild(resultOut);
    var sentence = document.createElement("p");
    sentence.className = "dl-odds-sentence";
    sentence.appendChild(document.createTextNode("to see at least "));
    sentence.appendChild(needInput);
    sentence.appendChild(document.createTextNode(" "));
    sentence.appendChild(select);
    sentence.appendChild(document.createTextNode(" in "));
    sentence.appendChild(seenInput);
    sentence.appendChild(document.createTextNode(" cards"));
    card.appendChild(sentence);
    card.appendChild(detailOut);
    section.appendChild(card);

    // Initial compute
    updateOdds();
  }

  // ---------------------------------------------------------------------------
  // Sample hand
  // ---------------------------------------------------------------------------

  function sampleHand(state, count, rng) {
    rng = rng || Math.random;
    var libEntries = libraryEntries(state);
    // Expand by quantity
    var expanded = [];
    libEntries.forEach(function (entry) {
      var qty = Number(entry.quantity || 0);
      for (var i = 0; i < qty; i++) {
        expanded.push(entry);
      }
    });
    // Fisher-Yates partial shuffle
    var drawn = [];
    var actualCount = Math.min(count, expanded.length);
    for (var i = 0; i < actualCount; i++) {
      var j = i + Math.floor(rng() * (expanded.length - i));
      var tmp = expanded[i];
      expanded[i] = expanded[j];
      expanded[j] = tmp;
      drawn.push(expanded[i]);
    }
    return drawn;
  }

  function handImage(entry) {
    if (entry.image_uri) return entry.image_uri;
    return entry.name
      ? "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + encodeURIComponent(entry.name)
      : "";
  }

  function renderSampleHandSection(state) {
    var section = window.DeckLabBuilder.railSection("sample-hand", "Sample hand", { tab: "tools" });
    section.replaceChildren();
    var heading = document.createElement("h2");
    heading.className = "dl-eyebrow";
    heading.textContent = "Sample hand";
    section.appendChild(heading);

    // Drop entries from _hand that no longer exist in library zones
    var currentIds = {};
    libraryEntries(state).forEach(function (e) { currentIds[e.id] = true; });
    _hand = _hand.filter(function (entry) { return currentIds[entry.id]; });

    var drawBtn = document.createElement("button");
    drawBtn.type = "button";
    drawBtn.className = "dl-button is-primary is-sm";
    drawBtn.setAttribute("data-sample-draw", "");
    drawBtn.textContent = "Draw 7";

    var nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "dl-button is-sm";
    nextBtn.setAttribute("data-sample-next", "");
    nextBtn.textContent = "Draw a card";
    nextBtn.disabled = true;

    var list = document.createElement("div");
    list.className = "dl-hand";
    list.setAttribute("data-sample-cards", "");

    var landCount = document.createElement("p");
    landCount.className = "dl-muted";
    landCount.setAttribute("data-sample-lands", "");

    function handSummary(hand) {
      var counts = {};
      hand.forEach(function (entry) {
        var key = /Land/i.test(entry.type_line || "") ? "lands" : (entry.role || "other");
        counts[key] = (counts[key] || 0) + 1;
      });
      var order = ["lands"];
      Object.keys(counts).sort().forEach(function (key) {
        if (order.indexOf(key) < 0) order.push(key);
      });
      return order.filter(function (key) { return counts[key]; }).map(function (key) {
        var n = counts[key];
        var label = key === "lands" ? (n === 1 ? "land" : "lands") : key;
        return n + " " + label;
      }).join(" · ");
    }

    function updateHandDisplay() {
      list.replaceChildren();
      _hand.forEach(function (entry) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dl-hand-card";
        btn.setAttribute("data-entry-id", entry.id);
        btn.setAttribute("aria-label", "Focus " + entry.name);
        var url = handImage(entry);
        if (url) {
          var img = document.createElement("img");
          img.alt = "";
          img.src = url;
          btn.appendChild(img);
        }
        var name = document.createElement("span");
        name.className = url ? "dl-visually-hidden" : "dl-hand-name";
        name.textContent = entry.name;
        btn.appendChild(name);
        btn.addEventListener("click", function () {
          window.DeckLabBuilder.focusEntry(entry.id);
        });
        list.appendChild(btn);
      });
      landCount.textContent = handSummary(_hand);
      if (_hand.length > 0 && window.DeckLabBuilder.getState) {
        var libEntries = libraryEntries(window.DeckLabBuilder.getState());
        var total = libEntries.reduce(function (n, e) { return n + Number(e.quantity || 0); }, 0);
        if (total < 7) {
          landCount.textContent += (landCount.textContent ? " " : "") + "(only " + total + " cards in library)";
        }
      }
    }

    drawBtn.addEventListener("click", function () {
      var libEntries = libraryEntries(state);
      var total = libEntries.reduce(function (n, e) { return n + Number(e.quantity || 0); }, 0);
      _hand = sampleHand(state, 7, Math.random);
      updateHandDisplay();
      nextBtn.disabled = !_hand.length;
    });

    nextBtn.addEventListener("click", function () {
      var libEntries = libraryEntries(state);
      var total = libEntries.reduce(function (n, e) { return n + Number(e.quantity || 0); }, 0);
      var newCards = sampleHand(state, _hand.length + 1, Math.random);
      _hand = newCards.slice(0, _hand.length + 1);
      updateHandDisplay();
      if (_hand.length >= total) nextBtn.disabled = true;
    });

    var btnRow = document.createElement("div");
    btnRow.className = "dl-odds-row";
    btnRow.appendChild(drawBtn);
    btnRow.appendChild(nextBtn);
    section.appendChild(btnRow);
    section.appendChild(list);
    section.appendChild(landCount);

    // Re-render the persisted hand if one exists
    updateHandDisplay();
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------

  function init() {
    if (!window.DeckLabBuilder) {
      setTimeout(init, 10);
      return;
    }

    window.DeckLabBuilder.onRender(function (state) {
      // Re-apply active curve class after each render
      reapplyActiveCurveClass();
      // Attach click handlers to curve bins (if not already attached)
      document.querySelectorAll(".dl-curve-bin").forEach(function (bin) {
        if (bin._statsHandlerAttached) return;
        bin._statsHandlerAttached = true;
        var bucket = parseInt(bin.getAttribute("data-mana-bucket"), 10);
        if (isNaN(bucket)) return;
        bin.addEventListener("click", handleCurveClick(bin, bucket));
        bin.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            handleCurveClick(bin, bucket)();
          }
        });
      });

      // Render pips
      var ids = commanderColorIdentity(state);
      var pipsData = pipsAndSources(state);
      renderPipsSection(ids, pipsData);

      // Render odds
      renderOddsSection(state);

      // Render sample hand
      renderSampleHandSection(state);
    });

    // Trigger the first render so the onRender listener fires immediately
    window.DeckLabBuilder.render();
  }

  function initRailTabs() {
    var root = document.querySelector("[data-rail-tabs]");
    if (!root || root.getAttribute("data-rail-tabs-ready")) return;
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-rail-tab]"));
    if (!tabs.length) return;
    root.setAttribute("data-rail-tabs-ready", "true");
    var deckId = "deck";
    try {
      var data = document.getElementById("deck-document-data");
      if (data) deckId = String(JSON.parse(data.textContent || "{}").id || deckId);
    } catch (error) {}
    var storageKey = "deck-lab-rail-tab:" + deckId;
    var rail = root.closest(".dl-stats-rail") || document;
    function paneFor(name) {
      return rail.querySelector('[data-rail-pane="' + name + '"]');
    }
    function select(name, focusTab) {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-rail-tab") === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        var pane = paneFor(tab.getAttribute("data-rail-tab"));
        if (pane) {
          if (on) pane.removeAttribute("hidden");
          else pane.setAttribute("hidden", "");
        }
        if (on && focusTab) tab.focus();
      });
      try { localStorage.setItem(storageKey, name); } catch (error) {}
    }
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        select(tab.getAttribute("data-rail-tab"), false);
      });
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
        else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
        else if (event.key === "Home") next = tabs[0];
        else if (event.key === "End") next = tabs[tabs.length - 1];
        else return;
        event.preventDefault();
        select(next.getAttribute("data-rail-tab"), true);
      });
    });
    var saved = null;
    try { saved = localStorage.getItem(storageKey); } catch (error) {}
    if (saved && tabs.some(function (tab) { return tab.getAttribute("data-rail-tab") === saved; })) {
      select(saved, false);
    }
  }

  init();
  initRailTabs();
})();
