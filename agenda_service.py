"""Agenda e agendamentos — queries via db_adapter."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import db_adapter as db
from database import _valor_linha, obter_id_inserido

import financeiro_service as fin

HORARIOS_PADRAO = (
    "08:00", "09:00", "10:00", "11:00",
    "12:00", "13:00", "14:00", "15:00",
    "16:00", "17:00", "18:00", "19:00", "20:00",
)


def ifnull_status(alias: str = "c") -> str:
    col = f"{alias}.status" if alias else "status"
    if db.get_backend() == "sqlserver":
        return f"ISNULL({col}, 'Agendado')"
    return f"IFNULL({col}, 'Agendado')"


def listar_horarios(barbearia_id: int, *, conn=None) -> list[str]:
    rows = db.execute_query(
        """
        SELECT hora FROM horarios
        WHERE barbearia_id = ? AND IFNULL(ativo, 1) = 1
        ORDER BY ordem, hora
        """,
        (barbearia_id,),
        conn=conn,
    )
    if rows:
        return [str(db.row_get(r, "hora", index=0) or "") for r in rows]
    return list(HORARIOS_PADRAO)


def listar_registros_agenda(
    barbearia_id: int,
    user_id: Optional[int],
    filtrar_meus: bool,
    *,
    conn=None,
) -> list[dict[str, Any]]:
    status_expr = ifnull_status()
    sql = f"""
        SELECT c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp, u.nome AS barbeiro_nome,
               c.barbeiro_id, {status_expr} AS status
        FROM Clientes c
        INNER JOIN usuarios u ON c.barbeiro_id = u.id
        WHERE c.barbearia_id = ?
          AND {status_expr} <> 'Concluído'
    """
    params: list[Any] = [int(barbearia_id)]
    if filtrar_meus and user_id:
        sql += " AND c.barbeiro_id = ?"
        params.append(int(user_id))
    sql += " ORDER BY c.Dia, c.Hora"
    rows = db.execute_query(sql, tuple(params), conn=conn)
    result = []
    for r in rows or []:
        result.append(
            {
                "Nome": db.row_get(r, "Nome", index=0),
                "Dia": db.row_get(r, "Dia", index=1),
                "Hora": db.row_get(r, "Hora", index=2),
                "Servico": db.row_get(r, "Servico", index=3),
                "Whatsapp": db.row_get(r, "Whatsapp", index=4),
                "barbeiro_nome": db.row_get(r, "barbeiro_nome", index=5),
                "barbeiro_id": db.row_get(r, "barbeiro_id", index=6),
                "status": db.row_get(r, "status", index=7),
            }
        )
    return result


def profissional_pertence_barbearia(
    profissional_id: int, barbearia_id: int, *, conn=None
) -> bool:
    return fin.profissional_pertence_barbearia(profissional_id, barbearia_id, conn=conn)


def buscar_agendamento_slot(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> Optional[dict]:
    status_expr = ifnull_status(alias="")
    row = db.execute_query(
        f"""
        SELECT Nome, Servico, {status_expr} AS status
        FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        LIMIT 1
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "Nome": db.row_get(row, "Nome", index=0),
        "Servico": db.row_get(row, "Servico", index=1),
        "status": db.row_get(row, "status", index=2),
    }


def marcar_agendamento_concluido(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> None:
    db.execute_write(
        """
        UPDATE Clientes
        SET status = 'Concluído'
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        conn=conn,
    )


def id_agendamento_slot(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn
) -> Optional[int]:
    return obter_id_inserido(
        db.cursor(conn),
        """
        SELECT id FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (dia, hora, barbeiro_id, barbearia_id),
    )


def horario_ocupado(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> bool:
    status_expr = ifnull_status(alias="")
    row = db.execute_query(
        f"""
        SELECT COUNT(*) AS total FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
          AND {status_expr} <> 'Concluído'
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    total = db.row_get(row, "total", index=0) if row else 0
    return int(total or 0) > 0


def inserir_agendamento(
    nome: str,
    dia: str,
    hora: str,
    servico: str,
    whatsapp: str,
    barbeiro_id: int,
    barbearia_id: int,
    valor: float = 0.0,
    *,
    conn,
) -> Optional[int]:
    valor_gravado = float(valor if valor is not None else 0)
    cur = db.cursor(conn)
    cur.execute(
        """
        INSERT INTO Clientes (
            Nome, Dia, Hora, Servico, Whatsapp,
            barbeiro_id, barbearia_id, status, valor
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Agendado', ?)
        """,
        (
            nome,
            dia,
            hora,
            servico,
            whatsapp,
            barbeiro_id,
            barbearia_id,
            valor_gravado,
        ),
    )
    lid = cur.lastrowid
    if lid not in (None, 0):
        try:
            return int(lid)
        except (TypeError, ValueError):
            pass
    return obter_id_inserido(
        cur,
        """
        SELECT id FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (dia, hora, barbeiro_id, barbearia_id),
    )


def buscar_agendamento_por_id(agendamento_id: int, *, conn=None) -> Optional[dict]:
    row = db.execute_query(
        """
        SELECT c.id, c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp,
               c.barbeiro_id, u.nome AS profissional_nome,
               c.barbearia_id, b.slug AS barbearia_slug
        FROM Clientes c
        LEFT JOIN usuarios u ON c.barbeiro_id = u.id
        LEFT JOIN barbearias b ON c.barbearia_id = b.id
        WHERE c.id = ?
        """,
        (agendamento_id,),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None

    dia = db.row_get(row, "Dia", index=2)
    if hasattr(dia, "strftime"):
        data_fmt = dia.strftime("%Y-%m-%d")
        data_exib = dia.strftime("%d/%m/%Y")
    else:
        data_fmt = str(dia)[:10]
        data_exib = data_fmt

    hora = str(db.row_get(row, "Hora", index=3) or "")[:5]

    return {
        "id": db.row_get(row, "id", index=0),
        "nome": db.row_get(row, "Nome", index=1),
        "data": data_fmt,
        "data_exib": data_exib,
        "hora": hora,
        "servico": db.row_get(row, "Servico", index=4),
        "whatsapp": db.row_get(row, "Whatsapp", index=5) or "",
        "barbeiro_id": db.row_get(row, "barbeiro_id", index=6),
        "profissional": db.row_get(row, "profissional_nome", index=7) or "—",
        "barbearia_id": db.row_get(row, "barbearia_id", index=8),
        "barbearia_slug": (db.row_get(row, "barbearia_slug", index=9) or "").strip(),
    }


def buscar_cliente_slot(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> Optional[dict]:
    row = db.execute_query(
        """
        SELECT Nome, Servico, Whatsapp FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        LIMIT 1
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "Nome": db.row_get(row, "Nome", index=0),
        "Servico": db.row_get(row, "Servico", index=1),
        "Whatsapp": db.row_get(row, "Whatsapp", index=2),
    }


def atualizar_cliente_slot(
    nome: str,
    servico: str,
    whatsapp: str,
    dia: str,
    hora: str,
    barbeiro_id: int,
    barbearia_id: int,
    *,
    conn=None,
) -> None:
    db.execute_write(
        """
        UPDATE Clientes SET Nome = ?, Servico = ?, Whatsapp = ?
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        """,
        (nome, servico, whatsapp, dia, hora, barbeiro_id, barbearia_id),
        conn=conn,
    )


def excluir_cliente_slot(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> None:
    db.execute_write(
        """
        DELETE FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        conn=conn,
    )


def buscar_whatsapp_cliente_slot(
    dia: str, hora: str, barbeiro_id: int, barbearia_id: int, *, conn=None
) -> Optional[tuple]:
    row = db.execute_query(
        """
        SELECT Nome, Whatsapp FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        LIMIT 1
        """,
        (dia, hora, barbeiro_id, barbearia_id),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return (db.row_get(row, "Nome", index=0), db.row_get(row, "Whatsapp", index=1))


def listar_agendamentos_export(barbearia_id: int, *, conn=None) -> list:
    rows = db.execute_query(
        "SELECT Nome, Dia, Hora, Servico FROM Clientes WHERE barbearia_id = ?",
        (barbearia_id,),
        conn=conn,
    )
    return [
        (
            db.row_get(r, "Nome", index=0),
            db.row_get(r, "Dia", index=1),
            db.row_get(r, "Hora", index=2),
            db.row_get(r, "Servico", index=3),
        )
        for r in rows or []
    ]


def listar_agendamentos_dia_pdf(
    data: str, barbearia_id: int, *, conn=None
) -> list:
    rows = db.execute_query(
        """
        SELECT c.Nome, c.Hora, c.Servico, u.nome
        FROM Clientes c
        JOIN usuarios u ON c.barbeiro_id = u.id
        WHERE c.Dia = ? AND c.barbearia_id = ?
        ORDER BY c.Hora
        """,
        (data, barbearia_id),
        conn=conn,
    )
    return [
        (
            db.row_get(r, index=0),
            db.row_get(r, index=1),
            db.row_get(r, index=2),
            db.row_get(r, index=3),
        )
        for r in rows or []
    ]


def obter_preco_servico(barbearia_id: int, nome_servico: str, *, conn=None) -> float:
    nome = (nome_servico or "").strip()
    if not nome:
        return 0.0
    if db.coluna_existe("servicos", "preco", conn=conn):
        row = db.execute_query(
            """
            SELECT IFNULL(preco, 0) FROM servicos
            WHERE barbearia_id = ? AND nome = ? AND IFNULL(ativo, 1) = 1
            LIMIT 1
            """,
            (barbearia_id, nome),
            fetch="one",
            conn=conn,
        )
        if row:
            val = db.row_get(row, index=0)
            if val and float(val or 0) > 0:
                return float(val)
    padroes = {
        "Corte": 50.0,
        "Corte e Barba": 70.0,
        "Corte + Sobrancelha": 55.0,
        "Barba": 35.0,
        "Outro": 0.0,
    }
    return float(padroes.get(nome, 0.0))
