"""Assinaturas SaaS: persistência, verificação de acesso e decorator Flask."""
from datetime import datetime, timedelta
from functools import wraps

from flask import flash, redirect, request, session, url_for


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
            if session.get("role") != "admin":
                flash("Acesso negado!", "error")
                return redirect(url_for("login"))

            barbearia_id = session.get("barbearia_id") or session.get("user_id")
            if not barbearia_id:
                flash("Acesso negado!", "error")
                return redirect(url_for("login"))

            conn = get_connection()
            cursor = conn.cursor()
            try:
                assinatura = obter_assinatura(cursor, barbearia_id)
            finally:
                conn.close()

            if assinatura_permite_acesso(assinatura):
                return view(*args, **kwargs)

            flash(
                "Seu período de teste expirou ou a assinatura está inativa. "
                "Assine o plano para continuar usando o sistema.",
                "warning",
            )
            return redirect(url_for("checkout"))

        return wrapped

    return decorator
