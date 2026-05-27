"""Métricas de conversão SaaS e desempenho por estabelecimento."""
from datetime import datetime, timedelta

import db_adapter as db


def _iso_limite(dias_atras):
    return (datetime.utcnow() - timedelta(days=dias_atras)).strftime("%Y-%m-%d %H:%M:%S")


def _contar(sql, params=()):
    row = db.execute_query(sql, params, fetch="one")
    if not row:
        return 0
    val = db.row_get(row, "total", index=0)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def metricas_plataforma():
    """KPIs globais do SaaS (cadastros, trial, checkouts)."""
    limite_7 = _iso_limite(7)
    limite_30 = _iso_limite(30)

    total_contas = _contar("SELECT COUNT(*) AS total FROM barbearias")
    cadastros_7d = _contar(
        "SELECT COUNT(*) AS total FROM barbearias WHERE data_cadastro >= ?",
        (limite_7,),
    )
    cadastros_30d = _contar(
        "SELECT COUNT(*) AS total FROM barbearias WHERE data_cadastro >= ?",
        (limite_30,),
    )
    em_trial = _contar(
        """
        SELECT COUNT(*) AS total FROM assinaturas
        WHERE LOWER(TRIM(plano_status)) = 'trialing'
        """
    )
    planos_ativos = _contar(
        """
        SELECT COUNT(*) AS total FROM assinaturas
        WHERE LOWER(TRIM(plano_status)) = 'active'
        """
    )
    expirados = _contar(
        """
        SELECT COUNT(*) AS total FROM assinaturas
        WHERE LOWER(TRIM(plano_status)) NOT IN ('trialing', 'active')
        """
    )
    checkouts_ok = _contar(
        """
        SELECT COUNT(*) AS total FROM assinaturas
        WHERE stripe_subscription_id IS NOT NULL
          AND TRIM(stripe_subscription_id) != ''
        """
    )
    checkouts_30d = _contar(
        """
        SELECT COUNT(*) AS total FROM assinaturas
        WHERE stripe_subscription_id IS NOT NULL
          AND TRIM(stripe_subscription_id) != ''
          AND atualizado_em >= ?
        """,
        (limite_30,),
    )

    base_trial = em_trial + planos_ativos + expirados
    taxa_conversao = (
        round(100.0 * planos_ativos / base_trial, 1) if base_trial > 0 else 0.0
    )

    return {
        "total_contas": total_contas,
        "cadastros_7d": cadastros_7d,
        "cadastros_30d": cadastros_30d,
        "em_trial": em_trial,
        "planos_ativos": planos_ativos,
        "expirados": expirados,
        "checkouts_total": checkouts_ok,
        "checkouts_30d": checkouts_30d,
        "taxa_conversao_pct": taxa_conversao,
    }


def metricas_estabelecimento(barbearia_id):
    """KPIs do negócio (agendamentos e clientes)."""
    bid = int(barbearia_id)
    limite_30 = _iso_limite(30)
    hoje = datetime.utcnow().strftime("%Y-%m-%d")

    agendamentos_mes = _contar(
        """
        SELECT COUNT(*) AS total FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ?
        """,
        (bid, limite_30[:10]),
    )
    agendamentos_hoje = _contar(
        """
        SELECT COUNT(*) AS total FROM Clientes
        WHERE barbearia_id = ? AND Dia = ?
        """,
        (bid, hoje),
    )
    clientes_unicos = _contar(
        """
        SELECT COUNT(DISTINCT nome) AS total FROM Clientes
        WHERE barbearia_id = ?
        """,
        (bid,),
    )
    concluidos_mes = _contar(
        """
        SELECT COUNT(*) AS total FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ?
          AND LOWER(TRIM(COALESCE(status, ''))) IN ('concluido', 'concluído', 'feito')
        """,
        (bid, limite_30[:10]),
    )

    return {
        "agendamentos_mes": agendamentos_mes,
        "agendamentos_hoje": agendamentos_hoje,
        "clientes_unicos": clientes_unicos,
        "concluidos_mes": concluidos_mes,
    }
