# AgendaSimples — SaaS com Stripe (passo a passo)

Este guia descreve como ativar planos mensais, trial gratuito e cobrança recorrente no seu projeto Flask.

---

## 1. Arquivos criados ou alterados

| Arquivo | Função |
|---------|--------|
| `migracao_assinaturas_stripe.sql` | Cria tabela `assinaturas` no SQL Server |
| `config_saas.py` | Lê chaves Stripe e `TRIAL_DAYS` do ambiente |
| `subscriptions.py` | Trial no cadastro, verificação de acesso, decorator `@requer_plano` |
| `stripe_payments.py` | Rotas `/checkout`, `/payment/success`, `/payment/cancel`, `/webhook/stripe` |
| `app.py` | Cadastro com trial, login com `barbearia_id`, rotas admin protegidas |
| `templates/checkout.html` | Página intermediária (se Stripe não redirecionar direto) |
| `templates/payment_success.html` | Retorno após pagamento |
| `templates/payment_cancel.html` | Usuário desistiu no Checkout |
| `templates/admin_assinatura.html` | Status da assinatura no painel |
| `.env.example` | Modelo de variáveis de ambiente |
| `requirements.txt` | Inclui `stripe` e `python-dotenv` |

---

## 2. Banco de dados (SQL Server)

Execute no **SQL Server Management Studio** (ou `sqlcmd`):

```sql
-- Arquivo: migracao_assinaturas_stripe.sql
```

A tabela `assinaturas` contém:

- `barbearia_id` — FK para `barbearias`
- `stripe_customer_id` — cliente no Stripe
- `stripe_subscription_id` — assinatura recorrente
- `plano_status` — `active`, `trialing`, `past_due`, `canceled`
- `data_fim_trial` — fim do trial local ou do Stripe
- `data_fim_plano` — fim do período de cobrança atual

Barbearias antigas recebem trial de 7 dias via script de migração.

---

## 3. Conta e produto no Stripe

1. Crie conta em [https://dashboard.stripe.com](https://dashboard.stripe.com).
2. **Produtos** → Novo produto → Preço **recorrente mensal** (ex.: R$ 49/mês).
3. Copie o **Price ID** (`price_...`) para `STRIPE_PRICE_ID`.
4. **Developers → API keys**: `sk_test_...` e `pk_test_...`.
5. **Developers → Webhooks** → Add endpoint:
   - URL: `https://SEU_DOMINIO/webhook/stripe`
   - Eventos:
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_succeeded`
     - `invoice.payment_failed`
   - Copie o **Signing secret** (`whsec_...`) para `STRIPE_WEBHOOK_SECRET`.

### Webhook em desenvolvimento local

```bash
stripe login
stripe listen --forward-to localhost:5000/webhook/stripe
```

Use o `whsec_...` exibido pelo CLI no `.env`.

---

## 4. Variáveis de ambiente

Copie `.env.example` para `.env` na raiz do projeto.

No início do `app.py` (opcional), carregue o `.env`:

```python
from dotenv import load_dotenv
load_dotenv()
```

Ou exporte no PowerShell antes de rodar:

```powershell
$env:STRIPE_SECRET_KEY="sk_test_..."
$env:STRIPE_PRICE_ID="price_..."
$env:STRIPE_WEBHOOK_SECRET="whsec_..."
$env:APP_BASE_URL="http://localhost:5000"
$env:TRIAL_DAYS="7"
```

Instale dependências:

```bash
pip install -r requirements.txt
```

---

## 5. Fluxo do sistema

### Cadastro (`/cadastro_barbearia`)

1. Insere barbearia em `barbearias`.
2. Cria linha em `assinaturas` com `plano_status = 'trialing'` e `data_fim_trial = hoje + TRIAL_DAYS`.
3. Tenta criar usuário `admin` em `usuarios`.

### Login admin

- Sessão guarda `barbearia_id` para amarrar assinatura e Stripe.

### Painel admin (rotas com `@requer_plano`)

- `admin_agenda`, `admin_financeiro`, `admin/galeria`, `admin/configuracoes`, etc.
- Se trial expirou e status não é `active` → redirect para `/checkout`.

### Checkout (`/checkout`)

- Cria/recupera `Customer` no Stripe.
- Abre **Stripe Checkout** em modo `subscription` com `trial_period_days` (dias restantes do trial local).
- Exige cartão (`payment_method_types: card`).

### Após pagamento

- `/payment/success?session_id=...` atualiza `assinaturas` no banco.
- Webhook mantém sincronizado em cancelamentos e falhas de pagamento.

---

## 6. Testar

1. Rode a migração SQL.
2. Configure `.env`.
3. `python app.py`
4. Cadastre nova barbearia → login admin → use o painel (trial ativo).
5. Para simular fim do trial, no SQL:

```sql
UPDATE assinaturas SET data_fim_trial = DATEADD(day, -1, GETDATE()) WHERE barbearia_id = 1;
```

6. Acesse `/admin_agenda` → deve redirecionar para `/checkout`.
7. Use cartão de teste Stripe: `4242 4242 4242 4242`.

---

## 7. Produção

- Troque chaves `sk_test` / `pk_test` por **live**.
- `APP_BASE_URL` = URL HTTPS real.
- Webhook apontando para o domínio público.
- Não commite `.env` no Git.

---

## 8. Personalizar dias de trial

Altere no `.env`:

```
TRIAL_DAYS=14
```

Isso afeta cadastro local e o cálculo de `trial_period_days` enviado ao Stripe Checkout.
