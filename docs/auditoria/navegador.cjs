/* Auditoria local via Chrome DevTools. Requer servidor_ui.py em 127.0.0.1:8765
 * e Chrome headless com remote-debugging-port=9229, perfil descartavel.
 * Nenhuma chamada de pagamento real e nenhuma dependencia npm adicional.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const base = 'http://127.0.0.1:8765';
const output = path.join(__dirname, 'imagens');
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
      body:new URLSearchParams({quantity:'1', option_group_1:'1', csrfmiddlewaretoken:document.querySelector('[name=csrfmiddlewaretoken]').value})}).then(r=>r.text()).then(()=>true)`);
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
    for (const [id,value] of Object.entries({'card-number':'4000 0000 0000 0000','card-holder':'Cliente Ficticio',
      'card-expiry':'12/30','card-cvv':'123','card-cpf':'11111111111'})) document.getElementById(id).value=value;
    document.getElementById('checkout-form-main').requestSubmit();
  })()`);
  await waitFor("!document.querySelector('[data-card-state=rejected]').hidden");
  await screenshot('cartao-recusado-mobile');
  await evaluate(`document.getElementById('card-retry-btn').click();
    document.getElementById('card-number').value='4111 1111 1111 1111';
    document.getElementById('checkout-form-main').requestSubmit();`);
  await waitFor("!document.querySelector('[data-card-state=error]').hidden");
  await screenshot('cartao-retentativa-erro-mobile');
  const retryBroken = await evaluate("!document.querySelector('[data-card-state=error]').hidden");

  await addViaFetch();
  await navigate('/orders/checkout/');
  await fillCheckout('pix');
  await evaluate("document.getElementById('id_customer_cpf').value='11111111111'; document.getElementById('checkout-form-main').requestSubmit()");
  await waitFor("!document.querySelector('[data-pix-state=waiting]').hidden");
  await screenshot('pix-mobile');
  const pixOrder = await evaluate("document.getElementById('pix-order-number').textContent");
  await navigate('/orders/confirmation/' + pixOrder + '/');
  const pixResumeMissing = await evaluate("!document.querySelector('#pix-qr-img') && !Array.from(document.querySelectorAll('a,button')).some(el => /pagar|gerar.*pix/i.test(el.textContent))");
  await screenshot('pix-confirmacao-sem-retomada-mobile');

  await rpc('Network.setCookie', { name: 'sessionid', value: fs.readFileSync(path.join(root, '.audit-tmp/browser-session.txt'), 'utf8'), url: base, httpOnly: true });
  await rpc('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  for (const [url,name] of [
    ['/', 'cardapio-desktop'], ['/informacoes/', 'informacoes-desktop'],
    ['/staff/orders/', 'painel-desktop'], ['/staff/relatorios/', 'relatorios-desktop'],
    ['/staff/cardapio/', 'gestao-desktop'], ['/staff/horarios/', 'horarios-desktop'],
    ['/accounts/perfil/', 'perfil-desktop'], ['/accounts/meus-pedidos/', 'historico-desktop'],
  ]) { await navigate(url); await screenshot(name); }

  let staticScripts = 0;
  for (const name of fs.readdirSync(path.join(root, 'static/js')).filter(n => n.endsWith('.js'))) {
    new vm.Script(fs.readFileSync(path.join(root, 'static/js', name), 'utf8'), { filename: name });
    staticScripts++;
  }
  const report = { navegador:'Chrome headless', pagamentos:'mock', redeExternaBloqueadaParaSDK:true,
    cartaoRetentativaErro:retryBroken, pixConfirmacaoSemRetomada:pixResumeMissing,
    scriptsEstaticosComSintaxeValida:staticScripts, scriptsInlineComSintaxeValida:inlineScripts,
    capturas:screens, excecoesJavaScript:errors };
  fs.writeFileSync(path.join(__dirname, 'navegador.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await rpc('Page.close');
  socket.close();
})().catch(error => { console.error(error.stack); process.exit(1); });
