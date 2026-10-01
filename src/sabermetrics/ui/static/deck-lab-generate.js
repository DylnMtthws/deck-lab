(function () {
  "use strict";

  // Job statuses from _execute_build_job / BuildJobsRepo, in order.
  // queued is the inserted row; running is the first worker write.
  var STEP_ORDER = ["queued", "running", "simulating", "explaining", "done"];
  var STEP_LABELS = {
    queued: "Queued",
    running: "Building",
    simulating: "Simulating",
    explaining: "Explaining",
    done: "Done"
  };
  var TIMEOUT_MS = 5 * 60 * 1000;
  var NO_PACK = "No strategy pack supports this commander yet.";

  function dialog() {
    return document.getElementById("new-deck-dialog");
  }

  function csrfToken() {
    var meta = document.querySelector("meta[name='csrf-token']");
    return meta ? meta.content : "";
  }

  function jsonHeaders() {
    return {
      "Accept": "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
      "X-Requested-With": "XMLHttpRequest"
    };
  }

  function delayForAttempt(attempt) {
    if (attempt <= 0) return 1000;
    if (attempt === 1) return 2000;
    return 4000;
  }

  function jsonStatusUrl(url) {
    var value = String(url || "");
    if (value.slice(-5) === ".json") return value;
    return value + ".json";
  }

  function plainError(code) {
    var known = {
      unsupported: "No supported strategy pack matches that request.",
      build_failed: "The build failed.",
      enqueue_failed: "The build could not be queued.",
      interrupted: "The build was interrupted before it finished.",
      cost_ceiling_reached: "The lab is paused because the monthly limit was reached."
    };
    if (known[code]) return known[code];
    if (!code) return "The build failed.";
    return String(code).replace(/_/g, " ");
  }

  function generateChosen(root) {
    var radio = root.querySelector("[data-start-generate]");
    return !!(radio && radio.checked);
  }

  function commanderId(root) {
    var hidden = root.querySelector("[data-commander-id]");
    return hidden && hidden.value ? hidden.value : "";
  }

  function setHidden(node, hidden) {
    if (node) node.hidden = hidden;
  }

  function paint(root, job) {
    var current = String((job && job.status) || "");
    var passed = job && Array.isArray(job.steps) ? job.steps.map(String) : null;
    var currentIndex = STEP_ORDER.indexOf(current);
    root.querySelectorAll("[data-generate-step]").forEach(function (item) {
      var step = item.getAttribute("data-generate-step");
      var index = STEP_ORDER.indexOf(step);
      var state = "upcoming";
      if (passed) {
        if (step === current) state = "current";
        else if (passed.indexOf(step) >= 0) state = "complete";
      } else if (currentIndex >= 0) {
        if (index < currentIndex) state = "complete";
        else if (index === currentIndex) state = "current";
      }
      item.setAttribute("data-state", state);
      var mark = item.querySelector("[data-generate-mark]");
      if (mark) mark.textContent = state === "complete" ? "✓" : "";
      if (state === "current") item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
  }

  function showForm(root) {
    var form = root.querySelector("[data-new-deck-form]");
    var progress = root.querySelector("[data-generate-progress]");
    setHidden(form, false);
    setHidden(progress, true);
  }

  function showProgress(root) {
    var form = root.querySelector("[data-new-deck-form]");
    var progress = root.querySelector("[data-generate-progress]");
    setHidden(form, true);
    setHidden(progress, false);
    if (progress) {
      progress.setAttribute("aria-busy", "true");
      if (progress.focus) progress.focus();
    }
  }

  function clearTerminal(root) {
    var error = root.querySelector("[data-generate-error]");
    var retry = root.querySelector("[data-generate-retry]");
    var timeout = root.querySelector("[data-generate-timeout]");
    var spinner = root.querySelector("[data-generate-spinner]");
    if (error) error.textContent = "";
    setHidden(error, true);
    setHidden(retry, true);
    setHidden(timeout, true);
    setHidden(spinner, false);
  }

  function showFailed(root, job) {
    var progress = root.querySelector("[data-generate-progress]");
    var error = root.querySelector("[data-generate-error]");
    var retry = root.querySelector("[data-generate-retry]");
    var spinner = root.querySelector("[data-generate-spinner]");
    var words = plainError(job && job.error_code);
    var detail = job && job.error_detail ? String(job.error_detail) : "";
    if (progress) progress.setAttribute("aria-busy", "false");
    setHidden(spinner, true);
    if (error) {
      error.textContent = detail ? words + " " + detail : words;
      setHidden(error, false);
    }
    setHidden(retry, false);
  }

  function showTimeout(root, pageUrl) {
    var progress = root.querySelector("[data-generate-progress]");
    var timeout = root.querySelector("[data-generate-timeout]");
    var link = root.querySelector("[data-generate-job-link]");
    var spinner = root.querySelector("[data-generate-spinner]");
    if (progress) progress.setAttribute("aria-busy", "false");
    setHidden(spinner, true);
    if (link) link.setAttribute("href", pageUrl || "");
    setHidden(timeout, false);
  }

  function ensurePackSelect(root) {
    var select = root.querySelector("[data-generate-pack]");
    if (select) return select;
    select = document.createElement("select");
    select.className = "dl-field";
    select.setAttribute("data-generate-pack", "");
    select.setAttribute("aria-label", "Strategy pack");
    var slot = root.querySelector("[data-generate-pack-slot]");
    if (slot) slot.appendChild(select);
    else root.appendChild(select);
    return select;
  }

  function renderPacks(root, payload) {
    var select = ensurePackSelect(root);
    var empty = root.querySelector("[data-generate-empty]");
    var button = root.querySelector("[data-generate-submit]");
    var packs = payload && payload.packs ? payload.packs : [];
    if (select) {
      select.replaceChildren();
      packs.forEach(function (pack) {
        var option = document.createElement("option");
        option.value = pack.pack_id;
        option.textContent = pack.supported ? pack.name : pack.name + " (unsupported)";
        option.disabled = !pack.supported;
        select.appendChild(option);
      });
    }
    var supported = packs.filter(function (pack) { return pack.supported; });
    if (!packs.length) {
      if (empty) {
        empty.textContent = (payload && payload.message) || NO_PACK;
        setHidden(empty, false);
      }
      if (select) select.disabled = true;
      if (button) button.disabled = true;
      return;
    }
    setHidden(empty, true);
    if (select) {
      select.disabled = supported.length === 0;
      if (supported.length) select.value = supported[0].pack_id;
    }
    if (button) button.disabled = supported.length === 0;
  }

  function refreshPacks(root) {
    if (!generateChosen(root)) return Promise.resolve();
    var url = "/api/generate/packs?commander=" + encodeURIComponent(commanderId(root));
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("packs");
        return response.json();
      })
      .then(function (payload) { renderPacks(root, payload); })
      .catch(function () {
        renderPacks(root, { packs: [], message: NO_PACK });
      });
  }

  function syncMode(root) {
    var fields = root.querySelector("[data-generate-fields]");
    var create = root.querySelector("[data-create-deck]");
    var generate = generateChosen(root);
    setHidden(fields, !generate);
    setHidden(create, generate);
    var submit = root.querySelector("[data-generate-submit]");
    setHidden(submit, !generate);
    if (generate) refreshPacks(root);
  }

  function bind(root) {
    var state = { timer: null, stopped: false, waited: 0 };

    function stopPoll() {
      state.stopped = true;
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
    }

    function finish(job) {
      var candidateId = job && job.candidate_id;
      if (!candidateId) {
        showFailed(root, {
          error_code: "build_failed",
          error_detail: "The build finished without a candidate."
        });
        return;
      }
      fetch("/build/import/candidate/" + encodeURIComponent(candidateId), {
        method: "POST",
        headers: jsonHeaders(),
        body: "{}"
      }).then(function (response) {
        if (!response.ok) throw new Error("import");
        return response.json();
      }).then(function (body) {
        var progress = root.querySelector("[data-generate-progress]");
        if (progress) progress.setAttribute("aria-busy", "false");
        if (body && body.url && window.location && window.location.assign) {
          window.location.assign(body.url);
        }
      }).catch(function () {
        showFailed(root, {
          error_code: "build_failed",
          error_detail: "The finished list could not be opened in the builder."
        });
      });
    }

    function poll(pageUrl, attempt) {
      if (state.stopped) return;
      if (state.waited >= TIMEOUT_MS) {
        showTimeout(root, pageUrl);
        return;
      }
      var wait = delayForAttempt(attempt);
      state.timer = setTimeout(function () {
        state.waited += wait;
        if (state.stopped) return;
        if (state.waited >= TIMEOUT_MS) {
          showTimeout(root, pageUrl);
          return;
        }
        fetch(jsonStatusUrl(pageUrl), { headers: { "Accept": "application/json" } })
          .then(function (response) {
            if (!response.ok) throw new Error("status");
            return response.json();
          })
          .then(function (job) {
            if (state.stopped) return;
            paint(root, job);
            if (job.status === "failed") {
              showFailed(root, job);
              return;
            }
            if (job.status === "done") {
              finish(job);
              return;
            }
            poll(pageUrl, attempt + 1);
          })
          .catch(function () {
            if (state.stopped) return;
            showFailed(root, {
              error_code: "build_failed",
              error_detail: "The status request failed."
            });
          });
      }, wait);
    }

    function begin() {
      var select = ensurePackSelect(root);
      var intent = root.querySelector("[data-generate-intent]");
      var packId = select ? select.value : "";
      stopPoll();
      state.stopped = false;
      state.waited = 0;
      showProgress(root);
      clearTerminal(root);
      paint(root, { status: "queued" });
      if (!packId) {
        showFailed(root, {
          error_code: "unsupported",
          error_detail: "Choose a supported strategy pack."
        });
        return;
      }
      var payload = { pack_id: packId, flex_slots: 0 };
      var focus = intent ? String(intent.value || "").trim() : "";
      if (focus) payload.raw_intent = focus;
      fetch("/lab/build", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(payload)
      }).then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      }).then(function (result) {
        if (!result.ok || !result.body || !result.body.status_url) {
          var body = result.body || {};
          showFailed(root, {
            error_code: body.error || "build_failed",
            error_detail: body.detail || body.error_detail || ""
          });
          return;
        }
        var link = root.querySelector("[data-generate-job-link]");
        if (link) link.setAttribute("href", result.body.status_url);
        paint(root, { status: result.body.status || "queued" });
        poll(result.body.status_url, 0);
      }).catch(function () {
        showFailed(root, {
          error_code: "build_failed",
          error_detail: "The build could not be started."
        });
      });
    }

    function resetDialog() {
      stopPoll();
      clearTerminal(root);
      showForm(root);
    }

    var empty = root.querySelector("[data-start-empty]");
    var generate = root.querySelector("[data-start-generate]");
    if (empty) empty.addEventListener("change", function () { syncMode(root); });
    if (generate) generate.addEventListener("change", function () { syncMode(root); });
    var submit = root.querySelector("[data-generate-submit]");
    if (submit) submit.addEventListener("click", function () { begin(); });
    var retry = root.querySelector("[data-generate-retry]");
    if (retry) retry.addEventListener("click", function () { begin(); });
    var form = root.querySelector("[data-new-deck-form]");
    if (form) {
      form.addEventListener("submit", function (event) {
        if (!generateChosen(root)) return;
        event.preventDefault();
        begin();
      });
    }
    root.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      if (target.closest("[data-commander-results]") || target.closest("[data-partner-results]")) {
        refreshPacks(root);
      }
    });
    var search = root.querySelector("[data-commander-search]");
    if (search) {
      search.addEventListener("input", function () { refreshPacks(root); });
    }
    root.addEventListener("close", function () { stopPoll(); });
    document.querySelectorAll("[data-new-deck]").forEach(function (button) {
      button.addEventListener("click", function () { resetDialog(); });
    });

    var prefillId = root.getAttribute("data-prefill-commander") || "";
    var prefillName = root.getAttribute("data-prefill-commander-name") || "";
    if (prefillId) {
      var hidden = root.querySelector("[data-commander-id]");
      var nameField = root.querySelector("[data-commander-search]");
      if (hidden) hidden.value = prefillId;
      if (nameField && prefillName) nameField.value = prefillName;
    }
    if (root.hasAttribute("data-open-generate")) {
      if (generate) generate.checked = true;
      syncMode(root);
      if (root.showModal) {
        try { root.showModal(); } catch (error) { /* already open */ }
      }
    } else {
      syncMode(root);
    }

    root._generateBegin = begin;
  }

  var root = dialog();
  if (root) bind(root);
})();
