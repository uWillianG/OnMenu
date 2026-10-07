/* Auditoria local via Chrome DevTools. Requer servidor_ui.py em 127.0.0.1:8765
 * e Chrome headless com remote-debugging-port=9229, perfil descartavel.
 * Nenhuma chamada de pagamento real e nenhuma dependencia npm adicional.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const base = 'http://127.0.0.1:8765';
const output = path.join(__dirname, 'imagens-correcoes');
fs.mkdirSync(output, { recursive: true });

(async () => {
  const target = await fetch('http://127.0.0.1:9229/json/new?about:blank', { method: 'PUT' }).then(r => r.json());
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  const listeners = new Map();
  const errors = [];
  const screens = [];
  let inlineScripts = 0;
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const handler = pending.get(message.id);
      if (handler) {
        clearTimeout(handler.timer);
        pending.delete(message.id);
        message.error ? handler.reject(new Error(message.error.message)) : handler.resolve(message.result);
      }
    } else {
      if (message.method === 'Runtime.exceptionThrown') {
        errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
      }
      for (const handler of listeners.get(message.method) || []) handler(message.params);
    }
  };
  function rpc(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Timeout: ' + method)); }, 20000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await rpc('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  async function waitFor(expression) {
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    throw new Error('Condicao nao atingida: ' + expression);
  }
  async function navigate(url) {
    await rpc('Page.navigate', { url: base + url });
    await waitFor(`location.pathname === ${JSON.stringify(url.split('?')[0])} && document.readyState === 'complete'`);
    const source = await evaluate("Array.from(document.scripts).filter(s => !s.src && (!s.type || s.type === 'text/javascript')).map(s => s.textContent)");
    for (const script of source) { new vm.Script(script); inlineScripts++; }
  }
  async function screenshot(name) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const picture = await rpc('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(path.join(output, name + '.png'), Buffer.from(picture.data, 'base64'));
    screens.push(await evaluate(`({nome:${JSON.stringify(name)}, titulo:document.title,
      largura:innerWidth, larguraDocumento:document.documentElement.scrollWidth,
      url:location.pathname})`));
  }
  async function fillCheckout(method) {
    await evaluate(`(() => {
      document.querySelector('[name=fulfillment_method][value=pickup]').click();
      document.querySelector('[name=customer_name]').value='Cliente Ficticio';
      document.querySelector('[name=phone]').value='(11) 99999-8888';
      document.querySelector('[name=payment_method][value=${method}]').click();
    })()`);
  }
  async function addViaFetch() {
    await evaluate(`fetch('/cart/add/1/', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({quantity:'1', option_group_1:'1', csrfmiddlewaretoken:(document.querySelector('[name=csrfmiddlewaretoken]')?.value || document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('csrftoken='))?.split('=')[1] || '')})}).then(r=>r.text()).then(()=>true)`);
  }

  await rpc('Page.enable');
  await rpc('Runtime.enable');
  await rpc('Network.enable');
  await rpc('Network.clearBrowserCookies');
  await rpc('Network.setBlockedURLs', { urls: ['*sdk.mercadopago.com*', '*cdnjs.cloudflare.com*'] });
  await rpc('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate('/');
  await screenshot('cardapio-mobile');
  await evaluate("document.querySelector('[data-open-modal]').click()");
  await waitFor("!document.getElementById('item-modal').hidden");
  await screenshot('produto-mobile');
  await evaluate("document.getElementById('modal-form').requestSubmit()");
  await waitFor("document.readyState === 'complete' && document.querySelector('.cart-btn-count')?.textContent.trim() === '1'");
  await navigate('/cart/');
  await screenshot('carrinho-mobile');
  await navigate('/orders/checkout/');
  await fillCheckout('credit_card');
  await evaluate(`(() => {
    document.querySelector('[name=fulfillment_method][value=delivery]').click();
    const city=document.getElementById('id_city'); city.value='1'; city.dispatchEvent(new Event('change'));
    const neighborhood=document.getElementById('id_neighborhood'); neighborhood.value='1'; neighborhood.dispatchEvent(new Event('change'));
    document.querySelector('[name=address_street]').value='Rua Ficticia';
    document.querySelector('[name=address_number]').value='10';
  })()`);
  if (await evaluate("document.getElementById('expected-total').value") !== '25.00') throw new Error('Taxa nao atualizou o total aprovado pelo cliente');
  await evaluate(`(() => {
    for (const [id,value] of Object.entries({'card-number':'4000 0000 0000 0000','card-holder':'Cliente Ficticio',
      'card-expiry':'12/30','card-cvv':'123','card-cpf':'39053344705'})) document.getElementById(id).value=value;
    document.getElementById('checkout-form-main').requestSubmit();
  })()`);
  await waitFor("!document.querySelector('[data-card-state=rejected]').hidden");
  await screenshot('cartao-recusado-mobile');
  await evaluate(`document.getElementById('card-retry-btn').click();
    document.getElementById('card-number').value='4111 1111 1111 1111';
    document.getElementById('checkout-form-main').requestSubmit();`);
  await waitFor("location.pathname.startsWith('/orders/confirmation/') && document.readyState === 'complete'");
  await screenshot('cartao-retentativa-aprovada-mobile');
  const retryFixed = await evaluate("document.body.textContent.includes('Pago')");
  if (!retryFixed) throw new Error('Retentativa do cartao nao foi aprovada');
  const paidOrder = await evaluate("location.pathname.split('/').filter(Boolean).at(-1)");

  await addViaFetch();
  await navigate('/orders/checkout/');
  await fillCheckout('pix');
  await evaluate("document.getElementById('id_customer_cpf').value='39053344705'; document.getElementById('id_customer_email').value='teste@example.invalid'; document.getElementById('checkout-form-main').requestSubmit()");
  await waitFor("!document.querySelector('[data-pix-state=waiting]').hidden");
  await screenshot('pix-mobile');
  const pixOrder = await evaluate("document.getElementById('pix-order-number').textContent");
  await navigate('/orders/confirmation/' + pixOrder + '/');
  const pixResumeAvailable = await evaluate("Array.from(document.querySelectorAll('a')).some(el => /Continuar pagamento/i.test(el.textContent))");
  if (!pixResumeAvailable) throw new Error('Pix nao oferece retomada');
  await screenshot('pix-confirmacao-com-retomada-mobile');
  await navigate('/orders/' + pixOrder + '/payment/');
  await screenshot('retomar-pix-mobile');

  await addViaFetch();
  await navigate('/orders/checkout/');
  await evaluate(`window.MercadoPago = function(){return {bricks:function(){return {create:function(type,id,settings){
    if(type==='cardPayment') window.__auditCardSubmit=settings.callbacks.onSubmit;
    if(type==='statusScreen') window.__auditChallenge=settings.initialization;
    return Promise.resolve({unmount:function(){}});
  }}}}};`);
  await fillCheckout('credit_card');
  await evaluate(`window.__auditCardSubmit({token:'MOCK-3DS',installments:1,payment_method_id:'visa',payer:{email:'teste@example.invalid',identification:{number:'39053344705'}}})`);
  await waitFor("Boolean(window.__auditChallenge)");
  const challenge = await evaluate("window.__auditChallenge");
  if(challenge.additionalInfo.creq !== 'TEST-CREQ' || !challenge.paymentId) throw new Error('Dados do desafio 3DS nao foram enviados ao Status Screen Brick');
  await screenshot('desafio-3ds-sdk-simulado-mobile');

  await rpc('Network.setCookie', { name: 'sessionid', value: fs.readFileSync(path.join(root, '.audit-tmp/browser-session.txt'), 'utf8'), url: base, httpOnly: true });
  await navigate('/staff/orders/' + paidOrder + '/');
  await evaluate("document.querySelector('section.detail-card').scrollIntoView({block:'center'})");
  await screenshot('pagamento-e-estorno-mobile');
  const refundActionAvailable = await evaluate("Boolean(document.querySelector('form[action$=\"/refund/\"] input[name=confirm_refund]'))");
  if (!refundActionAvailable) throw new Error('Pedido pago nao apresenta confirmacao de estorno no painel');
  await rpc('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await navigate('/staff/orders/' + paidOrder + '/');
  await evaluate("document.querySelector('section.detail-card').scrollIntoView({block:'center'})");
  await screenshot('pagamento-e-estorno-desktop');
  await evaluate("document.querySelector('form[action$=\"/refund/\"] input[name=confirm_refund]').checked=true; document.querySelector('form[action$=\"/refund/\"]').requestSubmit()");
  await waitFor("document.readyState === 'complete' && document.body.textContent.includes('Estorno solicitado e registrado.')");
  await evaluate("document.querySelector('section.detail-card').scrollIntoView({block:'center'})");
  await screenshot('pagamento-estornado-desktop');
  const refundConfirmed = await evaluate("document.body.textContent.includes('Estornado')");
  if (!refundConfirmed) throw new Error('O estorno simulado nao atualizou o painel');
  const refundedOrderClosed = await evaluate("document.querySelector('select[name=status]').value === 'cancelled'");
  if (!refundedOrderClosed) throw new Error('O estorno deixou o pedido na fila de preparo');
  await navigate('/staff/orders/' + pixOrder + '/');
  await evaluate("document.querySelector('select[name=status]').value='cancelled'; document.querySelector('select[name=status]').form.requestSubmit()");
  await waitFor("location.search.includes('updated=1') && document.readyState === 'complete'");
  const pendingPixCancelled = await evaluate("document.querySelector('select[name=status]').value === 'cancelled'");
  if (!pendingPixCancelled) throw new Error('O Pix pendente nao pode ser cancelado pelo painel');
  await evaluate("document.querySelector('section.detail-card').scrollIntoView({block:'center'})");
  await screenshot('pix-cancelado-pelo-painel-desktop');
  for (const [url,name] of [
    ['/', 'cardapio-desktop'], ['/informacoes/', 'informacoes-desktop'],
    ['/staff/orders/', 'painel-desktop'], ['/staff/relatorios/', 'relatorios-desktop'],
    ['/staff/cardapio/', 'gestao-desktop'], ['/staff/horarios/', 'horarios-desktop'],
    ['/accounts/perfil/', 'perfil-desktop'], ['/accounts/meus-pedidos/', 'historico-desktop'],
    ['/privacidade/', 'privacidade-desktop'], ['/termos/', 'termos-desktop'],
    ['/informacoes/editar/', 'configuracao-desktop'], ['/staff/entrega/', 'regioes-desktop'],
    ['/staff/lancamento/', 'lancamento-desktop'], ['/accounts/staff/privacidade/', 'solicitacoes-desktop'],
  ]) { await navigate(url); await screenshot(name); }

  let staticScripts = 0;
  for (const name of fs.readdirSync(path.join(root, 'static/js')).filter(n => n.endsWith('.js'))) {
    new vm.Script(fs.readFileSync(path.join(root, 'static/js', name), 'utf8'), { filename: name });
    staticScripts++;
  }
  const report = { navegador:'Chrome headless', pagamentos:'mock', redeExternaBloqueadaParaSDK:true,
    historicoEAcaoDeEstornoDisponiveis:refundActionAvailable, estornoConfirmadoNoPainel:refundConfirmed,
    estornoEncerraPedido:refundedOrderClosed, pixPendenteCanceladoNoPainel:pendingPixCancelled,
    cartaoRetentativaCorrigida:retryFixed, pixRetomadaDisponivel:pixResumeAvailable,
    desafio3dsComDadosCorretos:true, sdk3dsSimulado:true, scriptsEstaticosComSintaxeValida:staticScripts, scriptsInlineComSintaxeValida:inlineScripts,
    capturas:screens, excecoesJavaScript:errors };
  fs.writeFileSync(path.join(__dirname, 'navegador-correcoes.json'), JSON.stringify(report, null, 2));
  if (errors.length) throw new Error('Erros JavaScript: ' + errors.join('; '));
  if (screens.some(screen=>screen.larguraDocumento > screen.largura)) throw new Error('Rolagem horizontal encontrada');
  console.log(JSON.stringify(report, null, 2));
  await rpc('Page.close');
  socket.close();
})().catch(error => { console.error(error.stack); process.exit(1); });
