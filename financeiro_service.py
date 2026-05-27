"""
Módulo de gestão financeira: comissões, KPIs, fechamento de caixa e consultas tenant-safe.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from database import _coluna_existe, _valor_linha

CATEGORIAS_FINANCEIRAS = ("Serviço", "Produto", "Despesa")
DEFAULT_PCT_SERVICO = 60.0
DEFAULT_PCT_PRODUTO = 10.0


def coluna_financeiro_existe(cursor, coluna: str) -> bool:
    try:
        return _coluna_existe(cursor, "financeiro", coluna)
    except Exception:
        return False


def resolver_categoria_financeira(categoria, descricao, tipo_transacao) -> str:
    cat = (categoria or "").strip()
    if cat in CATEGORIAS_FINANCEIRAS:
        return cat
    if (tipo_transacao or "").strip() == "Despesa":
        return "Despesa"
    desc = (descricao or "").strip().lower()
    if desc.startswith("venda") or "produto" in desc[:24]:
        return "Produto"
    return "Serviço"


def tipo_transacao_de_categoria(categoria: str) -> str:
    return "Despesa" if categoria == "Despesa" else "Receita"


def normalizar_tags(tags_raw: str) -> str:
    if not tags_raw:
        return ""
    partes = []
    for parte in str(tags_raw).replace(";", ",").split(","):
        t = parte.strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = f"#{t}"
        partes.append(t.lower())
    return ",".join(dict.fromkeys(partes))


def parse_tags_list(tags_str: str) -> list[str]:
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


def chave_comissao(barbearia_id: int, tipo: str) -> str:
    return f"fin_pct_{tipo}_{int(barbearia_id)}"


def carregar_comissoes(cursor, barbearia_id: int) -> dict[str, float]:
    """Carrega percentuais; cria padrões 60/10 no banco se ainda não existirem."""
    try:
        from database import garantir_comissoes_defaults

        garantir_comissoes_defaults(cursor, barbearia_id)
    except Exception:
        pass

    pct_servico = DEFAULT_PCT_SERVICO
    pct_produto = DEFAULT_PCT_PRODUTO
    try:
        cursor.execute(
            """
            SELECT chave, valor FROM tb_configuracoes
            WHERE chave IN (?, ?)
            """,
            (chave_comissao(barbearia_id, "servico"), chave_comissao(barbearia_id, "produto")),
        )
        for row in cursor.fetchall() or []:
            k = _valor_linha(row, 0, "chave")
            v = _valor_linha(row, 1, "valor")
            try:
                num = float(str(v).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if k == chave_comissao(barbearia_id, "servico"):
                pct_servico = max(0.0, min(100.0, num))
            elif k == chave_comissao(barbearia_id, "produto"):
                pct_produto = max(0.0, min(100.0, num))
        if pct_servico == DEFAULT_PCT_SERVICO:
            cursor.execute(
                "SELECT valor FROM tb_configuracoes WHERE chave = ? LIMIT 1",
                ("fin_pct_servico_padrao",),
            )
            row_g = cursor.fetchone()
            if row_g:
                try:
                    pct_servico = max(
                        0.0,
                        min(100.0, float(str(_valor_linha(row_g, 0, "valor")).replace(",", "."))),
                    )
                except (TypeError, ValueError):
                    pass
        if pct_produto == DEFAULT_PCT_PRODUTO:
            cursor.execute(
                "SELECT valor FROM tb_configuracoes WHERE chave = ? LIMIT 1",
                ("fin_pct_produto_padrao",),
            )
            row_g = cursor.fetchone()
            if row_g:
                try:
                    pct_produto = max(
                        0.0,
                        min(100.0, float(str(_valor_linha(row_g, 0, "valor")).replace(",", "."))),
                    )
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return {"servico": pct_servico, "produto": pct_produto}


def salvar_comissoes(cursor, barbearia_id: int, pct_servico: float, pct_produto: float) -> None:
    pct_servico = max(0.0, min(100.0, float(pct_servico)))
    pct_produto = max(0.0, min(100.0, float(pct_produto)))
    for chave, valor in (
        (chave_comissao(barbearia_id, "servico"), pct_servico),
        (chave_comissao(barbearia_id, "produto"), pct_produto),
    ):
        cursor.execute("SELECT 1 FROM tb_configuracoes WHERE chave = ?", (chave,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE tb_configuracoes SET valor = ? WHERE chave = ?",
                (str(valor), chave),
            )
        else:
            cursor.execute(
                "INSERT INTO tb_configuracoes (chave, valor) VALUES (?, ?)",
                (chave, str(valor)),
            )


def parse_filtros_request(args) -> dict[str, Any]:
    """Extrai filtros de data e profissional da query string."""
    data_ini = (args.get("data_ini") or "").strip()
    data_fim = (args.get("data_fim") or "").strip()
    if not data_ini and not data_fim:
        hoje = datetime.now().date()
        primeiro = hoje.replace(day=1)
        data_ini = primeiro.isoformat()
        data_fim = hoje.isoformat()
    prof_raw = (args.get("profissional_id") or "").strip()
    profissional_id = None
    if prof_raw:
        try:
            profissional_id = int(prof_raw)
        except (TypeError, ValueError):
            profissional_id = None
    return {
        "data_ini": data_ini,
        "data_fim": data_fim,
        "profissional_id": profissional_id,
    }


def _select_colunas_financeiro(cursor) -> tuple[str, str]:
    cat_sel = (
        "f.categoria"
        if coluna_financeiro_existe(cursor, "categoria")
        else "NULL AS categoria"
    )
    tags_sel = (
        "f.tags"
        if coluna_financeiro_existe(cursor, "tags")
        else "NULL AS tags"
    )
    return cat_sel, tags_sel


def formatar_transacao_financeira(row) -> dict[str, Any]:
    descricao = _valor_linha(row, 0) if row is not None else ""
    valor = float(_valor_linha(row, 1) or 0)
    tipo_transacao = _valor_linha(row, 2) or "Receita"
    profissional = _valor_linha(row, 3) or "Geral / Estabelecimento"
    data = _valor_linha(row, 4)
    categoria_raw = _valor_linha(row, 6, "categoria")
    tags_raw = _valor_linha(row, 7, "tags")
    categoria = resolver_categoria_financeira(
        categoria_raw, descricao, tipo_transacao
    )
    return {
        "descricao": descricao,
        "valor": valor,
        "tipo_transacao": tipo_transacao,
        "profissional": profissional,
        "data": data,
        "profissional_id": _valor_linha(row, 5, "profissional_id"),
        "categoria": categoria,
        "tags": tags_raw or "",
        "tags_lista": parse_tags_list(tags_raw or ""),
    }


def buscar_transacoes_financeiro(
    cursor,
    barbearia_id: int,
    profissional_id: Optional[int] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> list[dict[str, Any]]:
    """SELECT tenant-safe com filtros opcionais de profissional e período."""
    cat_sel, tags_sel = _select_colunas_financeiro(cursor)
    sql = f"""
        SELECT f.descricao, f.valor, f.tipo_transacao,
               COALESCE(u.nome, f.barbeiro, 'Geral / Estabelecimento') AS profissional,
               f.data, f.profissional_id, {cat_sel}, {tags_sel}
        FROM financeiro f
        LEFT JOIN usuarios u ON f.profissional_id = u.id
        WHERE f.barbearia_id = ?
    """
    params: list[Any] = [int(barbearia_id)]
    if profissional_id:
        sql += " AND f.profissional_id = ?"
        params.append(int(profissional_id))
    if data_ini:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) >= ?"
        params.append(data_ini[:10])
    if data_fim:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) <= ?"
        params.append(data_fim[:10])
    sql += " ORDER BY f.data DESC"
    cursor.execute(sql, tuple(params))
    return [formatar_transacao_financeira(r) for r in cursor.fetchall()]


def calcular_comissao_item(valor: float, categoria: str, comissoes: dict[str, float]) -> float:
    if categoria == "Produto":
        pct = comissoes.get("produto", DEFAULT_PCT_PRODUTO)
    elif categoria == "Serviço":
        pct = comissoes.get("servico", DEFAULT_PCT_SERVICO)
    else:
        return 0.0
    return round(float(valor) * pct / 100.0, 2)


def calcular_kpis(transacoes: list[dict], comissoes: dict[str, float]) -> dict[str, float]:
    total_receitas = 0.0
    total_despesas = 0.0
    comissoes_a_pagar = 0.0
    for t in transacoes:
        valor = float(t.get("valor", 0))
        if t.get("tipo_transacao") == "Receita":
            total_receitas += valor
            comissoes_a_pagar += calcular_comissao_item(
                valor, t.get("categoria", "Serviço"), comissoes
            )
        else:
            total_despesas += valor
    lucro_liquido = total_receitas - total_despesas - comissoes_a_pagar
    return {
        "total_receitas": round(total_receitas, 2),
        "total_despesas": round(total_despesas, 2),
        "comissoes_a_pagar": round(comissoes_a_pagar, 2),
        "lucro_liquido": round(lucro_liquido, 2),
        "saldo_caixa": round(total_receitas - total_despesas, 2),
    }


def calcular_fechamento_caixa(
    transacoes: list[dict], comissoes: dict[str, float], profissional_id: Optional[int] = None
) -> dict[str, Any]:
    """Fechamento por profissional: bruto, comissões e líquido a pagar."""
    filtradas = transacoes
    if profissional_id:
        filtradas = [
            t
            for t in transacoes
            if t.get("profissional_id") == profissional_id
            and t.get("tipo_transacao") == "Receita"
        ]
    else:
        filtradas = [t for t in transacoes if t.get("tipo_transacao") == "Receita"]

    bruto_servicos = 0.0
    bruto_produtos = 0.0
    com_servicos = 0.0
    com_produtos = 0.0
    for t in filtradas:
        valor = float(t.get("valor", 0))
        cat = t.get("categoria", "Serviço")
        if cat == "Produto":
            bruto_produtos += valor
            com_produtos += calcular_comissao_item(valor, cat, comissoes)
        else:
            bruto_servicos += valor
            com_servicos += calcular_comissao_item(valor, "Serviço", comissoes)

    total_bruto = bruto_servicos + bruto_produtos
    total_comissao = com_servicos + com_produtos
    return {
        "total_bruto": round(total_bruto, 2),
        "bruto_servicos": round(bruto_servicos, 2),
        "bruto_produtos": round(bruto_produtos, 2),
        "comissao_servicos": round(com_servicos, 2),
        "comissao_produtos": round(com_produtos, 2),
        "total_comissao": round(total_comissao, 2),
        "liquido_a_pagar": round(total_comissao, 2),
        "retencao_estabelecimento": round(total_bruto - total_comissao, 2),
        "pct_servico": comissoes.get("servico", DEFAULT_PCT_SERVICO),
        "pct_produto": comissoes.get("produto", DEFAULT_PCT_PRODUTO),
    }


def buscar_faturamento_detalhado_profissionais(
    cursor, barbearia_id: int, data_ini: Optional[str] = None, data_fim: Optional[str] = None
) -> list[dict[str, Any]]:
    cat_sel = (
        "f.categoria"
        if coluna_financeiro_existe(cursor, "categoria")
        else "NULL AS categoria"
    )
    sql = f"""
        SELECT u.id, u.nome, f.descricao, f.valor, f.tipo_transacao, {cat_sel}
        FROM financeiro f
        INNER JOIN usuarios u ON f.profissional_id = u.id
        WHERE f.barbearia_id = ? AND f.tipo_transacao = 'Receita'
    """
    params: list[Any] = [int(barbearia_id)]
    if data_ini:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) >= ?"
        params.append(data_ini[:10])
    if data_fim:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) <= ?"
        params.append(data_fim[:10])
    cursor.execute(sql, tuple(params))
    comissoes_cfg = carregar_comissoes(cursor, barbearia_id)
    agregado: dict[int, dict] = {}
    for row in cursor.fetchall() or []:
        uid = _valor_linha(row, 0, "id")
        nome = _valor_linha(row, 1, "nome")
        cat = resolver_categoria_financeira(
            _valor_linha(row, 5, "categoria"),
            _valor_linha(row, 2, "descricao"),
            _valor_linha(row, 4, "tipo_transacao"),
        )
        valor = float(_valor_linha(row, 3, "valor") or 0)
        if uid not in agregado:
            agregado[uid] = {
                "id": uid,
                "nome": nome,
                "total_servicos": 0.0,
                "total_produtos": 0.0,
                "total": 0.0,
                "comissao_servicos": 0.0,
                "comissao_produtos": 0.0,
                "comissao_total": 0.0,
            }
        if cat == "Produto":
            agregado[uid]["total_produtos"] += valor
            agregado[uid]["comissao_produtos"] += calcular_comissao_item(
                valor, cat, comissoes_cfg
            )
        else:
            agregado[uid]["total_servicos"] += valor
            agregado[uid]["comissao_servicos"] += calcular_comissao_item(
                valor, "Serviço", comissoes_cfg
            )
        agregado[uid]["total"] += valor
        agregado[uid]["comissao_total"] = (
            agregado[uid]["comissao_servicos"] + agregado[uid]["comissao_produtos"]
        )
    return sorted(agregado.values(), key=lambda x: -x["total"])


def faturamento_diario_para_grafico(
    cursor,
    barbearia_id: int,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> dict[str, list]:
    """Labels e valores para Chart.js (receitas por dia)."""
    sql = """
        SELECT substr(COALESCE(f.data, ''), 1, 10) AS dia, SUM(f.valor) AS total
        FROM financeiro f
        WHERE f.barbearia_id = ? AND f.tipo_transacao = 'Receita'
    """
    params: list[Any] = [int(barbearia_id)]
    if data_ini:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) >= ?"
        params.append(data_ini[:10])
    if data_fim:
        sql += " AND substr(COALESCE(f.data, ''), 1, 10) <= ?"
        params.append(data_fim[:10])
    sql += " GROUP BY dia ORDER BY dia"
    cursor.execute(sql, tuple(params))
    labels = []
    valores = []
    for row in cursor.fetchall() or []:
        dia = _valor_linha(row, 0, "dia") or ""
        if not dia:
            continue
        labels.append(dia[8:10] + "/" + dia[5:7] if len(dia) >= 10 else dia)
        valores.append(round(float(_valor_linha(row, 1, "total") or 0), 2))
    return {"labels": labels, "valores": valores}


def preview_split_lancamento(valor: float, categoria: str, comissoes: dict[str, float]) -> dict[str, float]:
    """Split sugerido para o formulário inteligente."""
    valor = float(valor or 0)
    comissao = calcular_comissao_item(valor, categoria, comissoes)
    return {
        "valor_bruto": round(valor, 2),
        "comissao_profissional": comissao,
        "retencao_estabelecimento": round(valor - comissao, 2),
        "percentual_aplicado": (
            comissoes.get("produto", DEFAULT_PCT_PRODUTO)
            if categoria == "Produto"
            else comissoes.get("servico", DEFAULT_PCT_SERVICO)
        ),
    }
