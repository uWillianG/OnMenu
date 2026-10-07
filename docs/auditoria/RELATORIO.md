# Auditoria de preparação para lançamento — OnMenu

Data: 06/10/2026, fuso America/Sao_Paulo. Código analisado: `f743d49`.

Este é o diagnóstico histórico anterior às correções. O andamento e as evidências
do código corrigido estão em [CORRECOES.md](CORRECOES.md).

**Parecer: o sistema tem a estrutura principal implementada, mas ainda não está pronto para um lançamento público com Pix e cartão online.** Há falhas reproduzidas que interrompem pagamentos, permitem pedidos fora das regras do estabelecimento e tornam a fila e os relatórios pouco confiáveis. A prioridade deve ser concluir e proteger os fluxos existentes antes de adicionar funcionalidades comerciais.

O escopo considerado é o declarado no README: **um restaurante ativo por instalação**. Este documento é uma avaliação técnica do repositório e da execução local, não uma homologação das contas dos provedores ou de um servidor de produção.

## O que já está implementado

| Área | Recursos presentes | Situação para lançamento |
|---|---|---|
| Cardápio público | Categorias, destaques, imagens, detalhes e modal de produto | Funciona; conferir disponibilidade e isolamento na entrada e no checkout |
| Carrinho | Quantidades, complementos, observações, edição e preços calculados no servidor | Funciona; faltam revalidação final e limites de quantidade |
| Entrega/retirada | Endereço, cidades/bairros, taxas, pedido mínimo, entrega grátis e horários, inclusive madrugada | Implementado; as modalidades desativadas não são respeitadas no checkout |
| Contas | Cadastro, login por e-mail/usuário, Google, perfil, endereço, recuperação de senha e histórico | Implementado; falta garantir unicidade sob concorrência e fortalecer a validação dos dados |
| Pedidos | Numeração, snapshots de itens/preços, acompanhamento, repetição e acesso restrito ao dono/sessão/equipe | Implementado; faltam regras para pagamento pendente e retomada de pagamentos |
| Operação | Painel, filtros, atualização de status, ações em lote, comandas de caixa/cozinha/entregador | Implementado; pedidos online não pagos entram na fila de trabalho |
| Pagamentos | Pix, cartão, webhooks, consulta de status, sincronização periódica e função de reembolso | Parcialmente concluído: retentativas, concorrência, expiração, 3DS e estornos precisam de correção |
| Gestão | Produtos, categorias, complementos, fotos, logo, informações e horários | Funciona; cadastros duplicados podem gerar erro 500 e alguns ajustes dependem do Django admin |
| Relatórios | Períodos, faturamento, ticket médio, ranking e distribuição | Funciona tecnicamente; inclui pagamentos online pendentes/recusados no faturamento |
| Infraestrutura | Configuração por ambiente, HTTPS, WhiteNoise, migrações, logs, backups SQLite, limites de acesso e CI | Bases presentes; deploy real e integrações ainda precisam de configuração e validação |

## Evidências e validações executadas

- **240 testes existentes passaram**, com SQLite de teste e integrações simuladas. Registro em [suite.txt](suite.txt).
- `manage.py check`: sem erros. `makemigrations --check --dry-run`: sem alterações pendentes. `pip check`: sem incompatibilidades de dependências instaladas.
- **23 verificações adicionais reproduziram os problemas descritos**, com banco inteiramente em memória e dados fictícios. Script: [reproduzir.py](reproduzir.py); resultados: [resultados.json](resultados.json).
- Navegador Chrome headless: fluxo de adicionar produto, carrinho, recusa/retentativa de cartão e abertura de Pix; também cardápio, informações, painel, relatórios, gestão, horários, perfil e histórico. Foram feitas 15 capturas em 390px e 1440px, sem rolagem horizontal nas telas medidas. Os 5 scripts estáticos e 39 ocorrências de scripts inline avaliadas têm sintaxe válida. Registro: [navegador.json](navegador.json).
- O navegador confirmou a falha da retentativa do cartão: o frontend tenta interpretar o HTML do carrinho como JSON e exibe erro de pagamento. Captura: [cartao-retentativa-erro-mobile.png](imagens/cartao-retentativa-erro-mobile.png).
- Também confirmou que a página de confirmação de um Pix pendente não permite voltar ao QR ou retomar o pagamento. Captura: [pix-confirmacao-sem-retomada-mobile.png](imagens/pix-confirmacao-sem-retomada-mobile.png).
- A primeira execução da suíte encontrou erros de permissão na pasta temporária do sandbox. A repetição com `TEMP/TMP/TMPDIR` dentro do projeto passou; esses erros iniciais não foram considerados defeitos do produto.

As reproduções de concorrência intercalam operações de forma controlada. Elas demonstram o caminho vulnerável no código; não representam um teste de carga em múltiplos workers nem cobranças reais no Mercado Pago. O SDK externo foi bloqueado nos cenários de navegador, que utilizaram o modo mock. Não houve envio de mensagens ou e-mails reais, nem alteração de registros do banco existente.

## Correções que impedem o lançamento

P0 indica risco financeiro que precisa ser eliminado antes de receber pagamentos reais. P1 indica uma falha importante no fluxo de compra ou na operação que deve ser resolvida antes do lançamento público.

### A01 — P0: impedir múltiplas cobranças para o mesmo pagamento

`card_pay` verifica o pagamento existente antes de chamar o provedor, sem reserva ou bloqueio persistente da tentativa. A função do SDK gera um UUID diferente a cada chamada. Duas requisições intercaladas para o mesmo pedido podem solicitar dois pagamentos; depois, `update_or_create` conserva apenas um registro de `CardPayment`.

Na reprodução, duas chamadas ao serviço foram aprovadas por um provedor simulado, mas apenas um registro permaneceu. A repetição da mesma chamada ao wrapper também gerou chaves de idempotência diferentes.

**Desenvolver:** uma entidade/tentativa de pagamento persistida, identificador estável por tentativa e proteção de concorrência. Reenvios da mesma tentativa devem consultar/reutilizar seu resultado; uma nova tentativa deve ser criada apenas após um resultado conhecido que permita repetir. Preservar o histórico das cobranças, inclusive resultados incertos por timeout.

**Aceite:** clique duplo, duas abas e timeout seguido de reenvio produzem uma única cobrança para a mesma tentativa. O pedido mantém rastreabilidade de todos os pagamentos.

Evidência: `orders/views/payments.py:220`; `orders/services/mercadopago.py:182`; verificações `cartao_concorrente_duas_cobrancas` e `chaves_idempotencia`. O significado de reutilizar a chave em reenvios é descrito pelo [Mercado Pago](https://www.mercadopago.com.br/developers/pt/news/2023/01/04/Idempotency-key-usage-will-be-mandatory).

### A02 — P1: concluir retentativas e retomada de pagamento

O checkout grava o pedido e limpa o carrinho antes do pagamento. No cartão recusado, o botão “Tentar novamente” volta a chamar a criação do pedido. Como o carrinho já está vazio, recebe um redirecionamento para `/cart/`, seguido de erro de leitura de JSON. O comportamento foi confirmado no navegador.

Depois de criar um Pix, recarregar o checkout também leva ao carrinho vazio. A confirmação mostra “Pendente”, sem QR e sem ação para pagar. Fechar o modal ou perder a aba pode interromper a compra sem uma forma acessível de retomá-la.

**Desenvolver:** uma página/ação para pagar um pedido existente, reutilizada pelo modal, histórico e confirmação. Guardar o identificador do pedido no frontend e encaminhar a retentativa diretamente ao pagamento, sem recriar o checkout. Permitir trocar de método conforme uma regra explícita. Ajustar o tratamento de respostas HTTP e da ausência/falha do SDK.

**Aceite:** recusa seguida de aprovação mantém o mesmo pedido; recarregar, fechar o modal ou voltar pelo histórico permite continuar um pagamento pendente.

Evidência: `orders/views/checkout.py:85`; `templates/orders/checkout.html:917` e `:1007`; `templates/orders/confirmation.html`; verificações `cartao_recusado_retentativa` e `pix_recarregado_sem_retomada`.

### A03 — P1: tratar falhas de criação do Pix sem gerar pedidos duplicados

Quando o serviço Pix falha, o pedido já foi commitado. A resposta 502 não informa seu número. O Django não persiste as alterações da sessão em uma resposta com status >= 500, então o carrinho anterior permanece no navegador, embora tenha sido limpo em memória. Enviar novamente cria outro pedido sem cobrança: foram reproduzidos dois pedidos após duas falhas.

**Desenvolver:** checkout idempotente, estado explícito de pagamento não iniciado/falhou e resposta com identificação/ação de retomada. Reconciliar cobranças cujo resultado ficou incerto. Definir expiração de pedidos abandonados, inclusive aqueles sem `PixPayment`/`CardPayment`, que os jobs atuais não alcançam.

**Aceite:** indisponibilidade do provedor não duplica pedidos e o cliente consegue continuar ou abandonar a compra com um estado claro.

Evidência: `orders/views/checkout.py:75`; verificação `falha_pix_cria_pedidos_duplicados`.

### A04 — P1: separar “aguardando pagamento” da fila da cozinha

O painel seleciona todos os pedidos com status recebido/preparo/entrega, sem considerar pagamento. Assim, Pix pendente e cartão recusado aparecem como “Recebido” na mesma fila de pedidos que podem ser preparados. O cartão resumido nem exibe o pagamento. A assinatura usada pelo polling também não muda quando apenas o pagamento muda.

**Desenvolver:** fila/seção de aguardando pagamento, identificação clara do estado financeiro e bloqueio de avanço operacional para pagamentos online não aprovados. Dinheiro e cartão na entrega devem continuar operáveis pela regra de recebimento presencial. Incluir alterações relevantes do pagamento na atualização do painel e finalizar pedidos abandonados.

**Aceite:** pedido online não aprovado não entra em preparo; uma aprovação move o pedido para a fila e atualiza a tela sem recarregamento manual.

Evidência: `orders/views/staff.py:31` e `:46`; `templates/orders/_staff_order_card.html`; verificação `fila_online_sem_pagamento`.

### A05 — P1: reconciliar Pix antes de considerá-lo expirado e corrigir sua recriação

O polling e `sync_pending_pix` marcam um Pix vencido como expirado antes de consultar o provedor. Um pagamento feito dentro da validade, mas confirmado após atraso do webhook, pode ficar incorretamente expirado. A reprodução configurou o provedor simulado como aprovado e comprovou que nenhuma consulta foi feita.

Na recriação, o wrapper reutiliza o número do pedido como chave de idempotência. **Inferência baseada no contrato da API:** uma operação que deveria criar uma nova cobrança pode retornar a cobrança anterior; isso precisa de homologação. O `update_or_create` também substitui o ID anterior, reduzindo a rastreabilidade. As janelas de 60 minutos para Pix e 48 horas para cartão não resolvem pendências muito antigas.

**Desenvolver:** consulta/reconciliação antes da expiração definitiva, tentativas de cobrança distintas e histórico dos IDs, além de tratamento de pendências após indisponibilidade prolongada. Uma cobrança nova precisa de nova chave; reenvios dessa mesma cobrança precisam da mesma chave.

**Aceite:** aprovação com webhook atrasado é reconhecida, o novo QR corresponde a uma cobrança nova e pagamentos antigos continuam reconciliáveis.

Evidência: `orders/views/payments.py:78` e `:30`; `orders/management/commands/sync_pending_pix.py`; `orders/services/mercadopago.py:110`; verificações `pix_expirado_sem_consulta` e `chaves_idempotencia`.

### A06 — P1: concluir cancelamento, estorno e contestação

A proteção que evita regredir um pedido pago também impede aplicar estorno/contestação ao pedido. Foi reproduzido `CardPayment.status=refunded` junto com `Order.payment_status=paid`. A função de reembolso existe, mas não está conectada à operação do painel. Cancelar o pedido muda somente a situação operacional, sem um fluxo de decisão sobre o valor pago.

Além disso, o endpoint de cartão permite aprovar uma cobrança de um pedido já cancelado. O endpoint de recriação Pix permite gerar Pix para pedido cujo método é dinheiro.

**Desenvolver:** estados financeiros para estorno/contestação, regras de transição, ação autorizada de reembolso e histórico auditável. Validar método, situação do pedido e pagamento existente antes de qualquer nova cobrança. Definir a confirmação da equipe para cancelamentos que exigem reembolso.

**Aceite:** pedido cancelado não aceita nova cobrança; estorno confirmado deixa de aparecer como pago; mudar método não gera pagamentos concorrentes para o mesmo saldo.

Evidência: `orders/services/pedidos.py:18` e `:85`; `orders/services/mercadopago.py:231`; `orders/views/payments.py:106` e `:220`; verificações `estorno_pedido_continua_pago`, `cobranca_pedido_cancelado` e `pix_em_pedido_dinheiro`.

### A07 — P1: implementar corretamente o desafio 3DS

O wrapper descarta `three_ds_info.creq`, conserva apenas a URL externa e o frontend navega diretamente para essa URL. O callback existente não completa, sozinho, a integração de um desafio bancário.

A [documentação atual do Mercado Pago para Checkout Bricks](https://www.mercadopago.com.br/developers/pt/docs/checkout-bricks/how-tos/integrate-3ds) orienta passar o ID do pagamento e os dados do desafio, incluindo `creq`, ao Status Screen Brick; uma implementação própria requer passos adicionais. O código não implementa nenhuma dessas alternativas completas.

**Desenvolver:** preservar os dados do desafio e integrar seu componente/fluxo de conclusão, mantendo a consulta de status e uma forma de retomada.

**Aceite:** cenários de sandbox com desafio aprovado, negado e abandonado retornam ao pedido correto e exibem o estado correto.

Evidência: `orders/services/mercadopago.py:200`; `templates/orders/checkout.html:975`; verificação `3ds_descarta_creq`. O caminho real com o banco ainda precisa de homologação.

### A08 — P1: aplicar as regras de venda no servidor

As verificações reproduziram todos estes casos:

- Entrega e retirada continuam aceitas quando `accepts_delivery` e `accepts_pickup` estão desligados.
- Um carrinho existente ainda finaliza pedido quando o restaurante foi desativado, porque o checkout usa o restaurante do primeiro item como fallback.
- Produto de categoria inativa pode ser adicionado diretamente pelo endpoint público.
- Item de outro restaurante pode ser vendido e associado ao restaurante atual.
- Excluir um complemento obrigatório após adicioná-lo ao carrinho permite finalizar sem esse complemento.

**Desenvolver:** uma validação final comum para adicionar/editar/repetir e finalizar pedidos: restaurante atual ativo, categoria ativa, produto disponível, modalidade habilitada e escolhas atuais válidas. Revalidar o carrinho no momento de confirmar; solicitar correção quando a configuração mudou.

**Aceite:** ocultar/desativar uma venda tem efeito também para carrinhos antigos e requisições diretas. Um pedido nunca mistura produtos de restaurantes diferentes.

Evidência: `cart/views.py`; `cart/cart.py`; `orders/views/checkout.py:34`, `:205` e `:278`; verificações `entrega_retirada_desativadas`, `restaurante_inativo`, `itens_ocultos_e_outro_restaurante` e `complemento_obrigatorio_removido`.

### A09 — P1: proteger produção contra pagamentos simulados e limitações apenas no navegador

Sem access token, o modo mock fica ligado mesmo com `DEBUG=False`. Em uma configuração de produção simulada com HTTPS, host e chave adequados, `check --deploy` apontou somente `security.W021`, mas o pagamento ainda estava em mock e o e-mail no console. No cartão mock, um token comum é aprovado sem cobrança real.

No frontend, a ausência da chave pública **ou** do SDK também ativa o formulário manual de teste. Esse fallback deve depender explicitamente do ambiente, não da falha de carregamento do SDK.

O limite de três tentativas de cartão existe somente no JavaScript. Foram feitas cinco recusas no mesmo pedido, todas aceitas pelo servidor. O endpoint também retorna 500 para parcelas não numéricas.

**Desenvolver:** habilitação explícita de métodos online, checagens de produção que recusem mocks acidentais/credenciais incompletas, erro seguro quando o SDK falhar, controle persistente de tentativas no backend e validação dos campos do pagamento. Os meios não habilitados devem desaparecer do checkout.

**Aceite:** produção não aprova pagamentos fictícios; falha do SDK não mostra formulário de simulação; requisições diretas respeitam o limite de tentativas e entradas inválidas retornam erro de validação.

Evidência: `config/settings.py:365`; `orders/checks.py`; `templates/orders/checkout.html:704`, `:768` e `:787`; `orders/views/payments.py:220`; verificações `cartao_sem_limite_servidor` e `parcelas_invalidas_http500`.

### A10 — P1: validar dados e limitar quantidades

O checkout aceita telefone `abc` e CPF `00000000000`. O cadastro também só confere o tamanho do CPF, sem validar seus dígitos verificadores. Quantidades não têm um teto no servidor: uma quantidade extremamente grande foi aceita no carrinho e produziu erro 500 no checkout.

**Desenvolver:** validação compartilhada de telefone/CPF quando necessários; limites de quantidade, observações, subtotal e total compatíveis com o negócio e os campos do banco. Retornar mensagens úteis sem criar pedidos inválidos.

**Aceite:** entradas inválidas falham antes de criar pedido ou chamar o provedor; quantidades fora do limite recebem erro de validação.

Evidência: `orders/forms.py`; `accounts/forms.py:171`; `cart/views.py`; verificações `checkout_dados_cliente_invalidos` e `quantidade_excessiva_http500`.

## Pendências importantes de operação e produto

| ID / prioridade | O que falta | Evidência / ação concreta |
|---|---|---|
| A11 / P1 para confiar nos números | Relatório com distinção entre pedidos e receita recebida | `get_sales_report` exclui somente cancelados. A reprodução incluiu R$ 20 de Pix pendente no faturamento. Separar receita confirmada, pedidos presenciais ainda a receber, pendências e estornos. Arquivo: `orders/selectors.py:79` |
| A12 / P2 | Tratamento de categoria/produto com slug duplicado | Criar categoria com nome já existente devolve 500, pois a unicidade depende também do restaurante e o slug é criado no save. Validar antes de salvar ou gerar slugs únicos. Caso de categoria reproduzido; o mesmo padrão do produto precisa receber teste de regressão. Arquivos: `menu/models.py`, `menu/views.py:276` e `:310` |
| A13 / P2 | Cadastro transacional e unicidade de identidade | Dois formulários validados antes dos saves criaram contas com o mesmo e-mail; o login por e-mail passou a falhar. Garantir unicidade normalizada no banco, resolução de colisões de username e gravação consistente de User/Profile. Arquivos: `accounts/forms.py:162`, `accounts/models.py`, `accounts/backends.py` |
| A14 / P1 se WhatsApp automático fizer parte do lançamento | Templates, controle de envio e tratamento de falhas do WhatsApp | O serviço envia apenas texto livre e não registra entrega nem faz retry. Para notificações iniciadas pela empresa, implementar templates aprovados e os fluxos de aceitação/recusa necessários. O pedido no site, por si só, não abre uma conversa de atendimento no WhatsApp. Arquivos: `orders/services/whatsapp.py`, `orders/services/notificacoes.py` |
| A15 / P2 | Normalização correta do WhatsApp | Número local com DDD 55 não recebe o DDI brasileiro porque `startswith('55')` é tratado como código do país. Corrigir com base no formato/comprimento, e normalizar os links do estabelecimento de forma consistente. Verificação `whatsapp_ddd55`; `orders/services/whatsapp.py:27` |
| A16 / P1 de preparação de produto | Informação de privacidade e atendimento | Não há página/rota de privacidade, termos ou fluxo/canal específico para solicitações sobre dados pessoais. O sistema coleta CPF, telefone, endereço e histórico. Publicar as informações aplicáveis à operação e definir o atendimento; exclusão/exportação pela própria conta pode ser uma evolução posterior |
| A17 / P2 | Onboarding e autonomia do responsável | Criar/configurar restaurante, habilitar entrega/retirada, cadastrar cidades/bairros/taxas e ajustar alguns dados ainda depende do Django admin. O formulário do painel não cobre nome do estabelecimento nem todas as opções. Documentar o setup assistido do primeiro cliente ou desenvolver um assistente/telas para o responsável |

Sobre templates e mensagens iniciadas pela empresa, a referência usada foi a [política oficial do WhatsApp Business](https://business.whatsapp.com/policy/preview?lang=pt_BR). O envio real, a aprovação dos templates e a configuração da conta Meta não foram validados.

Verificação de e-mail no cadastro/troca, alteração de senha pelo perfil, permissões separadas por função da equipe, pausa temporária de pedidos e exceções de horário/feriados são evoluções úteis. A relevância para o primeiro cliente deve ser definida pelo modelo operacional. Cupons, fidelidade, estoque, avaliações, PWA e integrações com impressoras não são necessários para corrigir os bloqueios encontrados.

## Configuração e validação antes de publicar

Estas pendências não significam, por si só, que falta código: é necessário configurar e confirmar seu funcionamento no ambiente de destino.

| Item | Estado observado / validação necessária |
|---|---|
| Configuração local | `DEBUG=True`, chave de desenvolvimento, BASE_URL em HTTP, Mercado Pago e WhatsApp em mock. SMTP e Google têm configuração presente, mas não foram acionados. Não publicar o ambiente local como está |
| Segurança do deploy | Na configuração local, `check --deploy` produz sete avisos. Na simulação com DEBUG desativado, host/chave/HTTPS adequados, sobra apenas W021 de preload HSTS. Repetir no servidor real, adicionando a validação das integrações de A09 |
| Mercado Pago | Configurar access token, chave pública, secret, URL pública e webhooks. Homologar Pix/cartão com aprovação, recusa, 3DS, timeout, webhook repetido/atrasado, recriação, cancelamento e estorno |
| SMTP | Testar recuperação de senha ponta a ponta, entrega do link no domínio correto, remetente e tratamento de indisponibilidade. Presença de credenciais não comprova entrega |
| Google | Confirmar callback HTTPS autorizado e login/criação/vinculação de contas no domínio final; é opcional para lançamento |
| Dados do restaurante | Confirmar exatamente o restaurante ativo esperado, cardápio real, horários, regiões, taxas e modalidades. Há vários registros de restaurante no banco local; nenhum foi alterado na auditoria |
| Banco e uploads | Confirmar persistência do banco e de `media/`, servir fotos no ambiente de produção e executar migrate/collectstatic. Migrações coerentes no código não comprovam execução no servidor de destino |
| Jobs | Agendar sincronização Pix/cartão, backup e limpeza das tentativas de acesso. Verificar logs de sucesso/falha. Os comandos existem; não foi verificado nenhum agendador externo |
| Backups | Validar restauração, cópia fora do host e inclusão dos uploads. O comando atual cobre somente SQLite; Postgres e mídia precisam de estratégia própria |
| Servidor e proxy | Configurar servidor WSGI, HTTPS, domínio, hosts e origem CSRF. Quando confiar em X-Forwarded-For, o proxy precisa limpar/sobrescrever o cabeçalho recebido; a aplicação usa seu primeiro valor |
| Monitoramento | Confirmar alerta de falhas de pagamento, jobs e erros 500, e um procedimento para recuperar pedidos com resultado financeiro incerto |
| Desempenho | Validar múltiplos pedidos/workers no banco de destino e o polling do painel. A auditoria não mediu capacidade de produção nem contenção real em SQLite/Postgres |

## Sequência recomendada de desenvolvimento e aceite

1. **Pagamentos:** A01–A07 e A09. Entregar tentativa persistente, retomada, estados financeiros, 3DS, reconciliação e proteção contra mocks. Cobrir os cenários da auditoria com testes de regressão que esperem o comportamento correto.
2. **Regras de venda e operação:** A08, A10 e A11. Garantir disponibilidade, modalidade, complementos, dados válidos, limites e separação entre pedidos e receita.
3. **Preparação do primeiro cliente:** corrigir os cadastros críticos de A12/A13; resolver WhatsApp se prometido; publicar informações de privacidade e definir setup/atendimento. Configurar a infraestrutura e testar restauração.
4. **Homologação de ponta a ponta:** compra anônima e autenticada, entrega e retirada, todos os métodos habilitados, retomadas, impressão, acompanhamento, recuperação de senha e operação em celular/desktop. Incluir indisponibilidade do provedor e concorrência.
5. **Piloto:** operar com um restaurante e um grupo pequeno de clientes, acompanhar pagamentos e pedidos e liberar público após cumprir os critérios de aceite.

É possível planejar um piloto com pagamento presencial, desde que os meios online sejam explicitamente desabilitados e os problemas de regras de venda, dados, relatórios e preparação operacional aplicáveis também sejam resolvidos. O estado atual não oferece uma configuração suficiente para apenas “desligar os mocks” e considerar o produto pronto.

Se o objetivo for hospedar restaurantes independentes na mesma instalação, falta um projeto de isolamento por estabelecimento, permissões/vínculos da equipe, credenciais de integração, regiões de entrega e cadastro de cada restaurante. Isso é uma ampliação de escopo: hoje a seleção pública usa o primeiro restaurante ativo.

## Como reproduzir a avaliação

As verificações adicionais caracterizam defeitos existentes. **O exit code 0 do script significa que os defeitos foram reproduzidos, não que o sistema foi aprovado.** Depois de corrigir o produto, substituir essas caracterizações por testes de regressão com expectativas corretas e atualizar os resultados/relatório.

```powershell
.\.venv\Scripts\python docs\auditoria\reproduzir.py --output docs\auditoria\resultados.json
```

Para os testes existentes, usar temporários dentro de uma pasta gravável e serviços externos simulados. O comando da suíte continua sendo:

```powershell
.\.venv\Scripts\python manage.py test --noinput
```

O roteiro de navegador usa um servidor descartável, banco em memória e um perfil próprio do Chrome. Rodar a partir da raiz do projeto; manter o primeiro processo aberto enquanto executar o segundo:

```powershell
.\.venv\Scripts\python -u docs\auditoria\servidor_ui.py
```

```powershell
$auditBrowserProfile = Join-Path (Get-Location) '.audit-tmp\chrome-profile'
$auditBrowserArgs = @('--headless=new', '--disable-gpu', '--no-first-run',
    '--disable-background-networking', '--disable-sync', '--disable-extensions',
    '--remote-debugging-port=9229', ('--user-data-dir="' + $auditBrowserProfile + '"'),
    'about:blank')
$auditBrowserProcess = Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList $auditBrowserArgs -WindowStyle Hidden -PassThru
node docs\auditoria\navegador.cjs
# Encerrar somente o processo iniciado acima quando terminar.
Stop-Process -Id $auditBrowserProcess.Id
```

Encerrar o servidor temporário após a avaliação. Os scripts utilizam apenas `127.0.0.1`; o ambiente desktop pode exigir permissão para acesso aos sockets locais. Ajustar o caminho do Chrome se a instalação for diferente. As capturas e os resultados contêm somente dados fictícios da auditoria.
