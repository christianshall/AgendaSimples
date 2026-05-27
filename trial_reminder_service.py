"""Lembrete por e-mail quando o trial está perto do fim."""
from datetime import datetime

import config_saas as cfg
import db_adapter as db
from email_service import enviar_email_trial_terminando, smtp_configurado
from subscriptions import dias_restantes_trial, obter_assinatura_barbearia


def _link_checkout():
    base = (cfg.APP_BASE_URL or "").rstrip("/")
    if base and not base.startswith("http://localhost"):
        return f"{base}/checkout"
    try:
        from flask import url_for

        return url_for("checkout", _external=True)
    except Exception:
        return "/checkout"


def _email_admin_barbearia(barbearia_id):
    row = db.execute_query(
        """
        SELECT u.email, u.nome, b.nome AS nome_negocio
        FROM usuarios u
        INNER JOIN barbearias b ON b.id = u.barbearia_id
        WHERE u.barbearia_id = ? AND LOWER(TRIM(u.role)) = 'admin'
        ORDER BY u.id
        LIMIT 1
        """,
        (int(barbearia_id),),
        fetch="one",
    )
    if not row:
        row = db.execute_query(
            "SELECT email, nome FROM barbearias WHERE id = ? LIMIT 1",
            (int(barbearia_id),),
            fetch="one",
        )
        if not row:
            return None, None, None
        return (
            db.row_get(row, "email", index=0),
            db.row_get(row, "nome", index=1),
            db.row_get(row, "nome", index=1),
        )
    return (
        db.row_get(row, "email", index=0),
        db.row_get(row, "nome", index=1),
        db.row_get(row, "nome_negocio", index=2) or db.row_get(row, "nome", index=1),
    )


def _lembrete_ja_enviado(barbearia_id):
    row = db.execute_query(
        """
        SELECT email_trial_lembrete_em FROM assinaturas
        WHERE barbearia_id = ?
        LIMIT 1
        """,
        (int(barbearia_id),),
        fetch="one",
    )
    if not row:
        return False
    val = db.row_get(row, "email_trial_lembrete_em", index=0)
    return bool(val and str(val).strip())


def _marcar_lembrete_enviado(barbearia_id):
    agora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.execute_write(
        """
        UPDATE assinaturas
        SET email_trial_lembrete_em = ?, atualizado_em = ?
        WHERE barbearia_id = ?
        """,
        (agora, agora, int(barbearia_id)),
    )


def processar_lembrete_barbearia(barbearia_id, dias_aviso=None):
    """
    Envia e-mail se faltam exatamente `dias_aviso` dias de trial e ainda não foi enviado.
    Retorna 'enviado', 'ignorado' ou 'erro'.
    """
    if not smtp_configurado():
        return "smtp_desligado"

    dias_aviso = int(dias_aviso if dias_aviso is not None else cfg.TRIAL_EMAIL_DIAS_AVISO)
    assinatura = obter_assinatura_barbearia(barbearia_id)
    if not assinatura:
        return "sem_assinatura"

    if (assinatura.get("plano_status") or "").lower() != "trialing":
        return "nao_trial"

    dias = dias_restantes_trial(assinatura)
    if dias != dias_aviso:
        return "fora_janela"

    if _lembrete_ja_enviado(barbearia_id):
        return "ja_enviado"

    email, nome_user, nome_negocio = _email_admin_barbearia(barbearia_id)
    if not email:
        return "sem_email"

    ok = enviar_email_trial_terminando(
        email,
        nome_user,
        nome_negocio,
        dias_aviso,
        _link_checkout(),
        float(cfg.PLANO_MENSAL_VALOR),
    )
    if ok:
        _marcar_lembrete_enviado(barbearia_id)
        return "enviado"
    return "erro_envio"


def processar_lembretes_trial(dias_aviso=None):
    """Varre todas as assinaturas em trial e envia lembretes pendentes."""
    dias_aviso = int(dias_aviso if dias_aviso is not None else cfg.TRIAL_EMAIL_DIAS_AVISO)
    rows = db.execute_query(
        """
        SELECT barbearia_id FROM assinaturas
        WHERE LOWER(TRIM(plano_status)) = 'trialing'
        """,
        fetch="all",
    ) or []
    stats = {"enviados": 0, "ignorados": 0, "erros": 0, "smtp_desligado": not smtp_configurado()}
    for row in rows:
        bid = db.row_get(row, "barbearia_id", index=0)
        if bid is None:
            continue
        resultado = processar_lembrete_barbearia(int(bid), dias_aviso)
        if resultado == "enviado":
            stats["enviados"] += 1
        elif resultado in ("erro_envio",):
            stats["erros"] += 1
        else:
            stats["ignorados"] += 1
    return stats
