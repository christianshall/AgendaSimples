"""Assinaturas SaaS: persistência, verificação de acesso e decorator Flask."""
from datetime import datetime, timedelta
from functools import wraps

from flask import flash, redirect, request, session, url_for

import config_saas as cfg


PLANOS_ATIVOS = ("active", "trialing")


def _parse_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val[:19], fmt)
            except ValueError:
                continue
    return None


def criar_assinatura_trial(cursor, barbearia_id, dias_trial):
    """Insere registro de assinatura com trial local (sem Stripe ainda)."""
    agora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    fim_trial = (datetime.utcnow() + timedelta(days=dias_trial)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cursor.execute(
        """
        INSERT INTO assinaturas (
            barbearia_id, stripe_customer_id, stripe_subscription_id,
            plano_status, data_fim_trial, data_fim_plano, criado_em, atualizado_em
        )
        VALUES (?, NULL, NULL, 'trialing', ?, NULL, ?, ?)
        """,
        (barbearia_id, fim_trial, agora, agora),
    )


def dias_restantes_trial(assinatura):
    """Dias até o fim do trial (0 se hoje for o último dia). None se não aplicável."""
    if not assinatura:
        return None
    if (assinatura.get("plano_status") or "").lower() != "trialing":
        return None
    fim = assinatura.get("data_fim_trial")
    if not fim:
        return None
    agora = datetime.utcnow()
    if fim < agora:
        return 0
    return max(0, (fim.date() - agora.date()).days)


def obter_assinatura_barbearia(barbearia_id):
    """Versão sem cursor — usa db_adapter (Turso / SQL Server / SQLite)."""
    import db_adapter as db

    row = db.execute_query(
        """
        SELECT id, barbearia_id, stripe_customer_id, stripe_subscription_id,
               plano_status, data_fim_trial, data_fim_plano
        FROM assinaturas
        WHERE barbearia_id = ?
        LIMIT 1
        """,
        (int(barbearia_id),),
        fetch="one",
    )
    if not row:
        return None
    return {
        "id": db.row_get(row, "id", index=0),
        "barbearia_id": db.row_get(row, "barbearia_id", index=1),
        "stripe_customer_id": db.row_get(row, "stripe_customer_id", index=2),
        "stripe_subscription_id": db.row_get(row, "stripe_subscription_id", index=3),
        "plano_status": db.row_get(row, "plano_status", index=4),
        "data_fim_trial": _parse_dt(db.row_get(row, "data_fim_trial", index=5)),
        "data_fim_plano": _parse_dt(db.row_get(row, "data_fim_plano", index=6)),
    }


def resumo_plano_admin(barbearia_id):
    """
    Dados para banner de conversão no painel (trial acabando / assinar).
    """
    assinatura = obter_assinatura_barbearia(barbearia_id)
    preco = float(cfg.PLANO_MENSAL_VALOR)
    base = {
        "mostrar_banner": False,
        "urgente": False,
        "dias_restantes": None,
        "preco_mensal": preco,
        "status": None,
        "checkout_url": None,
    }
    if not assinatura:
        base.update(mostrar_banner=True, urgente=True, status="sem_assinatura")
        return base

    status = (assinatura.get("plano_status") or "").lower()
    base["status"] = status

    if status == "active":
        return base

    if status == "trialing":
        dias = dias_restantes_trial(assinatura)
        base["dias_restantes"] = dias
        if dias is not None and dias <= 7:
            base["mostrar_banner"] = True
            base["urgente"] = dias <= 3
        return base

    base["mostrar_banner"] = True
    base["urgente"] = True
    return base


def obter_assinatura(cursor, barbearia_id):
    cursor.execute(
        """
        SELECT id, barbearia_id, stripe_customer_id, stripe_subscription_id,
               plano_status, data_fim_trial, data_fim_plano
        FROM assinaturas
        WHERE barbearia_id = ?
        """,
        (barbearia_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "barbearia_id": row[1],
        "stripe_customer_id": row[2],
        "stripe_subscription_id": row[3],
        "plano_status": row[4],
        "data_fim_trial": _parse_dt(row[5]),
        "data_fim_plano": _parse_dt(row[6]),
    }


def assinatura_permite_acesso(assinatura):
    """True se o admin pode usar o painel (trial válido ou plano pago ativo)."""
    if not assinatura:
        return False

    status = (assinatura.get("plano_status") or "").lower()
    agora = datetime.utcnow()

    if status == "active":
        fim = assinatura.get("data_fim_plano")
        if fim and fim < agora:
            return False
        return True

    if status == "trialing":
        fim_trial = assinatura.get("data_fim_trial")
        if fim_trial and fim_trial >= agora:
            return True
        return False

    return False


def sincronizar_status_trial_usuario(cursor, barbearia_id):
    """
    Atualiza status_trial dos admins do estabelecimento conforme assinatura.
    Retorna 'trialing', 'active' (via assinatura) ou 'expired'.
    """
    assinatura = obter_assinatura(cursor, barbearia_id)
    if assinatura_permite_acesso(assinatura):
        status = (assinatura.get("plano_status") or "trialing").lower()
        trial_status = "trialing" if status == "trialing" else "active"
        cursor.execute(
            """
            UPDATE usuarios
            SET status_trial = ?
            WHERE barbearia_id = ? AND role = 'admin'
            """,
            (trial_status, barbearia_id),
        )
        return trial_status

    cursor.execute(
        """
        UPDATE usuarios
        SET status_trial = 'expired'
        WHERE barbearia_id = ? AND role = 'admin'
        """,
        (barbearia_id,),
    )
    return "expired"


def admin_tem_acesso_painel(cursor, barbearia_id):
    """Verifica assinatura e status_trial do administrador do negócio."""
    trial_status = sincronizar_status_trial_usuario(cursor, barbearia_id)
    if trial_status != "expired":
        return True
    assinatura = obter_assinatura(cursor, barbearia_id)
    return assinatura_permite_acesso(assinatura)


def atualizar_assinatura_por_stripe(cursor, barbearia_id, **kwargs):
    """Atualiza campos da assinatura (kwargs: stripe_customer_id, stripe_subscription_id, etc.)."""
    campos = []
    valores = []
    for chave, valor in kwargs.items():
        if valor is not None and chave in (
            "stripe_customer_id",
            "stripe_subscription_id",
            "plano_status",
            "data_fim_trial",
            "data_fim_plano",
        ):
            campos.append(f"{chave} = ?")
            valores.append(valor)
    if not campos:
        return
    campos.append("atualizado_em = CURRENT_TIMESTAMP")
    valores.append(barbearia_id)
    sql = f"UPDATE assinaturas SET {', '.join(campos)} WHERE barbearia_id = ?"
    cursor.execute(sql, valores)


def assinatura_por_customer_id(cursor, stripe_customer_id):
    cursor.execute(
        """
        SELECT barbearia_id FROM assinaturas WHERE stripe_customer_id = ?
        """,
        (stripe_customer_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def assinatura_por_subscription_id(cursor, stripe_subscription_id):
    cursor.execute(
        """
        SELECT barbearia_id FROM assinaturas WHERE stripe_subscription_id = ?
        """,
        (stripe_subscription_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def requer_assinatura_ativa(get_connection):
    """Decorator: bloqueia rotas admin se trial expirou ou plano inativo."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Faça login para continuar.", "warning")
                return redirect(url_for("login"))

            role = (session.get("role") or "").lower()
            if role not in ("admin", "barbeiro", "profissional"):
                flash("Acesso negado!", "error")
                return redirect(url_for("acesso_negado"))

            barbearia_id = session.get("barbearia_id")
            if not barbearia_id:
                flash("Sessão inválida. Faça login novamente.", "error")
                return redirect(url_for("login"))

            import db_adapter
            from database import safe_close, safe_commit

            conn = get_connection()
            cursor = db_adapter.cursor(conn)
            try:
                liberado = admin_tem_acesso_painel(cursor, barbearia_id)
                safe_commit(conn)
            finally:
                safe_close(conn)

            if liberado:
                return view(*args, **kwargs)

            return redirect(url_for("bloqueio_assinatura"))

        return wrapped

    return decorator
