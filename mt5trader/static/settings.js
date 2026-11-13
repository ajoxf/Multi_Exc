/* Settings: the two accounts, and the pairs that route across them.
 *
 * This panel is the one part of the UI that must work with the ENGINE
 * DOWN. The coordinator will not start until the symbols are right, and
 * these are the tools for finding out — so everything here talks to the
 * leg runners directly and never through the coordinator.
 *
 * Two house rules do most of the work in here:
 *   - A warning nobody can act on is not a fix. Where the system can
 *     name the wrong field AND the right value, it offers a one-click
 *     correction — but it stays a click. Nothing is corrected silently.
 *   - Structural fields need a restart and say so; nothing else does.
 */

(function () {
  'use strict';

  var UI = window.MT5Trader;
  var DASH = UI.DASH;

  var local = {
    accounts: [],
    nextPort: '127.0.0.1:9101',
    pairs: {},
    editing: null,          // the pair key being edited, '' for a new one
    draft: {},              // the pair form's current values
    derived: null,          // what MT5 says about the draft's two legs
    symbols: {},            // account -> last search result
    tests: {},              // account -> last connectivity answer
    settings: null,         // the engine's tunables, with their defaults
    hot: [],                // ...and which of them apply without a restart
    connection: null,       // is the SYSTEM connected, in one answer
    timer: null
  };

  function el(id) { return document.getElementById(id); }

  function escape(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function api(path, options) {
    // Every caller reads `result.body`, and NO call site has a .catch().
    // So a failure has to arrive AS a body: an unhandled rejection here
    // is a button that does nothing at all — no toast, no error, no
    // clue why. Both ways a request can fail without ever producing
    // JSON land in the two handlers below: an answer that is not JSON
    // (a traceback, a proxy page), and a fetch that never connected.
    function failed(status, error) {
      return {ok: false, status: status, body: {error: error}};
    }
    return fetch(path, options).then(function (response) {
      return response.json().then(function (body) {
        return {ok: response.ok && body.ok !== false, status: response.status,
                body: body};
      }, function () {
        return failed(response.status,
          'the server answered ' + response.status + ' with something that ' +
          'is not JSON — check the launcher console for a traceback');
      });
    }, function (e) {
      return failed(0, 'could not reach the server (' +
        ((e && e.message) || 'connection failed') +
        ') — is the launcher still running?');
    });
  }

  function refresh() {
    return Promise.all([
      api('/api/accounts'), api('/api/pairs'), api('/api/settings'),
      api('/api/lock'), api('/api/fx'), api('/api/units'),
      api('/api/notify')
    ]).then(function (results) {
      local.telegram = !!(results[6] && results[6].body &&
                          results[6].body.telegram);
      // The units a leg can be priced per, with their sizes: the unit
      // factor is worked out from these, on the screen, step by step.
      local.units = (results[5] && results[5].body &&
                     results[5].body.units) || [];
      // The exchange rates cross-currency pairs convert through.
      local.fx = (results[4] && results[4].body && results[4].body.rates) || {};
      local.accounts = results[0].body.accounts || [];
      local.nextPort = results[0].body.next_free_port || local.nextPort;
      local.pairs = results[1].body.pairs || {};
      local.settings = results[2].body.settings || {};
      local.defaults = results[2].body.defaults || {};
      local.hot = results[2].body.hot || [];
      // Whether a PIN EXISTS, never the PIN or its hash. The pane needs
      // it to decide between 'Set PIN' and 'Change PIN', and that is
      // the entire question it is allowed to ask.
      local.settings.__pin_set = !!(results[3].body || {}).pin_set;
      render();
    });
  }

  // -- the panel ---------------------------------------------------------

  function node() {
    var existing = document.querySelector('.window.settings');
    if (existing) { return existing; }
    var panel = document.createElement('section');
    panel.className = 'window settings';
    panel.innerHTML =
      '<div class="titlebar"><span class="swatch"></span>' +
      '<span class="title">Exchanges — accounts, pairs and settings</span>' +
      '<span class="winbtns"><button class="winbtn close">&times;</button>' +
      '</span></div>' +
      '<div class="settings-body">' +
      '<section class="trading"></section>' +
      '<section class="accounts"></section>' +
      '<section class="pairs"></section>' +
      '</div>' +
      '<div class="note">Accounts, symbols and the hedge ratio are ' +
      'STRUCTURAL: the launcher reads them at startup, so a change here ' +
      'takes effect when you restart it. Everything else on the ladder ' +
      '(mode, time in force, overnight, increment, quantity) applies at ' +
      'once.</div>';
    panel.querySelector('.close').addEventListener('click', function () {
      UI.closePanel(UI.panelId('settings'));
    });
    panel.addEventListener('click', onClick);
    panel.addEventListener('change', onChange);
    // ...and as each key goes in, so a lot typed is a contract size
    // seen before the field is left.
    panel.addEventListener('input', function (e) {
      if (e.target.closest('.pair-form')) { readDraft(); refreshWorking(); }
    });
    // A click is mousedown AND mouseup on the SAME element. Replace
    // that element in between — which the connection poll does every
    // five seconds, wholesale, with `innerHTML =` — and the browser
    // fires no click at all: the button the operator pressed no longer
    // exists to receive it. Save pair did nothing, said nothing, and
    // sent nothing (live 2026-08-31).
    //
    // So while a button is held down, nothing repaints. The redraw is
    // not skipped, only deferred to the mouseup.
    panel.addEventListener('mousedown', function () {
      local.pressing = true;
    });
    ['mouseup', 'mouseleave', 'blur'].forEach(function (event) {
      window.addEventListener(event, function () {
        if (!local.pressing) { return; }
        local.pressing = false;
        // AFTER the click has been dispatched, not instead of it.
        window.setTimeout(function () {
          if (local.missed) { local.missed = false; render(); }
        }, 0);
      });
    });
    document.getElementById('desktop').appendChild(panel);
    refresh();
    refreshConnection();
    // The connection state is the one thing on this page that changes
    // without the operator doing anything.
    if (!local.timer) {
      local.timer = window.setInterval(function () {
        if (document.querySelector('.window.settings')) {
          refreshConnection();
        }
      }, 5000);
    }
    return panel;
  }

  function render(fromForm) {
    var panel = document.querySelector('.window.settings');
    if (!panel) { return; }
    // The ladders repaint three times a second. A form that repaints
    // with them loses whatever is half-typed into it — and a field that
    // empties itself under the operator is worse than no form at all.
    // So: keep what is in the pair form (it is the draft), and do not
    // touch a section the cursor is inside.
    //
    // `fromForm === false` says the draft was just changed in code — a
    // one-click correction the operator asked for — and re-reading the
    // form here would immediately undo it.
    if (fromForm !== false) { readDraft(); }
    redraw(panel.querySelector('.trading'), tradingHtml);
    redraw(panel.querySelector('.accounts'), accountsHtml);
    redraw(panel.querySelector('.pairs'), pairsHtml);
  }

  //: The blank "new account" row's fields, in render order.
  var NEW_ACCOUNT_FIELDS = ['f-name', 'f-kind', 'f-terminal', 'f-login',
                            'f-server', 'f-endpoint', 'f-password',
                            'f-appid', 'f-userid', 'f-exchange',
                            'f-apisecret', 'f-totp'];

  function newAccountDraft(section) {
    // What is half-typed into the NEW account row, or null.
    //
    // The connection poll repaints this table every 5 seconds and
    // `innerHTML =` throws away anything not yet saved. isTyping()
    // only protects a field that HAS FOCUS, so the moment the operator
    // clicks Save — or leaves the window to copy a terminal path — the
    // row empties under them, and the save then refuses for a missing
    // name while the screen shows no reason at all. The row is unsaved
    // work: carry it across the repaint.
    if (!section) { return null; }
    var row = section.querySelector('tr.new');
    if (!row) { return null; }
    var draft = {};
    NEW_ACCOUNT_FIELDS.forEach(function (field) {
      var input = row.querySelector('.' + field);
      draft[field] = input ? input.value : '';
    });
    // The endpoint arrives PRE-FILLED with the next free port, so it
    // alone is not the operator having started: carrying it would pin
    // the row to a port that may no longer be free.
    var started = NEW_ACCOUNT_FIELDS.some(function (field) {
      return field !== 'f-endpoint' && field !== 'f-kind' &&
        field !== 'f-exchange' && draft[field];
    });
    return started ? draft : null;
  }

  function restoreNewAccount(section, draft) {
    if (!draft || !section) { return; }
    var row = section.querySelector('tr.new');
    if (!row) { return; }
    NEW_ACCOUNT_FIELDS.forEach(function (field) {
      var input = row.querySelector('.' + field);
      if (input) { input.value = draft[field]; }
    });
    kindClass(row);
  }

  function kindClass(row) {
    /* An MT5 row and an Arrow row ask for different things. Both sets of
     * fields are drawn and the kind picks which are SHOWN, so switching
     * kind never throws away what was typed. */
    var kind = (row.querySelector('.f-kind') || {}).value || 'mt5';
    row.classList.toggle('kind-arrow', kind === 'arrow');
    row.classList.toggle('kind-mt5', kind !== 'arrow');
  }

  function stampRendered(section) {
    // What each field was RENDERED with. Comparing against this is how
    // an operator's unapplied edit is told apart from a value that
    // simply came back unchanged from the server.
    // input AND select: the ladder-click convention is a <select>,
    // and a control left out here reverts under the 5s poll and is
    // then SAVED back at its old value by Apply.
    Array.prototype.forEach.call(
      section.querySelectorAll('input, select'), function (input) {
        input.dataset.rendered = input.type === 'checkbox'
          ? String(input.checked) : input.value;
      });
  }

  function editedFields(section) {
    // Settings the operator has CHANGED but not yet applied, or null.
    //
    // The same hazard as the new-account row, one section over: the 5s
    // connection poll repaints this form from the SAVED settings, and
    // isTyping() stops protecting the moment focus leaves the field. So
    // a number typed and then clicked away from silently reverts — and
    // Apply, reading the form, then saves the OLD value back over the
    // new one. Live: the stale-quote limit was set to 15 three times
    // and stayed 5.
    //
    // Keyed by class, which is unique per field in this form. The
    // accounts table repeats classes down its rows and is carried by
    // newAccountDraft() instead.
    if (!section || !section.querySelector('.s-stale')) { return null; }
    var edited = null;
    Array.prototype.forEach.call(
      section.querySelectorAll('input, select'), function (input) {
        if (input.dataset.rendered === undefined) { return; }
        var now = input.type === 'checkbox'
          ? String(input.checked) : input.value;
        if (now === input.dataset.rendered) { return; }
        edited = edited || {};
        edited[input.className] = now;
      });
    return edited;
  }

  function restoreEdited(section, edited) {
    if (!edited) { return; }
    Object.keys(edited).forEach(function (cls) {
      var input = section.querySelector('.' + cls);
      if (!input) { return; }
      if (input.type === 'checkbox') {
        input.checked = edited[cls] === 'true';
      } else {
        input.value = edited[cls];
      }
    });
  }

  function redraw(section, build) {
    if (!section) { return; }
    if (isTyping(section)) { return; }
    // Not with a button held down: see the mousedown listener.
    if (local.pressing) { local.missed = true; return; }
    // Nor while a window is being dragged: these three sections are
    // large tables, and rebuilding them under the pointer is most of
    // why this window moved like treacle.
    if (document.querySelector('.window.dragging')) { return; }
    // Sections without a new-account row or a settings form give null
    // for their half of this, so each is a no-op outside its own table.
    var draft = newAccountDraft(section);
    var edited = editedFields(section);
    section.innerHTML = build();
    stampRendered(section);
    restoreNewAccount(section, draft);
    restoreEdited(section, edited);
  }

  function isTyping(section) {
    var active = document.activeElement;
    if (!active || !section.contains(active)) { return false; }
    // A BUTTON holding focus is a click that just happened, and the
    // whole point of that click is usually to change what is drawn.
    // Only a field being typed into blocks the repaint.
    var tag = active.tagName;
    return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
  }

  // -- how a click behaves ------------------------------------------------

  function tradingHtml() {
    var settings = local.settings;
    if (!settings) { return '<h3>Trading</h3><p>loading…</p>'; }
    var confirm = settings.CONFIRM_MARKET_CLICKS;
    var html = '<h3>Trading <small>what a click does, and how fast</small>' +
      '</h3><div class="fields trading-fields">';
    html += field('Market clicks',
      '<label class="check"><input type="checkbox" class="s-confirm"' +
      (confirm ? ' checked' : '') + '> ask before crossing</label>' +
      '<div class="hint">' + (confirm
        ? 'ON: every market click asks first. Slower, and deliberate.'
        : 'OFF (default): ONE CLICK IS ONE ORDER — a market click ' +
          'crosses both accounts immediately. The arming carries the ' +
          'weight instead: the mode badge, the tinted columns, the ' +
          'cursor.') + '</div>');
    var tt = (settings.CLICK_CONVENTION || 'TT') !== 'TOUCH';
    html += field('Ladder click',
      '<select class="s-click">' +
      '<option value="TT"' + (tt ? ' selected' : '') + '>' +
      'Bids buy — TT price ladder</option>' +
      '<option value="TOUCH"' + (tt ? '' : ' selected') + '>' +
      'Asks buy — hit and lift</option></select>' +
      '<div class="hint">' + (tt
        ? 'TT: clicking BIDS joins the bid, which is a resting BUY; ' +
          'clicking ASKS joins the offer and sells. What every desk ' +
          'arrives with.'
        : 'HIT/LIFT: clicking ASKS lifts the offer and BUYS; clicking ' +
          'BIDS hits the bid and sells.') +
      ' It moves only which column sends which side — the price is the ' +
      'row you clicked either way, and the BUY and SELL buttons name ' +
      'their own side and do not change.</div>');
    // WHY A CLICK SOMETIMES CLOSES SOMETHING. Surprising the first
    // time it happens on a mixed book, and there was no switch and no
    // sentence about it anywhere on the screen.
    var reduce = settings.CLOSE_FIRST !== false;
    html += field('Opposite clicks',
      '<label class="check"><input type="checkbox" class="s-closefirst"' +
      (reduce ? ' checked' : '') + '> reduce before opening</label>' +
      '<div class="hint">' + (reduce
        ? 'ON (default): a click the other way CLOSES open tickets ' +
          'first, oldest first, taking the last one in part. These ' +
          'accounts are hedging, so MT5 never nets — without this, ' +
          'covering a short leaves you holding a short AND a long, ' +
          'both paying carry. On a mixed book a click that ADDS to ' +
          'the net still clears an opposite ticket: the net moves by ' +
          'what you clicked either way, but that part CROSSES when ' +
          'the level prints instead of earning it.'
        : 'OFF: every click purely OPENS. On a hedging account that ' +
          'stacks a second, opposite ticket beside what you already ' +
          'have — both live, both paying carry.') + '</div>');
    // -- the screen lock --------------------------------------------
    //
    //     Set here rather than at install time on purpose: the PIN
    //     belongs to the TRADER, and one typed by whoever set the
    //     machine up is one the whole office knows.
    html += field('Screen lock PIN',
      '<div class="pinset">' +
      '<input class="s-pin-current" type="password" autocomplete="off" ' +
      'placeholder="current PIN"' +
      (settings.__pin_set ? '' : ' disabled') + '>' +
      '<input class="s-pin-new" type="password" autocomplete="off" ' +
      'placeholder="new PIN">' +
      '<input class="s-pin-again" type="password" autocomplete="off" ' +
      'placeholder="again">' +
      '<button class="btn s-pin-save" type="button">' +
      (settings.__pin_set ? 'Change PIN' : 'Set PIN') + '</button>' +
      '</div>' +
      '<div class="hint pin-note">' + (settings.__pin_set
        ? 'A PIN is set on this PC. The padlock beside NEXUS locks the ' +
          'screen, Ctrl+L does the same, and it locks itself after the ' +
          'idle time below. Changing it needs the current one.'
        : 'No PIN on this PC, so the lock is off. Set one and the ' +
          'screen locks when you ask it to and when it is left alone - ' +
          'nothing on a locked screen can trade.') +
      ' It is stored as a one-way hash in .env, never as the PIN. ' +
      'It does NOT stop somebody at this keyboard opening MetaTrader 5 ' +
      'directly: a Windows account per trader is what does that.' +
      '</div>');
    html += field('Lock itself after (minutes)',
      '<input class="s-autolock" type="number" min="0" step="1" ' +
      'value="' + escape(settings.AUTO_LOCK_MINUTES) + '">' +
      '<div class="hint">Minutes with nobody touching the screen ' +
      'before it locks. 15 is long enough to watch a ladder without ' +
      'clicking. 0 turns it off and leaves only the padlock. It does ' +
      'nothing until a PIN is set.</div>');
    html += field('Slippage protection (ticks)',
      '<input class="s-protection" type="number" min="0" step="0.5" ' +
      'value="' + escape(settings.MARKET_PROTECTION_TICKS) + '">' +
      '<div class="hint">A market click is market-WITH-protection: a ' +
      'fill worse than the clicked spread by more than this many ' +
      'increments is refused, and the ladder says why. 0 turns it off.' +
      '</div>');
    html += field('Ladder row height (px)',
      '<input class="s-rowheight" type="number" min="12" max="40" ' +
      'step="1" value="' + escape(settings.ROW_HEIGHT_PX) + '">' +
      '<div class="hint">17 is the reference screen\'s. A bigger target ' +
      'is a faster and safer click on a large monitor.</div>');
    html += field('Click drain (seconds)',
      '<input class="s-drain" type="number" min="0.005" max="1" ' +
      'step="0.005" value="' + escape(settings.COMMAND_POLL_SEC) + '">' +
      '<div class="hint">How often the engine picks clicks up, on its ' +
      'own thread. This is the click-to-order latency you feel; the ' +
      'price poll is separate and slower.</div>');
    html += field('Re-peg dead band (ticks)',
      '<input class="s-repeg" type="number" min="0" step="0.5" value="' +
      escape(settings.REPEG_DEAD_BAND_TICKS) + '">' +
      '<div class="hint">LIMIT mode only. Every re-peg loses queue ' +
      'position, so a tight band means never being at the front of a ' +
      'queue — which defeats quoting.</div>');
    html += field('Stale quote limit (seconds)',
      '<input class="s-stale" type="number" min="0" step="0.5" value="' +
      escape(settings.MAX_QUOTE_AGE_SEC) + '">' +
      '<div class="hint">A pair is only as good as its worse leg. 0 ' +
      'turns the guard off — it can withhold an order, never a close.' +
      '</div>');
    html += '</div>';

    // What a trade COSTS — and therefore where it gets out — is NOT
    // here. Commission, the slippage allowance, the nights held, the
    // take-profit percentage and the carry rate belong to ONE LADDER:
    // a gold basis and an oil differential are charged differently and
    // held for different lengths of time, and one set of numbers
    // covering both is a set that is wrong for at least one of them.
    // They live behind the cog on each ladder.
    html += '<p class="hint where-exits">Commission, the slippage ' +
      'allowance, nights held, the take-profit % and the carry rate are ' +
      'per LADDER \u2014 open a ladder\u2019s <b>&#9881;</b> for its own. ' +
      'They are not the same trade on every pair.</p>';

    html += '</div><div class="actions">' +
      '<button class="btn save-settings">Apply</button>' +
      '<span class="hint">These apply to the running engine at once — ' +
      'no restart.</span></div>';
    return html;
  }

  // -- accounts -----------------------------------------------------------

  function accountsHtml() {
    var html = '<h3>Exchanges <small>one login, one terminal, one port — ' +
      'the three things that cannot be shared</small></h3>';
    html += connectionHtml();
    html += fxHtml();
    html += '<table class="grid-form"><thead><tr><th>Name</th>' +
      '<th>Kind</th>' +
      '<th>Terminal (terminal64.exe) / Arrow app id</th>' +
      '<th>Login / Arrow user id</th><th>Server / exchange</th>' +
      '<th>Runner endpoint</th><th>Secrets (to .env)</th><th></th>' +
      '</tr></thead><tbody>';
    local.accounts.forEach(function (account) {
      html += accountRow(account);
    });
    html += accountRow({name: '', endpoint: local.nextPort, isNew: true});
    html += '</tbody></table>';
    return html;
  }

  function accountRow(account) {
    var clashes = [account.endpoint_clash, account.login_clash,
                   account.terminal_clash].filter(Boolean);
    var test = local.tests[account.name];
    var kind = account.kind === 'arrow' ? 'arrow' : 'mt5';
    var html = '<tr data-account="' + escape(account.name) + '" class="' +
      (account.isNew ? 'new ' : '') + 'kind-' + kind + '">';
    html += '<td>' + (account.isNew
      ? '<input class="f-name" placeholder="e.g. CFI Spot">'
      : escape(account.name)) + '</td>';
    html += '<td><select class="f-kind">' +
      '<option value="mt5"' + (kind === 'mt5' ? ' selected' : '') +
      '>MT5</option><option value="arrow"' +
      (kind === 'arrow' ? ' selected' : '') + '>Arrow (MCX)</option>' +
      '</select></td>';
    html += '<td><input class="f-terminal mt5-only" value="' +
      escape(account.terminal_path) + '" placeholder="blank = attach to ' +
      'whichever terminal is open">' +
      '<input class="f-appid arrow-only" value="' + escape(account.app_id) +
      '" placeholder="Arrow app id"></td>';
    html += '<td><input class="f-login mt5-only" value="' +
      escape(account.login) + '">' +
      '<input class="f-userid arrow-only" value="' +
      escape(account.user_id) + '" placeholder="Arrow user id"></td>';
    html += '<td><input class="f-server mt5-only" value="' +
      escape(account.server) + '">' +
      '<input class="f-exchange arrow-only" value="' +
      escape(account.exchange || 'MCX') + '"></td>';
    html += '<td><input class="f-endpoint" value="' +
      escape(account.endpoint) + '" placeholder="127.0.0.1:9101"></td>';
    html += '<td><input class="f-password" type="password" placeholder="' +
      'password: ' + (account.password_set ? 'set — type to replace'
                                           : 'not set') + '">' +
      '<input class="f-apisecret arrow-only" type="password" ' +
      'placeholder="API secret: ' + (account.api_secret_set
        ? 'set — type to replace' : 'not set') + '">' +
      '<input class="f-totp arrow-only" type="password" ' +
      'placeholder="TOTP seed (base32): ' + (account.totp_secret_set
        ? 'set — type to replace' : 'not set') + '">' +
      '<div class="hint">' + (account.isNew
        ? 'goes to .env under keys derived from the name'
        : '→ ' + escape(account.password_env) + ' in .env') +
      '</div></td>';
    html += '<td class="actions">' +
      '<button class="btn save-account">Save</button>' +
      (account.isNew ? '' :
        // The three questions an operator actually asks, in the order
        // they ask them.
        '<button class="btn connect-account" title="Is the leg runner ' +
        'there, and is its terminal logged in?">Connect</button>' +
        '<button class="btn test-account" title="Can this account ' +
        'trade — Algo Trading, permissions, hedging?">Test</button>' +
        '<button class="btn diagnose-account" title="Everything: the ' +
        'account, its symbols, and whether the two legs fit">Diagnose' +
        '</button>' +
        '<button class="btn danger delete-account">Delete</button>') +
      '</td></tr>';

    if (clashes.length) {
      html += '<tr class="problem"><td colspan="8">' +
        clashes.map(escape).join('<br>') + '</td></tr>';
    }
    if (test) {
      html += '<tr class="checklist"><td colspan="8">' +
        checklistHtml(test) + '</td></tr>';
    }
    return html;
  }

  function fxHtml() {
    /* The exchange rates cross-currency pairs convert through — USDINR
     * for a dollar leg against a rupee leg. Typed here, stamped with the
     * time it was typed, and shown with its age: a rate older than its
     * limit still draws the spread, but withholds orders until updated.
     */
    var names = Object.keys(local.fx || {}).sort();
    if (!names.length) { names = ['USDINR']; }
    var html = '<h3>Exchange rates <small>leg A x rate x unit factor = ' +
      'leg A in leg B\'s money — a stale rate withholds orders, never a ' +
      'close</small></h3><table class="grid-form fx-rates"><thead><tr>' +
      '<th>Pair</th><th>Rate</th><th>Source</th><th>Age</th>' +
      '<th>State</th><th></th></tr></thead><tbody>';
    names.forEach(function (name) {
      var row = (local.fx || {})[name] || {};
      var age = row.age_sec === null || row.age_sec === undefined
        ? DASH : (row.age_sec / 3600).toFixed(1) + 'h';
      html += '<tr data-fx="' + escape(name) + '"><td>' + escape(name) +
        '</td><td><input class="fx-rate" type="number" step="0.0001" ' +
        'value="' + escape(row.source === 'symbol' ? '' :
                           (row.rate === null || row.rate === undefined
                             ? '' : row.rate)) + '" placeholder="' +
        (row.source === 'symbol' ? 'live: ' + UI.fmt(row.rate, 4)
                                 : 'e.g. 84.25') + '"></td>' +
        '<td>' + escape(row.source === 'symbol'
          ? 'live ' + (row.symbol || '') + ' on ' + (row.account || '')
          : (row.source === 'missing' ? 'not set' : 'manual')) + '</td>' +
        '<td>' + age + '</td>' +
        '<td' + (row.stale ? ' class="problem-text"' : '') + '>' +
        escape(row.stale || 'OK') + '</td>' +
        '<td class="actions"><button class="btn save-fx">Set</button>' +
        '</td></tr>';
    });
    html += '</tbody></table>';
    // Where the arbitrage alerts go besides this screen.
    html += '<div class="telegram-row">Arbitrage alerts to Telegram: ' +
      (local.telegram
        ? '<b>set up</b> <button class="btn tiny test-telegram">Send test' +
          '</button>'
        : '<b class="problem-text">not set up</b> — put TELEGRAM_BOT_TOKEN ' +
          'and TELEGRAM_CHAT_ID in .env (docs/SETUP.md). Alerts still show ' +
          'here and sound.') +
      ' Each pair\'s alert is switched on in its ladder ⚙ settings.</div>';
    return html;
  }

  function saveFx(row) {
    var name = row.dataset.fx;
    var rate = parseFloat(row.querySelector('.fx-rate').value);
    if (!(rate > 0)) {
      UI.toast(name + ' must be a positive number');
      return;
    }
    return api('/api/fx/' + encodeURIComponent(name),
               {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({rate: rate})}).then(function (result) {
      if (!result.ok) { UI.toast(result.body.error); return; }
      UI.toast(name + ' set to ' + rate + ' — the ladders reprice on the ' +
               'next poll', 'ok');
      refresh();
    });
  }

  function checklistHtml(result) {
    // The answer, as a checklist with a FIX on every failure.
    if (result.error && !result.checks) {
      return '<div class="problem-text">' + escape(result.error) + '</div>';
    }
    var overall = result.overall || (result.ok ? 'PASS' : 'FAIL');
    var html = '<div class="checklist-head ' + overall.toLowerCase() + '">' +
      escape(result.ran || '') + '<b>' + overall + '</b> — ' +
      result.passed + ' passed, ' + result.warnings + ' warning(s), ' +
      result.failed + ' failed' +
      (result.connected ? ' · CONNECTED' : '') + '</div>';
    html += '<table class="checks"><tbody>';
    (result.checks || []).forEach(function (check) {
      html += '<tr class="c-' + check.status.toLowerCase() + '">';
      html += '<td class="status">' + check.status + '</td>';
      html += '<td class="what">' + escape(check.name) + '</td>';
      html += '<td>' + escape(check.message);
      // A warning nobody can act on is not a fix: every failure carries
      // the step that makes it pass.
      if ((check.fix || []).length) {
        html += '<ul class="fix">';
        check.fix.forEach(function (step) {
          html += '<li>' + escape(step) + '</li>';
        });
        html += '</ul>';
      }
      html += '</td></tr>';
    });
    return html + '</tbody></table>';
  }

  function connectionHtml() {
    // Is the system connected — plainly, at the top, in one line.
    var state = local.connection;
    if (!state) { return '<div class="conn checking">checking…</div>'; }
    var html = '<div class="conn ' + (state.connected ? 'up' : 'down') +
      '"><b>' + (state.connected ? 'CONNECTED' : 'NOT READY') + '</b> ' +
      escape(state.summary);
    if ((state.blockers || []).length > 1) {
      html += '<ul>';
      state.blockers.forEach(function (blocker) {
        html += '<li>' + escape(blocker) + '</li>';
      });
      html += '</ul>';
    }
    var clock = state.broker_clock || {};
    if (clock.broker_time) {
      html += '<div class="hint">Broker time ' + escape(clock.broker_time) +
        ' · ' + escape(clock.note) + '</div>';
    } else if (clock.note) {
      html += '<div class="hint problem-text">' + escape(clock.note) +
        '</div>';
    }
    return html + '</div>';
  }

  // -- pairs ---------------------------------------------------------------

  function pairsHtml() {
    var html = '<h3>Pairs <small>each ladder is leg A on one account and ' +
      'leg B on the other; the spread is B − β × A</small></h3>';
    html += '<table class="grid-form"><thead><tr><th>Key</th><th>Name</th>' +
      '<th>Leg A</th><th>Leg B</th><th>β</th><th>Increment</th>' +
      '<th>Enabled</th><th>Status</th><th></th></tr></thead><tbody>';
    Object.keys(local.pairs).forEach(function (key) {
      var pair = local.pairs[key];
      html += '<tr data-pair="' + escape(key) + '">';
      html += '<td>' + escape(key) + '</td>';
      html += '<td>' + escape(pair.name || key) + '</td>';
      html += '<td>' + legText(pair.leg_a) + '</td>';
      html += '<td>' + legText(pair.leg_b) + '</td>';
      html += '<td>' + escape(pair.hedge_ratio) +
        (pair.hedge_ratio_for
          ? '<div class="hint">stamped for ' + escape(pair.hedge_ratio_for) +
            '</div>'
          : '<div class="hint problem-text">not stamped for any pair</div>') +
        '</td>';
      html += '<td>' + escape(pair.increment === null ||
                              pair.increment === undefined
                              ? 'derived' : pair.increment) + '</td>';
      html += '<td>' + (pair.enabled === false ? 'no' : 'yes') + '</td>';
      html += pairStatusCell(key, pair);
      html += '<td class="actions">' +
        '<button class="btn edit-pair">Edit</button>' +
        '<button class="btn danger delete-pair">Delete</button></td></tr>';
    });
    html += '</tbody></table>';
    html += '<button class="btn new-pair">New pair</button>';
    if (local.editing !== null) { html += pairForm(); }
    return html;
  }

  function pairStatus(key, pair) {
    /* Is this ladder actually working? Read from the ENGINE's own
     * snapshot, never from the fact that a row was saved.
     *
     * The distinction that matters to whoever is setting this up: a
     * pair that is configured, a pair the engine has picked up, and a
     * pair that is quoting are three different things, and only the
     * last one can be traded. Saying "connected" for any of the others
     * is how a symbol that does not exist at the broker survives all
     * the way to a click.
     */
    var snapshot = (UI.state && UI.state.snapshot) || {};
    if (pair.enabled === false) {
      return {state: 'off', text: 'disabled'};
    }
    if (snapshot.engine !== 'up') {
      return {state: 'unknown',
              text: 'the engine is not running — nothing to check against'};
    }
    var live = (snapshot.pairs || {})[key];
    if (!live) {
      return {state: 'bad',
              text: 'saved, but the engine has not picked it up yet — ' +
                    'it restarts itself within a few seconds'};
    }
    if ((live.errors || []).length) {
      // The engine's own words, which name the symbol and what the
      // account actually offers.
      return {state: 'bad', text: live.errors[0]};
    }
    if (!live.market || live.short_spread === null ||
        live.short_spread === undefined) {
      return {state: 'bad',
              text: 'both legs resolved, but no two-sided quote has ' +
                    'arrived yet'};
    }
    return {state: 'ok',
            text: 'CONNECTED — quoting at ' +
                  UI.fmt(live.short_spread, 4) + ' / ' +
                  UI.fmt(live.long_spread, 4)};
  }

  function pairStatusCell(key, pair) {
    var status = pairStatus(key, pair);
    var className = status.state === 'ok' ? 'c-pass'
      : status.state === 'bad' ? 'c-fail' : 'c-info';
    return '<td class="pair-status ' + className + '">' +
      escape(status.text) + '</td>';
  }

  function legText(leg) {
    /* The symbol, and the ACCOUNT it trades on — by name, MT5 login and
     * server. Two accounts is the whole architecture of this thing, and
     * which login a leg is actually on is the first question anyone
     * asks of a spread that has gone wrong. Reading it off the account
     * row instead of the pair row means holding two tables in your head
     * at once.
     */
    leg = leg || {};
    if (!leg.account && !leg.symbol) { return DASH; }
    var account = null;
    (local.accounts || []).forEach(function (row) {
      if (row.name === leg.account) { account = row; }
    });
    var detail = escape(leg.account || '?');
    if (account && account.login) { detail += ' · #' + escape(account.login); }
    if (account && account.kind === 'arrow') {
      detail += ' · Arrow ' + escape(account.user_id || '') + ' ' +
        escape(account.exchange || 'MCX');
    }
    if (account && account.server) {
      detail += ' · ' + escape(account.server);
    }
    if (!account) {
      // A leg pointing at an account that is no longer configured is a
      // pair that cannot trade, and it says so here rather than at the
      // first click.
      detail += ' — no such account';
    }
    return escape(leg.symbol) + (leg.fx || leg.price_factor
      ? ' <small>x ' + escape(leg.fx || '1') + ' x ' +
        escape(leg.price_factor || 1) +
        (leg.price_unit ? ' (' + escape(leg.price_unit) + ')' : '') +
        '</small>' : leg.price_unit
        ? ' <small>per ' + escape(leg.price_unit) + '</small>' : '') +
      '<div class="hint">on ' + detail + '</div>';
  }

  function pairForm() {
    var draft = local.draft;
    var derived = local.derived;
    var html = '<div class="pair-form"><h4>' +
      (local.editing ? 'Editing ' + escape(local.editing) : 'New pair') +
      '</h4><div class="fields">';
    html += field('Key', '<input class="p-key" value="' +
                  escape(draft.key) + '" placeholder="' +
                  escape(autoKey() || 'built from the two symbols') + '"' +
                  (local.editing ? ' disabled' : '') + '>' +
                  '<div class="hint">Leave it blank and it is built from ' +
                  'the two symbols — the key is an identifier, not a ' +
                  'setting, and typing one by hand is not something this ' +
                  'should ask for.' +
                  (local.editing && autoKey() && autoKey() !== local.editing
                    ? ' <b class="problem-text">Saving will rename this ' +
                      'pair to ' + escape(autoKey()) + ', because its ' +
                      'symbols changed.</b>'
                    : '') + '</div>');
    html += field('Name', '<input class="p-name" value="' +
                  escape(draft.name) + '">');
    html += field('Leg A', accountSelect('p-account-a', draft.leg_a_account) +
                  symbolInput('a', draft.leg_a_symbol));
    html += field('Leg B', accountSelect('p-account-b', draft.leg_b_account) +
                  symbolInput('b', draft.leg_b_symbol));
    var suggestion = derived && derived.conversion_suggestion;
    html += field('Leg A FX',
                  '<input class="p-fx-a" value="' +
                  escape(draft.leg_a_fx || '') + '" placeholder="blank = ' +
                  'same currency, e.g. USDINR">' +
                  '<div class="hint">Leg A is restated in leg B\'s money ' +
                  'before the two are subtracted: A x rate x unit factor. ' +
                  'Set the rate under Exchange rates above.</div>');
    html += field('Leg A priced per',
                  unitSelect('p-unit-a', draft.leg_a_unit) +
                  '<div class="hint">The unit leg A\'s PRICE is quoted ' +
                  'per — COMEX gold and silver per troy ounce, NYMEX crude ' +
                  'per barrel.</div>');
    html += field('Leg B priced per',
                  unitSelect('p-unit-b', draft.leg_b_unit) +
                  '<div class="hint">MCX gold is quoted per 10 g, MCX ' +
                  'silver per kg, MCX crude per barrel.</div>');
    html += field('Unit factor', factorControl(draft, suggestion));
    html += field('Lots per Qty — same quantity both sides',
                  clipControl(draft));
    html += field('Leg A contract (override)', contractControl(draft, 'a'));
    html += field('Leg B contract (override)', contractControl(draft, 'b'));
    html += field('Pair type',
                  '<select class="p-type">' +
                  option('SPOT_FUTURE', draft.pair_type) +
                  option('FUTURE_FUTURE', draft.pair_type) +
                  option('RELATED', draft.pair_type) + '</select>' +
                  '<div class="hint">Same underlying → β is 1 and the ' +
                  'spread IS the basis. Different instruments on the same ' +
                  'price scale → β 1 and the spread is the differential. ' +
                  'Different scales → the price ratio.</div>');
    html += field('β (hedge ratio)',
                  '<input class="p-beta" type="number" step="0.000001" ' +
                  'value="' + escape(draft.hedge_ratio) + '">' +
                  (derived && derived.suggested_beta !== null &&
                   derived.suggested_beta !== undefined
                    ? '<div class="hint">' + escape(derived.beta_reason) +
                      ' <button class="btn tiny use-beta" data-value="' +
                      derived.suggested_beta + '">use ' +
                      derived.suggested_beta + '</button></div>'
                    : ''));
    html += field('Futures expiry',
                  '<input class="p-expiry" type="date" value="' +
                  escape(draft.expiry || '') + '">' +
                  '<div class="hint">The futures leg\'s last trading ' +
                  'day. With the broker\'s own swap it gives the FAIR ' +
                  'spread: a basis converges to zero at expiry, and ' +
                  'until then it is worth its carry. The swaps live on ' +
                  'the ladder\'s own settings, per leg and per side.' +
                  '</div>');
    html += field('Increment',
                  '<input class="p-increment" type="number" step="0.000001" ' +
                  'value="' + escape(draft.increment) +
                  '" placeholder="blank = derived">' +
                  (derived
                    ? '<div class="hint">' +
                      escape(derived.increment_derivation) + ' = ' +
                      escape(derived.increment) +
                      ' <button class="btn tiny use-increment" ' +
                      'data-value="' + derived.increment + '">use it</button>' +
                      '</div>'
                    : ''));
    html += field('Default quantity (spreads)',
                  '<input class="p-quantity" type="number" min="0" ' +
                  'step="0.01" value="' + escape(draft.default_quantity) +
                  '">' + (derived
                    ? '<div class="hint">' + escape(derived.clip_derivation) +
                      ', ' + UI.money(derived.spread_units,
                                      ((derived.specs || {}).b || {}).currency) +
                      ' per 1.00 of spread</div>' : ''));
    html += field('Quoting leg (LIMIT mode)',
                  '<select class="p-quoting">' +
                  '<option value="">auto — the wider book</option>' +
                  option('a', draft.quoting_leg) +
                  option('b', draft.quoting_leg) + '</select>' +
                  (derived
                    ? '<div class="hint">measured widths: A ' +
                      UI.fmt(derived.widths.a, 4) + ' · B ' +
                      UI.fmt(derived.widths.b, 4) + ' — ' +
                      escape(derived.quoting_note) + '</div>'
                    : ''));
    html += field('Enabled',
                  '<input class="p-enabled" type="checkbox"' +
                  (draft.enabled === false ? '' : ' checked') + '>');
    html += '</div>';

    if (derived) {
      html += '<div class="derived">';
      html += '<div>Spread now: <b>' + UI.fmt(derived.spread_now, 4) +
        '</b></div>';
      html += '<div>Minimum this pair can trade: <b>' +
        (derived.min_notional_usd
          ? UI.money(derived.min_notional_usd,
                     ((derived.specs || {}).b || {}).currency) + ' a leg'
          : DASH) +
        '</b> — state it before anyone tries to trade under it</div>';
      html += '<div>Contract sizes read from the brokers: A ' +
        escape((derived.specs.a || {}).contract_size) + ' · B ' +
        escape((derived.specs.b || {}).contract_size) +
        ' (never typed in' + ((derived.specs.a || {}).contract_size_raw
          ? '; A is ' + escape(derived.specs.a.contract_size_raw) +
            ' per lot in its own unit' : '') + ')</div>';
      if (derived.expiry_watch) {
        var w = derived.expiry_watch;
        html += '<div>Expiry: A ' + (w.days_a === null ? DASH
          : UI.fmt(w.days_a, 1) + ' days') + ' · B ' +
          (w.days_b === null ? DASH : UI.fmt(w.days_b, 1) + ' days') +
          (w.warnings.length ? ' — <b class="problem-text">' +
            escape(w.warnings.join('; ')) + '</b>' : '') + '</div>';
      }
      html += '</div>';
    }
    html += '<div class="actions">' +
      '<button class="btn derive-pair">Read both legs from MT5</button>' +
      '<button class="btn save-pair">Save pair</button>' +
      '<button class="btn cancel-pair">Cancel</button></div></div>';
    return html;
  }

  function unitOf(key) {
    var found = null;
    (local.units || []).forEach(function (unit) {
      if (unit.key === key) { found = unit; }
    });
    return found;
  }

  function unitSelect(cls, selected) {
    /* Grouped by what the unit MEASURES — an ounce converts to a gram,
     * never to a barrel — with "custom" for a broker that quotes in
     * something this list does not have. */
    var groups = {};
    (local.units || []).forEach(function (unit) {
      (groups[unit.dimension] = groups[unit.dimension] || []).push(unit);
    });
    var html = '<select class="' + cls + '"><option value="">' +
      'choose…</option>';
    Object.keys(groups).forEach(function (dimension) {
      html += '<optgroup label="' + escape(dimension) + '">';
      groups[dimension].forEach(function (unit) {
        html += '<option value="' + escape(unit.key) + '"' +
          (unit.key === selected ? ' selected' : '') + '>' +
          escape(unit.label) + '</option>';
      });
      html += '</optgroup>';
    });
    html += '<option value="custom"' + (selected === 'custom'
      ? ' selected' : '') + '>custom — type the factor</option>';
    return html + '</select>';
  }

  function factorSteps(fromKey, toKey) {
    /* The same working the server does (contracts.factor_steps), so the
     * operator sees it change as they pick, before anything is saved. */
    var a = unitOf(fromKey), b = unitOf(toKey);
    if (!a || !b) { return null; }
    if (a.dimension !== b.dimension) {
      return {factor: null, steps: [a.key + ' measures ' + a.dimension +
        ' and ' + b.key + ' measures ' + b.dimension +
        ' — they do not convert']};
    }
    var factor = b.size / a.size;
    return {factor: factor, steps: [
      'Leg A is priced per ' + a.key + ' = ' + a.size + ' ' + a.base,
      'Leg B is priced per ' + b.key + ' = ' + b.size + ' ' + b.base,
      'one ' + b.key + ' is ' + b.size + ' ÷ ' + a.size + ' = ' +
        factor.toPrecision(8) + ' of a ' + a.key + ', so a price per ' +
        b.key + ' = a price per ' + a.key + ' × ' + factor.toPrecision(8),
      'unit factor = ' + factor.toPrecision(10)]};
  }

  function factorControl(draft, suggestion) {
    var custom = draft.leg_a_unit === 'custom' ||
      draft.leg_b_unit === 'custom' || !draft.leg_a_unit ||
      !draft.leg_b_unit;
    var worked = custom ? null : factorSteps(draft.leg_a_unit,
                                             draft.leg_b_unit);
    var factor = worked && worked.factor !== null ? worked.factor
      : draft.leg_a_factor;
    var html = '<input class="p-factor-a" type="number" step="any" ' +
      'value="' + escape(factor === null || factor === undefined ||
                         isNaN(factor) ? '' : factor) + '"' +
      (custom ? ' placeholder="blank = 1"'
              : ' readonly title="computed from the two units"') + '>';
    return html + '<div class="factor-working unit-working">' +
      unitWorking(draft, suggestion) + '</div>';
  }

  function unitWorking(draft, suggestion) {
    var custom = draft.leg_a_unit === 'custom' ||
      draft.leg_b_unit === 'custom' || !draft.leg_a_unit ||
      !draft.leg_b_unit;
    var worked = custom ? null : factorSteps(draft.leg_a_unit,
                                             draft.leg_b_unit);
    var html = '';
    if (worked) {
      html += '<ol>' + worked.steps.map(function (step) {
        return '<li>' + escape(step) + '</li>';
      }).join('') + '</ol>';
      html += workedExample(draft, worked.factor);
    } else {
      html += '<div class="hint">Pick both units above and the factor is ' +
        'worked out here, or choose <b>custom</b> to type one. Common: ' +
        'oz → 10 g = 0.32150747 (gold) · oz → kg = 32.150747 (silver) · ' +
        'bbl → bbl = 1 (crude) · lb → kg = 2.2046226 (copper)</div>';
    }
    if (suggestion && suggestion.price_factor) {
      html += '<div class="hint">' + escape(suggestion.why) +
        ' <button class="btn tiny use-conversion" data-fx="' +
        escape(suggestion.fx || '') + '" data-factor="' +
        suggestion.price_factor + '" data-unit-a="' +
        escape(suggestion.unit_a || '') + '" data-unit-b="' +
        escape(suggestion.unit_b || '') + '">use ' +
        escape(suggestion.unit_a || '') + ' → ' +
        escape(suggestion.unit_b || '') + '</button></div>';
    }
    return html;
  }

  function clipControl(draft) {
    /* What ONE Qty sends on each leg. A spread is only a spread when
     * both legs hold the SAME AMOUNT of the commodity — 1 CL is 1,000
     * bbl, so it is matched by 10 MCX CRUDEOIL of 100 bbl, not by 1.
     * The match is worked out from the contracts and shown; the trader
     * types or takes it. */
    var html = '<span class="symbol-row"><input class="p-clip-a" ' +
      'type="number" min="0" step="any" title="Leg A lots per Qty" ' +
      'value="' + escape(draft.clip_lots_a || 1) + '"> : ' +
      '<input class="p-clip-b" type="number" min="0" step="any" ' +
      'title="Leg B lots per Qty" value="' + escape(draft.clip_lots_b || 1) +
      '"></span>';
    return html + '<div class="factor-working clip-working">' +
      clipWorking(draft) + '</div>';
  }

  function qtyText(value, base) {
    /* Grams past a kilo are written in kilos: 155,517 g is 155.52 kg. */
    if (base === 'g' && Math.abs(value) >= 1000) {
      return UI.fmt(value / 1000, 3) + ' kg';
    }
    return UI.fmt(value, 2) + ' ' + base;
  }

  function clipWorking(draft) {
    var m = (local.derived || {}).lot_match;
    if (!m) {
      return '<div class="hint">Press Read both legs: the quantity each ' +
        'lot holds is worked out from the two contracts.</div>';
    }
    // Re-scaled to the lots now in the boxes, so the line follows typing.
    var la = parseFloat(draft.clip_lots_a) || 1;
    var lb = parseFloat(draft.clip_lots_b) || 1;
    var qa = la * m.lot_a, qb = lb * m.lot_b;
    var off = qa ? 100 * (qb - qa) / qa : null;
    var bad = off !== null && Math.abs(off) > 1;
    var html = '<div class="worked' + (bad ? ' override' : '') + '">' +
      'one Qty: A ' + la + ' × ' + qtyText(m.lot_a, m.base_unit) +
      ' = <b>' + qtyText(qa, m.base_unit) + '</b> · B ' + lb + ' × ' +
      qtyText(m.lot_b, m.base_unit) + ' = <b>' + qtyText(qb, m.base_unit) +
      '</b> · ' + (off === null ? DASH
        : (off >= 0 ? '+' : '') + off.toFixed(2) + '%') + '</div>';
    if (bad) {
      html += '<div class="hint problem-text">The ' + Math.abs(off).toFixed(2) +
        '% difference is an OUTRIGHT position in the commodity, not part ' +
        'of the spread.</div>';
    }
    if (m.best_clip_a) {
      html += '<div class="hint">closest small match ' + m.best_clip_a +
        ' : ' + m.best_clip_b + ' (' + UI.fmt(m.best_mismatch_pct, 2) +
        '% off) <button class="btn tiny use-clip" data-a="' +
        m.best_clip_a + '" data-b="' + m.best_clip_b + '">use ' +
        m.best_clip_a + ' : ' + m.best_clip_b + '</button></div>';
    }
    return html + '<div class="hint">from ' + escape(m.source ||
      'the contracts') + '</div>';
  }

  function lotUnitSelect(cls, selected) {
    var html = '<select class="' + cls + '"><option value="">' +
      'price units — type the contract size</option>';
    (local.units || []).forEach(function (unit) {
      html += '<option value="' + escape(unit.key) + '"' +
        (unit.key === selected ? ' selected' : '') + '>' +
        escape(unit.label) + '</option>';
    });
    return html + '</select>';
  }

  function contractSteps(qty, lotUnit, priceUnit) {
    /* The same working as the server's contracts.contract_from_lot. */
    if (!(qty > 0)) { return null; }
    if (!lotUnit) {
      return {size: qty, steps: ['contract size = ' + qty +
        ' per lot, as typed']};
    }
    var a = unitOf(lotUnit), b = unitOf(priceUnit);
    if (!a || !b || a.dimension !== b.dimension) {
      return {size: null, steps: ['a lot in ' + lotUnit + ' cannot be ' +
        'counted in ' + (priceUnit && priceUnit !== 'custom'
          ? priceUnit : 'a price unit nobody picked') +
        ' — pick the "priced per" unit above first']};
    }
    var size = qty * a.size / b.size;
    return {size: size, steps: [
      'one lot = ' + qty + ' ' + a.key + ' = ' +
        +(qty * a.size).toPrecision(10) + ' ' + a.base,
      'priced per ' + b.key + ' = ' + b.size + ' ' + b.base,
      'contract size = ' + +(qty * a.size).toPrecision(10) + ' ÷ ' +
        b.size + ' = ' + +size.toPrecision(10) + ' per lot']};
  }

  function contractControl(draft, leg) {
    /* What ONE LOT is, typed only when the broker's own figure is wrong.
     * Blank is the normal case: the broker's number is used, and the
     * exchange's spec is shown beside it to check against. */
    var qty = draft['leg_' + leg + '_lotqty'];
    var lotUnit = draft['leg_' + leg + '_lotunit'] || '';
    var priceUnit = draft['leg_' + leg + '_unit'];
    var html = '<input class="p-lotqty-' + leg +
      '" type="number" min="0" step="any" placeholder="one lot — blank = ' +
      'the broker\'s" value="' + escape(qty === null || qty === undefined ||
                                      isNaN(qty) ? '' : qty) + '">' +
      lotUnitSelect('p-lotunit-' + leg, lotUnit);
    return html + '<div class="factor-working contract-working-' + leg +
      '">' + contractWorking(draft, leg) + '</div>';
  }

  function contractWorking(draft, leg) {
    var qty = draft['leg_' + leg + '_lotqty'];
    var lotUnit = draft['leg_' + leg + '_lotunit'] || '';
    var priceUnit = draft['leg_' + leg + '_unit'];
    var worked = contractSteps(parseFloat(qty), lotUnit, priceUnit);
    var html = '';
    if (worked) {
      html += '<ol>' + worked.steps.map(function (step) {
        return '<li>' + escape(step) + '</li>';
      }).join('') + '</ol>';
      if (worked.size !== null) {
        html += '<div class="worked override">OVERRIDE: every money ' +
          'figure on this ladder uses <b>' + +worked.size.toPrecision(10) +
          '</b> per lot for leg ' + leg.toUpperCase() + '</div>';
      }
    }
    // What to check it against: the broker's figure, in the leg's own
    // units, and the exchange's published lot.
    var derived = local.derived || {};
    var spec = (derived.specs || {})[leg] || {};
    var broker = spec.contract_size_raw !== undefined &&
      spec.contract_size_raw !== null ? spec.contract_size_raw
      : spec.contract_size;
    var exchange = derived['spec_' + leg];
    html += '<div class="hint">' +
      (broker === undefined || broker === null
        ? 'broker: ' + DASH + ' (press Read both legs)'
        : 'broker says ' + escape(broker) + ' per lot') +
      (exchange ? ' · ' + escape(exchange.description) + ' spec: ' +
        escape(exchange.lot_quantity) + ' ' + escape(exchange.lot_unit) +
        ' priced per ' + escape(exchange.quote_unit) + ' = ' +
        escape(+Number(exchange.contract_size).toPrecision(10)) : '') +
      (exchange && broker && Math.abs(broker - exchange.contract_size) >
        1e-6 * exchange.contract_size
        ? ' <b class="problem-text">— they disagree</b>' : '') +
      '</div>';
    return html;
  }

  function refreshWorking() {
    /* The calculations, redrawn IN PLACE as the operator types or picks:
     * the section itself is never repainted while a field has focus
     * (that would eat what is being typed), so the working boxes are
     * the only thing replaced. */
    var form = document.querySelector('.pair-form');
    if (!form) { return; }
    var draft = local.draft;
    var suggestion = (local.derived || {}).conversion_suggestion;
    var box = form.querySelector('.unit-working');
    if (box) { box.innerHTML = unitWorking(draft, suggestion); }
    var factorInput = form.querySelector('.p-factor-a');
    var custom = draft.leg_a_unit === 'custom' ||
      draft.leg_b_unit === 'custom' || !draft.leg_a_unit ||
      !draft.leg_b_unit;
    if (factorInput) {
      var worked = custom ? null : factorSteps(draft.leg_a_unit,
                                               draft.leg_b_unit);
      factorInput.readOnly = !custom;
      if (worked && worked.factor !== null) {
        factorInput.value = worked.factor;
        draft.leg_a_factor = worked.factor;
      }
    }
    var clip = form.querySelector('.clip-working');
    if (clip) { clip.innerHTML = clipWorking(draft); }
    ['a', 'b'].forEach(function (leg) {
      var target = form.querySelector('.contract-working-' + leg);
      if (target) { target.innerHTML = contractWorking(draft, leg); }
    });
  }

  function workedExample(draft, factor) {
    /* One real number pushed through the whole conversion, so the
     * result can be held up against leg B's own price on the ladder. */
    if (factor === null || factor === undefined) { return ''; }
    var specs = (local.derived || {}).specs || {};
    var a = specs.a || {};
    var raw = a.raw_bid && a.raw_ask ? (a.raw_bid + a.raw_ask) / 2 : null;
    var fxName = draft.leg_a_fx || '';
    var rate = fxName ? ((local.fx || {})[fxName] || {}).rate : 1;
    var price = raw === null ? (a.bid && a.ask && !fxName
      ? (a.bid + a.ask) / 2 : null) : raw;
    var sample = price === null;
    if (sample) { price = 100; }
    if (rate === null || rate === undefined) {
      return '<div class="hint problem-text">' + escape(fxName) +
        ' has no rate yet — set it under Exchange rates</div>';
    }
    var result = price * rate * factor;
    return '<div class="worked">' + (sample ? 'e.g. ' : 'now: ') +
      'A ' + UI.fmt(price, 4) + ' per ' + escape(draft.leg_a_unit) +
      (fxName ? ' × ' + escape(fxName) + ' ' + UI.fmt(rate, 4) : '') +
      ' × ' + factor.toPrecision(8) + ' = <b>' + UI.fmt(result, 2) +
      '</b> per ' + escape(draft.leg_b_unit) + '</div>';
  }

  function field(label, control) {
    return '<label class="field"><span>' + label + '</span>' + control +
      '</label>';
  }

  function option(value, selected) {
    return '<option value="' + value + '"' +
      (value === selected ? ' selected' : '') + '>' + value + '</option>';
  }

  function accountSelect(cls, selected) {
    var html = '<select class="' + cls + '"><option value="">account…' +
      '</option>';
    local.accounts.forEach(function (account) {
      if (!account.name) { return; }
      html += '<option value="' + escape(account.name) + '"' +
        (account.name === selected ? ' selected' : '') + '>' +
        escape(account.name) + '</option>';
    });
    return html + '</select>';
  }

  function symbolInput(side, value) {
    var found = local.symbols[side] || null;
    var html = '<span class="symbol-row">' +
      '<input class="p-symbol-' + side + '" value="' + escape(value) +
      '" placeholder="symbol">' +
      '<button class="btn tiny find-symbol" data-side="' + side +
      '">Find</button></span>';
    if (found) {
      html += '<div class="hint symbols">';
      if (found.error) {
        html += escape(found.error);
      } else if (!found.symbols.length) {
        html += 'nothing on that account matches — it is probably the ' +
          'wrong account for this leg';
      } else {
        found.symbols.slice(0, 12).forEach(function (symbol) {
          html += '<button class="btn tiny pick-symbol" data-side="' + side +
            '" data-symbol="' + escape(symbol.symbol) + '">' +
            escape(symbol.symbol) + '</button> ';
        });
      }
      html += '</div>';
    }
    return html;
  }

  // -- events ---------------------------------------------------------------

  function onChange(e) {
    if (e.target.closest('.pair-form')) {
      readDraft();
      // A unit picked is a factor worked out: the working and the
      // example follow the choice at once.
      refreshWorking();
    }
    if (e.target.classList.contains('f-kind')) {
      kindClass(e.target.closest('tr'));
    }
  }

  function savePin(panel) {
    /* The PIN goes to the server and NOWHERE else - not into
     * local.settings, not into the settings POST, not into a toast.
     * `.env` holds the hash; this function holds the PIN for as long
     * as one fetch takes and then clears the boxes. */
    var current = panel.querySelector('.s-pin-current');
    var fresh = panel.querySelector('.s-pin-new');
    var again = panel.querySelector('.s-pin-again');
    return api('/api/pin', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({current: current.value, pin: fresh.value,
                            again: again.value})
    }).then(function (result) {
      current.value = fresh.value = again.value = '';
      if (result.body && result.body.ok) {
        UI.toast('PIN set. The screen locks from now on.');
        return refresh();
      }
      UI.toast((result.body && result.body.error) ||
               'the PIN was not changed');
    });
  }

  function onClick(e) {
    /* Every click on this page goes through here, and one that throws
     * used to do nothing and SAY nothing — indistinguishable, from the
     * operator's side, from a button that is not wired up. */
    try {
      return dispatch(e);
    } catch (error) {
      UI.toast('that click failed: ' + (error && error.message));
      throw error;                      // still in the console, in full
    }
  }

  function dispatch(e) {
    var button = e.target.closest('button');
    if (!button) { return; }
    var row = button.closest('tr');

    if (button.classList.contains('s-pin-save')) {
      return savePin(button.closest('.window'));
    }
    if (button.classList.contains('save-settings')) {
      return saveSettings();
    }
    if (button.classList.contains('save-account')) {
      return saveAccount(row);
    }
    if (button.classList.contains('save-fx')) {
      return saveFx(row);
    }
    if (button.classList.contains('test-telegram')) {
      return api('/api/notify/test', {method: 'POST'}).then(function (r) {
        UI.toast(r.ok ? 'test message sent to Telegram'
                      : r.body.error, r.ok ? 'ok' : undefined);
      });
    }
    if (button.classList.contains('use-clip')) {
      readDraft();
      local.draft.clip_lots_a = parseFloat(button.dataset.a);
      local.draft.clip_lots_b = parseFloat(button.dataset.b);
      return render(false);
    }
    if (button.classList.contains('use-conversion')) {
      readDraft();
      local.draft.leg_a_fx = button.dataset.fx || '';
      local.draft.leg_a_factor = parseFloat(button.dataset.factor);
      if (button.dataset.unitA && button.dataset.unitB) {
        local.draft.leg_a_unit = button.dataset.unitA;
        local.draft.leg_b_unit = button.dataset.unitB;
      }
      return render(false);
    }
    if (button.classList.contains('connect-account')) {
      return runCheck(row.dataset.account, 'connect', button);
    }
    if (button.classList.contains('test-account')) {
      return runCheck(row.dataset.account, 'test', button);
    }
    if (button.classList.contains('diagnose-account')) {
      return runCheck(row.dataset.account, 'diagnose', button);
    }
    if (button.classList.contains('delete-account')) {
      var target = row.dataset.account;
      return UI.ask('Delete account ' + target + '?',
        'The password stays in .env until you clear it there. A pair ' +
        'still routing to this account will refuse the deletion.',
        'Delete', function () {
          api('/api/accounts/' + encodeURIComponent(target),
              {method: 'DELETE'}).then(afterWrite);
        });
    }
    if (button.classList.contains('new-pair')) {
      local.editing = '';
      local.draft = {enabled: true, pair_type: 'SPOT_FUTURE',
                     hedge_ratio: 1.0, default_quantity: 1.0};
      local.derived = null;
      local.symbols = {};
      return render(false);
    }
    if (button.classList.contains('edit-pair')) {
      var key = row.dataset.pair;
      var pair = local.pairs[key] || {};
      local.editing = key;
      local.draft = {
        key: key, name: pair.name, pair_type: pair.pair_type || 'SPOT_FUTURE',
        hedge_ratio: pair.hedge_ratio, increment: pair.increment,
        default_quantity: pair.default_quantity,
        quoting_leg: pair.quoting_leg || '',
        enabled: pair.enabled !== false,
        leg_a_account: (pair.leg_a || {}).account,
        leg_a_symbol: (pair.leg_a || {}).symbol,
        leg_b_account: (pair.leg_b || {}).account,
        leg_b_symbol: (pair.leg_b || {}).symbol,
        leg_a_fx: (pair.leg_a || {}).fx || '',
        leg_a_factor: (pair.leg_a || {}).price_factor,
        clip_lots_a: pair.clip_lots_a || 1,
        clip_lots_b: pair.clip_lots_b || 1,
        leg_a_lotqty: (pair.leg_a || {}).lot_quantity !== undefined
          ? (pair.leg_a || {}).lot_quantity : pair.contract_size_a,
        leg_a_lotunit: (pair.leg_a || {}).lot_unit || '',
        leg_b_lotqty: (pair.leg_b || {}).lot_quantity !== undefined
          ? (pair.leg_b || {}).lot_quantity : pair.contract_size_b,
        leg_b_lotunit: (pair.leg_b || {}).lot_unit || '',
        leg_a_unit: (pair.leg_a || {}).price_unit ||
          ((pair.leg_a || {}).price_factor ? 'custom' : ''),
        leg_b_unit: (pair.leg_b || {}).price_unit ||
          ((pair.leg_a || {}).price_factor ? 'custom' : ''),
        expiry: pair.expiry || '',
      };
      local.derived = null;
      local.symbols = {};
      return render(false);
    }
    if (button.classList.contains('delete-pair')) {
      var doomed = row.dataset.pair;
      return UI.ask('Delete pair ' + doomed + '?',
        'A pair with an open position refuses this — flatten it first.',
        'Delete', function () {
          api('/api/pairs/' + encodeURIComponent(doomed),
              {method: 'DELETE'}).then(afterWrite);
        });
    }
    if (button.classList.contains('cancel-pair')) {
      local.editing = null;
      return render();
    }
    if (button.classList.contains('find-symbol')) {
      return findSymbols(button.dataset.side);
    }
    if (button.classList.contains('pick-symbol')) {
      readDraft();
      local.draft['leg_' + button.dataset.side + '_symbol'] =
        button.dataset.symbol;
      local.symbols[button.dataset.side] = null;
      return render(false);
    }
    if (button.classList.contains('use-beta')) {
      readDraft();
      local.draft.hedge_ratio = parseFloat(button.dataset.value);
      return render(false);
    }
    if (button.classList.contains('use-increment')) {
      readDraft();
      local.draft.increment = parseFloat(button.dataset.value);
      return render(false);
    }
    if (button.classList.contains('derive-pair')) { return derive(); }
    if (button.classList.contains('save-pair')) { return savePair(); }
  }

  function runCheck(name, kind, button) {
    var label = button.textContent;
    button.textContent = '…';
    button.disabled = true;
    return api('/api/accounts/' + encodeURIComponent(name) + '/' + kind)
      .then(function (result) {
        var body = result.body || {};
        body.ran = kind.charAt(0).toUpperCase() + kind.slice(1) + ': ';
        local.tests[name] = body;
        button.textContent = label;
        button.disabled = false;
        render(false);
        refreshConnection();
      })
      .catch(function (error) {
        button.textContent = label;
        button.disabled = false;
        UI.toast(kind + ' could not run: ' + error.message);
      });
  }

  function refreshConnection() {
    return api('/api/connection').then(function (result) {
      var was = local.connection && local.connection.connected;
      // A real answer always carries `blockers` (possibly empty). Any
      // body without it is api()'s failure shape — render THAT as the
      // reason, rather than an empty banner that reads like a
      // considered "not ready".
      local.connection = result.body && result.body.blockers
        ? result.body
        : {connected: false,
           summary: (result.body && result.body.error) ||
             'the connection check did not answer',
           blockers: [(result.body && result.body.error) ||
             'the connection check did not answer']};
      // Say it ONCE when it becomes true, rather than every poll: a
      // banner that never changes is a banner nobody reads. The words
      // are the SERVER's — a second copy here drifts from it, and this
      // one still said "both accounts" with one account configured.
      if (local.connection.connected && was === false) {
        UI.toast(local.connection.summary, 'ok');
      }
      render(false);
    });
  }

  function saveSettings() {
    var panel = document.querySelector('.window.settings .trading');
    function number(selector) {
      return parseFloat(panel.querySelector(selector).value);
    }
    var fields = {
      CONFIRM_MARKET_CLICKS: panel.querySelector('.s-confirm').checked,
      CLICK_CONVENTION: panel.querySelector('.s-click').value,
      AUTO_LOCK_MINUTES: panel.querySelector('.s-autolock').value,
      CLOSE_FIRST: panel.querySelector('.s-closefirst').checked,
      MARKET_PROTECTION_TICKS: number('.s-protection'),
      ROW_HEIGHT_PX: number('.s-rowheight'),
      COMMAND_POLL_SEC: number('.s-drain'),
      REPEG_DEAD_BAND_TICKS: number('.s-repeg'),
      MAX_QUOTE_AGE_SEC: number('.s-stale')
    };
    return api('/api/settings',
               {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({fields: fields})})
      .then(function (result) {
        if (!result.ok) { UI.toast(result.body.error); return; }
        var cold = result.body.restart_required || [];
        UI.toast(cold.length
          ? 'applied — except ' + cold.join(', ') + ', which the launcher ' +
            'only reads at startup'
          : 'applied to the running engine', 'ok');
        refresh();
      });
  }

  function saveAccount(row) {
    var name = row.dataset.account ||
      (row.querySelector('.f-name') || {}).value;
    if (!name) {
      UI.toast('an account needs a name before it can be saved');
      return;
    }
    function read(cls) {
      var input = row.querySelector('.' + cls);
      return input ? input.value : '';
    }
    var kind = read('f-kind') || 'mt5';
    var body = {
      kind: kind,
      terminal_path: kind === 'mt5' ? read('f-terminal') : '',
      login: kind === 'mt5' ? read('f-login') : '',
      server: kind === 'mt5' ? read('f-server') : '',
      endpoint: read('f-endpoint'),
      password: read('f-password')
    };
    if (kind === 'arrow') {
      body.app_id = read('f-appid');
      body.user_id = read('f-userid');
      body.exchange = read('f-exchange') || 'MCX';
      body.api_secret = read('f-apisecret');
      body.totp_secret = read('f-totp');
    }
    return api('/api/accounts/' + encodeURIComponent(name),
               {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)}).then(function (result) {
      if (!result.ok) {
        // The refusal's own words — it names the field and, where it
        // can, the value to use instead.
        UI.toast(result.body.error);
        return;
      }
      UI.toast('saved ' + name + ' — restart the launcher for it to take ' +
               'effect', 'ok');
      // This row has BECOME an account. Clear it, or the draft the
      // repaint carries across would reappear in the next blank row.
      if (!row.dataset.account) {
        NEW_ACCOUNT_FIELDS.forEach(function (field) {
          var input = row.querySelector('.' + field);
          if (input) { input.value = ''; }
        });
      }
      refresh();
    });
  }

  function readDraft() {
    var form = document.querySelector('.pair-form');
    if (!form) { return; }
    function value(selector) {
      var input = form.querySelector(selector);
      return input ? input.value : '';
    }
    function checked(selector, fallback) {
      var input = form.querySelector(selector);
      return input ? input.checked : fallback;
    }
    local.draft = {
      key: value('.p-key') || local.draft.key,
      name: value('.p-name'),
      pair_type: value('.p-type'),
      hedge_ratio: parseFloat(value('.p-beta')) || local.draft.hedge_ratio,
      increment: value('.p-increment') === ''
        ? null : parseFloat(value('.p-increment')),
      default_quantity: parseFloat(value('.p-quantity')) || 1.0,
      quoting_leg: value('.p-quoting'),
      // Never a hard read: this runs on every repaint, and one missing
      // control must not take the whole form down with it.
      enabled: checked('.p-enabled', local.draft.enabled !== false),
      leg_a_account: value('.p-account-a'),
      leg_a_symbol: value('.p-symbol-a'),
      leg_b_account: value('.p-account-b'),
      leg_b_symbol: value('.p-symbol-b'),
      leg_a_fx: value('.p-fx-a').toUpperCase(),
      leg_a_unit: value('.p-unit-a'),
      leg_b_unit: value('.p-unit-b'),
      clip_lots_a: parseFloat(value('.p-clip-a')) || local.draft.clip_lots_a,
      clip_lots_b: parseFloat(value('.p-clip-b')) || local.draft.clip_lots_b,
      leg_a_lotqty: value('.p-lotqty-a') === ''
        ? null : parseFloat(value('.p-lotqty-a')),
      leg_a_lotunit: value('.p-lotunit-a'),
      leg_b_lotqty: value('.p-lotqty-b') === ''
        ? null : parseFloat(value('.p-lotqty-b')),
      leg_b_lotunit: value('.p-lotunit-b'),
      leg_a_factor: value('.p-factor-a') === ''
        ? null : parseFloat(value('.p-factor-a')),
      expiry: value('.p-expiry') || null
    };
  }

  function findSymbols(side) {
    readDraft();
    var account = local.draft['leg_' + side + '_account'];
    if (!account) {
      UI.toast('choose the account for leg ' + side.toUpperCase() +
               ' first — symbols are per broker');
      return;
    }
    var query = local.draft['leg_' + side + '_symbol'] || '';
    return api('/api/accounts/' + encodeURIComponent(account) +
               '/symbols?q=' + encodeURIComponent(query))
      .then(function (result) {
        local.symbols[side] = result.ok
          ? {symbols: result.body.symbols || []}
          : {error: result.body.error};
        render();
      });
  }

  function draftPayload() {
    var draft = local.draft;
    return {
      name: draft.name || draft.key,
      // FX and unit factor ride on leg A. Blank is sent as '' so the
      // server CLEARS them rather than keeping a conversion the form no
      // longer shows.
      leg_a: {account: draft.leg_a_account, symbol: draft.leg_a_symbol,
              fx: draft.leg_a_fx || '',
              price_unit: draft.leg_a_unit || '',
              lot_quantity: draft.leg_a_lotqty === null ||
                draft.leg_a_lotqty === undefined ||
                isNaN(draft.leg_a_lotqty) ? '' : draft.leg_a_lotqty,
              lot_unit: draft.leg_a_lotunit || '',
              price_factor: (draft.leg_a_factor === null ||
                             draft.leg_a_factor === undefined ||
                             isNaN(draft.leg_a_factor))
                ? '' : draft.leg_a_factor},
      leg_b: {account: draft.leg_b_account, symbol: draft.leg_b_symbol,
              price_unit: draft.leg_b_unit || '',
              lot_quantity: draft.leg_b_lotqty === null ||
                draft.leg_b_lotqty === undefined ||
                isNaN(draft.leg_b_lotqty) ? '' : draft.leg_b_lotqty,
              lot_unit: draft.leg_b_lotunit || ''},
      pair_type: draft.pair_type,
      hedge_ratio: draft.hedge_ratio,
      clip_lots_a: draft.clip_lots_a || 1,
      clip_lots_b: draft.clip_lots_b || 1,
      increment: draft.increment,
      default_quantity: draft.default_quantity,
      quoting_leg: draft.quoting_leg || null,
      enabled: draft.enabled,
      expiry: draft.expiry || null
    };
  }

  function derive() {
    readDraft();
    var key = local.editing || cleanKey(local.draft.key) || autoKey();
    if (!key) {
      UI.toast('give leg A and leg B a symbol first — the key is built '
               + 'from them');
      return;
    }
    return api('/api/pairs/' + encodeURIComponent(key) + '/derive',
               {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(draftPayload())})
      .then(function (result) {
        if (!result.ok) { UI.toast(result.body.error); return; }
        local.derived = result.body;
        render();
      });
  }

  function cleanKey(key) {
    /* A pair's key is an IDENTIFIER, and it is matched exactly — by the
     * snapshot, by every panel, and in the URL of every call about this
     * pair. Typed by hand it arrives with the spaces a person puts
     * round a separator, and `XAUUSD.f | GCZ6.f` is then a different
     * pair from the `XAUUSD.f|GCZ6.f` everything else writes.
     *
     * So it is tidied here rather than refused: same two symbols, one
     * spelling. */
    return String(key || '').trim().replace(/\s*\|\s*/g, '|');
  }

  function autoKey() {
    /* A pair's key IS its two symbols. Deriving it means the operator
     * never has to know that, and a key can never drift away from the
     * instruments it names. */
    var draft = local.draft || {};
    if (!draft.leg_a_symbol || !draft.leg_b_symbol) { return ''; }
    return draft.leg_a_symbol + '|' + draft.leg_b_symbol;
  }

  function openOn(key) {
    var live = ((UI.state.snapshot || {}).pairs || {})[key] || {};
    return (live.positions || []).length;
  }

  function savePair() {
    readDraft();
    var payload = draftPayload();
    if (!payload.leg_a.symbol || !payload.leg_b.symbol) {
      UI.toast('both legs need a symbol — Find lists what each account '
               + 'actually offers');
      return;
    }
    var key = local.editing || cleanKey(local.draft.key) || autoKey();
    // The symbols were changed on an existing pair: the key names the
    // instruments, so it moves with them rather than being left
    // pointing at something that is no longer traded here.
    var rename = (local.editing && autoKey() && autoKey() !== local.editing)
      ? autoKey() : null;
    if (rename && openOn(local.editing)) {
      UI.toast('this pair has an open position — flatten it before ' +
               'changing its symbols; the position is on the OLD ' +
               'instruments and nothing here can move it');
      return;
    }
    var target = rename || key;
    return api('/api/pairs/' + encodeURIComponent(target),
               {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)})
      .then(function (result) {
        if (!result.ok) { UI.toast(result.body.error); return; }
        if (!rename) { return result; }
        return api('/api/pairs/' + encodeURIComponent(local.editing),
                   {method: 'DELETE'}).then(function (dropped) {
          if (!dropped.ok) {
            UI.toast('saved as ' + target + ', but the old ' +
                     local.editing + ' could not be removed: ' +
                     dropped.body.error);
          }
          return result;
        });
      })
      .then(function (result) {
        if (!result) { return; }
        UI.toast(result.body.restart_required
          ? 'saved ' + target + ' — symbols, accounts and β are structural, ' +
            'so the engine restarts itself to pick them up'
          : 'saved ' + target, 'ok');
        local.editing = null;
        refresh();
      });
  }

  function afterWrite(result) {
    if (!result.ok) { UI.toast(result.body.error); return; }
    refresh();
  }

  window.MT5Settings = {render: function () { node(); render(true); },
                        refresh: refresh, state: local};
})();
