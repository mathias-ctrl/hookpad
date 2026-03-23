# HookPad 🪝

Execute scripts Python como webhooks com editor Monaco, auto-install de dependências, venv isolado por script e autenticação por token.

---

## Screenshots

| Editor de código | Configuração & Webhook |
|:---:|:---:|
| ![Editor](/.github/screenshots/editor.png) | ![Configuração](/.github/screenshots/config.png) |

| Testar inline | Histórico de execuções |
|:---:|:---:|
| ![Testar](/.github/screenshots/test.png) | ![Histórico](/.github/screenshots/history.png) |

---

## Funcionalidades

- **Editor Monaco** (mesmo do VS Code) direto no browser
- **Webhook por script** — cada script tem sua URL e token únicos
- **GET ou POST** configurável por script
- **`def main()`** com detecção automática de tipos e geração de curl
- **venv isolado** por script — sem conflito de dependências
- **Auto-install** — basta dar `import requests` no código, o HookPad instala automaticamente
- **Parâmetros** injetados de query string, headers `X-*` e body JSON/form/multipart
- **Teste inline** — execute o script com params direto na UI
- **Histórico** de execuções por script
- **Agendamento** — execute scripts em intervalos (5min, 1h, diário, semanal)
- **Limpeza automática** de pacotes ociosos (venvs não usados há 7 dias)

---

## Rodando localmente

```bash
# Instale as dependências
pip install fastapi uvicorn python-multipart

# Configure
export ADMIN_TOKEN="meu-token-admin"
export BASE_URL="http://localhost:8000"

# Rode
cd app
uvicorn main:app --reload --port 8000
```

Acesse: http://localhost:8000

---

## Deploy com Docker

```bash
# Clone o repositório
git clone https://github.com/mathias-ctrl/hookpad && cd hookpad

# Edite as variáveis de ambiente
nano docker-compose.yml

# Suba
docker compose up -d
```

---

## Deploy no EasyPanel

1. Crie um novo serviço do tipo **App**
2. Aponte para o repositório
3. Configure as variáveis de ambiente:
   - `ADMIN_TOKEN` → token para acessar a UI (mude isso!)
   - `BASE_URL` → URL pública do seu serviço (ex: `https://hooks.meusite.com`)
   - `EXEC_TIMEOUT` → timeout em segundos (padrão: `30`)
   - `VENV_TTL_DAYS` → dias sem uso para limpar pacotes (padrão: `7`)
4. Monte um volume em `/data` para persistência
5. Exponha a porta `8000`

---

## Estrutura do script

Use `def main()` para detecção automática de parâmetros:

```python
def main(
    nome: str,
    numero: int = 42,
    dados: dict = {},
    arquivo: bytes = bytes(0),
):
    return {"mensagem": f"Olá, {nome}!", "dobro": numero * 2}
```

O HookPad detecta os tipos, gera o curl de exemplo automaticamente e preenche os campos de teste.

### Tipos suportados

| Tipo | Descrição | Exemplo |
|---|---|---|
| `str` | Texto | `"valor"` |
| `int` / `float` | Número | `42` |
| `bool` | Booleano | `true` |
| `dict` | JSON object | `{}` |
| `list` | JSON array | `[]` |
| `bytes` | Arquivo binário | base64 ou multipart |

---

## Chamando o webhook

### Via POST com JSON
```bash
curl -X POST "https://hooks.seusite.com/hook/abc12345" \
     -H "Content-Type: application/json" \
     -H "X-Token: SEU_TOKEN" \
     -d '{"nome": "João", "numero": 42}'
```

### Enviando arquivo binário
```bash
# Multipart (recomendado)
curl -X POST "https://hooks.seusite.com/hook/abc12345" \
     -H "X-Token: SEU_TOKEN" \
     -F "arquivo=@meu.pdf"

# JSON com base64
curl -X POST "https://hooks.seusite.com/hook/abc12345" \
     -H "X-Token: SEU_TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"arquivo\": \"$(base64 -w0 meu.pdf)\"}"
```

### Via GET
```bash
curl "https://hooks.seusite.com/hook/abc12345?token=SEU_TOKEN&param=valor"
```

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `ADMIN_TOKEN` | `admin-mude-isso` | Token para acessar a UI e API admin |
| `BASE_URL` | `http://localhost:8000` | URL pública do serviço |
| `EXEC_TIMEOUT` | `30` | Timeout por execução (segundos) |
| `DATA_DIR` | `./scripts_data` | Onde salvar scripts e venvs |
| `VENV_TTL_DAYS` | `7` | Dias sem uso para limpeza de pacotes |
| `SANDBOX_MEM_MB` | `512` | Limite de memória por script (MB) |
| `SANDBOX_CPU_SEC` | `60` | Limite de CPU por script (segundos) |

---

## API Admin

Todos os endpoints exigem o header `X-Admin-Token: SEU_ADMIN_TOKEN`.

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/scripts` | Lista todos os scripts |
| `POST` | `/api/scripts` | Cria novo script |
| `GET` | `/api/scripts/{id}` | Busca script por ID |
| `PUT` | `/api/scripts/{id}` | Atualiza script |
| `DELETE` | `/api/scripts/{id}` | Deleta script + venv |
| `POST` | `/api/scripts/{id}/generate-token` | Gera token de acesso |
| `POST` | `/api/scripts/{id}/revoke-token` | Revoga token |
| `POST` | `/api/scripts/{id}/install` | Pré-instala dependências |
| `POST` | `/api/scripts/{id}/test` | Executa script com params de teste |
| `GET` | `/api/scripts/{id}/history` | Histórico de execuções |
| `GET` | `/api/scripts/{id}/signature` | Detecta params do `def main()` |
