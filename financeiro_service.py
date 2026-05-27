"""
Módulo de gestão financeira: comissões, KPIs, fechamento de caixa e consultas tenant-safe.
Todas as queries passam por db_adapter.execute_query (SQLite/Turso ou SQL Server).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import db_adapter as db
from database import _valor_linha, garantir_comissoes_defaults

CATEGORIAS_FINANCEIRAS = ("Serviço", "Produto", "Despesa")
DEFAULT_PCT_SERVICO = 60.0
DEFAULT_PCT_PRODUTO = 10.0

_COLUNAS_FIN_CACHE: dict[str, bool] = {}


def coluna_financeiro_existe(coluna: str, *, conn=None) -> bool:
    if coluna in _COLUNAS_FIN_CACHE:
        return _COLUNAS_FIN_CACHE[coluna]
    try:
        ok = db.coluna_existe("financeiro", coluna, conn=conn)
    except Exception:
        ok = False
    _COLUNAS_FIN_CACHE[coluna] = ok
    return ok


def _expr_profissional_financeiro() -> str:
    """Nome exibido do profissional — só referencia colunas que existem no banco."""
    partes = []
    if coluna_financeiro_existe("profissional_id"):
        partes.append("u.nome")
    if coluna_financeiro_existe("barbeiro"):
        partes.append("f.barbeiro")
    if not partes:
        return "'Geral / Estabelecimento'"
    expr = "COALESCE(" + ", ".join(partes) + ", 'Geral / Estabelecimento')"
    return expr


def _filtro_data_sql(alias: str = "f") -> str:
    return db.expr_data_yyyy_mm_dd(f"{alias}.data")


def limpar_cache_colunas_financeiro() -> None:
    """Após migração do schema, recarrega detecção de colunas."""
    _COLUNAS_FIN_CACHE.clear()


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


def _row_get(row: Any, *keys, index: int = 0, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        for k in keys:
            if k and k in row:
                return row[k]
        vals = list(row.values())
        if index < len(vals):
            return vals[index]
        return default
    return _valor_linha(row, index, keys[0] if keys else None) or default


def carregar_comissoes(barbearia_id: int, *, conn=None) -> dict[str, float]:
    """Carrega percentuais; cria padrões 60/10 no banco se ainda não existirem."""
    if conn is not None:
        garantir_comissoes_defaults(conn.cursor(), barbearia_id)
    else:
        db.ensure_financeiro_schema()

    pct_servico = DEFAULT_PCT_SERVICO
    pct_produto = DEFAULT_PCT_PRODUTO
    try:
        rows = db.execute_query(
            """
            SELECT chave, valor FROM tb_configuracoes
            WHERE chave IN (?, ?)
            """,
            (chave_comissao(barbearia_id, "servico"), chave_comissao(barbearia_id, "produto")),
            conn=conn,
        )
        for row in rows or []:
            k = _row_get(row, "chave", index=0)
            v = _row_get(row, "valor", index=1)
            try:
                num = float(str(v).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if k == chave_comissao(barbearia_id, "servico"):
                pct_servico = max(0.0, min(100.0, num))
            elif k == chave_comissao(barbearia_id, "produto"):
                pct_produto = max(0.0, min(100.0, num))
        if pct_servico == DEFAULT_PCT_SERVICO:
            row_g = db.execute_query(
                "SELECT valor FROM tb_configuracoes WHERE chave = ? LIMIT 1",
                ("fin_pct_servico_padrao",),
                fetch="one",
                conn=conn,
            )
            if row_g:
                try:
                    pct_servico = max(
                        0.0,
                        min(
                            100.0,
                            float(str(_row_get(row_g, "valor", index=0)).replace(",", ".")),
                        ),
                    )
                except (TypeError, ValueError):
                    pass
        if pct_produto == DEFAULT_PCT_PRODUTO:
            row_g = db.execute_query(
                "SELECT valor FROM tb_configuracoes WHERE chave = ? LIMIT 1",
                ("fin_pct_produto_padrao",),
                fetch="one",
                conn=conn,
            )
            if row_g:
                try:
                    pct_produto = max(
                        0.0,
                        min(
                            100.0,
                            float(str(_row_get(row_g, "valor", index=0)).replace(",", ".")),
                        ),
                    )
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return {"servico": pct_servico, "produto": pct_produto}


def salvar_comissoes(
    barbearia_id: int, pct_servico: float, pct_produto: float, *, conn=None
) -> None:
    pct_servico = max(0.0, min(100.0, float(pct_servico)))
    pct_produto = max(0.0, min(100.0, float(pct_produto)))
    for chave, valor in (
        (chave_comissao(barbearia_id, "servico"), pct_servico),
        (chave_comissao(barbearia_id, "produto"), pct_produto),
    ):
        exists = db.execute_query(
            "SELECT 1 FROM tb_configuracoes WHERE chave = ? LIMIT 1",
            (chave,),
            fetch="one",
            conn=conn,
        )
        if exists:
            db.execute_write(
                "UPDATE tb_configuracoes SET valor = ? WHERE chave = ?",
                (str(valor), chave),
                conn=conn,
            )
        else:
            db.execute_write(
                "INSERT INTO tb_configuracoes (chave, valor) VALUES (?, ?)",
                (chave, str(valor)),
                conn=conn,
            )


def parse_filtros_request(args) -> dict[str, Any]:
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


def _select_colunas_financeiro() -> tuple[str, str, str, str]:
    cat_sel = "f.categoria" if coluna_financeiro_existe("categoria") else "NULL AS categoria"
    tags_sel = "f.tags" if coluna_financeiro_existe("tags") else "NULL AS tags"
    prof_sel = (
        "f.profissional_id"
        if coluna_financeiro_existe("profissional_id")
        else "NULL AS profissional_id"
    )
    data_sel = "f.data" if coluna_financeiro_existe("data") else "NULL AS data"
    return cat_sel, tags_sel, prof_sel, data_sel


def formatar_transacao_financeira(row) -> dict[str, Any]:
    descricao = _row_get(row, "descricao", index=0, default="")
    valor = float(_row_get(row, "valor", index=1, default=0) or 0)
    tipo_transacao = _row_get(row, "tipo_transacao", index=2, default="Receita") or "Receita"
    profissional = _row_get(row, "profissional", index=3, default="Geral / Estabelecimento")
    data = _row_get(row, "data", index=4)
    categoria_raw = _row_get(row, "categoria", index=6)
    tags_raw = _row_get(row, "tags", index=7)
    categoria = resolver_categoria_financeira(categoria_raw, descricao, tipo_transacao)
    return {
        "descricao": descricao,
        "valor": valor,
        "tipo_transacao": tipo_transacao,
        "profissional": profissional or "Geral / Estabelecimento",
        "data": data,
        "profissional_id": _row_get(row, "profissional_id", index=5),
        "categoria": categoria,
        "tags": tags_raw or "",
        "tags_lista": parse_tags_list(tags_raw or ""),
    }


def buscar_transacoes_financeiro(
    barbearia_id: int,
    profissional_id: Optional[int] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    *,
    conn=None,
) -> list[dict[str, Any]]:
    cat_sel, tags_sel, prof_sel, data_sel = _select_colunas_financeiro()
    prof_expr = _expr_profissional_financeiro()
    join_usuarios = (
        "LEFT JOIN usuarios u ON f.profissional_id = u.id"
        if coluna_financeiro_existe("profissional_id")
        else ""
    )
    sql = f"""
        SELECT f.descricao, f.valor, f.tipo_transacao,
               {prof_expr} AS profissional,
               {data_sel}, {prof_sel}, {cat_sel}, {tags_sel}
        FROM financeiro f
        {join_usuarios}
        WHERE f.barbearia_id = ?
    """
    params: list[Any] = [int(barbearia_id)]
    if profissional_id and coluna_financeiro_existe("profissional_id"):
        sql += " AND f.profissional_id = ?"
        params.append(int(profissional_id))
    dpart = _filtro_data_sql("f")
    if data_ini and coluna_financeiro_existe("data"):
        sql += f" AND {dpart} >= ?"
        params.append(data_ini[:10])
    if data_fim and coluna_financeiro_existe("data"):
        sql += f" AND {dpart} <= ?"
        params.append(data_fim[:10])
    if coluna_financeiro_existe("data"):
        sql += " ORDER BY f.data DESC"
    else:
        sql += " ORDER BY f.id DESC"
    rows = db.execute_query(sql, tuple(params), conn=conn) or []
    return [formatar_transacao_financeira(r) for r in rows]


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
    barbearia_id: int,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    *,
    conn=None,
) -> list[dict[str, Any]]:
    if not coluna_financeiro_existe("profissional_id"):
        return []

    cat_sel = (
        "f.categoria" if coluna_financeiro_existe("categoria") else "NULL AS categoria"
    )
    sql = f"""
        SELECT u.id, u.nome, f.descricao, f.valor, f.tipo_transacao, {cat_sel}
        FROM financeiro f
        INNER JOIN usuarios u ON f.profissional_id = u.id
        WHERE f.barbearia_id = ? AND f.tipo_transacao = 'Receita'
    """
    params: list[Any] = [int(barbearia_id)]
    if coluna_financeiro_existe("data"):
        dpart = _filtro_data_sql("f")
        if data_ini:
            sql += f" AND {dpart} >= ?"
            params.append(data_ini[:10])
        if data_fim:
            sql += f" AND {dpart} <= ?"
            params.append(data_fim[:10])
    try:
        rows = db.execute_query(sql, tuple(params), conn=conn) or []
    except Exception:
        return []
    comissoes_cfg = carregar_comissoes(barbearia_id, conn=conn)
    agregado: dict[int, dict] = {}
    for row in rows or []:
        uid = _row_get(row, "id", index=0)
        nome = _row_get(row, "nome", index=1)
        cat = resolver_categoria_financeira(
            _row_get(row, "categoria", index=5),
            _row_get(row, "descricao", index=2),
            _row_get(row, "tipo_transacao", index=4),
        )
        valor = float(_row_get(row, "valor", index=3) or 0)
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


def _grafico_a_partir_transacoes(transacoes: list[dict]) -> dict[str, list]:
    """Fallback: agrega receitas por dia em Python (Turso/SQLite legado)."""
    from collections import defaultdict

    por_dia: dict[str, float] = defaultdict(float)
    for t in transacoes:
        if t.get("tipo_transacao") != "Receita":
            continue
        raw = t.get("data")
        if not raw:
            continue
        dia_str = str(raw)[:10]
        if len(dia_str) < 8:
            continue
        por_dia[dia_str] += float(t.get("valor") or 0)
    labels, valores = [], []
    for dia in sorted(por_dia.keys()):
        labels.append(dia[8:10] + "/" + dia[5:7] if len(dia) >= 10 else dia)
        valores.append(round(por_dia[dia], 2))
    return {"labels": labels, "valores": valores}


def faturamento_diario_para_grafico(
    barbearia_id: int,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    *,
    conn=None,
    transacoes_cache: Optional[list[dict]] = None,
) -> dict[str, list]:
    if not coluna_financeiro_existe("data"):
        if transacoes_cache is not None:
            return _grafico_a_partir_transacoes(transacoes_cache)
        txs = buscar_transacoes_financeiro(
            barbearia_id, None, data_ini, data_fim, conn=conn
        )
        return _grafico_a_partir_transacoes(txs)

    dpart = _filtro_data_sql("f")
    sql = f"""
        SELECT {dpart} AS dia, SUM(f.valor) AS total
        FROM financeiro f
        WHERE f.barbearia_id = ? AND f.tipo_transacao = 'Receita'
    """
    params: list[Any] = [int(barbearia_id)]
    if data_ini:
        sql += f" AND {dpart} >= ?"
        params.append(data_ini[:10])
    if data_fim:
        sql += f" AND {dpart} <= ?"
        params.append(data_fim[:10])
    sql += f" GROUP BY {dpart} ORDER BY {dpart}"
    try:
        rows = db.execute_query(sql, tuple(params), conn=conn) or []
    except Exception:
        if transacoes_cache is not None:
            return _grafico_a_partir_transacoes(transacoes_cache)
        txs = buscar_transacoes_financeiro(
            barbearia_id, None, data_ini, data_fim, conn=conn
        )
        return _grafico_a_partir_transacoes(txs)

    labels = []
    valores = []
    for row in rows:
        dia = _row_get(row, "dia", index=0) or ""
        if not dia:
            continue
        dia_str = str(dia)[:10]
        labels.append(dia_str[8:10] + "/" + dia_str[5:7] if len(dia_str) >= 10 else dia_str)
        valores.append(round(float(_row_get(row, "total", index=1) or 0), 2))
    return {"labels": labels, "valores": valores}


def preview_split_lancamento(valor: float, categoria: str, comissoes: dict[str, float]) -> dict[str, float]:
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


def listar_profissionais(barbearia_id: int, *, conn=None) -> list[tuple]:
    rows = db.execute_query(
        """
        SELECT id, nome FROM usuarios
        WHERE barbearia_id = ? AND role IN ('barbeiro', 'profissional')
        ORDER BY nome
        """,
        (barbearia_id,),
        conn=conn,
    )
    return [(_row_get(r, "id", index=0), _row_get(r, "nome", index=1)) for r in rows]


def profissional_pertence_barbearia(
    profissional_id: int, barbearia_id: int, *, conn=None
) -> bool:
    row = db.execute_query(
        """
        SELECT id FROM usuarios
        WHERE id = ? AND barbearia_id = ?
          AND role IN ('barbeiro', 'profissional', 'admin')
        LIMIT 1
        """,
        (profissional_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    return row is not None


def nome_profissional_lancamento(
    profissional_id: Optional[int], barbearia_id: int, *, conn=None
) -> tuple[Optional[str], Optional[int]]:
    if not profissional_id:
        return None, None
    row = db.execute_query(
        """
        SELECT nome FROM usuarios
        WHERE id = ? AND barbearia_id = ?
        LIMIT 1
        """,
        (profissional_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None, None
    return _row_get(row, "nome", index=0), profissional_id


def inserir_lancamento_financeiro(
    barbearia_id: int,
    descricao: str,
    valor_num: float,
    tipo: str,
    categoria: str,
    nome_profissional: Optional[str],
    profissional_id: Optional[int],
    data_lanc: str,
    tags: str = "",
    *,
    conn=None,
) -> None:
    cols = [
        "descricao",
        "valor",
        "tipo_transacao",
        "barbeiro",
        "profissional_id",
        "barbearia_id",
        "data",
    ]
    vals = [
        descricao,
        valor_num,
        tipo,
        nome_profissional,
        profissional_id,
        int(barbearia_id),
        data_lanc,
    ]
    if coluna_financeiro_existe("categoria"):
        cols.append("categoria")
        vals.append(categoria)
    if tags and coluna_financeiro_existe("tags"):
        cols.append("tags")
        vals.append(tags)
    if categoria == "Produto" and coluna_financeiro_existe("produto"):
        cols.append("produto")
        vals.append(descricao[:500])
    elif coluna_financeiro_existe("servico"):
        cols.append("servico")
        vals.append(descricao[:500])
    placeholders = ", ".join("?" for _ in vals)
    sql = f"INSERT INTO financeiro ({', '.join(cols)}) VALUES ({placeholders})"
    db.execute_write(sql, tuple(vals), conn=conn)
