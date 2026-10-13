/* Shared coverage presentation. Native text is always inserted as textContent. */
(function () {
  "use strict";
  var categories = ["supported", "partial", "unsupported", "inactive"];
  function node(tag, text) { var item = document.createElement(tag); if (text != null) item.textContent = text; return item; }
  function render(host, coverage) {
    coverage = coverage || {};
    var audit = coverage.card_coverage;
    if (audit && audit.version === 1 && audit.unit === "card_slots" && Array.isArray(audit.cards)) {
      host.appendChild(node("p", categories.map(function (key) { return audit.counts[key] + " " + (key === "inactive" ? "intentionally inactive" : key); }).join(" · ") + " card slots. Support applies only to the recorded simulation scope."));
      var details = node("details"); details.appendChild(node("summary", "Card effects and limitations (" + audit.cards.length + " slots)"));
      var list = node("ul");
      audit.cards.forEach(function (card) {
        var item = node("li"); var row = node("details");
        row.appendChild(node("summary", card.name + " · " + card.status));
        row.appendChild(node("p", "Implemented effects: " + (card.supported_effects.length ? card.supported_effects.join("; ") : "None reported.")));
        row.appendChild(node("p", "Limitations: " + (card.limitations.length ? card.limitations.join("; ") : "None reported within the recorded scope.")));
        item.appendChild(row); list.appendChild(item);
      });
      details.appendChild(list); host.appendChild(details);
    } else {
      host.appendChild(node("p", "Legacy coverage: " + ["modeled_cards", "inert_cards", "unauthored_cards"].map(function (key) { return key.replace(/_cards$/, "") + " " + (coverage[key] == null ? "unknown" : coverage[key]); }).join(" · ") + ". These authoring labels do not distinguish partial from missing effects. Rerun for per-card coverage."));
    }
    host.appendChild(node("p", "Missing effects can distort every metric. Unknown outcomes are not failures; more runs do not repair missing mechanics."));
  }
  window.DeckLabCoverage = { render: render };
}());
