# Correções para disponibilização

Objetivo: resolver as pendências técnicas A01–A17 do relatório, deixando apenas
dados do estabelecimento, credenciais, aprovações das contas externas e escolhas
do ambiente de hospedagem a cargo do responsável.

O relatório e suas evidências iniciais são históricos. A aprovação do lançamento
dependerá dos testes de regressão e das evidências finais, não do sucesso do
script que reproduz os defeitos antigos.

| Item | Entrega | Estado |
|---|---|---|
| A01 | Tentativas persistentes, idempotência e proteção de concorrência | Implementado e validado |
| A02 | Retentativas e página de retomada de pagamento | Implementado e validado |
| A03 | Checkout idempotente e recuperação de falhas Pix | Implementado e validado |
| A04 | Fila de pagamento separada, guardas operacionais e polling | Implementado e validado |
| A05 | Reconciliação e histórico/recriação de cobranças | Implementado e validado |
| A06 | Estados financeiros, cancelamento e reembolso | Implementado e validado |
| A07 | Desafio 3DS e conclusão por consulta/webhook | Implementado e validado |
| A08 | Revalidação de modalidade, restaurante, itens e complementos | Implementado e validado |
| A09 | Métodos habilitados, segurança de mocks e limites no backend | Implementado e validado |
| A10 | CPF/telefone, quantidades e limites financeiros | Implementado e validado |
| A11 | Receita, valores a receber e pendências discriminados | Implementado e validado |
| A12 | Slugs/cadastros duplicados sem erro 500 | Implementado e validado |
| A13 | Identidade única e cadastro transacional | Implementado e validado |
| A14 | Templates WhatsApp, fila de envio e consentimento | Implementado e validado |
| A15 | Normalização de telefone e links WhatsApp | Implementado e validado |
| A16 | Privacidade, termos e solicitações sobre dados pessoais | Implementado e validado |
| A17 | Setup e gestão de estabelecimento/regiões sem depender do admin | Implementado e validado |
| Operação | Jobs, backup de banco/mídia, checagens e documentação de deploy | Implementado e validado |
| Verificação | Suíte completa, migrações, concorrência e navegador | Implementado e validado |

Nenhuma alteração de credenciais reais ou cobrança externa é necessária para
desenvolver e testar as correções. A validação com a conta do estabelecimento será
executada depois que o responsável informar suas credenciais e autorizações.


## Evidências finais — 07/10/2026

- A primeira rodada passou com 303 testes: os 240 existentes e 63 regressões/validações novas. Registros em `correcoes-testes-completos.txt` e `correcoes-testes.txt`. A continuação ampliou a suíte para 324 testes, descritos abaixo.
- Duas threads/conexões reais ao SQLite WAL confirmaram uma criação de pedido e uma solicitação de cobrança para requisições simultâneas. Resultado em `concorrencia.json`.
- As migrações passaram sobre uma cópia do banco existente, preservando 20 pedidos, 21 itens de pedido, 5 restaurantes, 58 produtos e 5 contas/perfis. Resultado em `migracoes.json`.
- O navegador confirmou recusa seguida de aprovação do cartão no mesmo pedido, entrega com taxa incluída no total confirmado, retomada do Pix e passagem de ID/URL/creq para o Status Screen Brick. A continuação verificou também o histórico de cobranças e a ação de estorno. O SDK/banco do desafio foram simulados, sem cobrança. Foram registradas 26 capturas, sem erros JavaScript ou rolagem horizontal nas larguras verificadas. Resultado em `navegador-correcoes.json`.
- Backup/restauração preservam banco, uploads e chave da instalação; testes também verificam proteção contra sobrescrita e caminho inválido, envio S3 simulado e preservação da cópia local em falha externa.
- A imagem de produção foi construída e a inicialização local com Gunicorn, migrações, estáticos, healthcheck, tarefas automáticas e backup foi verificada em contêiner descartável. Os serviços reais ficam para a configuração da conta do estabelecimento.

## O que permanece com o responsável

Não há uma implementação técnica planejada A01–A17 deixada para o responsável. Permanecem o preenchimento de dados e credenciais, as autorizações/aprovações dos provedores e a ativação no domínio/servidor escolhido. O roteiro está em [CONFIGURACAO_FINAL.md](../CONFIGURACAO_FINAL.md).

As evidências iniciais do relatório e de `reproduzir.py` são históricas: o script caracteriza os defeitos anteriores e não é o verificador do código corrigido. Use os testes de regressão e as evidências finais acima.

## Continuação da revisão — 07/10/2026

- Cancelamento durante cobrança em processamento agora aguarda a confirmação. Aprovações tardias de pedidos cancelados geram uma solicitação persistente de estorno; cobranças excedentes são estornadas individualmente, preservando o pagamento válido e sua receita.
- A reconciliação por referência valida identificação, valor, moeda, método e referência antes de vincular a cobrança. Resultados ambíguos continuam em verificação. Webhooks inválidos não vinculam uma identificação ao pedido.
- Consultas de cobranças antigas continuam funcionando após recriação/substituição. O retorno 3DS exige acesso ao pedido antes de consultar o provedor e utiliza a mesma validação financeira dos demais fluxos.
- O detalhe do pedido mostra as tentativas, valores estornados e estornos pendentes, com ação de confirmação para quem tem permissão. A confirmação do cliente informa estornos em verificação.
- A revisão em PostgreSQL revelou e corrigiu um erro de bloqueio na exclusão de dados pessoais: a consulta bloqueia a solicitação, preservando o relacionamento opcional com a conta.
- A preparação para lançamento detecta horários incompletos, SMTP incompleto e tarefas automáticas paradas ou com falhas recentes.
- O cliente PostgreSQL da imagem é selecionável por `POSTGRES_CLIENT_VERSION`. O backup exige a mesma versão principal do servidor e a registra no manifesto. A restauração foi comprovada com servidor e clientes PostgreSQL 16.

Validação desta rodada: **324 testes passaram em SQLite/Windows e PostgreSQL 16/Linux**. Os cenários novos passaram também sem alterar os hashers padrão. Requisições simultâneas resultaram em um pedido e uma cobrança nos dois bancos. Backup e restauração reais em PostgreSQL preservaram pedido, conta e upload fictícios.

Registros: [testes SQLite](continuacao-testes.txt), [testes PostgreSQL](postgres-testes.txt), [concorrência PostgreSQL](concorrencia-postgres.json), [backup PostgreSQL](postgres-backup.json) e [navegador](navegador-correcoes.json).

A checagem do ambiente local ainda aponta razão social/responsável, e-mail de atendimento/privacidade, credenciais dos pagamentos ou desativação desses métodos, HTTPS/produção, backup externo e ativação do serviço de operação. O SMTP consta como configurado; a entrega real depende da homologação do responsável. As credenciais reais e o banco do estabelecimento foram preservados nesta continuação.

## Ajustes operacionais seguintes — 07/10/2026

- Confirmações repetidas e simultâneas preservam o pagamento válido enquanto a cobrança excedente aguarda estorno.
- A solicitação de estorno é registrada na mesma transação do cancelamento, antes dos avisos e da chamada ao provedor. Uma interrupção não deixa um pedido cancelado e pago sem encaminhamento de estorno.
- O botão de estorno encerra o pedido e o retira da fila. Exige permissão e confirmação e mantém solicitações pendentes disponíveis para retomada automática.
- A equipe pode cancelar um Pix pendente no detalhe do pedido, após confirmação do provedor. Aprovação durante a consulta exige confirmação de estorno; resultados desconhecidos continuam bloqueados. A atualização em massa mantém as chamadas de rede fora da transação.
- A restauração rejeita arquivos incompletos, bancos SQLite corrompidos e caminhos/entradas ambíguos. Extrai e valida em uma pasta temporária antes de substituir um destino vazio e preserva o manifesto. No SQLite, limpa os bloqueios/saúde antiga da operação; no PostgreSQL, o novo comando `reset_operations` faz essa preparação após `pg_restore`.
- Os logs da operação mostram as falhas registradas pelas tarefas e os erros de configuração, sem repassar corpos privados de erro do provedor de pagamento.
- O deploy bloqueia chaves de desenvolvimento ou fracas, incluindo chaves alternativas. A geração automática da chave no Compose continua disponível. A imagem exclui a chave local e as capturas de auditoria.

Validação final: **352 testes passaram em SQLite/Windows e PostgreSQL 16/Linux**. Concorrência com duas conexões também confirmou que apenas a cobrança excedente recebe solicitação de estorno. O navegador confirmou encerramento após estorno e cancelamento de Pix pendente, com 27 capturas, sem erros JavaScript ou rolagem horizontal nas larguras verificadas. A restauração real do PostgreSQL preservou pedido, conta e upload e liberou a operação para retomar.

Evidências: [testes SQLite](ajustes-testes.txt), [testes PostgreSQL](ajustes-postgres-testes.txt), [concorrência PostgreSQL](ajustes-concorrencia-postgres.json), [restauração PostgreSQL](ajustes-postgres-backup.json) e [navegador](navegador-correcoes.json).
