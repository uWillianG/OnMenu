# O que preencher para ativar o OnMenu

As correções técnicas estão no projeto. A instalação continua sendo de um restaurante por ambiente. A página **Preparação para lançamento**, em `/staff/lancamento/`, mostra os dados e serviços que ainda precisam ser preenchidos.

## Dados do estabelecimento, no painel

Preencha nome, razão social/responsável, identificação, endereço, telefone e e-mail de atendimento/privacidade em **Configurar estabelecimento**. Cadastre o cardápio, fotos e complementos; os sete dias de funcionamento; as modalidades de entrega/retirada; e cidades, bairros e taxas em **Regiões de entrega**. O botão “Receber pedidos agora” permite pausar novas compras.

O período de conservação dos dados pessoais de pedidos finalizados é definido por `CUSTOMER_DATA_RETENTION_DAYS` (padrão: 365 dias). O responsável deve escolher o período aplicável à sua operação; a rotina implementada anonimiza os dados após esse período, preservando os registros de itens e valores.

## Credenciais, no ambiente do servidor

Copie `.env.example` para `.env` e preencha os campos dos serviços que utilizará. Não publique esse arquivo.

| Serviço | Campos / ação do responsável |
|---|---|
| Endereço público | `SITE_DOMAIN`: apenas o domínio, sem `https://`. Aponte o DNS para o servidor e permita as portas 80/443 |
| Mercado Pago | `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_PUBLIC_KEY` e `MERCADOPAGO_WEBHOOK_SECRET` da mesma integração/ambiente |
| Recebimento bancário | Configure a conta, os dados bancários e a chave Pix **na conta Mercado Pago**. O OnMenu usa as credenciais do provedor, não armazena senha bancária nem recebe número de cartão completo |
| Webhooks Mercado Pago | Cadastre `https://SEU_DOMINIO/webhook/pix/` e `https://SEU_DOMINIO/webhook/cartao/` no painel do provedor |
| E-mail | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` e `DEFAULT_FROM_EMAIL` do seu serviço SMTP |
| Backup externo | `ONMENU_BACKUP_BUCKET`, `ONMENU_BACKUP_ACCESS_KEY`, `ONMENU_BACKUP_SECRET_KEY`; região e endpoint HTTPS se usar um serviço compatível com S3. Mantenha o bucket privado e configure sua conservação/versionamento no provedor |
| Google, opcional | `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET`. Autorize o retorno `https://SEU_DOMINIO/accounts/entrar/google/callback/` |
| WhatsApp, opcional | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_STATUS_TEMPLATE`, `WHATSAPP_TEMPLATE_LANGUAGE`, `WHATSAPP_APP_SECRET` e `WHATSAPP_WEBHOOK_VERIFY_TOKEN` |

Para começar com pagamento presencial, defina `PAYMENT_PIX_ENABLED=False` e `PAYMENT_CARD_ENABLED=False`. Com pagamento online habilitado, a inicialização de produção exige as credenciais correspondentes. Mocks funcionam apenas em desenvolvimento.

Para WhatsApp, aprove no painel Meta um template de utilidade com dois parâmetros de texto no corpo, nesta ordem: **número do pedido** e **situação do pedido**. Exemplo de corpo: `Seu pedido {{1}} agora está: {{2}}.` Preencha o nome aprovado em `WHATSAPP_STATUS_TEMPLATE`. Cadastre `https://SEU_DOMINIO/webhook/whatsapp/` e assine o campo `messages`. O envio ocorre somente se o cliente solicitou os avisos no checkout. Falhas e reenvios podem ser acompanhados em **Avisos de WhatsApp**.

## Instalação e verificação

Com Docker e o `.env` preenchido:

```powershell
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

O Compose inicia o servidor, o proxy HTTPS e a operação automática. Banco SQLite, uploads, backups locais e a chave privada gerada pela instalação ficam no volume persistente `onmenu-data`. A chave é gerada automaticamente se `DJANGO_SECRET_KEY` estiver vazio. Não remova esse volume para atualizar o sistema.

Deixe `DJANGO_SECRET_KEY` vazio ao instalar pelo Compose para usar a geração automática. Uma chave conhecida de desenvolvimento ou considerada fraca pelo Django bloqueia a inicialização de produção. Em outra hospedagem, gere uma chave forte e exclusiva. Não reutilize a chave padrão do ambiente de desenvolvimento.

Os serviços são separados: a operação consulta pagamentos, envia a fila WhatsApp, finaliza pedidos abandonados, limpa registros de tentativas, aplica a conservação de dados e copia banco/uploads. O backup externo será enviado quando suas credenciais estiverem preenchidas. O painel de lançamento mostra falhas/último sucesso das tarefas.

O detalhe de cada pedido mostra o histórico de cobranças e estornos. Para cancelar uma compra online paga, confirme o estorno com uma conta que tenha a permissão correspondente. A ação de estorno também encerra o pedido, retirando-o da fila. O pedido conserva a solicitação para que a operação retome a confirmação se houver uma interrupção.

Um Pix pendente pode ser cancelado pelo detalhe do pedido: o sistema confirma o cancelamento da cobrança no provedor antes de encerrar o pedido. Se a cobrança for aprovada durante essa consulta, a equipe precisa confirmar o estorno. Cobranças com resultado ainda desconhecido aguardam a verificação. Se uma cobrança antiga for aprovada depois do cancelamento, ou além de outro pagamento já confirmado, a operação encaminha seu estorno individual e preserva o pagamento válido, inclusive após notificações repetidas.

Depois de preencher o restaurante, execute:

```powershell
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py check_launch
```

O aviso `security.W021` de preload HSTS é opcional. `check_launch` deve deixar de apontar os campos pendentes. No banco local sem Docker, execute `python manage.py migrate` e inicie `python manage.py run_operations` como serviço separado do servidor de aplicação.

Para restaurar um ZIP, escolha uma pasta vazia:

Pare o servidor e o serviço de operação antes de ativar um banco restaurado. O comando valida o arquivo e prepara a restauração em uma pasta temporária; um erro de validação preserva a pasta de destino. O manifesto fica junto aos arquivos restaurados.

```powershell
python manage.py restore_site CAMINHO_DO_BACKUP.zip --target-dir PASTA_VAZIA
```

O comando restaura SQLite, mídia e a chave persistida sem sobrescrever a instalação atual. Para um backup Postgres, também restaura o arquivo `database.dump`; utilize `pg_restore` no banco de destino escolhido.

No SQLite, a restauração verifica a integridade e libera as tarefas automáticas, apagando o histórico de saúde copiado para exigir uma nova execução. Depois de apontar o banco e a mídia para a pasta restaurada, reinicie os serviços.

No PostgreSQL, depois de executar `pg_restore` e configurar o aplicativo para esse banco, mantenha o serviço de operação parado e execute:

```powershell
python manage.py reset_operations
```

Reinicie os serviços e confira a saúde das tarefas em **Preparação para lançamento**. Essa etapa libera os bloqueios antigos e exige uma execução nova das tarefas no banco de destino.

Se usar PostgreSQL, preencha `POSTGRES_CLIENT_VERSION` com a mesma versão principal do seu servidor (por exemplo, `16` para PostgreSQL 16) antes de construir a imagem. O padrão é `17`; com SQLite, mantenha esse padrão. O backup verifica a correspondência das versões e a registra no manifesto. Use essa versão de `pg_restore` e um servidor de destino da mesma versão para a restauração. O roteiro foi validado em PostgreSQL 16 com cliente 16. A documentação oficial explica que [a restauração com cliente de versão superior ao servidor não é garantida](https://www.postgresql.org/docs/17/app-pgdump.html#APP-PGDUMP-NOTES).

Antes de receber compras reais, use as contas/cartões de teste do Mercado Pago para confirmar aprovação, recusa, Pix, 3DS, webhook e estorno; confirme o recebimento de e-mail e WhatsApp e faça uma restauração do backup do seu ambiente. Isso depende das contas e autorizações do estabelecimento. As validações de desenvolvimento usaram dados fictícios e provedores simulados, sem cobranças ou mensagens reais.

As páginas de privacidade e condições dos pedidos utilizam os dados que você cadastrar. Confira esse conteúdo e o período de conservação para sua operação antes da publicação.
