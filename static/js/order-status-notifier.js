// Notifica o cliente quando o administrador altera a situação do pedido.
// Faz polling do status (apenas nas telas de acompanhamento/confirmação),
// atualiza o texto exibido e mostra um toast a cada mudança. Sem dependências.
//
// Uso: um elemento com os data-attrs abaixo precisa existir na página.
//   <div data-order-notifier
//        data-order-number="OM-…"
//        data-order-status="received"></div>
// Elementos com [data-order-status-text] têm seu texto atualizado ao vivo.
// A página também pode ouvir o evento `order-status-changed` (detail =
// { order_number, status, status_display }).
(function () {
  var root = document.querySelector('[data-order-notifier]');
  if (!root) return;

  var num = root.getAttribute('data-order-number');
  var current = root.getAttribute('data-order-status') || '';
  var POLL_MS = 20000;
  var FINAL = ['delivered', 'cancelled'];

  var container = document.createElement('div');
  container.className = 'order-toasts';
  container.setAttribute('aria-live', 'polite');
  container.setAttribute('aria-atomic', 'false');
  document.body.appendChild(container);

  var ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';

  function dismiss(toast) {
    toast.classList.remove('is-in');
    setTimeout(function () { toast.remove(); }, 320);
  }

  function showToast(statusDisplay, cancelled) {
    var toast = document.createElement('div');
    toast.className = 'order-toast';
    toast.innerHTML =
      '<span class="order-toast-ico' + (cancelled ? ' is-cancel' : '') + '">' + ICON + '</span>' +
      '<span class="order-toast-body">' +
        '<span class="order-toast-title">Pedido ' + num + '</span>' +
        '<span class="order-toast-text">Situação: <strong>' + statusDisplay + '</strong></span>' +
      '</span>' +
      '<button type="button" class="order-toast-close" aria-label="Fechar">✕</button>';
    toast.querySelector('.order-toast-close').addEventListener('click', function () {
      dismiss(toast);
    });
    container.appendChild(toast);
    requestAnimationFrame(function () { toast.classList.add('is-in'); });
    setTimeout(function () { dismiss(toast); }, 9000);
  }

  function applyStatus(status, statusDisplay) {
    if (!status || status === current) return;
    current = status;
    document.querySelectorAll('[data-order-status-text]').forEach(function (el) {
      el.textContent = statusDisplay;
    });
    document.dispatchEvent(new CustomEvent('order-status-changed', {
      detail: { order_number: num, status: status, status_display: statusDisplay },
    }));
    showToast(statusDisplay, status === 'cancelled');
  }

  function poll() {
    fetch('/orders/track/' + num + '/?json=1')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) applyStatus(d.status, d.status_display); })
      .catch(function () {})
      .finally(function () {
        if (FINAL.indexOf(current) === -1) setTimeout(poll, POLL_MS);
      });
  }

  if (FINAL.indexOf(current) === -1) setTimeout(poll, POLL_MS);
})();
