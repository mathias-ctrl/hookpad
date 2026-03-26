# HookPad — Guia de uso

HookPad transforma scripts Python em endpoints HTTP prontos para usar.
Escreva uma função `main()`, publique, e o script vira um webhook ativo.

---

## Criando seu primeiro script

1. Clique em **+ Novo** na sidebar
2. Dê um nome ao script (ex: `soma`)
3. O editor abre com um exemplo pronto
4. Edite o código na aba **Código**
5. Clique em **Salvar** para salvar o rascunho
6. Clique em **Publicar** para criar uma versão ativa

---

## Estrutura do código

Todo script deve ter uma função `main()`. Os parâmetros dela são detectados automaticamente e viram os inputs do webhook.

```python
def main(nome: str = "Mundo", numero: int = 42):
    return {"mensagem": f"Olá, {nome}!", "dobro": numero * 2}
```

### Tipos suportados

| Tipo | Exemplo de valor |
|------|-----------------|
| `str` | `"texto"` |
| `int` | `42` |
| `float` | `3.14` |
| `bool` | `true` / `false` |
| `dict` | `{"chave": "valor"}` |
| `list` | `[1, 2, 3]` |
| `bytes` | base64 ou multipart |

### Retornando valores

```python
def main():
    return {"status": "ok"}        # → JSON direto na resposta

def main():
    return b"\x89PNG..."           # → application/octet-stream

def main():
    # sem return = resposta vazia
    print("executado!")
```

### Usando bibliotecas externas

Basta importar. O HookPad detecta os imports e instala as dependências no momento do **Publicar**.

```python
import requests
import pandas as pd
from bs4 import BeautifulSoup

def main(url: str):
    r = requests.get(url)
    return {"status": r.status_code, "tamanho": len(r.text)}
```

---

## Publicando e versionamento

Cada vez que você clica em **Publicar**:

- Um snapshot imutável do código é salvo como nova versão (`v0.0.1`, `v0.0.2`...)
- As dependências são instaladas em background (você verá o banner de build)
- O webhook passa a usar esse código publicado
- O rascunho continua editável sem afetar o que está em produção

O patch vai até `v0.0.999` → vira `v0.1.0`. O minor vai até `v0.999.999` → vira `v1.0.0`.

### Restaurando versões antigas

Na aba **Versões**, cada versão tem um botão **Restaurar** que carrega o código no editor como novo rascunho.

---

## Configurando o webhook

Vá na aba **Config**:

### Método HTTP
Escolha entre `GET`, `POST`, `PUT`, `DELETE`, `PATCH`.

### Modo de execução
- **Síncrono** — aguarda o script terminar e devolve o resultado direto
- **Assíncrono** — devolve `{ execution_id, status: "queued" }` imediatamente e executa em background

### Token de autenticação

O token protege o endpoint. Gere na seção **Token de autenticação**.

**Usando o token:**
```bash
# Header (recomendado)
curl -H "X-Token: seu-token" https://seudominio.com/hook/abc123

# Query param
curl "https://seudominio.com/hook/abc123?token=seu-token"
```

O token pode ter expiração: 1h, 24h, 7d, 30d ou nunca.

---

## Chamando o webhook

### GET com query params
```bash
curl "http://localhost:8000/hook/SEU_ID?token=SEU_TOKEN&nome=João&numero=10"
```

### POST com JSON
```bash
curl -X POST "http://localhost:8000/hook/SEU_ID" \
     -H "Content-Type: application/json" \
     -H "X-Token: SEU_TOKEN" \
     -d '{"nome": "João", "numero": 10}'
```

### Enviando arquivo binário
```bash
# via multipart
curl -X POST "http://localhost:8000/hook/SEU_ID" \
     -H "X-Token: SEU_TOKEN" \
     -F "arquivo=@meu.pdf"

# via JSON com base64
curl -X POST "http://localhost:8000/hook/SEU_ID" \
     -H "Content-Type: application/json" \
     -H "X-Token: SEU_TOKEN" \
     -d "{\"arquivo\": \"$(base64 -w0 meu.pdf)\"}"
```

---

## Agendamento (Schedule)

Em vez de webhook, você pode agendar a execução do script:

1. Na aba **Config**, em **Trigger**, selecione **Agendado**
2. Configure o intervalo:
   - **A cada N segundos/minutos/horas/dias/meses**
   - Para dias: selecione também o horário e opcionalmente os dias da semana
3. Defina o **Timezone**
4. Salve e Publique

O script será chamado automaticamente no intervalo configurado, sem parâmetros de entrada.

---

## Execuções

Cada chamada ao webhook (ou disparo agendado) gera uma **execução** registrada.

Na aba **Execuções** você vê:
- Status: `queued`, `running`, `success`, `failed`, `timeout`
- Tempo de duração
- Preview do input e output
- Detalhes expandíveis (stdout, stderr, erro)
- Links **Input raw** e **Output raw** para abrir o conteúdo completo no navegador

### Paginação
A lista carrega 20 execuções por vez. Role até o final para ver o botão **Carregar mais**.

---

## Pastas

Organize seus scripts em pastas para facilitar a navegação:

- Clique no ícone de **pasta+** na sidebar para criar uma pasta
- **Arraste** um script sobre uma pasta para movê-lo
- Arraste de volta para a seção **Sem pasta** para desvinculá-lo

---

## Limites e configurações

Em **Config > Limites**:

| Campo | Padrão | Descrição |
|-------|--------|-----------|
| Timeout (ms) | 30.000 | Tempo máximo de execução |
| Máx. body (bytes) | 10 MB | Tamanho máximo do payload de entrada |

---

## Atualizações em tempo real

O HookPad usa **SSE (Server-Sent Events)** para manter a UI atualizada automaticamente:

- Novas execuções aparecem na lista sem recarregar
- Mudanças de status (running → success/failed) são refletidas em tempo real
- O banner de build atualiza assim que as dependências terminam de instalar
- Token revogado ou expirado atualiza o badge na sidebar imediatamente

---

## Dicas

**Script leve (CRUD, validação)?** Use modo **Síncrono**.

**Script pesado (ML, scraping, processamento de arquivo)?** Use modo **Assíncrono** e consulte o status via `execution_id`.

**Precisa de segredos (API keys)?** Use variáveis de ambiente no servidor e acesse via `os.getenv("MINHA_KEY")`.

**Script com Playwright ou Selenium?** Funciona, mas aumente o timeout para 60.000ms ou mais.

---

## Endpoints da API

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET/POST/...` | `/hook/{id}` | Executa o script publicado |
| `GET` | `/api/scripts` | Lista scripts |
| `POST` | `/api/scripts` | Cria script |
| `PUT` | `/api/scripts/{id}` | Atualiza rascunho |
| `POST` | `/api/scripts/{id}/publish` | Publica versão |
| `GET` | `/api/executions` | Lista execuções (cursor) |
| `GET` | `/api/executions/{id}/input/raw` | Input completo |
| `GET` | `/api/executions/{id}/output/raw` | Output completo |
| `GET` | `/api/events` | Stream SSE de eventos |
| `GET` | `/api/folders` | Lista pastas |

Todos os endpoints admin exigem o header `X-Admin-Token` ou query param `admin_token`.
