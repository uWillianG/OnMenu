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

  // ── Máscara de telefone BR: (XX) XXXXX-XXXX ──────────────────
  function maskPhone(value) {
    var d = value.replace(/\D/g, '').slice(0, 11);
    if (!d) return '';
    if (d.length < 2) return '(' + d;
    var out = '(' + d.slice(0, 2) + ') ';
    if (d.length <= 6) {
      out += d.slice(2);
    } else if (d.length <= 10) {
      out += d.slice(2, 6) + '-' + d.slice(6);   // fixo: XXXX-XXXX
    } else {
      out += d.slice(2, 7) + '-' + d.slice(7);   // celular: XXXXX-XXXX
    }
    return out;
  }

  document.querySelectorAll('input[data-mask="phone"]').forEach(function (input) {
    function apply() { input.value = maskPhone(input.value); }
    input.addEventListener('input', apply);
    apply();  // formata também valor pré-preenchido (ex.: tela de perfil)
  });

  // ── Máscara de CPF BR: XXX.XXX.XXX-XX ────────────────────────
  function maskCpf(value) {
    var d = value.replace(/\D/g, '').slice(0, 11);
    var out = d.slice(0, 3);
    if (d.length > 3) out += '.' + d.slice(3, 6);
    if (d.length > 6) out += '.' + d.slice(6, 9);
    if (d.length > 9) out += '-' + d.slice(9, 11);
    return out;
  }

  document.querySelectorAll('input[data-mask="cpf"]').forEach(function (input) {
    function apply() { input.value = maskCpf(input.value); }
    input.addEventListener('input', apply);
    apply();
  });

  // ── Validação da senha em tempo real ─────────────────────────
  var pwd = document.getElementById('id_password1') || document.getElementById('id_new_password1');
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
