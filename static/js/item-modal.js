/* Modal de produto (adicionar ao carrinho) — reutilizado na tela principal e
   no carrinho. Os dados de cada item vêm de divs ocultas #modal-data-<pk>
   (ver templates/menu/_modal_data.html) e os disparadores são qualquer
   elemento com [data-open-modal="<pk>"]. A moeda é lida de #item-modal
   [data-currency]. Seguro em páginas sem modal (sai cedo). */
(function () {
  const modal = document.getElementById('item-modal');
  if (!modal) return;

  const CURRENCY = modal.dataset.currency || 'R$';

  const modalClose  = document.getElementById('modal-close');
  const modalImg    = document.getElementById('modal-img');
  const modalName   = document.getElementById('modal-name');
  const modalDesc   = document.getElementById('modal-desc');
  const modalGroups = document.getElementById('modal-groups');
  const modalForm   = document.getElementById('modal-form');
  const modalQtyIn  = document.getElementById('modal-qty-input');
  const qtyMinus    = document.getElementById('qty-minus');
  const qtyPlus     = document.getElementById('qty-plus');
  const qtyVal      = document.getElementById('qty-val');
  const totalVal    = document.getElementById('modal-total-val');
  const notesField  = document.getElementById('modal-notes');
  const addBtn      = modalForm.querySelector('.modal-add-btn');

  const ADD_LABEL  = 'Adicionar ao carrinho';
  const EDIT_LABEL = 'Salvar alterações';

  let basePrice = 0;
  let qty = 1;

  function fmt(n) {
    return CURRENCY + ' ' + n.toFixed(2).replace('.', ',');
  }

  function calcExtra() {
    let e = 0;
    modalGroups.querySelectorAll('input[type="radio"]:checked').forEach(r => { e += parseFloat(r.dataset.extra || 0); });
    modalGroups.querySelectorAll('input[type="checkbox"]:checked').forEach(c => { e += parseFloat(c.dataset.extra || 0); });
    return e;
  }

  function updateTotal() { totalVal.textContent = fmt((basePrice + calcExtra()) * qty); }

  function setQty(n) {
    qty = Math.max(1, n);
    qtyVal.textContent = qty;
    modalQtyIn.value = qty;
    qtyMinus.disabled = qty <= 1;
    updateTotal();
  }

  // Ícones SVG centralizam melhor que os glifos "+"/"✓" dentro do círculo.
  const ICON_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  function setToggleIcon(el, checked) {
    el.innerHTML = checked ? ICON_CHECK : ICON_PLUS;
  }

  function buildGroup(groupEl) {
    const choices   = groupEl.querySelectorAll('[data-cid]');
    if (!choices.length) return null;
    const required  = groupEl.dataset.req === 'true';
    const single    = groupEl.dataset.selection !== 'multiple';
    const inputType = single ? 'radio' : 'checkbox';
    const wrap = document.createElement('div');
    wrap.className = 'modal-group';
    wrap.dataset.required = required ? '1' : '';
    wrap.dataset.single = single ? '1' : '';
    wrap.dataset.gname = groupEl.dataset.gname;
    const titleRow = document.createElement('div');
    titleRow.className = 'modal-group-title';
    titleRow.textContent = groupEl.dataset.gname;
    if (required) {
      const b = document.createElement('span');
      b.className = 'modal-required-badge';
      b.textContent = 'Obrigatório';
      titleRow.appendChild(b);
    } else {
      const s = document.createElement('span');
      s.className = 'modal-group-subtitle';
      s.textContent = 'Opcional';
      titleRow.appendChild(s);
    }
    wrap.appendChild(titleRow);
    choices.forEach((c, i) => {
      const label = document.createElement('label');
      label.className = 'modal-choice-item';
      const input = document.createElement('input');
      input.type = inputType;
      input.name = 'option_group_' + groupEl.dataset.gid;
      input.value = c.dataset.cid;
      input.dataset.extra = c.dataset.extra;
      // Auto-seleciona a 1ª opção só quando é escolha única obrigatória.
      if (inputType === 'radio' && required && i === 0) input.checked = true;
      const lbl = document.createElement('span');
      lbl.className = 'modal-choice-label';
      lbl.textContent = c.dataset.cname;
      const price = document.createElement('span');
      price.className = 'modal-choice-price';
      const extra = parseFloat(c.dataset.extra || 0);
      price.textContent = extra > 0 ? '+' + fmt(extra) : 'Incluso';
      const toggle = document.createElement('span');
      toggle.className = 'modal-choice-toggle';
      setToggleIcon(toggle, inputType === 'radio' && required && i === 0);
      label.append(input, lbl, price, toggle);
      if (inputType === 'checkbox') {
        label.addEventListener('click', (e) => {
          e.preventDefault();
          input.checked = !input.checked;
          label.classList.toggle('selected', input.checked);
          setToggleIcon(toggle, input.checked);
          updateTotal();
        });
      } else {
        input.addEventListener('change', () => {
          wrap.querySelectorAll('.modal-choice-item').forEach(l => {
            const tog = l.querySelector('.modal-choice-toggle');
            const inp = l.querySelector('input');
            if (tog) setToggleIcon(tog, inp && inp.checked);
          });
          updateTotal();
        });
      }
      wrap.appendChild(label);
    });
    return wrap;
  }

  // Aplica uma seleção existente (modo edição) marcando as escolhas salvas.
  function applySelection(selected) {
    const set = new Set((selected || []).map(String));
    modalGroups.querySelectorAll('.modal-choice-item').forEach((label) => {
      const input = label.querySelector('input');
      const toggle = label.querySelector('.modal-choice-toggle');
      const on = !!input && set.has(String(input.value));
      if (input) input.checked = on;
      label.classList.toggle('selected', on);
      if (toggle) setToggleIcon(toggle, on);
    });
  }

  // edit (opcional): { action, selected: [ids], qty, notes } → abre pré-preenchido.
  function openModal(itemId, edit) {
    const data = document.getElementById('modal-data-' + itemId);
    if (!data) return;
    basePrice = parseFloat(data.dataset.price);
    modalImg.src = data.dataset.image;
    modalImg.alt = data.dataset.name;
    modalName.textContent = data.dataset.name;
    modalDesc.textContent = data.dataset.desc || '';
    modalDesc.style.display = data.dataset.desc ? '' : 'none';
    modalGroups.innerHTML = '';
    data.querySelectorAll('[data-gid]').forEach((g) => {
      const el = buildGroup(g);
      if (el) modalGroups.appendChild(el);
    });

    if (edit) {
      modalForm.action = edit.action;
      if (notesField) notesField.value = edit.notes || '';
      applySelection(edit.selected);
      setQty(edit.qty || 1);
      if (addBtn) addBtn.textContent = EDIT_LABEL;
    } else {
      modalForm.action = data.dataset.url;
      if (notesField) notesField.value = '';
      setQty(1);
      if (addBtn) addBtn.textContent = ADD_LABEL;
    }

    updateTotal();
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  document.querySelectorAll('[data-open-modal]').forEach((card) => {
    card.addEventListener('click', () => openModal(card.dataset.openModal));
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openModal(card.dataset.openModal); }
    });
  });

  // Editar uma linha do carrinho: reabre o modal pré-preenchido e substitui a linha.
  document.querySelectorAll('[data-edit-line]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openModal(btn.dataset.item, {
        action: btn.dataset.action,
        selected: (btn.dataset.selected || '').split(',').filter(Boolean),
        qty: parseInt(btn.dataset.qty, 10) || 1,
        notes: btn.dataset.notes || '',
      });
    });
  });

  modalClose.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) closeModal(); });
  qtyMinus.addEventListener('click', () => setQty(qty - 1));
  qtyPlus.addEventListener('click',  () => setQty(qty + 1));

  // Bloqueia envio se algum grupo obrigatório estiver sem seleção.
  modalForm.addEventListener('submit', (e) => {
    const groups = modalGroups.querySelectorAll('.modal-group[data-required="1"]');
    for (const g of groups) {
      const checked = g.querySelector('input:checked');
      if (!checked) {
        e.preventDefault();
        alert('Escolha uma opção em “' + (g.dataset.gname || 'complemento obrigatório') + '”.');
        g.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return;
      }
    }
  });
})();
