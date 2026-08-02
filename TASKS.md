# HookPad — melhorias futuras e tarefas técnicas

Este arquivo centraliza problemas conhecidos, riscos técnicos e melhorias planejadas.
As tarefas estão ordenadas por prioridade. Marque uma tarefa concluída trocando `[ ]` por `[x]`.

## P0 — segurança, perda de dados ou indisponibilidade

### [ ] Fazer streaming de uploads grandes
**Problema:** o fluxo atual lê o corpo recebido em memória e arquivos podem ser convertidos para Base64. Isso aumenta o consumo de RAM, cria cópias do payload e não é adequado para arquivos grandes.

**Implementação sugerida:**
- Receber uploads em blocos e gravar em arquivo temporário ou object storage.
- Passar ao script somente caminho, identificador, tamanho, MIME type e hash.
- Definir limite global e limite por script.
- Remover o arquivo temporário em `finally`, inclusive em timeout ou erro.
- Não inserir Base64 de arquivos grandes no código temporário.

**Critério de conclusão:** um upload maior que a memória disponível não derruba o processo e o histórico guarda apenas metadados/preview limitado.

### [ ] Limitar stdout e stderr durante a execução
**Problema:** `subprocess.run(..., capture_output=True)` pode acumular toda a saída em memória antes do truncamento posterior.

**Implementação sugerida:**
- Usar `subprocess.Popen`.
- Consumir stdout/stderr incrementalmente.
- Manter somente os primeiros/últimos bytes até `max_log_bytes`.
- Encerrar ou descartar saída excedente sem crescimento ilimitado de RAM.

**Critério de conclusão:** um script que imprime vários gigabytes não faz o processo principal armazenar vários gigabytes em memória ou no banco.

### [ ] Remover token administrativo das URLs
**Problema:** query strings podem aparecer em histórico do navegador, proxy, observabilidade e cabeçalho Referer.

**Implementação sugerida:**
- Preferir cookie de sessão `HttpOnly`, `Secure` e `SameSite` ou header `Authorization`.
- Remover `admin_token` de links de download, SSE e chamadas do frontend.
- Implementar expiração/rotação de sessão.

**Critério de conclusão:** nenhum segredo aparece em URL ou log de acesso padrão.

### [ ] Mascarar dados sensíveis antes de persistir logs
**Problema:** headers e payloads podem conter senha, cookie, token ou chave de API.

**Implementação sugerida:**
- Redigir headers como `authorization`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization` e variantes configuráveis.
- Permitir lista de campos JSON sensíveis por instalação/script.
- Aplicar a máscara antes de criar preview e antes de persistir conteúdo completo.

**Critério de conclusão:** segredos de teste não aparecem na API, interface ou banco SQLite.

### [ ] Aplicar limites por script em toda a execução
**Problema:** campos como `max_body_bytes`, `max_response_bytes` e `max_log_bytes` existem, mas partes do fluxo ainda usam limites globais.

**Implementação sugerida:** carregar uma política de execução única e passá-la ao webhook, executor, sandbox, preview e persistência.

**Critério de conclusão:** testes comprovam que dois scripts com limites diferentes respeitam suas próprias configurações.

## P1 — estabilidade e controle de recursos

### [ ] Implementar `max_concurrency` por script
**Problema:** um script pode consumir todos os workers do executor.

**Implementação sugerida:**
- Semáforo/fila por script.
- Definir comportamento ao atingir o limite: `429`, fila limitada ou descarte explícito.
- Mostrar estado `queued` na interface quando aplicável.

**Critério de conclusão:** um script saturado não bloqueia execuções de outros scripts.

### [ ] Tornar o scheduler seguro para múltiplas instâncias
**Problema:** cada worker/processo pode iniciar seu próprio scheduler e disparar tarefas duplicadas.

**Implementação sugerida:**
- Lock transacional no banco, lease com expiração ou scheduler externo.
- Registrar `scheduled_for`, tentativa e identificador idempotente.

**Critério de conclusão:** com dois processos ativos, cada agenda é disparada apenas uma vez.

### [ ] Melhorar política de retenção e compactação do SQLite
**Estado atual:** a retenção automática foi implementada e o botão de limpeza executa compactação.

**Melhorias restantes:**
- Permitir retenção por quantidade e por tamanho total, além de dias.
- Executar `VACUUM` apenas em janela segura; ele pode bloquear e exigir espaço temporário.
- Avaliar `PRAGMA auto_vacuum=INCREMENTAL` para instalações grandes.
- Exibir no painel o tamanho do banco e a data da última limpeza.

**Critério de conclusão:** o banco permanece abaixo do limite configurado sem bloquear execuções por períodos longos.

### [ ] Separar metadados de blobs pesados
**Problema:** SQLite pode crescer rapidamente quando usado para payloads binários ou textos grandes.

**Implementação sugerida:**
- Manter metadados e previews no SQLite.
- Armazenar blobs opcionais em filesystem/object storage com TTL.
- Referenciar conteúdo por ID/hash.

**Critério de conclusão:** listagem de execuções continua rápida mesmo com grande volume de dados.

### [ ] Adicionar idempotência e proteção contra repetição
**Implementação sugerida:** aceitar uma chave de idempotência por webhook e evitar a execução duplicada dentro de uma janela configurável.

### [ ] Validar IP encaminhado somente por proxies confiáveis
**Problema:** `X-Forwarded-For` pode ser falsificado pelo cliente.

**Implementação sugerida:** configurar proxies confiáveis e usar o IP direto quando a origem não for confiável.

## P2 — interface e experiência operacional

### [x] Manter apenas uma execução expandida
Ao abrir uma execução, as demais são recolhidas. Clicar novamente na mesma execução a fecha.

### [ ] Indicar visualmente quando um payload foi truncado
**Implementação sugerida:**
- Badge “truncado”.
- Mostrar tamanho original e tamanho armazenado.
- Renomear “raw” para “conteúdo armazenado” quando não houver conteúdo integral.

### [ ] Exibir uso de armazenamento e retenção
Mostrar tamanho do banco, quantidade de execuções, conteúdo estimado, última limpeza e próxima verificação.

### [ ] Melhorar mensagens de erro do frontend
**Problema:** existem blocos `catch` silenciosos.

**Implementação sugerida:** mostrar erro contextual para o usuário e registrar detalhes técnicos no console sem revelar segredos.

### [ ] Adicionar filtros e paginação robusta ao histórico
Filtrar por script, status, período, origem, duração e tamanho; paginação no servidor e ordenação indexada.

### [ ] Melhorar acessibilidade do accordion
Adicionar `aria-expanded`, foco por teclado, Enter/Espaço e associação entre cabeçalho e painel.

## P2 — observabilidade e diagnóstico

### [ ] Criar logs estruturados da aplicação
Usar JSON ou campos estruturados com execution ID, script ID, duração, status e erro, sempre com redação de segredos.

### [ ] Adicionar métricas
Métricas sugeridas: execuções por status, duração, fila, timeout, bytes recebidos, bytes descartados, banco, limpeza e workers ocupados.

### [ ] Adicionar endpoint de saúde e prontidão completos
Separar `liveness` e `readiness`; validar banco, executor e capacidade de criar arquivos temporários.

### [ ] Registrar motivo de truncamento e rejeição
Diferenciar body grande, resposta grande, stdout grande, timeout, limite de concorrência e erro do usuário.

## P3 — arquitetura e manutenção

### [ ] Criar testes automatizados
Cobrir:
- retenção por dias;
- exclusão em cascata de payloads;
- truncamento UTF-8 e binário;
- limite de resposta;
- timeout;
- accordion;
- autenticação;
- concorrência;
- scheduler em múltiplas instâncias.

### [ ] Adicionar lint, type checking e CI
Sugestão: Ruff, MyPy/Pyright, Pytest e workflow de CI.

### [ ] Centralizar configurações
Criar uma estrutura tipada de settings e política de execução, evitando imports de constantes globais espalhados.

### [ ] Versionar migrações do banco
Usar Alembic ou mecanismo próprio com versão de schema, backup e rollback documentado.

### [ ] Definir estratégia de shutdown
Parar scheduler, impedir novas execuções, aguardar tarefas em andamento por um limite e finalizar subprocessos órfãos.

### [ ] Avaliar isolamento real do sandbox
O subprocesso Python não é uma barreira de segurança completa. Para código não confiável, usar container/VM com usuário sem privilégios, filesystem restrito, rede controlada, limites de CPU/RAM/PIDs e seccomp/AppArmor.

## Tarefas concluídas nesta revisão

- [x] Aplicar retenção automática com base em `history_days`.
- [x] Limpar registros associados em `execution_payloads`.
- [x] Realizar checkpoint após limpeza automática.
- [x] Compactar o banco na limpeza manual.
- [x] Limitar o conteúdo persistido de input/output.
- [x] Evitar persistência duplicada do request body.
- [x] Aplicar limite ao resultado serializado.
- [x] Fazer a interface expandir somente uma execução por vez.
- [x] Ignorar caches, bytecode, bancos locais, ambientes e segredos no Git/ZIP.

## Convenções para novas alterações

1. Nunca persistir payload sem limite explícito.
2. Nunca colocar segredo em URL, log ou mensagem de erro.
3. Todo limite configurável deve ter teste de aplicação real.
4. Toda tarefa em background deve ser idempotente e segura para múltiplas instâncias.
5. Arquivos gerados (`__pycache__`, bancos, logs e builds) não devem entrar no pacote fonte.
