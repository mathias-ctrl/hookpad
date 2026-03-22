# HookPad 🪝

Execute scripts Python como webhooks com auto-install de dependências, venv isolado por script e editor Monaco.

---

## Funcionalidades

- **Editor Monaco** (mesmo do VS Code) direto no browser
- **Webhook por script** — cada script tem sua URL e token únicos
- **GET ou POST** configurável por script
- **venv isolado** por script — sem conflito de dependências
- **Auto-install** — basta dar `import requests` no código, o HookPad instala automaticamente
- **Parâmetros** injetados de query string, headers `X-*` e body JSON/form
- **Teste inline** — execute o script com params direto na UI
- **Rotacionar token** sem reiniciar nada

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
# Clone / copie os arquivos para sua VPS
git clone <seu-repo> hookpad && cd hookpad

# Edite as variáveis de ambiente no docker-compose.yml
nano docker-compose.yml

# Suba
docker compose up -d
```

---

## Deploy no EasyPanel

1. Crie um novo serviço do tipo **App**
2. Aponte para o repositório ou faça upload dos arquivos
3. Configure as variáveis de ambiente:
   - `ADMIN_TOKEN` → token para acessar a UI (mude isso!)
   - `BASE_URL` → URL pública do seu serviço (ex: `https://hooks.meusite.com`)
   - `EXEC_TIMEOUT` → timeout em segundos (padrão: 30)
4. Monte um volume em `/data` para persistência dos scripts e venvs
5. Exponha a porta `8000`

---

## Como usar os webhooks

### Via GET
```bash
curl "https://hooks.seusite.com/hook/abc12345?token=SEU_TOKEN&param=valor"
```

### Via POST com JSON
```bash
curl -X POST "https://hooks.seusite.com/hook/abc12345" \
     -H "Content-Type: application/json" \
     -H "X-Token: SEU_TOKEN" \
     -d '{"nome": "João", "valor": 42}'
```

### Token na query string (GET e POST)
```bash
curl -X POST "https://hooks.seusite.com/hook/abc12345?token=SEU_TOKEN" \
     -d '{"x": 1}'
```

---

## Variáveis disponíveis no script

```python
# Todos os params recebidos (query + body + headers X-)
print(__params__)   # {'nome': 'João', 'valor': 42}

# Variáveis diretas (nome do param vira variável)
print(nome)   # João
print(valor)  # 42

# Headers X- customizados chegam sem o "x-" prefix
# X-Origem: webhook → origem = "webhook"
print(origem)
```

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `ADMIN_TOKEN` | `admin-mude-isso` | Token para acessar a UI e API admin |
| `BASE_URL` | `http://localhost:8000` | URL pública do serviço |
| `EXEC_TIMEOUT` | `30` | Timeout por execução em segundos |
| `DATA_DIR` | `./scripts_data` | Onde salvar scripts e venvs |

---

## API Admin

Todos os endpoints abaixo exigem o header `X-Admin-Token: SEU_ADMIN_TOKEN`.

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/scripts` | Lista todos os scripts |
| POST | `/api/scripts` | Cria novo script |
| GET | `/api/scripts/{id}` | Busca script por ID |
| PUT | `/api/scripts/{id}` | Atualiza script |
| DELETE | `/api/scripts/{id}` | Deleta script + venv |
| POST | `/api/scripts/{id}/rotate-token` | Rotaciona o token do script |
| POST | `/api/scripts/{id}/test` | Executa script com params de teste |
