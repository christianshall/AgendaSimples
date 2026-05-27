"""Configuração SaaS / Stripe — carregada de variáveis de ambiente."""
import os

# Stripe
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# ID do Price recorrente mensal criado no Dashboard Stripe (mode=subscription)
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")

# Trial
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))

# Plano mensal (exibido na tela de bloqueio)
PLANO_MENSAL_VALOR = float(os.environ.get("PLANO_MENSAL_VALOR", "49.90"))

# URL base do app (sem barra final) — ex: https://meudominio.com ou http://localhost:5000
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")

# Imagem para compartilhamento (Open Graph) — URL absoluta opcional
OG_IMAGE_URL = os.environ.get("OG_IMAGE_URL", "").strip()

# E-mail do dono da plataforma (vê métricas globais em /admin/metricas)
SAAS_OWNER_EMAIL = os.environ.get("SAAS_OWNER_EMAIL", "").strip().lower()

# Cron Vercel: Authorization: Bearer <CRON_SECRET>
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

# Dias antes do fim do trial para enviar lembrete por e-mail
TRIAL_EMAIL_DIAS_AVISO = int(os.environ.get("TRIAL_EMAIL_DIAS_AVISO", "2"))
