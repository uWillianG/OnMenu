(function () {
  'use strict';

  // ── Mostrar / ocultar senha (ícone de olho) ──────────────────
  document.querySelectorAll('.password-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = document.getElementById(btn.getAttribute('data-toggle-for'));
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.classList.toggle('is-visible', show);
      btn.setAttribute('aria-pressed', show ? 'true' : 'false');
      btn.setAttribute('aria-label', show ? 'Ocultar senha' : 'Mostrar senha');
    });
  });

  // ── Validação da senha em tempo real ─────────────────────────
  var pwd = document.getElementById('id_password1');
  var reqs = document.getElementById('password-reqs');
  if (!pwd || !reqs) return;

  var rules = {
    length: function (v) { return v.length >= 8; },
    upper: function (v) { return /[A-Z]/.test(v); },
    lower: function (v) { return /[a-z]/.test(v); },
    number: function (v) { return /\d/.test(v); },
    special: function (v) { return /[^A-Za-z0-9]/.test(v); }
  };
  var items = reqs.querySelectorAll('[data-req]');

  function update() {
    var value = pwd.value;
    items.forEach(function (li) {
      var rule = rules[li.getAttribute('data-req')];
      li.classList.toggle('is-valid', !!(rule && rule(value)));
    });
  }

  pwd.addEventListener('input', update);
  update();
})();
