// Narrow state-injection bridge into the official Forge 2.0.15 release jar.
//
// Reads a bridge request (JSON) on stdin, injects it with forge.game.GameState
// (the loader Forge uses for res/puzzle/*.pzl), drives scripted actions
// through Forge's own casting, cost-payment, mana, trigger, stack, state-based
// action and turn code, and writes one JSON receipt line per request to the
// ORIGINAL stdout. Everything Forge prints goes to stderr.
//
// Startup mirrors Forge's own test harness at tag forge-2.0.15:
//   forge-gui-desktop/src/test/java/forge/ai/AITest.java (GuiDesktop + FModel.initialize)
// settle() mirrors PhaseHandler.checkStateBasedEffects():
//   do { checkStateEffects } while (stack.addAllTriggeredAbilitiesToStack())
//
// What the bridge decides, and nothing else:
//   * which object an action names (lookup by name/zone/controller);
//   * player choices the scenario fixes (mana colours, optional triggers,
//     targets, cards to put back, ring-bearer) -- each use is recorded;
//   * that a cast pays from the mana pool only (the scenario's mana comes from
//     explicit activate_mana actions), tested with Forge's ManaPool.
// Legality verdicts come from Forge predicates (canPlay, canCastTiming,
// canTarget, canPlayLand, pool payment); reason_source names the predicate.
// Zones, life, counters, mana, stack and trace events are read back from
// Forge objects and Forge's EventBus.

import com.google.common.collect.Lists;
import com.google.common.eventbus.Subscribe;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.GuiDesktop;
import forge.StaticData;
import forge.ai.ComputerUtil;
import forge.ai.ComputerUtilMana;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.card.ColorSet;
import forge.card.MagicColor;
import forge.card.mana.ManaAtom;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.GameLogEntry;
import forge.game.GameObject;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameState;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.event.GameEvent;
import forge.game.event.GameEventCardChangeZone;
import forge.game.event.GameEventCardCounters;
import forge.game.event.GameEventCardDamaged;
import forge.game.event.GameEventPlayerDamaged;
import forge.game.event.GameEventSpellResolved;
import forge.game.event.GameEventTurnBegan;
import forge.game.mana.Mana;
import forge.game.mana.ManaCostBeingPaid;
import forge.game.mana.ManaPool;
import forge.game.phase.PhaseType;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.trigger.WrappedAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.collect.FCollectionView;

import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class ForgeBridge {

    static final String BRIDGE_SCHEMA = "forge-bridge-receipt.v2";
    static final int MAX_RESOLUTIONS = 64;
    static final int MAX_LOOP_STEPS = 400;
    static final ZoneType[] ZONES = {
        ZoneType.Hand, ZoneType.Library, ZoneType.Battlefield,
        ZoneType.Graveyard, ZoneType.Exile, ZoneType.Command,
    };

    // ---- per-scenario recorders (reset between scenarios) ------------------
    static JsonArray TRACE = new JsonArray();
    static JsonArray DECISIONS = new JsonArray();
    static JsonArray RAW_EVENTS = new JsonArray();
    /** Scenario-fixed choices; null entries mean "no scripted choice". */
    static JsonObject CHOICES = new JsonObject();
    static String OPTIONAL_TRIGGERS = "accept";
    /** Pending mana choice for the activation in progress. */
    static Map<Byte, Integer> PENDING_COMBO = null;
    /** Scripted colours for ManaEffect's per-mana chooseColor calls, in order. */
    static List<Byte> PENDING_COLORS = null;
    /** Players already reported as having lost (sba_loses emitted once each). */
    static Set<Player> REPORTED_LOST = new HashSet<>();
    static boolean IN_SBA = false;
    static int PUT_BACK_PICKED = 0;
    static Card RESOLVING_CARD = null;
    static Game GAME = null;
    static List<String> PLAYER_LABELS = new ArrayList<>();

    static void trace(String event, JsonObject fields, String origin) {
        JsonObject e = new JsonObject();
        e.addProperty("event", event);
        for (String k : fields.keySet()) {
            e.add(k, fields.get(k));
        }
        e.addProperty("origin", origin);
        TRACE.add(e);
    }

    static void decision(String kind, JsonObject fields) {
        fields.addProperty("kind", kind);
        DECISIONS.add(fields);
    }

    static String label(Player p) {
        if (p == null) {
            return null;
        }
        // getRegisteredPlayers keeps players who have lost; getPlayers drops them.
        int i = GAME.getRegisteredPlayers().indexOf(p);
        return i >= 0 && i < PLAYER_LABELS.size() ? PLAYER_LABELS.get(i) : p.getName();
    }

    static String labelByName(String forgeName) {
        for (Player p : GAME.getRegisteredPlayers()) {
            if (p.getName().equals(forgeName)) {
                return label(p);
            }
        }
        return forgeName;
    }

    /**
     * Forge's AI controller with every scenario-relevant choice fixed by the
     * scenario. Forge's AI still orders simultaneous triggers (APNAP is
     * enforced by MagicStack before the controller is asked).
     */
    static final class ScriptedController extends PlayerControllerAi {
        ScriptedController(Game game, Player p, LobbyPlayerAi lp) {
            super(game, p, lp);
        }

        @Override
        public boolean confirmTrigger(WrappedAbility wrapped) {
            boolean accept = optionalAccept(wrapped.getHostCard());
            JsonObject d = new JsonObject();
            d.addProperty("player", label(player));
            d.addProperty("source", wrapped.getHostCard() == null ? null : wrapped.getHostCard().getName());
            d.addProperty("description", wrapped.getStackDescription());
            d.addProperty("decision", accept ? "accept" : "decline");
            decision("optional_trigger", d);
            if (!accept) {
                JsonObject f = new JsonObject();
                f.addProperty("source", wrapped.getHostCard() == null ? null : wrapped.getHostCard().getName());
                trace("declined", f, "bridge_choice");
            }
            return accept;
        }

        @Override
        public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String message,
                                     List<String> options, Card cardToShow, Map<String, Object> params) {
            Card host = sa == null ? null : sa.getHostCard();
            if (host != null && sa.isTrigger()) {
                boolean accept = optionalAccept(host);
                JsonObject d = new JsonObject();
                d.addProperty("player", label(player));
                d.addProperty("source", host.getName());
                d.addProperty("mode", String.valueOf(mode));
                d.addProperty("message", message);
                d.addProperty("decision", accept ? "accept" : "decline");
                decision("confirm_action", d);
                return accept;
            }
            return super.confirmAction(sa, mode, message, options, cardToShow, params);
        }

        @Override
        public Map<Byte, Integer> specifyManaCombo(SpellAbility sa, ColorSet colorSet, int manaAmount,
                                                   boolean different) {
            if (PENDING_COMBO != null) {
                JsonObject d = new JsonObject();
                d.addProperty("source", sa.getHostCard().getName());
                d.addProperty("amount", manaAmount);
                d.addProperty("chosen", PENDING_COMBO.toString());
                decision("mana_combo", d);
                return new HashMap<>(PENDING_COMBO);
            }
            return super.specifyManaCombo(sa, colorSet, manaAmount, different);
        }

        @Override
        public byte chooseColor(String message, SpellAbility sa, ColorSet colors) {
            // ManaEffect asks once per mana for combo abilities whose amount is 1
            // or that carry an express choice, and for single-colour choices.
            if (PENDING_COLORS != null) {
                for (int i = 0; i < PENDING_COLORS.size(); i++) {
                    byte c = PENDING_COLORS.get(i);
                    if (colors.hasAnyColor(c)) {
                        PENDING_COLORS.remove(i);
                        JsonObject d = new JsonObject();
                        d.addProperty("source", sa == null ? null : sa.getHostCard().getName());
                        d.addProperty("chosen", MagicColor.toShortString(c));
                        decision("mana_color", d);
                        return c;
                    }
                }
                throw new IllegalStateException("scripted mana colours " + PENDING_COLORS
                    + " not offered by Forge (" + colors + ")");
            }
            return super.chooseColor(message, sa, colors);
        }

        @Override
        public void orderAndPlaySimultaneousSa(List<SpellAbility> sas) {
            // Forge's AI orders the triggers; targets the scenario fixes are
            // applied before the trigger is put on the stack (CR 603.3d).
            List<SpellAbility> ordered = orderSimultaneousSa(sas);
            List<SpellAbility> rest = new ArrayList<>();
            for (SpellAbility sa : ordered) {
                GameObject target = scriptedTriggerTarget(sa);
                if (target == null) {
                    rest.add(sa);
                    continue;
                }
                if (!rest.isEmpty()) {
                    super.orderAndPlaySimultaneousSa(rest);
                    rest = new ArrayList<>();
                }
                SpellAbility tgtSa = sa;
                while (tgtSa != null && !tgtSa.usesTargeting()) {
                    tgtSa = tgtSa.getSubAbility();
                }
                if (tgtSa == null || !tgtSa.canTarget(target)) {
                    throw new IllegalStateException("scripted trigger target not legal for " + sa.getHostCard());
                }
                tgtSa.resetTargets();
                tgtSa.getTargets().add(target);
                JsonObject d = new JsonObject();
                d.addProperty("source", sa.getHostCard().getName());
                d.addProperty("target", String.valueOf(target));
                decision("trigger_target", d);
                ComputerUtil.playStack(sa, player, getGame());
            }
            if (!rest.isEmpty()) {
                super.orderAndPlaySimultaneousSa(rest);
            }
        }

        GameObject scriptedTriggerTarget(SpellAbility sa) {
            if (!sa.isTrigger() || sa.getHostCard() == null
                    || !sa.getHostCard().getName().equals("Orcish Bowmasters")) {
                return null;
            }
            JsonObject t = CHOICES.has("bowmasters_target_resolved")
                ? CHOICES.getAsJsonObject("bowmasters_target_resolved") : null;
            if (t == null) {
                return null;
            }
            return resolveTarget(getGame(), t);
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // Only scripted actions are taken; at every priority the player passes.
            return null;
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            // No unscripted attacks.
        }

        @Override
        public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> options,
                DelayedReveal delayedReveal, SpellAbility sa, String title, boolean isOptional,
                Player targetedPlayer, Map<String, Object> params) {
            String bearer = CHOICES.has("ring_bearer") ? CHOICES.get("ring_bearer").getAsString() : null;
            if (bearer != null && sa != null && sa.getApi() != null
                    && sa.getApi().toString().equals("RingTemptsYou")) {
                for (T o : options) {
                    if (o instanceof Card && ((Card) o).getName().equals(bearer)) {
                        JsonObject d = new JsonObject();
                        d.addProperty("chosen", bearer);
                        decision("ring_bearer", d);
                        return o;
                    }
                }
            }
            return super.chooseSingleEntityForEffect(options, delayedReveal, sa, title, isOptional,
                targetedPlayer, params);
        }

        @Override
        public List<Card> chooseCardsForZoneChange(ZoneType destination, List<ZoneType> origin,
                SpellAbility sa, CardCollection fetchList, int min, int max, DelayedReveal delayedReveal,
                String selectPrompt, Player decider) {
            if (CHOICES.has("put_back_top_first") && destination == ZoneType.Library) {
                List<Card> picked = pickByNames(fetchList, CHOICES.getAsJsonArray("put_back_top_first"));
                if (picked != null) {
                    JsonObject d = new JsonObject();
                    d.add("chosen", CHOICES.get("put_back_top_first"));
                    decision("put_back", d);
                    return picked;
                }
            }
            return super.chooseCardsForZoneChange(destination, origin, sa, fetchList, min, max,
                delayedReveal, selectPrompt, decider);
        }

        @Override
        public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin,
                SpellAbility sa, CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt,
                boolean isOptional, Player decider) {
            // ChangeZoneEffect asks for hidden-zone choices one card at a time.
            if (CHOICES.has("put_back_top_first") && destination == ZoneType.Library
                    && origin != null && origin.contains(ZoneType.Hand)) {
                JsonArray names = CHOICES.getAsJsonArray("put_back_top_first");
                if (PUT_BACK_PICKED < names.size()) {
                    Card hit = findIn(fetchList, names.get(PUT_BACK_PICKED).getAsString());
                    if (hit == null) {
                        throw new IllegalStateException("put_back choice " + names.get(PUT_BACK_PICKED)
                            + " not among " + fetchList);
                    }
                    PUT_BACK_PICKED++;
                    JsonObject d = new JsonObject();
                    d.addProperty("chosen", hit.getName());
                    decision("put_back_pick", d);
                    return hit;
                }
            }
            return super.chooseSingleCardForZoneChange(destination, origin, sa, fetchList, delayedReveal,
                selectPrompt, isOptional, decider);
        }

        @Override
        public CardCollectionView orderMoveToZoneList(CardCollectionView cards, ZoneType destinationZone,
                SpellAbility source) {
            if (CHOICES.has("put_back_top_first") && destinationZone == ZoneType.Library) {
                List<Card> picked = pickByNames(cards, CHOICES.getAsJsonArray("put_back_top_first"));
                if (picked != null && picked.size() == cards.size()) {
                    // Forge moves the list in order, each to the top, so the
                    // card that must end on top goes last.
                    List<Card> order = Lists.reverse(new ArrayList<>(picked));
                    JsonObject d = new JsonObject();
                    d.add("top_first", CHOICES.get("put_back_top_first"));
                    decision("library_order", d);
                    return new CardCollection(order);
                }
            }
            return super.orderMoveToZoneList(cards, destinationZone, source);
        }
    }

    static List<Card> pickByNames(Iterable<Card> pool, JsonArray names) {
        List<Card> left = Lists.newArrayList(pool);
        List<Card> out = new ArrayList<>();
        for (JsonElement n : names) {
            Card hit = null;
            for (Card c : left) {
                if (c.getName().equals(n.getAsString())) {
                    hit = c;
                    break;
                }
            }
            if (hit == null) {
                return null;
            }
            left.remove(hit);
            out.add(hit);
        }
        return out;
    }

    static boolean optionalAccept(Card host) {
        String name = host == null ? "" : host.getName();
        if (name.startsWith("Sauron") && CHOICES.has("sauron_discard")) {
            return CHOICES.get("sauron_discard").getAsBoolean();
        }
        return !"decline".equals(OPTIONAL_TRIGGERS);
    }

    static final class ScriptedLobbyPlayer extends LobbyPlayerAi {
        ScriptedLobbyPlayer(String name) {
            super(name, null);
        }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new ScriptedController(game, p, this));
            return p;
        }
    }

    /** Typed subscriptions on the game's (synchronous) Guava EventBus. */
    public static final class EventRecorder {
        @Subscribe
        public void any(GameEvent ev) {
            JsonObject e = new JsonObject();
            e.addProperty("type", ev.getClass().getSimpleName());
            String s = String.valueOf(ev);
            e.addProperty("text", s.length() > 300 ? s.substring(0, 300) : s);
            RAW_EVENTS.add(e);
        }

        @Subscribe
        public void playerDamaged(GameEventPlayerDamaged ev) {
            JsonObject f = new JsonObject();
            f.addProperty("source", ev.source() == null ? null : ev.source().getName());
            f.addProperty("target", "player:" + labelByName(ev.target().getName()));
            f.addProperty("amount", ev.amount());
            f.addProperty("combat", ev.combat());
            trace("damage", f, "forge_event:GameEventPlayerDamaged");
        }

        @Subscribe
        public void cardDamaged(GameEventCardDamaged ev) {
            JsonObject f = new JsonObject();
            f.addProperty("source", ev.source() == null ? null : ev.source().getName());
            f.addProperty("target", "perm:" + ev.card().getName());
            f.addProperty("amount", ev.amount());
            trace("damage", f, "forge_event:GameEventCardDamaged");
        }

        @Subscribe
        public void turnBegan(GameEventTurnBegan ev) {
            JsonObject f = new JsonObject();
            f.addProperty("active", labelByName(ev.turnOwner().getName()));
            f.addProperty("turn", ev.turnNumber());
            trace("turn", f, "forge_event:GameEventTurnBegan");
        }

        @Subscribe
        public void counters(GameEventCardCounters ev) {
            JsonObject f = new JsonObject();
            f.addProperty("target", ev.card().getName());
            f.addProperty("kind", counterName(ev.type()));
            f.addProperty("n", ev.newValue() - ev.oldValue());
            trace("counters", f, "forge_event:GameEventCardCounters");
        }

        @Subscribe
        public void resolved(GameEventSpellResolved ev) {
            if (ev.hasFizzled()) {
                JsonObject f = new JsonObject();
                f.addProperty("card", ev.spell().getHostCard().getName());
                trace("fizzle", f, "forge_event:GameEventSpellResolved");
            }
        }

        @Subscribe
        public void zone(GameEventCardChangeZone ev) {
            ZoneType from = ev.from() == null ? null : ev.from().zoneType();
            ZoneType to = ev.to() == null ? null : ev.to().zoneType();
            String name = ev.card().getName();
            String owner = ev.card().getOwner() == null ? null : labelByName(ev.card().getOwner().getName());
            String ctrl = ev.card().getController() == null ? owner : labelByName(ev.card().getController().getName());
            JsonObject f = new JsonObject();
            String origin = "forge_event:GameEventCardChangeZone";
            if (from == ZoneType.Library && to == ZoneType.Hand) {
                f.addProperty("player", owner);
                f.addProperty("card", name);
                trace("draw", f, origin);
            } else if (from == ZoneType.Hand && to == ZoneType.Library) {
                f.addProperty("player", owner);
                f.addProperty("card", name);
                trace("put_back_card", f, origin);
            } else if (from == ZoneType.Hand && to == ZoneType.Graveyard) {
                f.addProperty("player", owner);
                f.addProperty("card", name);
                trace("discard", f, origin);
            } else if (to == ZoneType.Battlefield) {
                f.addProperty("card", name);
                f.addProperty("controller", ctrl);
                if (ev.card().isToken()) {
                    trace("token_created", f, origin);
                } else if (from == ZoneType.Stack) {
                    trace("enters", f, origin);
                } else {
                    f.addProperty("from", String.valueOf(from));
                    trace("enters_from_zone", f, origin);
                }
            } else if (from == ZoneType.Battlefield && to == ZoneType.Graveyard) {
                f.addProperty("card", name);
                trace(IN_SBA ? "sba_dies" : "leaves_battlefield", f, origin);
            } else if (from == ZoneType.Stack && to == ZoneType.Graveyard) {
                // A spell leaving the stack other than by its own resolution was countered.
                boolean ownResolution = RESOLVING_CARD != null && RESOLVING_CARD.getName().equals(name);
                if (!ownResolution) {
                    f.addProperty("card", name);
                    trace("countered", f, origin);
                }
            } else if (to == ZoneType.Command) {
                f.addProperty("card", name);
                f.addProperty("to", "command");
                trace("zone", f, origin);
            }
        }
    }

    static String counterName(CounterType ct) {
        String s = String.valueOf(ct);
        return "P1P1".equals(s) ? "+1/+1" : ("M1M1".equals(s) ? "-1/-1" : s);
    }

    public static void main(String[] args) throws Exception {
        final PrintStream receiptOut = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        // Forge (and handlePlayingSpellAbility) print to System.out; keep stdout clean.
        System.setOut(System.err);

        final JsonObject input = JsonParser.parseReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8)).getAsJsonObject();
        // {"batch": [request, ...]} runs every request in one JVM: one engine
        // init, one fresh Game per scenario, one receipt line per scenario.
        final List<JsonObject> requests = new ArrayList<>();
        if (input.has("batch")) {
            for (JsonElement e : input.getAsJsonArray("batch")) {
                requests.add(e.getAsJsonObject());
            }
        } else {
            requests.add(input);
        }
        final Gson gson = new GsonBuilder().serializeNulls().disableHtmlEscaping().create();
        // ThreadUtil.isGameThread() keys off the thread name; running on a "Game"
        // thread makes GameAction.invoke() synchronous, as it is inside a match.
        Thread t = new Thread(() -> {
            long t0 = System.nanoTime();
            Throwable initFailure = null;
            try {
                initEngine();
            } catch (Throwable ex) {
                initFailure = ex;
                ex.printStackTrace(System.err);
            }
            long initMs = (System.nanoTime() - t0) / 1_000_000;
            for (JsonObject request : requests) {
                JsonObject receipt = new JsonObject();
                receipt.addProperty("schema", BRIDGE_SCHEMA);
                receipt.addProperty("scenario_id", str(request, "scenario_id", null));
                receipt.addProperty("engine_init_ms", initMs);
                if (initFailure != null) {
                    receipt.addProperty("status", "engine_init_error");
                    receipt.addProperty("error", initFailure.toString());
                } else {
                    resetRecorders();
                    try {
                        run(request, receipt);
                    } catch (Throwable ex) {
                        receipt.addProperty("status", "bridge_error");
                        receipt.addProperty("error", ex.toString());
                        receipt.add("trace_so_far", TRACE);
                        ex.printStackTrace(System.err);
                    }
                }
                receiptOut.println(gson.toJson(receipt));
                receiptOut.flush();
            }
        }, "Game-bridge");
        t.start();
        t.join();
        // Forge leaves non-daemon pools running.
        System.exit(0);
    }

    static void initEngine() {
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, true);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
    }

    static void resetRecorders() {
        TRACE = new JsonArray();
        DECISIONS = new JsonArray();
        RAW_EVENTS = new JsonArray();
        CHOICES = new JsonObject();
        OPTIONAL_TRIGGERS = "accept";
        PENDING_COMBO = null;
        PENDING_COLORS = null;
        REPORTED_LOST = new HashSet<>();
        IN_SBA = false;
        PUT_BACK_PICKED = 0;
        RESOLVING_CARD = null;
        GAME = null;
        PLAYER_LABELS = new ArrayList<>();
    }

    static void run(JsonObject request, JsonObject receipt) {
        long t0 = System.nanoTime();
        if (request.has("optional_trigger_policy")) {
            OPTIONAL_TRIGGERS = request.get("optional_trigger_policy").getAsString();
        }
        JsonArray players = request.getAsJsonArray("players");
        for (int i = 0; i < players.size(); i++) {
            PLAYER_LABELS.add(str(players.get(i).getAsJsonObject(), "label", "p" + i));
        }

        // Lazy loading: load exactly the scripts this scenario names.
        Set<String> names = new HashSet<>();
        for (JsonElement pe : players) {
            JsonObject zones = pe.getAsJsonObject().getAsJsonObject("zones");
            for (String z : zones.keySet()) {
                for (JsonElement ce : zones.getAsJsonArray(z)) {
                    names.add(ce.getAsString().split("\\|")[0].trim());
                }
            }
        }
        JsonArray missing = new JsonArray();
        for (String n : names) {
            if (StaticData.instance().getCommonCards().getCard(n) == null) {
                StaticData.instance().attemptToLoadCard(n);
            }
            if (StaticData.instance().getCommonCards().getCard(n) == null) {
                missing.add(n);
            }
        }
        if (missing.size() > 0) {
            receipt.addProperty("status", "unknown_cards");
            receipt.add("unknown_cards", missing);
            return;
        }

        List<RegisteredPlayer> regs = Lists.newArrayList();
        for (int i = 0; i < players.size(); i++) {
            regs.add(new RegisteredPlayer(new Deck()).setPlayer(new ScriptedLobbyPlayer("p" + i)));
        }
        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, regs, "decklab-bridge");
        Game game = new Game(regs, rules, match);
        GAME = game;
        game.setAge(GameStage.Play);
        game.subscribeToEvents(new EventRecorder());

        // GameState text in the documented .pzl [state] format.
        List<String> lines = new ArrayList<>();
        lines.add("turn=" + intOr(request, "turn", 1));
        lines.add("activeplayer=p" + intOr(request, "active_player", 0));
        lines.add("activephase=" + str(request, "phase", "MAIN1"));
        lines.add("removesummoningsickness=" + (request.has("remove_summoning_sickness")
            ? request.get("remove_summoning_sickness").getAsBoolean() : true));
        for (int i = 0; i < players.size(); i++) {
            JsonObject p = players.get(i).getAsJsonObject();
            String k = "p" + i;
            lines.add(k + "life=" + intOr(p, "life", 20));
            JsonObject zones = p.getAsJsonObject("zones");
            for (String z : zones.keySet()) {
                List<String> cards = new ArrayList<>();
                for (JsonElement ce : zones.getAsJsonArray(z)) {
                    cards.add(ce.getAsString());
                }
                lines.add(k + z + "=" + String.join(";", cards));
            }
            if (p.has("mana_pool") && !p.get("mana_pool").getAsString().isEmpty()) {
                lines.add(k + "manapool=" + p.get("mana_pool").getAsString());
            }
        }
        receipt.add("injected_state_lines", toJsonArray(lines));
        GameState state = new GameState();
        state.parse(lines);
        state.applyToGame(game);

        // Injection must be exact: GameState prints and skips unknown cards.
        JsonArray mismatches = new JsonArray();
        for (int i = 0; i < players.size(); i++) {
            JsonObject p = players.get(i).getAsJsonObject();
            JsonObject zones = p.getAsJsonObject("zones");
            Player pl = game.getPlayers().get(i);
            for (String z : zones.keySet()) {
                int want = zones.getAsJsonArray(z).size();
                int got = realCards(pl.getCardsIn(zoneOf(z))).size();
                if (want != got) {
                    mismatches.add(PLAYER_LABELS.get(i) + "." + z + ": requested " + want + ", Forge has " + got);
                }
            }
            if (p.has("commander_casts")) {
                JsonObject casts = p.getAsJsonObject("commander_casts");
                for (String cname : casts.keySet()) {
                    Card cmd = null;
                    for (Card c : pl.getCommanders()) {
                        if (c.getName().equals(cname)) {
                            cmd = c;
                        }
                    }
                    if (cmd == null) {
                        mismatches.add(PLAYER_LABELS.get(i) + ".commander_casts: " + cname + " is not a Forge commander");
                        continue;
                    }
                    for (int n = 0; n < casts.get(cname).getAsInt(); n++) {
                        pl.incCommanderCast(cmd);
                    }
                }
            }
        }
        receipt.add("initial_state", snapshot(game));
        if (mismatches.size() > 0) {
            receipt.addProperty("status", "injection_mismatch");
            receipt.add("injection_mismatches", mismatches);
            return;
        }
        int logBase = game.getGameLog().getAllEntries().size();
        TRACE = new JsonArray();
        RAW_EVENTS = new JsonArray();

        if (request.has("initial_choices")) {
            CHOICES = request.getAsJsonObject("initial_choices").deepCopy();
        }
        JsonArray steps = new JsonArray();
        JsonArray actions = request.getAsJsonArray("actions");
        for (int index = 0; index < actions.size(); index++) {
            JsonObject action = actions.get(index).getAsJsonObject();
            JsonObject step = new JsonObject();
            step.addProperty("index", index);
            step.add("action", action);
            // Choices persist until used, as in the reference kernel: a choice
            // given on a cast applies when that spell later resolves.
            if (action.has("choices")) {
                JsonObject c = action.getAsJsonObject("choices");
                for (String k : c.keySet()) {
                    CHOICES.add(k, c.get(k));
                }
            }
            String op = action.get("op").getAsString();
            Rejection r;
            if (game.isGameOver()) {
                r = new Rejection("game_over", "Forge reports the game is over before action " + index,
                    "forge:Game.isGameOver");
            } else switch (op) {
                case "play_land": r = doPlayLand(game, action); break;
                case "activate_mana": r = doActivateMana(game, action); break;
                case "cast": r = doCast(game, action); break;
                case "resolve": r = doResolve(game, action); break;
                case "advance_turn": r = doAdvanceTurn(game); break;
                case "move_to_command_zone": r = doMoveToCommand(game, action); break;
                default: r = new Rejection("unsupported_op", op, "bridge");
            }
            if (r != null) {
                step.addProperty("outcome", "illegal");
                step.addProperty("reason_code", r.code);
                step.addProperty("detail", r.detail);
                step.addProperty("reason_source", r.source);
                JsonObject f = new JsonObject();
                f.addProperty("action_index", index);
                f.addProperty("reason_code", r.code);
                trace("illegal", f, "bridge_driver:" + r.source);
                steps.add(step);
                break;
            }
            step.addProperty("outcome", "ok");
            steps.add(step);
        }
        receipt.add("steps", steps);
        receipt.add("trace", TRACE);
        receipt.add("final_state", snapshot(game));
        receipt.add("decisions", DECISIONS);

        JsonArray log = new JsonArray();
        List<GameLogEntry> all = game.getGameLog().getAllEntries();
        // GameLog stores newest first.
        for (int i = all.size() - 1 - logBase; i >= 0; i--) {
            JsonObject o = new JsonObject();
            o.addProperty("type", String.valueOf(all.get(i).type()));
            o.addProperty("message", all.get(i).message());
            log.add(o);
        }
        receipt.add("game_log", log);
        receipt.add("events", RAW_EVENTS);
        receipt.addProperty("game_over", game.isGameOver());
        receipt.addProperty("scenario_ms", (System.nanoTime() - t0) / 1_000_000);
        Runtime rt = Runtime.getRuntime();
        JsonObject mem = new JsonObject();
        mem.addProperty("heap_used_mb", (rt.totalMemory() - rt.freeMemory()) / (1024 * 1024));
        mem.addProperty("heap_max_mb", rt.maxMemory() / (1024 * 1024));
        receipt.add("memory", mem);
        receipt.addProperty("status", "executed");
    }

    static final class Rejection {
        final String code;
        final String detail;
        final String source;

        Rejection(String code, String detail, String source) {
            this.code = code;
            this.detail = detail;
            this.source = source;
        }
    }

    // ---- actions ------------------------------------------------------------

    static Player playerOf(Game game, JsonObject action) {
        return game.getPlayers().get(action.get("player").getAsInt());
    }

    static Map<String, Integer> poolMap(Player p) {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (byte atom : ManaAtom.MANATYPES) {
            int amt = p.getManaPool().getAmountOfColor(atom);
            if (amt > 0) {
                m.put(MagicColor.toShortString(atom), amt);
            }
        }
        return m;
    }

    static JsonObject poolDelta(Map<String, Integer> before, Map<String, Integer> after, boolean spent) {
        JsonObject o = new JsonObject();
        Set<String> keys = new java.util.TreeSet<>();
        keys.addAll(before.keySet());
        keys.addAll(after.keySet());
        for (String k : keys) {
            int d = after.getOrDefault(k, 0) - before.getOrDefault(k, 0);
            if (spent) {
                d = -d;
            }
            if (d > 0) {
                o.addProperty(k, d);
            }
        }
        return o;
    }

    static Rejection doPlayLand(Game game, JsonObject action) {
        Player p = playerOf(game, action);
        String name = action.get("card").getAsString();
        Card land = findIn(p.getCardsIn(ZoneType.Hand), name);
        if (land == null) {
            return new Rejection("card_not_in_zone", name, "bridge_lookup");
        }
        if (!land.isLand()) {
            return new Rejection("not_a_land", name, "forge:Card.isLand");
        }
        SpellAbility landSa = null;
        for (SpellAbility sa : land.getAllSpellAbilities()) {
            if (sa.isLandAbility()) {
                landSa = sa;
                break;
            }
        }
        if (landSa == null) {
            return new Rejection("not_a_land", name, "forge:no LandAbility");
        }
        landSa.setActivatingPlayer(p);
        if (!p.canPlayLand(land, false, landSa) || !landSa.canPlay()) {
            String code = "land_not_playable";
            if (game.getPhaseHandler().getPlayerTurn() != p) {
                code = "not_your_turn";
            } else if (!game.getStack().isEmpty()) {
                code = "stack_not_empty";
            } else if (p.getLandsPlayedThisTurn() >= 1) {
                code = "land_drop_used";
            }
            return new Rejection(code, name, "forge:Player.canPlayLand");
        }
        Card played = p.playLand(land, landSa);
        settle(game);
        JsonObject f = new JsonObject();
        f.addProperty("player", label(p));
        f.addProperty("card", name);
        Card now = played == null ? null : game.getCardState(played);
        f.addProperty("tapped", now != null && now.isTapped());
        trace("play_land", f, "bridge_driver+forge_state");
        return null;
    }

    static Rejection doActivateMana(Game game, JsonObject action) {
        Player p = playerOf(game, action);
        String name = action.get("card").getAsString();
        int count = action.has("count") ? action.get("count").getAsInt() : 1;
        JsonElement produce = action.get("produce");
        for (int k = 0; k < count; k++) {
            Card src = null;
            SpellAbility manaSa = null;
            for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
                if (!c.getName().equals(name)) {
                    continue;
                }
                for (SpellAbility sa : c.getManaAbilities()) {
                    sa.setActivatingPlayer(p);
                    if (sa.canPlay() && producesWanted(sa, produce)) {
                        src = c;
                        manaSa = sa;
                        break;
                    }
                }
                if (src != null) {
                    break;
                }
            }
            if (src == null) {
                return manaRejection(game, p, name, produce);
            }
            PENDING_COMBO = null;
            PENDING_COLORS = null;
            if (produce != null) {
                // The scenario's colour choice reaches Forge through whichever
                // controller hook ManaEffect uses: specifyManaCombo (combo,
                // amount > 1, no express choice) or chooseColor once per mana.
                PENDING_COMBO = new HashMap<>();
                PENDING_COLORS = new ArrayList<>();
                if (produce.isJsonObject()) {
                    for (String col : "WUBRGC".split("")) {
                        JsonElement n = produce.getAsJsonObject().get(col);
                        if (n != null && n.getAsInt() > 0) {
                            PENDING_COMBO.put(MagicColor.fromName(col), n.getAsInt());
                            for (int k2 = 0; k2 < n.getAsInt(); k2++) {
                                PENDING_COLORS.add(MagicColor.fromName(col));
                            }
                        }
                    }
                } else {
                    byte col = MagicColor.fromName(produce.getAsString());
                    PENDING_COMBO.put(col, 1);
                    PENDING_COLORS.add(col);
                }
            }
            Map<String, Integer> before = poolMap(p);
            // Forge's activation path: pay costs, then MagicStack.add(), which
            // resolves mana abilities immediately and records the activation
            // (ActivationLimit) and SpellAbilityCast/AbilityCast triggers.
            boolean ok = ComputerUtil.handlePlayingSpellAbility(p, manaSa, null);
            PENDING_COMBO = null;
            PENDING_COLORS = null;
            Map<String, Integer> after = poolMap(p);
            JsonObject made = poolDelta(before, after, false);
            if (produce != null && produce.isJsonObject()) {
                // Forge must have produced exactly the scripted choice.
                JsonObject want = new JsonObject();
                for (String col : produce.getAsJsonObject().keySet()) {
                    int n = produce.getAsJsonObject().get(col).getAsInt();
                    if (n > 0) {
                        want.addProperty(col, n);
                    }
                }
                if (!want.equals(made)) {
                    return new Rejection("invalid_mana_choice",
                        "requested " + want + ", Forge produced " + made, "forge:ManaEffect");
                }
            }
            if (!ok) {
                return new Rejection("activation_failed", name, "forge:ComputerUtil.handlePlayingSpellAbility");
            }
            JsonObject f = new JsonObject();
            f.addProperty("player", label(p));
            f.addProperty("source", name);
            f.add("produced", made);
            trace("mana", f, "forge_state:mana_pool_delta");
            settle(game);
        }
        return null;
    }

    /** Prefer the mana ability that can make the requested colours (e.g. a dual land's U vs R ability). */
    static boolean producesWanted(SpellAbility sa, JsonElement produce) {
        if (produce == null || sa.getManaPart() == null) {
            return true;
        }
        String orig = sa.getManaPart().getOrigProduced();
        if (orig.startsWith("Combo") || orig.startsWith("Any")) {
            return true;
        }
        Set<String> wanted = new HashSet<>();
        if (produce.isJsonPrimitive()) {
            wanted.add(produce.getAsString());
        } else {
            for (String k : produce.getAsJsonObject().keySet()) {
                if (produce.getAsJsonObject().get(k).getAsInt() > 0) {
                    wanted.add(k);
                }
            }
        }
        for (String w : wanted) {
            if (!(" " + orig + " ").contains(" " + w + " ")) {
                return false;
            }
        }
        return true;
    }

    static Rejection manaRejection(Game game, Player p, String name, JsonElement produce) {
        Card any = findIn(p.getCardsIn(ZoneType.Battlefield), name);
        if (any == null) {
            return new Rejection("no_such_permanent", name, "bridge_lookup");
        }
        for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
            if (!c.getName().equals(name)) {
                continue;
            }
            for (SpellAbility sa : c.getManaAbilities()) {
                sa.setActivatingPlayer(p);
                if (sa.canPlay()) {
                    return new Rejection("invalid_mana_choice", name + " cannot make " + produce,
                        "forge:AbilityManaPart.getOrigProduced");
                }
            }
        }
        for (SpellAbility sa : any.getManaAbilities()) {
            sa.setActivatingPlayer(p);
            if (sa.getPayCosts() != null && sa.getPayCosts().hasTapCost()) {
                return new Rejection("no_untapped_source", name, "forge:canPlay(tap cost, all tapped)");
            }
            if (sa.getActivationsThisTurn() >= 1 && sa.hasParam("ActivationLimit")) {
                return new Rejection("activation_limit", name, "forge:SpellAbilityRestriction ActivationLimit");
            }
            if (sa.hasParam("PlayerTurn") && game.getPhaseHandler().getPlayerTurn() != p) {
                return new Rejection("not_your_turn", name, "forge:SpellAbilityRestriction PlayerTurn");
            }
        }
        return new Rejection("cannot_activate", name, "forge:SpellAbility.canPlay");
    }

    static Rejection doCast(Game game, JsonObject action) {
        Player p = playerOf(game, action);
        String name = action.get("card").getAsString();
        String from = str(action, "from", "hand");
        ZoneType zone = zoneOf(from);
        Card card = findIn(p.getCardsIn(zone), name);
        if (card == null) {
            return new Rejection("card_not_in_zone", name + " in " + from, "bridge_lookup");
        }
        if (card.isLand()) {
            return new Rejection("not_castable", name, "forge:Card.isLand");
        }
        // getAllPossibleAbilities is how Forge offers a player its castable
        // options: it adds the MayPlay copies (e.g. a commander's command-zone
        // permission) and alternative costs to the printed spell ability.
        SpellAbility spell = null;
        for (SpellAbility sa : card.getAllPossibleAbilities(p, false)) {
            if (sa.isSpell()) {
                sa.setActivatingPlayer(p);
                if (spell == null || sa.canPlay()) {
                    spell = sa;
                }
                if (sa.canPlay()) {
                    break;
                }
            }
        }
        if (spell == null) {
            // getAllPossibleAbilities drops options that cannot be cast now
            // (e.g. a sorcery on the opponent's turn); fall back to the printed
            // spell so Forge's own timing/zone checks below give the reason.
            for (SpellAbility sa : card.getSpellAbilities()) {
                if (sa.isSpell()) {
                    sa.setActivatingPlayer(p);
                    spell = sa;
                    break;
                }
            }
        }
        if (spell == null) {
            return new Rejection("not_castable", name, "forge:no spell ability");
        }
        if (zone == ZoneType.Command && !card.isCommander()) {
            return new Rejection("not_a_commander", name, "forge:Card.isCommander");
        }
        if (!spell.canCastTiming(p)) {
            return new Rejection("timing", name, "forge:SpellAbility.canCastTiming");
        }
        if (!spell.canPlay()) {
            return new Rejection("cannot_cast", name, "forge:SpellAbility.canPlay");
        }
        // Targets: resolve and check with Forge's canTarget before paying.
        final List<GameObject> targets = new ArrayList<>();
        if (action.has("targets")) {
            for (JsonElement te : action.getAsJsonArray("targets")) {
                GameObject obj = resolveTarget(game, te.getAsJsonObject());
                if (obj == null) {
                    return new Rejection("illegal_target", te.toString(), "bridge_lookup");
                }
                if (!spell.usesTargeting() || !spell.canTarget(obj)) {
                    return new Rejection("illegal_target", te.toString(), "forge:SpellAbility.canTarget");
                }
                targets.add(obj);
            }
        }
        if (spell.usesTargeting() && spell.getTargetRestrictions().getMinTargets(card, spell) > targets.size()) {
            return new Rejection("illegal_target", name + " needs a target", "forge:TargetRestrictions.minTargets");
        }
        // Pool-only payment test with Forge's adjusted cost (commander tax etc.).
        ManaCostBeingPaid cost = ComputerUtilMana.calculateManaCost(spell.getPayCosts(), spell, p, true, 0, false);
        int printed = card.getManaCost().getCMC();
        int adjusted = cost.getConvertedManaCost();
        List<Mana> spentTest = new ArrayList<>();
        ManaPool pool = p.getManaPool();
        boolean poolCanPay = cost.isPaid() || pool.payManaCostFromPool(new ManaCostBeingPaid(cost), spell, true, spentTest);
        if (!poolCanPay) {
            pool.refundMana(spentTest);
            return new Rejection("unpayable_cost", "pool " + poolMap(p) + " cannot pay " + cost,
                "forge:ManaPool.payManaCostFromPool");
        }
        Map<String, Integer> before = poolMap(p);
        Set<Card> untappedBefore = untapped(p);
        int stackBefore = game.getStack().size();
        int castsBefore = card.isCommander() ? p.getCommanderCast(card) : -1;
        boolean played = ComputerUtil.handlePlayingSpellAbility(p, spell, targets.isEmpty() ? null : s -> {
            for (GameObject o : targets) {
                s.getTargets().add(o);
            }
        });
        if (!played) {
            return new Rejection("cast_failed", name, "forge:ComputerUtil.handlePlayingSpellAbility");
        }
        Set<Card> nowTapped = new HashSet<>(untappedBefore);
        nowTapped.removeAll(untapped(p));
        if (!nowTapped.isEmpty()) {
            throw new IllegalStateException("payment tapped permanents although the pool covered the cost: " + nowTapped);
        }
        JsonObject f = new JsonObject();
        f.addProperty("player", label(p));
        f.addProperty("card", name);
        f.addProperty("zone", from);
        f.add("paid", poolDelta(before, poolMap(p), true));
        f.addProperty("tax", adjusted - printed);
        f.addProperty("stack_size_after", game.getStack().size());
        trace("cast", f, "bridge_driver+forge_state");
        if (castsBefore >= 0) {
            JsonObject d = new JsonObject();
            d.addProperty("commander_casts_after", p.getCommanderCast(card));
            decision("commander_cast_counter", d);
        }
        settle(game);
        return null;
    }

    static Set<Card> untapped(Player p) {
        Set<Card> s = new HashSet<>();
        for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
            if (!c.isTapped()) {
                s.add(c);
            }
        }
        return s;
    }

    static GameObject resolveTarget(Game game, JsonObject t) {
        if (t.has("player")) {
            return game.getPlayers().get(t.get("player").getAsInt());
        }
        if (t.has("stack_card")) {
            String name = t.get("stack_card").getAsString();
            for (SpellAbilityStackInstance si : game.getStack()) {
                if (!si.isTrigger() && si.getSpellAbility().getHostCard().getName().equals(name)) {
                    return si.getSpellAbility();
                }
            }
            return null;
        }
        String name = t.get("card").getAsString();
        Integer ctrl = t.has("controller") ? t.get("controller").getAsInt() : null;
        for (Card c : game.getCardsIn(ZoneType.Battlefield)) {
            if (c.getName().equals(name)
                    && (ctrl == null || c.getController() == game.getPlayers().get(ctrl))) {
                return c;
            }
        }
        return null;
    }

    static Rejection doResolve(Game game, JsonObject action) {
        settle(game);
        if (game.getStack().isEmpty()) {
            return new Rejection("stack_empty", "", "forge:MagicStack.isEmpty");
        }
        int n = 0;
        while (!game.getStack().isEmpty() && !game.isGameOver() && n < MAX_RESOLUTIONS) {
            SpellAbilityStackInstance top = game.getStack().peek();
            Card host = top.getSpellAbility().getHostCard();
            JsonObject f = new JsonObject();
            if (top.isTrigger()) {
                f.addProperty("item", "trigger");
                f.addProperty("source", host.getName());
            } else {
                f.addProperty("item", "spell");
                f.addProperty("card", host.getName());
            }
            f.addProperty("stack_description", top.getStackDescription());
            trace("resolve", f, "bridge_driver:MagicStack.peek");
            RESOLVING_CARD = top.isTrigger() ? null : host;
            Map<Player, Integer> ringBefore = ringLevels(game);
            game.getStack().resolveStack();
            RESOLVING_CARD = null;
            ringTrace(game, ringBefore);
            settle(game);
            n++;
        }
        if (!game.getStack().isEmpty() && !game.isGameOver()) {
            throw new IllegalStateException("resolution cap hit with " + game.getStack().size() + " items left");
        }
        return null;
    }

    static Map<Player, Integer> ringLevels(Game game) {
        Map<Player, Integer> m = new HashMap<>();
        for (Player p : game.getPlayers()) {
            m.put(p, p.getNumRingTemptedYou());
        }
        return m;
    }

    static void ringTrace(Game game, Map<Player, Integer> before) {
        for (Player p : game.getPlayers()) {
            if (p.getNumRingTemptedYou() != before.get(p)) {
                JsonObject f = new JsonObject();
                f.addProperty("player", label(p));
                f.addProperty("level", Math.min(4, p.getNumRingTemptedYou()));
                f.addProperty("bearer", p.getRingBearer() == null ? null : p.getRingBearer().getName());
                trace("ring_tempts", f, "forge_state:Player.getNumRingTemptedYou");
            }
        }
    }

    static Rejection doAdvanceTurn(Game game) {
        settle(game);
        if (!game.getStack().isEmpty()) {
            return new Rejection("stack_not_empty", "", "forge:MagicStack.isEmpty");
        }
        Player current = game.getPhaseHandler().getPlayerTurn();
        int steps = 0;
        // Forge's own turn structure: priority passes (every controller passes),
        // cleanup, untap, upkeep, draw -- until the next player's first main phase.
        do {
            game.getPhaseHandler().mainLoopStep();
            // State-based actions run inside mainLoopStep (e.g. CR 704.5b in
            // the draw step), so losses are read back after every step.
            reportLosses(game);
            steps++;
        } while (!game.isGameOver() && steps < MAX_LOOP_STEPS
            && (game.getPhaseHandler().getPlayerTurn() == current
                || !game.getPhaseHandler().is(PhaseType.MAIN1)));
        if (steps >= MAX_LOOP_STEPS) {
            throw new IllegalStateException("advance_turn did not reach the next main phase");
        }
        // The "turn" trace event comes from GameEventTurnBegan (EventRecorder),
        // so it precedes that turn's draw-step events.
        return null;
    }

    static Rejection doMoveToCommand(Game game, JsonObject action) {
        String name = action.get("card").getAsString();
        Card card = findIn(game.getCardsIn(ZoneType.Battlefield), name);
        if (card == null) {
            return new Rejection("no_such_permanent", name, "bridge_lookup");
        }
        if (!card.isCommander()) {
            return new Rejection("not_a_commander", name, "forge:Card.isCommander");
        }
        game.getAction().moveTo(ZoneType.Command, card, null, null);
        settle(game);
        return null;
    }

    /**
     * Emits sba_loses once per newly lost player. Forge fires no event for an
     * attempted draw from an empty library: Player.doDraw only sets a private
     * flag that checkLoseCondition consumes, recording GameLossReason.Milled
     * (CR 704.5b). A Milled loss therefore also emits draw_empty, sourced from
     * PlayerOutcome.lossState. An empty-library draw that does not end in a
     * loss (e.g. under a replacement effect) is not observable this way.
     */
    static void reportLosses(Game game) {
        for (Player p : game.getRegisteredPlayers()) {
            if (!p.hasLost() || REPORTED_LOST.contains(p)) {
                continue;
            }
            REPORTED_LOST.add(p);
            String reason = p.getOutcome() == null ? null : String.valueOf(p.getOutcome().lossState);
            if ("Milled".equals(reason)) {
                JsonObject d = new JsonObject();
                d.addProperty("player", label(p));
                trace("draw_empty", d, "forge_state:PlayerOutcome.lossState=Milled");
            }
            JsonObject f = new JsonObject();
            f.addProperty("player", label(p));
            f.addProperty("loss_reason", reason);
            trace("sba_loses", f, "forge_state:Player.hasLost");
        }
    }

    /** PhaseHandler.checkStateBasedEffects(): SBAs, then put waiting triggers on the stack. */
    static void settle(Game game) {
        int guard = 0;
        List<SpellAbilityStackInstance> before = Lists.newArrayList(game.getStack());
        do {
            IN_SBA = true;
            try {
                game.getAction().checkStateEffects(false, new HashSet<>());
            } finally {
                IN_SBA = false;
            }
        } while (game.getStack().addAllTriggeredAbilitiesToStack() && ++guard < MAX_RESOLUTIONS);
        reportLosses(game);
        // New stack items, bottom-most first, i.e. the order they were put on.
        List<SpellAbilityStackInstance> now = Lists.newArrayList(game.getStack());
        List<SpellAbilityStackInstance> added = new ArrayList<>();
        for (SpellAbilityStackInstance si : now) {
            if (!before.contains(si)) {
                added.add(si);
            }
        }
        for (SpellAbilityStackInstance si : Lists.reverse(added)) {
            if (!si.isTrigger()) {
                continue;
            }
            SpellAbility sa = si.getSpellAbility();
            JsonObject f = new JsonObject();
            f.addProperty("source", sa.getHostCard().getName());
            f.addProperty("controller", label(si.getActivatingPlayer()));
            f.addProperty("forge_mode", sa.getTrigger() == null ? null : String.valueOf(sa.getTrigger().getMode()));
            f.addProperty("stack_description", si.getStackDescription());
            trace("trigger", f, "forge_state:MagicStack");
        }
    }

    // ---- snapshots ----------------------------------------------------------

    static JsonArray stackView(Game game) {
        JsonArray a = new JsonArray();
        for (SpellAbilityStackInstance si : game.getStack()) {
            JsonObject o = new JsonObject();
            o.addProperty("description", si.getStackDescription());
            o.addProperty("is_trigger", si.isTrigger());
            o.addProperty("source", si.getSpellAbility().getHostCard().getName());
            a.add(o);
        }
        return a;
    }

    static JsonObject snapshot(Game game) {
        JsonObject s = new JsonObject();
        JsonArray ps = new JsonArray();
        for (Player p : game.getRegisteredPlayers()) {
            JsonObject po = new JsonObject();
            po.addProperty("label", label(p));
            po.addProperty("life", p.getLife());
            po.addProperty("poison", p.getPoisonCounters());
            po.addProperty("lost", p.hasLost());
            po.addProperty("cards_drawn_this_turn", p.getNumDrawnThisTurn());
            po.addProperty("ring_tempted_count", p.getNumRingTemptedYou());
            po.addProperty("ring_bearer", p.getRingBearer() == null ? null : p.getRingBearer().getName());
            JsonObject mana = new JsonObject();
            poolMap(p).forEach(mana::addProperty);
            po.add("mana_pool", mana);
            JsonObject casts = new JsonObject();
            for (Card c : p.getCommanders()) {
                casts.addProperty(c.getName(), p.getCommanderCast(c));
            }
            po.add("commander_casts", casts);
            JsonObject zones = new JsonObject();
            for (ZoneType z : ZONES) {
                JsonArray cards = new JsonArray();
                for (Card c : realCards(p.getCardsIn(z))) {
                    cards.add(cardView(c, z));
                }
                if (z == ZoneType.Command) {
                    JsonArray effects = new JsonArray();
                    for (Card c : p.getCardsIn(z)) {
                        if (c.isImmutable()) {
                            effects.add(c.getName());
                        }
                    }
                    po.add("command_effects", effects);
                }
                zones.add(z.name().toLowerCase(), cards);
            }
            po.add("zones", zones);
            ps.add(po);
        }
        s.add("players", ps);
        s.add("stack", stackView(game));
        s.addProperty("phase", String.valueOf(game.getPhaseHandler().getPhase()));
        s.addProperty("turn", game.getPhaseHandler().getTurn());
        s.addProperty("active_player", label(game.getPhaseHandler().getPlayerTurn()));
        return s;
    }

    static JsonObject cardView(Card c, ZoneType z) {
        JsonObject o = new JsonObject();
        o.addProperty("name", c.getName());
        o.addProperty("forge_id", c.getId());
        if (z == ZoneType.Battlefield) {
            o.addProperty("controller", label(c.getController()));
            o.addProperty("tapped", c.isTapped());
            o.addProperty("token", c.isToken());
            if (c.isCreature()) {
                o.addProperty("power", c.getNetPower());
                o.addProperty("toughness", c.getNetToughness());
                o.addProperty("damage", c.getDamage());
            }
            if (c.getEntityAttachedTo() != null) {
                GameEntity att = c.getEntityAttachedTo();
                o.addProperty("attached_to", att instanceof Card ? ((Card) att).getName() : att.toString());
            }
            JsonObject counters = new JsonObject();
            for (CounterType ct : c.getCounters().elementSet()) {
                int n = c.getCounters().count(ct);
                if (n != 0) {
                    counters.addProperty(counterName(ct), n);
                }
            }
            o.add("counters", counters);
        }
        return o;
    }

    // ---- helpers ------------------------------------------------------------

    /** Forge keeps rules "effect" objects (e.g. the commander effect) as immutable cards in the command zone. */
    static List<Card> realCards(Iterable<Card> cards) {
        List<Card> out = new ArrayList<>();
        for (Card c : cards) {
            if (!c.isImmutable()) {
                out.add(c);
            }
        }
        return out;
    }

    static Card findIn(Iterable<Card> cards, String name) {
        for (Card c : cards) {
            if (c.getName().equals(name)) {
                return c;
            }
        }
        return null;
    }

    static ZoneType zoneOf(String key) {
        switch (key) {
            case "hand": return ZoneType.Hand;
            case "library": return ZoneType.Library;
            case "battlefield": return ZoneType.Battlefield;
            case "graveyard": return ZoneType.Graveyard;
            case "exile": return ZoneType.Exile;
            case "command": return ZoneType.Command;
            default: throw new IllegalArgumentException("unsupported zone " + key);
        }
    }

    static JsonArray toJsonArray(List<String> xs) {
        JsonArray a = new JsonArray();
        xs.forEach(a::add);
        return a;
    }

    static String str(JsonObject o, String k, String dflt) {
        return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : dflt;
    }

    static int intOr(JsonObject o, String k, int dflt) {
        return o.has(k) ? o.get(k).getAsInt() : dflt;
    }
}
