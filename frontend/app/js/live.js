/* Small shared SSE client. Page callbacks own data fetching and rendering. */
(function (global) {
  const isEditing = () => /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName || '');
  const ACTIONABLE = new Set(['order.submitted', 'quality.sample_submitted', 'payout.proposed', 'dispute.opened']);

  function start({ scope = [], since = -1, onEvent, onState = () => {} }) {
    const scopes = Array.isArray(scope) ? scope : [scope];
    let source, retryTimer, pollTimer, batchTimer, failures = 0, closed = false, lastUpdate = Date.now(), lastSeq = since, connectionState = 'connecting';
    const pending = new Map();
    const setState = state => onState({ state, secondsAgo: Math.max(0, Math.floor((Date.now() - lastUpdate) / 1000)) });
    const ageTimer = setInterval(() => setState(connectionState), 1000);
    const onFocusOut = () => {
      if (pending.size && !batchTimer) batchTimer = setTimeout(publish, 50);
    };
    document.addEventListener('focusout', onFocusOut);

    function publish() {
      batchTimer = null;
      if (!pending.size || closed) return;
      if (isEditing()) return;
      const batch = [...pending.values()];
      pending.clear();
      lastUpdate = Date.now();
      setState(source && source.readyState === EventSource.OPEN ? 'live' : 'polling');
      for (const event of batch) {
        if (event.order_id != null) {
          document.querySelectorAll(`[data-order-id="${Number(event.order_id)}"]`).forEach(node => {
            node.classList.add('just-updated');
            setTimeout(() => node.classList.remove('just-updated'), 1600);
          });
        }
        if (ACTIONABLE.has(event.type)) global.toast?.(event.summary || 'An update needs your attention');
      }
      onEvent(batch);
    }

    function enqueue(event) {
      if (event.type === 'resync') {
        pending.clear();
        pending.set('resync', { type: 'resync', refresh: scopes });
      } else {
        const key = `${event.type}:${event.order_id ?? ''}:${event.seq ?? ''}`;
        pending.set(key, event);
      }
      if (!batchTimer) batchTimer = setTimeout(publish, 300);
    }

    function stopSource() {
      if (source) source.close();
      source = null;
    }
    function startPolling() {
      stopSource();
      if (pollTimer) return;
      setState('polling');
      pollTimer = setInterval(() => {
        if (!isEditing()) enqueue({ type: 'poll', refresh: scopes });
      }, 8000);
    }
    function connect() {
      if (closed) return;
      stopSource();
      const query = `?since=${encodeURIComponent(lastSeq)}`;
      source = new EventSource(`/api/stream${lastSeq == null ? '' : query}`);
      source.addEventListener('change', event => {
        failures = 0;
        try {
          const item = JSON.parse(event.data);
          if (item.type === 'resync') {
            lastSeq = null;
            stopSource();
            enqueue(item);
            retryTimer = setTimeout(connect, 500);
            return;
          }
          if (Number.isInteger(item.seq)) lastSeq = item.seq;
          enqueue(item);
        } catch (_) { enqueue({ type: 'resync', refresh: scopes }); }
      });
      source.onopen = () => {
        failures = 0;
        connectionState = 'live';
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        lastUpdate = Date.now();
        setState('live');
      };
      source.onerror = () => {
        stopSource();
        failures += 1;
        if (failures >= 3) { connectionState = 'polling'; startPolling(); return; }
        connectionState = 'reconnecting';
        setState('reconnecting');
        retryTimer = setTimeout(connect, Math.min(1000 * (2 ** (failures - 1)), 8000));
      };
    }
    connect();
    return {
      refresh: () => enqueue({ type: 'poll', refresh: scopes }),
      close: () => {
        closed = true;
        stopSource();
        clearInterval(ageTimer);
        clearInterval(pollTimer);
        clearTimeout(retryTimer);
        clearTimeout(batchTimer);
        document.removeEventListener('focusout', onFocusOut);
      }
    };
  }
  global.SakhiLive = { start };
})(window);
