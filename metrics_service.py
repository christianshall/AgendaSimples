"""Métricas de conversão SaaS, evolução semanal, insights e exportação CSV."""
import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta

import db_adapter as db

STATUS_CONCLUIDO = frozenset({"concluido", "concluído", "feito"})


def _iso_limite(dias_atras):
    return (datetime.utcnow() - timedelta(days=dias_atras)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dia(val):
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    s = str(val).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _contar(sql, params=()):
    row = db.execute_query(sql, params, fetch="one")
    if not row:
        return 0
    val = db.row_get(row, "total", index=0)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _float_val(row, *keys, index=0, default=0.0):
    val = db.row_get(row, *keys, index=index, default=default)
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return default


def _chaves_semanas(semanas=8):
    """Últimas N semanas (chave ISO + rótulo curto)."""
    hoje = datetime.utcnow().date()
    chaves, labels, intervalos = [], [], []
    for i in range(semanas - 1, -1, -1):
        ref = hoje - timedelta(weeks=i)
        chave = ref.strftime("%Y-%W")
        inicio = ref - timedelta(days=ref.weekday())
        fim = inicio + timedelta(days=6)
        chaves.append(chave)
        labels.append(inicio.strftime("%d/%m"))
        intervalos.append((inicio, fim))
    return chaves, labels, intervalos


def _variacao_pct(atual, anterior):
    if anterior <= 0:
        return 100.0 if atual > 0 else 0.0
    return round(100.0 * (atual - anterior) / anterior, 1)


def metricas_plataforma():
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
    mrr_estimado = round(planos_ativos * _plano_mensal_valor(), 2)

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
        "mrr_estimado": mrr_estimado,
    }


def _plano_mensal_valor():
    try:
        import config_saas as cfg

        return float(cfg.PLANO_MENSAL_VALOR)
    except Exception:
        return 49.90


def metricas_estabelecimento(barbearia_id):
    bid = int(barbearia_id)
    limite_30 = _iso_limite(30)
    hoje = datetime.utcnow().strftime("%Y-%m-%d")

    agendamentos_mes = _contar(
        "SELECT COUNT(*) AS total FROM Clientes WHERE barbearia_id = ? AND Dia >= ?",
        (bid, limite_30[:10]),
    )
    agendamentos_hoje = _contar(
        "SELECT COUNT(*) AS total FROM Clientes WHERE barbearia_id = ? AND Dia = ?",
        (bid, hoje),
    )
    clientes_unicos = _contar(
        "SELECT COUNT(DISTINCT Nome) AS total FROM Clientes WHERE barbearia_id = ?",
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

    receita_mes = _float_receita_clientes(bid, limite_30[:10])
    receita_fin = _float_receita_financeiro(bid, limite_30[:10])
    receita_total = round(receita_mes + receita_fin, 2)

    taxa_conclusao = (
        round(100.0 * concluidos_mes / agendamentos_mes, 1)
        if agendamentos_mes > 0
        else 0.0
    )

    evo = evolucao_semanal_negocio(bid, semanas=8)
    series = evo.get("series") or []
    ag_atual = series[-1]["agendamentos"] if series else 0
    ag_anterior = series[-2]["agendamentos"] if len(series) > 1 else 0
    rec_atual = series[-1]["receita"] if series else 0
    rec_anterior = series[-2]["receita"] if len(series) > 1 else 0

    return {
        "agendamentos_mes": agendamentos_mes,
        "agendamentos_hoje": agendamentos_hoje,
        "clientes_unicos": clientes_unicos,
        "concluidos_mes": concluidos_mes,
        "receita_mes": receita_total,
        "taxa_conclusao_pct": taxa_conclusao,
        "tendencia_agendamentos_pct": _variacao_pct(ag_atual, ag_anterior),
        "tendencia_receita_pct": _variacao_pct(rec_atual, rec_anterior),
        "melhor_dia_semana": evo.get("melhor_dia_semana"),
    }


def _float_receita_clientes(barbearia_id, desde_data):
    rows = db.execute_query(
        """
        SELECT valor FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ?
          AND LOWER(TRIM(COALESCE(status, ''))) IN ('concluido', 'concluído', 'feito')
        """,
        (int(barbearia_id), desde_data),
        fetch="all",
    ) or []
    return round(sum(_float_val(r, "valor", index=0) for r in rows), 2)


def _float_receita_financeiro(barbearia_id, desde_data):
    try:
        rows = db.execute_query(
            """
            SELECT valor FROM financeiro
            WHERE barbearia_id = ?
              AND LOWER(TRIM(COALESCE(tipo_transacao, ''))) IN ('entrada', 'receita', 'recebimento')
              AND data >= ?
            """,
            (int(barbearia_id), desde_data),
            fetch="all",
        ) or []
    except Exception:
        return 0.0
    return round(sum(_float_val(r, "valor", index=0) for r in rows), 2)


def evolucao_semanal_negocio(barbearia_id, semanas=8):
    bid = int(barbearia_id)
    chaves, labels, intervalos = _chaves_semanas(semanas)
    buckets = {
        k: {"agendamentos": 0, "concluidos": 0, "receita": 0.0}
        for k in chaves
    }
    dia_semana_count = defaultdict(int)

    inicio = intervalos[0][0].isoformat()
    rows = db.execute_query(
        """
        SELECT Dia, status, valor, Servico FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ?
        """,
        (bid, inicio),
        fetch="all",
    ) or []

    for row in rows:
        dia = _parse_dia(db.row_get(row, "Dia", index=0))
        if not dia:
            continue
        chave = dia.strftime("%Y-%W")
        if chave not in buckets:
            continue
        buckets[chave]["agendamentos"] += 1
        status = (db.row_get(row, "status", index=1) or "").strip().lower()
        if status in STATUS_CONCLUIDO:
            buckets[chave]["concluidos"] += 1
            buckets[chave]["receita"] += _float_val(row, "valor", index=2)
        dia_semana_count[dia.strftime("%A")] += 1

    nomes_dia = {
        "Monday": "Segunda",
        "Tuesday": "Terça",
        "Wednesday": "Quarta",
        "Thursday": "Quinta",
        "Friday": "Sexta",
        "Saturday": "Sábado",
        "Sunday": "Domingo",
    }
    melhor = max(dia_semana_count, key=dia_semana_count.get) if dia_semana_count else None
    melhor_dia = nomes_dia.get(melhor, melhor)

    series = []
    for chave, label in zip(chaves, labels):
        b = buckets[chave]
        series.append(
            {
                "label": label,
                "agendamentos": b["agendamentos"],
                "concluidos": b["concluidos"],
                "receita": round(b["receita"], 2),
            }
        )

    return {"labels": labels, "series": series, "melhor_dia_semana": melhor_dia}


def evolucao_semanal_plataforma(semanas=8):
    chaves, labels, intervalos = _chaves_semanas(semanas)
    cadastros = {k: 0 for k in chaves}
    checkouts = {k: 0 for k in chaves}

    inicio = intervalos[0][0].strftime("%Y-%m-%d %H:%M:%S")

    rows_b = db.execute_query(
        "SELECT data_cadastro FROM barbearias WHERE data_cadastro >= ?",
        (inicio,),
        fetch="all",
    ) or []
    for row in rows_b:
        dt = _parse_dia(str(db.row_get(row, "data_cadastro", index=0))[:10])
        if dt:
            k = dt.strftime("%Y-%W")
            if k in cadastros:
                cadastros[k] += 1

    rows_a = db.execute_query(
        """
        SELECT atualizado_em FROM assinaturas
        WHERE stripe_subscription_id IS NOT NULL
          AND TRIM(stripe_subscription_id) != ''
          AND atualizado_em >= ?
        """,
        (inicio,),
        fetch="all",
    ) or []
    for row in rows_a:
        raw = db.row_get(row, "atualizado_em", index=0)
        dt = _parse_dia(str(raw)[:10]) if raw else None
        if dt:
            k = dt.strftime("%Y-%W")
            if k in checkouts:
                checkouts[k] += 1

    series = []
    for chave, label in zip(chaves, labels):
        series.append(
            {
                "label": label,
                "cadastros": cadastros[chave],
                "checkouts": checkouts[chave],
            }
        )
    return {"labels": labels, "series": series}


def distribuicao_status_negocio(barbearia_id):
    bid = int(barbearia_id)
    limite = _iso_limite(30)[:10]
    rows = db.execute_query(
        """
        SELECT status, COUNT(*) AS total FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ?
        GROUP BY status
        """,
        (bid, limite),
        fetch="all",
    ) or []
    out = []
    for row in rows:
        st = (db.row_get(row, "status", index=0) or "Agendado").strip()
        out.append({"status": st, "total": int(db.row_get(row, "total", index=1) or 0)})
    return out


def top_servicos_negocio(barbearia_id, limite=5):
    bid = int(barbearia_id)
    desde = _iso_limite(30)[:10]
    rows = db.execute_query(
        """
        SELECT Servico, COUNT(*) AS total FROM Clientes
        WHERE barbearia_id = ? AND Dia >= ? AND Servico IS NOT NULL
        GROUP BY Servico
        ORDER BY total DESC
        LIMIT ?
        """,
        (bid, desde, limite),
        fetch="all",
    ) or []
    return [
        {
            "servico": db.row_get(r, "Servico", index=0) or "Outro",
            "total": int(db.row_get(r, "total", index=1) or 0),
        }
        for r in rows
    ]


def insights_negocio(negocio, evolucao, top_servicos):
    dicas = []
    n = negocio or {}
    series = (evolucao or {}).get("series") or []

    if n.get("agendamentos_hoje", 0) > 0:
        dicas.append(
            {
                "tipo": "sucesso",
                "icone": "fa-calendar-check",
                "texto": f"Hoje você tem {n['agendamentos_hoje']} agendamento(s) — ótimo dia para fidelizar clientes!",
            }
        )

    tend = n.get("tendencia_agendamentos_pct", 0)
    if tend >= 15:
        dicas.append(
            {
                "tipo": "sucesso",
                "icone": "fa-rocket",
                "texto": f"Sua agenda cresceu {tend}% vs. a semana passada. Continue divulgando seu link!",
            }
        )
    elif tend <= -15 and series:
        dicas.append(
            {
                "tipo": "alerta",
                "icone": "fa-bullhorn",
                "texto": f"Agendamentos caíram {abs(tend)}% na última semana. Poste seu link de agendamento no Stories.",
            }
        )

    taxa = n.get("taxa_conclusao_pct", 0)
    if taxa < 60 and n.get("agendamentos_mes", 0) > 5:
        dicas.append(
            {
                "tipo": "alerta",
                "icone": "fa-check-double",
                "texto": "Marque atendimentos como concluídos no painel — isso melhora sua receita e métricas reais.",
            }
        )

    if n.get("receita_mes", 0) > 0 and n.get("tendencia_receita_pct", 0) > 10:
        dicas.append(
            {
                "tipo": "ouro",
                "icone": "fa-coins",
                "texto": f"Receita estimada no mês: R$ {n['receita_mes']:.2f} ({n['tendencia_receita_pct']:+.1f}% vs. semana anterior).",
            }
        )

    if top_servicos:
        top = top_servicos[0]
        dicas.append(
            {
                "tipo": "info",
                "icone": "fa-star",
                "texto": f"Serviço campeão: «{top['servico']}» com {top['total']} reservas em 30 dias.",
            }
        )

    melhor = n.get("melhor_dia_semana")
    if melhor:
        dicas.append(
            {
                "tipo": "info",
                "icone": "fa-chart-simple",
                "texto": f"Seu dia mais forte é {melhor} — considere promoções nos dias mais fracos.",
            }
        )

    if not dicas:
        dicas.append(
            {
                "tipo": "info",
                "icone": "fa-lightbulb",
                "texto": "Compartilhe sua página de agendamento — cada reserva online é um cliente a mais.",
            }
        )
    return dicas[:5]


def insights_plataforma(plat):
    p = plat or {}
    dicas = []
    if p.get("cadastros_7d", 0) == 0:
        dicas.append(
            {
                "tipo": "alerta",
                "icone": "fa-megaphone",
                "texto": "Nenhum cadastro nos últimos 7 dias — invista em anúncios ou indicações na landing.",
            }
        )
    elif p.get("cadastros_7d", 0) >= 3:
        dicas.append(
            {
                "tipo": "sucesso",
                "icone": "fa-user-plus",
                "texto": f"{p['cadastros_7d']} novos negócios esta semana — ritmo excelente de crescimento!",
            }
        )

    trial, ativos = p.get("em_trial", 0), p.get("planos_ativos", 0)
    if trial > ativos and trial > 0:
        dicas.append(
            {
                "tipo": "ouro",
                "icone": "fa-crown",
                "texto": f"Você tem {trial} contas em trial vs. {ativos} pagantes — priorize e-mail e banner de conversão.",
            }
        )

    mrr = p.get("mrr_estimado", 0)
    if ativos > 0:
        dicas.append(
            {
                "tipo": "sucesso",
                "icone": "fa-sack-dollar",
                "texto": f"MRR estimado: R$ {mrr:.2f}/mês com {ativos} assinante(s) ativo(s).",
            }
        )

    conv = p.get("taxa_conversao_pct", 0)
    if conv < 25 and (trial + ativos) > 2:
        dicas.append(
            {
                "tipo": "alerta",
                "icone": "fa-filter",
                "texto": f"Taxa de conversão em {conv}% — teste desconto no 1º mês ou onboarding guiado.",
            }
        )

    if not dicas:
        dicas.append(
            {
                "tipo": "info",
                "icone": "fa-chart-line",
                "texto": "Acompanhe cadastros e checkouts semana a semana para prever receita recorrente.",
            }
        )
    return dicas[:5]


def pacote_graficos_negocio(barbearia_id):
    evo = evolucao_semanal_negocio(barbearia_id)
    return {
        "evolucao": evo,
        "status_pizza": distribuicao_status_negocio(barbearia_id),
        "top_servicos": top_servicos_negocio(barbearia_id),
    }


def pacote_graficos_plataforma():
    return {"evolucao": evolucao_semanal_plataforma()}


def gerar_csv_negocio(barbearia_id, nome_estabelecimento=""):
    evo = evolucao_semanal_negocio(barbearia_id, semanas=12)
    neg = metricas_estabelecimento(barbearia_id)
    dist = distribuicao_status_negocio(barbearia_id)
    tops = top_servicos_negocio(barbearia_id, 10)

    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Relatório AgendaSimples — " + (nome_estabelecimento or "Negócio")])
    w.writerow(["Gerado em", datetime.utcnow().strftime("%Y-%m-%d %H:%M")])
    w.writerow([])
    w.writerow(["Resumo 30 dias"])
    w.writerow(["Agendamentos", neg.get("agendamentos_mes", 0)])
    w.writerow(["Concluídos", neg.get("concluidos_mes", 0)])
    w.writerow(["Clientes únicos", neg.get("clientes_unicos", 0)])
    w.writerow(["Receita estimada R$", f"{neg.get('receita_mes', 0):.2f}"])
    w.writerow(["Taxa conclusão %", neg.get("taxa_conclusao_pct", 0)])
    w.writerow([])
    w.writerow(["Evolução semanal"])
    w.writerow(["Semana", "Agendamentos", "Concluídos", "Receita R$"])
    for row in evo.get("series") or []:
        w.writerow(
            [
                row["label"],
                row["agendamentos"],
                row["concluidos"],
                f"{row['receita']:.2f}",
            ]
        )
    w.writerow([])
    w.writerow(["Status (30 dias)", "Quantidade"])
    for item in dist:
        w.writerow([item["status"], item["total"]])
    w.writerow([])
    w.writerow(["Top serviços", "Reservas"])
    for item in tops:
        w.writerow([item["servico"], item["total"]])
    return buf.getvalue()


def gerar_csv_plataforma():
    plat = metricas_plataforma()
    evo = evolucao_semanal_plataforma(12)

    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Relatório SaaS — AgendaSimples Plataforma"])
    w.writerow(["Gerado em", datetime.utcnow().strftime("%Y-%m-%d %H:%M")])
    w.writerow([])
    w.writerow(["KPI", "Valor"])
    for chave, val in plat.items():
        w.writerow([chave, val])
    w.writerow([])
    w.writerow(["Semana", "Cadastros", "Checkouts"])
    for row in evo.get("series") or []:
        w.writerow([row["label"], row["cadastros"], row["checkouts"]])
    return buf.getvalue()
