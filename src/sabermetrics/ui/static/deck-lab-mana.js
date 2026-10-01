(function () {
  "use strict";

  var FILE_NAME_PATTERN = /^[0-9WUBRGCSXYZTQPE\/½∞]+$/i;

  function fileName(symbol) {
    var raw = String(symbol || "");
    if (!FILE_NAME_PATTERN.test(raw)) return null;
    var upper = raw.toUpperCase().replace(/\//g, "");
    return upper + ".svg";
  }

  function symbol(sym, opts) {
    var file = fileName(sym);
    if (!file) {
      var upper = String(sym || "").toUpperCase().replace("/", "⁄");
      var fallback = document.createElement("span");
      fallback.className = "dl-mana-symbol dl-mana-generic";
      fallback.textContent = upper;
      fallback.setAttribute("aria-hidden", "true");
      return fallback;
    }
    var img = document.createElement("img");
    img.className = "dl-ms";
    img.src = "https://svgs.scryfall.io/card-symbols/" + file;
    img.width = 16;
    img.height = 16;
    img.loading = "lazy";
    img.decoding = "async";
    if (opts && opts.decorative) {
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
    } else {
      img.alt = "{" + sym + "}";
    }
    img.addEventListener("error", function () {
      if (!img.parentNode) return;
      var upper = String(sym || "").toUpperCase().replace("/", "⁄");
      var fallback = document.createElement("span");
      fallback.className = "dl-mana-symbol dl-mana-generic";
      fallback.textContent = upper;
      fallback.setAttribute("aria-hidden", "true");
      img.parentNode.replaceChild(fallback, img);
    });
    return img;
  }

  window.DeckLabMana = { fileName: fileName, symbol: symbol };
})();
