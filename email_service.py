"""Envio de e-mails via SMTP (recuperação de senha, lembrete de trial, etc.)."""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config_saas as cfg

logger = logging.getLogger(__name__)


def smtp_configurado():
    return bool(os.environ.get("SMTP_HOST", "").strip())


def enviar_email(destinatario, assunto, corpo_texto, corpo_html=None):
    """
    Envia e-mail em texto plano (e HTML opcional).
    Retorna True se enviado; False se SMTP não configurado ou falha.
    """
    destinatario = (destinatario or "").strip()
    if not destinatario:
        return False

    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    if not smtp_host:
        logger.warning("SMTP_HOST não configurado — e-mail não enviado para %s", destinatario)
        return False

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "noreply@agendasimples.local")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_from
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
    if corpo_html:
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [destinatario], msg.as_string())
        return True
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail para %s: %s", destinatario, exc)
        return False


def enviar_email_recuperacao(destinatario, nome_usuario, link_redefinir):
    assunto = "Redefinição de senha — Agenda Simples"
    corpo = f"""Olá, {nome_usuario or 'usuário'}!

Recebemos um pedido para redefinir sua senha no Agenda Simples.

Clique no link abaixo (válido por 30 minutos):
{link_redefinir}

Se você não solicitou isso, ignore este e-mail.

Atenciosamente,
Equipe Agenda Simples
"""
    return enviar_email(destinatario, assunto, corpo)


def enviar_email_trial_terminando(
    destinatario,
    nome_usuario,
    nome_negocio,
    dias_restantes,
    link_checkout,
    preco_mensal,
):
    """Avisa que o teste grátis está acabando (conversão para plano pago)."""
    assunto = f"Seu teste do AgendaSimples termina em {dias_restantes} dia(s)"
    corpo = f"""Olá, {nome_usuario or 'administrador'}!

O teste grátis de {nome_negocio or 'seu estabelecimento'} no AgendaSimples termina em {dias_restantes} dia(s).

Para não perder a agenda online, o financeiro e os agendamentos dos seus clientes, assine o plano mensal por R$ {preco_mensal:.2f}:

{link_checkout}

Sem fidelidade — cancele quando quiser pelo painel Stripe.

Atenciosamente,
Equipe AgendaSimples
"""
    html = f"""<p>Olá, <strong>{nome_usuario or 'administrador'}</strong>!</p>
<p>O teste grátis de <strong>{nome_negocio or 'seu estabelecimento'}</strong> termina em <strong>{dias_restantes} dia(s)</strong>.</p>
<p><a href="{link_checkout}" style="display:inline-block;padding:12px 24px;background:#d4af37;color:#000;text-decoration:none;border-radius:8px;font-weight:bold;">Assinar por R$ {preco_mensal:.2f}/mês</a></p>
<p>Sem fidelidade — cancele quando quiser.</p>
<p>Equipe AgendaSimples</p>"""
    return enviar_email(destinatario, assunto, corpo, corpo_html=html)
