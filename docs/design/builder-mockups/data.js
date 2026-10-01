// Realistic Kinnan cEDH slice for mock-ups. Images/symbols load from Scryfall.
window.DECK = {
  title: "Kinnan Turbo — v3",
  commander: { name: "Kinnan, Bonder Prodigy", cost: "{G}{U}", type: "Legendary Creature — Human Druid", mv: 2,
    text: "Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced.\n{5}{G}{U}: Look at the top five cards of your library. You may put a non-Human creature card from among them onto the battlefield. Put the rest on the bottom of your library in a random order." },
  groups: [
    { role: "Ramp", cards: [
      ["Sol Ring","{1}","Artifact",1], ["Mana Crypt","{0}","Artifact",1], ["Mana Vault","{1}","Artifact",1],
      ["Arcane Signet","{2}","Artifact",1], ["Birds of Paradise","{G}","Creature — Bird",1], ["Elvish Mystic","{G}","Creature — Elf Druid",1],
      ["Basalt Monolith","{3}","Artifact",1], ["Utopia Sprawl","{G}","Enchantment — Aura",1] ] },
    { role: "Draw", cards: [
      ["Rhystic Study","{2}{U}","Enchantment",1], ["Mystic Remora","{U}","Enchantment",1], ["Sylvan Library","{1}{G}","Enchantment",1] ] },
    { role: "Tutor", cards: [
      ["Worldly Tutor","{G}","Instant",1], ["Mystical Tutor","{U}","Instant",1], ["Survival of the Fittest","{1}{G}","Enchantment",1] ] },
    { role: "Interaction", cards: [
      ["Force of Will","{3}{U}{U}","Instant",1], ["Mental Misstep","{U/P}","Instant",1], ["Pact of Negation","{0}","Instant",1], ["Swan Song","{U}","Instant",1] ] },
    { role: "Win condition", cards: [
      ["Thrasios, Triton Hero","{G}{U}","Legendary Creature — Merfolk Wizard",1], ["Hullbreaker Horror","{5}{U}{U}","Creature — Kraken Horror",1] ] },
    { role: "Land", cards: [
      ["Breeding Pool","","Land — Forest Island",1], ["Misty Rainforest","","Land",1], ["Forest","","Basic Land — Forest",3], ["Island","","Basic Land — Island",3] ] },
  ],
  considering: [["Dramatic Reversal","{1}{U}","Instant",1], ["Freed from the Real","{2}{U}","Enchantment — Aura",1]]
};
// Images are cached locally (img/) to respect Scryfall rate limits; fetched once by names.txt.
window.img = n => "img/" + n.replace(/[^A-Za-z0-9]/g, "") + ".jpg";
window.art = n => "https://api.scryfall.com/cards/named?format=image&version=art_crop&exact=" + encodeURIComponent(n);
window.sym = cost => (cost.match(/\{[^}]+\}/g) || []).map(s => '<img class="ms" alt="' + s + '" src="https://svgs.scryfall.io/card-symbols/' + s.slice(1, -1).replace("/", "") + '.svg">').join("");
window.rules = t => t.replace(/\{[^}]+\}/g, m => sym(m)).replace(/\n/g, "<br>");
