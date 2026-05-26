from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from urllib.parse import quote
from datetime import datetime, timedelta

# Conexão: Turso HTTP (TURSO_DATABASE_URL + TURSO_AUTH_TOKEN) ou SQLite local (agenda.db)
from database import (
    DbError,
    SERVICOS_PADRAO,
    _coluna_existe,
    ensure_schema_migrations,
    get_connection,
    init_database,
    obter_id_inserido,
    safe_close,
    safe_commit,
    safe_rollback,
    seed_servicos_horarios_padrao,
)
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from subscriptions import criar_assinatura_trial, requer_assinatura_ativa
from stripe_payments import register_stripe_routes
import config_saas as cfg
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
import socket
import io
import re
import os
import secrets
import traceback

# 1. IMPORTAR O FLASK-BABEL
from flask_babel import Babel, _

app = Flask(__name__)
app.secret_key = "CHRISTIAN_BARBESHOP_KEY_2025"

# 2. CONFIGURAR O FLASK-BABEL (PT-BR, EN-US, ES-ES)
app.config["BABEL_DEFAULT_LOCALE"] = "pt"
app.config["BABEL_SUPPORTED_LOCALES"] = ["pt", "en", "es"]
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"


def get_locale():
    """Prioridade: sessão do usuário → idioma do navegador → português."""
    lang = session.get("lang")
    if lang in app.config["BABEL_SUPPORTED_LOCALES"]:
        return lang
    match = request.accept_languages.best_match(app.config["BABEL_SUPPORTED_LOCALES"])
    return match or app.config["BABEL_DEFAULT_LOCALE"]


babel = Babel(app, locale_selector=get_locale)


@app.context_processor
def inject_i18n():
    return {
        "current_locale": get_locale(),
        "supported_locales": app.config["BABEL_SUPPORTED_LOCALES"],
    }

HORARIOS = [
    "08:00", "09:00", "10:00", "11:00",
    "12:00", "13:00", "14:00", "15:00",
    "16:00", "17:00", "18:00", "19:00",
    "20:00"
]

DIAS_PT = {
    "Monday": "Segunda-feira",
    "Tuesday": "Terça-feira",
    "Wednesday": "Quarta-feira",
    "Thursday": "Quinta-feira",
    "Friday": "Sexta-feira",
    "Saturday": "Sábado",
    "Sunday": "Domingo"
}

# -------------------------- RECUPERAÇÃO DE SENHA --------------------------
_RESET_TOKEN_MAX_AGE = 30 * 60  # 30 minutos
_reset_serializer = None


def _get_reset_serializer():
    global _reset_serializer
    if _reset_serializer is None:
        _reset_serializer = URLSafeTimedSerializer(
            app.secret_key, salt="password-reset-v1"
        )
    return _reset_serializer


def _gerar_token_recuperacao(user_id):
    return _get_reset_serializer().dumps({"uid": int(user_id)})


def _validar_token_recuperacao(token):
    try:
        data = _get_reset_serializer().loads(token, max_age=_RESET_TOKEN_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def _limpar_telefone(valor):
    return re.sub(r"\D", "", (valor or "").strip())


PERFIL_UPLOAD_DIR = "static/uploads/perfil"
_EXTENSOES_FOTO_PERFIL = frozenset({"jpg", "jpeg", "png", "webp", "gif"})


def _coluna_usuarios_existe(cursor, coluna):
    cursor.execute("PRAGMA table_info(usuarios)")
    return any(row[1] == coluna for row in cursor.fetchall())


def _extensao_imagem_segura(filename):
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in _EXTENSOES_FOTO_PERFIL:
        return None
    return ext


def _salvar_upload_foto_perfil(arquivo):
    """Salva foto em static/uploads/perfil; retorna nome do arquivo ou None."""
    if not arquivo or not arquivo.filename:
        return None
    ext = _extensao_imagem_segura(arquivo.filename)
    if not ext:
        return None
    os.makedirs(PERFIL_UPLOAD_DIR, exist_ok=True)
    nome = f"perfil_{secrets.token_hex(12)}.{ext}"
    arquivo.save(os.path.join(PERFIL_UPLOAD_DIR, nome))
    return nome


def _iniciar_sessao_usuario(cursor, user, email):
    """Preenche session Flask após login ou cadastro bem-sucedido."""
    session.clear()
    session["user_id"] = user["id"]
    session["user_name"] = user["nome"]
    session["role"] = user["role"]

    try:
        barbearia_id = user["barbearia_id"]
    except (KeyError, TypeError, IndexError):
        barbearia_id = getattr(user, "barbearia_id", None)

    b_row = None
    if barbearia_id:
        cursor.execute(
            "SELECT id, nome, slug FROM barbearias WHERE id = ?",
            (barbearia_id,),
        )
        b_row = cursor.fetchone()
    if not b_row and email:
        cursor.execute(
            "SELECT id, nome, slug FROM barbearias WHERE LOWER(TRIM(email)) = LOWER(?) LIMIT 1",
            (email,),
        )
        b_row = cursor.fetchone()
    if b_row:
        session["barbearia_id"] = b_row["id"]
        session["nome_barbearia"] = b_row["nome"]
        session["barbearia_slug"] = b_row["slug"] or ""
    else:
        session["barbearia_id"] = barbearia_id
        session["nome_barbearia"] = "AgendaSimples"
        session["barbearia_slug"] = ""


def _email_ja_cadastrado(cursor, email, ignorar_id=None):
    if ignorar_id:
        cursor.execute(
            "SELECT id FROM usuarios WHERE LOWER(TRIM(email)) = LOWER(?) AND id <> ?",
            (email, ignorar_id),
        )
    else:
        cursor.execute(
            "SELECT id FROM usuarios WHERE LOWER(TRIM(email)) = LOWER(?)",
            (email,),
        )
    return cursor.fetchone() is not None


def _listar_profissionais(cursor, barbearia_id):
    cursor.execute(
        """
        SELECT id, nome FROM usuarios
        WHERE barbearia_id = ? AND role IN ('barbeiro', 'profissional')
        ORDER BY nome
        """,
        (barbearia_id,),
    )
    return cursor.fetchall()


def _listar_servicos(cursor, barbearia_id):
    cursor.execute(
        """
        SELECT nome FROM servicos
        WHERE barbearia_id = ? AND IFNULL(ativo, 1) = 1
        ORDER BY ordem, nome
        """,
        (barbearia_id,),
    )
    rows = cursor.fetchall()
    if rows:
        return [r[0] for r in rows]
    return list(SERVICOS_PADRAO)


def _listar_horarios(cursor, barbearia_id):
    cursor.execute(
        """
        SELECT hora FROM horarios
        WHERE barbearia_id = ? AND IFNULL(ativo, 1) = 1
        ORDER BY ordem, hora
        """,
        (barbearia_id,),
    )
    rows = cursor.fetchall()
    if rows:
        return [r[0] for r in rows]
    return HORARIOS


def _obter_barbearia_por_slug(cursor, slug):
    slug = (slug or "").strip()
    if not slug:
        return None
    cursor.execute(
        """
        SELECT id, nome, slug FROM barbearias
        WHERE LOWER(TRIM(slug)) = LOWER(?)
        """,
        (slug,),
    )
    return cursor.fetchone()


def _obter_barbearia_por_id(cursor, barbearia_id):
    cursor.execute(
        "SELECT id, nome, slug FROM barbearias WHERE id = ?",
        (barbearia_id,),
    )
    return cursor.fetchone()


def _barbearia_id_admin_obrigatorio():
    """ID do negócio na sessão do administrador."""
    return session.get("barbearia_id")


def _profissional_pertence_barbearia(cursor, profissional_id, barbearia_id):
    cursor.execute(
        """
        SELECT id FROM usuarios
        WHERE id = ? AND barbearia_id = ?
          AND role IN ('barbeiro', 'profissional', 'admin')
        """,
        (profissional_id, barbearia_id),
    )
    return cursor.fetchone() is not None


def _url_segura_apos_login(next_url):
    """Evita open redirect: só paths relativos do mesmo app."""
    if not next_url:
        return None
    next_url = next_url.strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


def _exigir_admin_ou_login():
    """
    Garante sessão ativa com role estritamente 'admin'.
    Retorna redirect para login com flash, ou None se autorizado.
    """
    if not session.get("user_id") or session.get("role") != "admin":
        flash("Acesso negado!", "error")
        return redirect(url_for("login"))
    return None


def _identificador_e_email(identificador):
    return "@" in (identificador or "")


def _senha_confere(senha_armazenada, senha_digitada):
    if senha_armazenada and (
        senha_armazenada.startswith("pbkdf2:")
        or senha_armazenada.startswith("scrypt:")
    ):
        return check_password_hash(senha_armazenada, senha_digitada)
    return senha_armazenada == senha_digitada


def _usuarios_tem_coluna_telefone(cursor):
    return _coluna_usuarios_existe(cursor, "telefone")


def _buscar_usuario_por_identificador(cursor, identificador):
    identificador = (identificador or "").strip()
    if not identificador:
        return None
    tem_telefone = _usuarios_tem_coluna_telefone(cursor)
    if _identificador_e_email(identificador):
        cursor.execute(
            """
            SELECT id, nome, email
            FROM usuarios
            WHERE LOWER(TRIM(email)) = LOWER(?)
            """,
            (identificador,),
        )
    elif tem_telefone:
        telefone = _limpar_telefone(identificador)
        if len(telefone) < 8:
            return None
        sufixo = telefone[-9:] if len(telefone) >= 9 else telefone
        cursor.execute(
            """
            SELECT id, nome, email, telefone
            FROM usuarios
            WHERE telefone IS NOT NULL
              AND TRIM(telefone) <> ''
              AND (
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    telefone, ' ', ''), '-', ''), '(', ''), ')', ''), '+', ''), '.', '')
                = ?
                OR RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    telefone, ' ', ''), '-', ''), '(', ''), ')', ''), '+', ''), '.', ''),
                    9) = ?
              )
            """,
            (telefone, sufixo),
        )
    else:
        return None
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "nome": row[1],
        "email": row[2],
        "telefone": row[3] if tem_telefone and len(row) > 3 else None,
    }


def _whatsapp_suporte_url(cursor):
    cursor.execute(
        """
        SELECT link_whatsapp FROM barbearias
        WHERE link_whatsapp IS NOT NULL AND TRIM(link_whatsapp) <> ''
        ORDER BY id
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return _normalizar_link_whatsapp(row[0])


def _url_whatsapp_com_texto(link_base, texto):
    if not link_base:
        return None
    separador = "&" if "?" in link_base else "?"
    return f"{link_base}{separador}text={quote(texto)}"


def _link_redefinir_senha(token):
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    path = url_for("redefinir_senha", token=token)
    if base:
        return f"{base}{path}"
    return url_for("redefinir_senha", token=token, _external=True)


def enviar_email_recuperacao(destinatario, nome_usuario, link_redefinir):
    """
    Envia e-mail com link de redefinição via SMTP (variáveis de ambiente).
    Retorna True se enviado; False se SMTP não configurado ou falha no envio.
    """
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "noreply@agendasimples.local")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

    if not smtp_host:
        app.logger.warning(
            "SMTP_HOST não configurado — e-mail de recuperação não enviado para %s",
            destinatario,
        )
        return False

    assunto = "Redefinição de senha — Agenda Simples"
    corpo = f"""Olá, {nome_usuario or 'usuário'}!

Recebemos um pedido para redefinir sua senha no Agenda Simples.

Clique no link abaixo (válido por 30 minutos):
{link_redefinir}

Se você não solicitou isso, ignore este e-mail.

Atenciosamente,
Equipe Agenda Simples
"""
    msg = MIMEMultipart()
    msg["From"] = smtp_from
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [destinatario], msg.as_string())
        return True
    except Exception as exc:
        app.logger.exception("Falha ao enviar e-mail de recuperação: %s", exc)
        return False


init_database()

requer_plano = requer_assinatura_ativa(get_connection)
register_stripe_routes(app, get_connection)

# Chaves internas da galeria (tb_galeria.categoria) — títulos exibidos vêm do banco
CATEGORIAS_GALERIA_HOME = [
    {"slug": "corte", "modal_id": "modalCat1", "carousel_id": "carouselCat1"},
    {"slug": "corte_barba", "modal_id": "modalCat2", "carousel_id": "carouselCat2"},
    {"slug": "sobrancelha", "modal_id": "modalCat3", "carousel_id": "carouselCat3"},
    {"slug": "outros", "modal_id": "modalCat4", "carousel_id": "carouselCat4"},
]

CAPA_CATALOGO_KEYS = (
    "capa_catalogo1",
    "capa_catalogo2",
    "capa_catalogo3",
    "capa_catalogo4",
)

DEFAULT_TITULOS_CATALOGO = (
    "Catálogo 1",
    "Catálogo 2",
    "Catálogo 3",
    "Catálogo 4",
)


def _render_home_estabelecimento(cursor, barbearia_id, barbearia_slug):
    """Home pública do estabelecimento (catálogo + galeria)."""
    configs = _carregar_configs_home(cursor, barbearia_id) or {
        "nome_negocio": "AgendaSimples",
        "titulo_catalogo1": DEFAULT_TITULOS_CATALOGO[0],
        "titulo_catalogo2": DEFAULT_TITULOS_CATALOGO[1],
        "titulo_catalogo3": DEFAULT_TITULOS_CATALOGO[2],
        "titulo_catalogo4": DEFAULT_TITULOS_CATALOGO[3],
        "logotipo_url": None,
        "link_instagram": None,
        "link_facebook": None,
        "link_whatsapp": None,
    }
    cursor.execute(
        "SELECT id, categoria, caminho_foto FROM tb_galeria WHERE barbearia_id = ? ORDER BY id DESC",
        (barbearia_id,),
    )
    todas_fotos = cursor.fetchall()
    galeria = {"corte": [], "corte_barba": [], "sobrancelha": [], "outros": []}
    for f in todas_fotos:
        if f.categoria in galeria:
            galeria[f.categoria].append({"id": f.id, "foto": f.caminho_foto})
    catalogos = _montar_catalogos_home(configs, galeria)
    return render_template(
        "home.html",
        galeria=galeria,
        configs=configs,
        catalogos=catalogos,
        barbearia_slug=barbearia_slug,
    )


def _carregar_configs_home(cursor, barbearia_id):
    """Nome, logotipo, catálogos da Home e bloco direito da tela /marcar."""
    base = {
        "nome_negocio": "AgendaSimples",
        "titulo_catalogo1": DEFAULT_TITULOS_CATALOGO[0],
        "titulo_catalogo2": DEFAULT_TITULOS_CATALOGO[1],
        "titulo_catalogo3": DEFAULT_TITULOS_CATALOGO[2],
        "titulo_catalogo4": DEFAULT_TITULOS_CATALOGO[3],
        "logotipo_url": None,
        "texto_marcar_direito": None,
        "foto_fundo_direito_url": None,
        "capa_catalogo1": None,
        "capa_catalogo2": None,
        "capa_catalogo3": None,
        "capa_catalogo4": None,
        "link_instagram": None,
        "link_facebook": None,
        "link_whatsapp": None,
    }
    try:
        cursor.execute(
            """
            SELECT nome, titulo_catalogo1, titulo_catalogo2, titulo_catalogo3,
                   titulo_catalogo4, logotipo_url, texto_marcar_direito,
                   foto_fundo_direito_url, capa_catalogo1, capa_catalogo2,
                   capa_catalogo3, capa_catalogo4, link_instagram, link_facebook,
                   link_whatsapp
            FROM barbearias WHERE id = ?
            """,
            (barbearia_id,),
        )
        row = cursor.fetchone()
    except DbError:
        cursor.execute("SELECT nome FROM barbearias WHERE id = ?", (barbearia_id,))
        row = cursor.fetchone()
        if row:
            base["nome_negocio"] = row[0]
            base["texto_marcar_direito"] = row[0]
        return base

    if not row:
        return base

    titulos_db = [row[1], row[2], row[3], row[4]]
    nome = row[0] or "AgendaSimples"
    return {
        "nome_negocio": nome,
        "titulo_catalogo1": titulos_db[0] or DEFAULT_TITULOS_CATALOGO[0],
        "titulo_catalogo2": titulos_db[1] or DEFAULT_TITULOS_CATALOGO[1],
        "titulo_catalogo3": titulos_db[2] or DEFAULT_TITULOS_CATALOGO[2],
        "titulo_catalogo4": titulos_db[3] or DEFAULT_TITULOS_CATALOGO[3],
        "logotipo_url": row[5],
        "texto_marcar_direito": row[6] or nome,
        "foto_fundo_direito_url": row[7],
        "capa_catalogo1": row[8],
        "capa_catalogo2": row[9],
        "capa_catalogo3": row[10],
        "capa_catalogo4": row[11],
        "link_instagram": row[12],
        "link_facebook": row[13],
        "link_whatsapp": row[14],
    }


def _normalizar_link_whatsapp(valor):
    """Aceita URL completa ou apenas número para wa.me."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if valor.startswith("http://") or valor.startswith("https://"):
        return valor
    digitos = re.sub(r"\D", "", valor)
    if not digitos:
        return None
    if not digitos.startswith("55") and len(digitos) <= 11:
        digitos = "55" + digitos
    return f"https://wa.me/{digitos}"


def _normalizar_link_url(valor):
    """Garante URL com protocolo para Instagram/Facebook."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if valor.startswith("http://") or valor.startswith("https://"):
        return valor
    return f"https://{valor.lstrip('/')}"


def _processar_upload_capas_catalogo(request, cursor, barbearia_id):
    """Salva imagens de capa dos 4 catálogos da Home."""
    upload_dir = "static/uploads/catalogos"
    os.makedirs(upload_dir, exist_ok=True)
    for i, coluna in enumerate(CAPA_CATALOGO_KEYS, start=1):
        arquivo = request.files.get(coluna)
        if not arquivo or not arquivo.filename:
            continue
        ext = (
            arquivo.filename.rsplit(".", 1)[1].lower()
            if "." in arquivo.filename
            else "jpg"
        )
        filename = f"capa_catalogo{i}_{barbearia_id}.{ext}"
        arquivo.save(os.path.join(upload_dir, filename))
        try:
            cursor.execute(
                f"UPDATE barbearias SET {coluna} = ? WHERE id = ?",
                (filename, barbearia_id),
            )
        except DbError:
            pass


def _url_capa_catalogo(capa_filename, fotos_galeria):
    """URL da imagem de fundo do card: capa admin ou 1ª foto da galeria."""
    if capa_filename:
        return f"uploads/catalogos/{capa_filename}"
    if fotos_galeria:
        return f"uploads/galeria/{fotos_galeria[0]['foto']}"
    return None


def _titulo_catalogo_exibicao(titulo_raw):
    """Traduz títulos padrão (Catálogo 1…4); mantém títulos customizados do admin."""
    titulo_raw = (titulo_raw or "").strip()
    if titulo_raw in DEFAULT_TITULOS_CATALOGO:
        return _(titulo_raw)
    return titulo_raw


def _montar_catalogos_home(configs, galeria):
    catalogos = []
    titulo_keys = [
        "titulo_catalogo1",
        "titulo_catalogo2",
        "titulo_catalogo3",
        "titulo_catalogo4",
    ]
    for i, meta in enumerate(CATEGORIAS_GALERIA_HOME):
        slug = meta["slug"]
        fotos = galeria.get(slug, [])
        capa_file = configs.get(CAPA_CATALOGO_KEYS[i])
        capa_path = _url_capa_catalogo(capa_file, fotos)
        titulo_raw = configs.get(titulo_keys[i], DEFAULT_TITULOS_CATALOGO[i])
        catalogos.append({
            **meta,
            "titulo": _titulo_catalogo_exibicao(titulo_raw),
            "fotos": fotos,
            "capa_path": capa_path,
        })
    return catalogos


# Função auxiliar para buscar a foto de capa cadastrada no banco de dados
def obter_foto_capa(cursor):
    try:
        cursor.execute("SELECT valor FROM tb_configuracoes WHERE chave = 'foto_capa'")
        config_foto = cursor.fetchone()
        return config_foto[0] if config_foto else None
    except Exception as e:
        print(f"Erro ao obter foto de capa: {e}")
        return None

# Função para criar links amigáveis (SaaS)
def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text

# 3. ROTA PARA TROCAR O IDIOMA (PT | EN | ES)
@app.route("/mudar_idioma/<string:lang>")
def mudar_idioma(lang):
    if lang in app.config["BABEL_SUPPORTED_LOCALES"]:
        session["lang"] = lang
        flash(_("Idioma alterado com sucesso."), "success")
    else:
        flash(_("Idioma não suportado."), "warning")

    destino = request.args.get("next")
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return redirect(destino)
    if request.referrer:
        return redirect(request.referrer)
    return redirect(url_for("home"))

# -------------------------- ROTAS DO SAAS / CADASTRO --------------------------

RAMOS_ATIVIDADE = (
    ("barbearia", _("Barbearia")),
    ("salao", _("Salão de Beleza")),
    ("estetica", _("Clínica de Estética")),
    ("outros", _("Outros")),
)
_RAMOS_VALIDOS = {c for c, _ in RAMOS_ATIVIDADE}


def _resolver_barbearia(cursor, identificador):
    """Busca estabelecimento por slug ou ID numérico."""
    identificador = (identificador or "").strip()
    if not identificador:
        return None
    if identificador.isdigit():
        return _obter_barbearia_por_id(cursor, int(identificador))
    return _obter_barbearia_por_slug(cursor, identificador)


def _ctx_landing_vendas(form=None):
    return {
        "trial_days": cfg.TRIAL_DAYS,
        "preco_mensal": cfg.PLANO_MENSAL_VALOR,
        "ramos": RAMOS_ATIVIDADE,
        "form": form or {},
    }


def _log_erro_cadastro_saas(exc, etapa, **contexto):
    print("=== ERRO CADASTRO SAAS ===")
    print(f"Etapa: {etapa}")
    print(f"Exceção: {type(exc).__name__}: {exc}")
    for chave, valor in contexto.items():
        print(f"  {chave}: {valor}")
    traceback.print_exc()
    print("=== FIM ERRO CADASTRO SAAS ===")
    app.logger.exception("Cadastro SaaS [%s]: %s", etapa, exc)


def _inserir_barbearia_cadastro(cursor, nome_negocio, slug, email, senha_hash, agora, ramo):
    """INSERT em barbearias com colunas opcionais conforme schema migrado."""
    base_cols = [
        "nome", "slug", "email", "senha", "plano_ativo",
        "titulo_catalogo1", "titulo_catalogo2", "titulo_catalogo3", "titulo_catalogo4",
        "texto_marcar_direito", "data_cadastro",
    ]
    base_vals = [
        nome_negocio,
        slug,
        email,
        senha_hash,
        1,
        DEFAULT_TITULOS_CATALOGO[0],
        DEFAULT_TITULOS_CATALOGO[1],
        DEFAULT_TITULOS_CATALOGO[2],
        DEFAULT_TITULOS_CATALOGO[3],
        nome_negocio,
        agora,
    ]
    if _coluna_existe(cursor, "barbearias", "ramo_atividade"):
        base_cols.append("ramo_atividade")
        base_vals.append(ramo)

    placeholders = ", ".join("?" for _ in base_vals)
    colunas_sql = ", ".join(base_cols)
    cursor.execute(
        f"INSERT INTO barbearias ({colunas_sql}) VALUES ({placeholders})",
        tuple(base_vals),
    )


def _inserir_usuario_admin_cadastro(
    cursor, nome_profissional, email, senha_hash, agora, barbearia_id
):
    """INSERT em usuarios — só inclui colunas que existem no Turso/SQLite."""
    cols = ["nome", "email", "senha", "role"]
    vals = [nome_profissional, email, senha_hash, "admin"]
    if _coluna_existe(cursor, "usuarios", "data_cadastro"):
        cols.append("data_cadastro")
        vals.append(agora)
    if _coluna_existe(cursor, "usuarios", "status_trial"):
        cols.append("status_trial")
        vals.append("trialing")
    if _coluna_existe(cursor, "usuarios", "barbearia_id"):
        cols.append("barbearia_id")
        vals.append(barbearia_id)

    placeholders = ", ".join("?" for _ in vals)
    colunas_sql = ", ".join(cols)
    cursor.execute(
        f"INSERT INTO usuarios ({colunas_sql}) VALUES ({placeholders})",
        tuple(vals),
    )


def _processar_cadastro_saas(template_name="home_vendas.html"):
    """Valida e cria conta admin + trial; retorna redirect ou template com erros."""
    conn = None
    form = {}

    try:
        nome_negocio = (request.form.get("nome_negocio") or "").strip()
        nome_profissional = (request.form.get("nome_profissional") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        ramo = (request.form.get("ramo_atividade") or "").strip().lower()
        senha = request.form.get("password") or ""
        senha_confirma = request.form.get("password_confirm") or ""

        form = {
            "nome_negocio": nome_negocio,
            "nome_profissional": nome_profissional,
            "email": email,
            "ramo_atividade": ramo,
        }

        erros = []
        if not nome_negocio:
            erros.append(_("Informe o nome do seu negócio."))
        if not nome_profissional:
            erros.append(_("Informe o seu nome."))
        if not email or "@" not in email:
            erros.append(_("Informe um e-mail válido."))
        if ramo not in _RAMOS_VALIDOS:
            erros.append(_("Selecione o ramo de atividade."))
        if len(senha) < 6:
            erros.append(_("A senha deve ter pelo menos 6 caracteres."))
        if senha != senha_confirma:
            erros.append(_("As senhas não coincidem."))

        conn = get_connection()
        cursor = conn.cursor()

        try:
            ensure_schema_migrations(cursor)
            safe_commit(conn)
        except Exception as exc_mig:
            _log_erro_cadastro_saas(exc_mig, "ensure_schema_migrations", email=email)
            flash(
                _("Erro ao preparar o banco de dados. Tente novamente em instantes."),
                "error",
            )
            return render_template(template_name, **_ctx_landing_vendas(form))

        if not erros and _email_ja_cadastrado(cursor, email):
            erros.append(
                _("Este e-mail já está cadastrado. Faça login ou use outro e-mail.")
            )

        slug = slugify(nome_negocio) if nome_negocio else ""
        if not erros and slug:
            cursor.execute(
                """
                SELECT id FROM barbearias
                WHERE slug = ? OR LOWER(TRIM(email)) = LOWER(?)
                """,
                (slug, email),
            )
            if cursor.fetchone():
                erros.append(
                    _(
                        "Já existe uma conta com este e-mail ou nome de negócio semelhante."
                    )
                )

        if erros:
            for msg in erros:
                flash(msg, "error")
            return render_template(template_name, **_ctx_landing_vendas(form))

        senha_hash = generate_password_hash(senha)
        agora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        _inserir_barbearia_cadastro(
            cursor, nome_negocio, slug, email, senha_hash, agora, ramo
        )
        barbearia_id = obter_id_inserido(
            cursor,
            "SELECT id FROM barbearias WHERE LOWER(TRIM(email)) = LOWER(?) ORDER BY id DESC LIMIT 1",
            (email,),
        )
        if not barbearia_id:
            raise RuntimeError(
                "INSERT barbearias OK, mas ID não retornado (lastrowid/Turso)."
            )

        criar_assinatura_trial(cursor, barbearia_id, cfg.TRIAL_DAYS)
        seed_servicos_horarios_padrao(cursor, barbearia_id)
        _inserir_usuario_admin_cadastro(
            cursor, nome_profissional, email, senha_hash, agora, barbearia_id
        )
        safe_commit(conn)

        cursor.execute(
            """
            SELECT id, nome, role, barbearia_id FROM usuarios
            WHERE LOWER(TRIM(email)) = LOWER(?)
            """,
            (email,),
        )
        user = cursor.fetchone()
        if not user:
            flash(
                _("Conta criada, mas falhou o login automático. Entre com seu e-mail."),
                "warning",
            )
            return redirect(url_for("login"))

        _iniciar_sessao_usuario(cursor, user, email)
        safe_close(conn)
        conn = None

        flash(
            _("Bem-vindo! Sua conta foi criada com %(days)s dias de teste grátis.")
            % {"days": cfg.TRIAL_DAYS},
            "success",
        )
        return redirect(url_for("admin_agenda"))

    except Exception as exc:
        safe_rollback(conn)
        _log_erro_cadastro_saas(
            exc,
            "processamento_cadastro",
            email=form.get("email"),
            negocio=form.get("nome_negocio"),
        )
        flash(
            _("Não foi possível criar sua conta. Tente novamente em instantes."),
            "error",
        )
        return render_template(template_name, **_ctx_landing_vendas(form))

    finally:
        safe_close(conn)


@app.route("/registrar", methods=["GET", "POST"])
@app.route("/cadastro", methods=["GET", "POST"])
def registrar():
    """Cadastro SaaS — GET redireciona à landing; POST processa o formulário."""
    if request.method == "GET":
        if session.get("user_id") and session.get("role") == "admin":
            return redirect(url_for("admin_agenda"))
        return redirect(url_for("home") + "#cadastro")
    return _processar_cadastro_saas("home_vendas.html")


@app.route("/cadastro_barbearia", methods=["GET", "POST"])
def cadastro_barbearia():
    """Legado — redireciona para a landing de vendas."""
    return redirect(url_for("home") + "#cadastro")


# -------------------------- LANDING DE VENDAS (/) --------------------------
@app.route("/", methods=["GET", "POST"])
def home():
    """Landing Page SaaS — conversão para plano mensal."""
    if request.method == "POST":
        return _processar_cadastro_saas("home_vendas.html")
    if session.get("user_id") and session.get("role") == "admin":
        return redirect(url_for("admin_agenda"))
    return render_template("home_vendas.html", **_ctx_landing_vendas())


@app.route("/b/<identificador>")
def barbearia_home(identificador):
    """Home pública do estabelecimento (slug ou ID numérico)."""
    conn = get_connection()
    cursor = conn.cursor()
    ensure_schema_migrations(cursor)
    barbearia = _resolver_barbearia(cursor, identificador)
    if not barbearia:
        conn.close()
        flash(_("Estabelecimento não encontrado."), "warning")
        return redirect(url_for("home"))
    slug_exib = barbearia["slug"] or str(barbearia["id"])
    pagina = _render_home_estabelecimento(cursor, barbearia["id"], slug_exib)
    conn.close()
    return pagina


@app.route("/assinatura/bloqueio")
def bloqueio_assinatura():
    """Tela de bloqueio quando trial expirou e plano não está ativo."""
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    barbearia_id = session.get("barbearia_id")
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    from subscriptions import admin_tem_acesso_painel

    if admin_tem_acesso_painel(cursor, barbearia_id):
        conn.commit()
        conn.close()
        return redirect(url_for("admin_agenda"))
    conn.commit()
    conn.close()
    return render_template(
        "bloqueio_assinatura.html",
        preco=cfg.PLANO_MENSAL_VALOR,
    )


# -------------------------- LOGIN / LOGOUT --------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("password")
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT id, nome, role, senha, barbearia_id FROM usuarios
            WHERE LOWER(TRIM(email)) = LOWER(?)
            """,
            (email,),
        )
        user = cursor.fetchone()

        if user and _senha_confere(user.senha, senha):
            _iniciar_sessao_usuario(cursor, user, email)
            conn.close()
            destino = _url_segura_apos_login(request.form.get("next") or request.args.get("next"))
            if user.role == "admin":
                if destino:
                    return redirect(destino)
                return redirect(url_for("admin_agenda"))
            if destino:
                flash(
                    "Esta página é exclusiva do administrador. Você foi redirecionado para sua agenda.",
                    "warning",
                )
            return redirect(url_for("agenda"))

        conn.close()
        flash("Usuário ou senha incorretos!", "error")
        return redirect(url_for("login", next=request.form.get("next")))
    proxima = request.args.get("next")
    if proxima and _url_segura_apos_login(proxima):
        flash("Faça login como administrador para acessar esta página.", "warning")
    return render_template("login.html", next_url=proxima)


@app.route("/esqueci_senha", methods=["GET", "POST"])
def esqueci_senha():
    whatsapp_url = None
    telefone_exibicao = None
    modo_whatsapp = False

    if request.method == "POST":
        identificador = (request.form.get("identificador") or "").strip()
        conn = get_connection()
        cursor = conn.cursor()
        usuario = _buscar_usuario_por_identificador(cursor, identificador)
        conn.close()

        if not usuario:
            flash(
                "Não encontramos cadastro com esse e-mail ou telefone. Verifique os dados.",
                "error",
            )
            return render_template("esqueci_senha.html")

        if _identificador_e_email(identificador):
            token = _gerar_token_recuperacao(usuario["id"])
            link = _link_redefinir_senha(token)
            enviado = enviar_email_recuperacao(
                usuario["email"], usuario["nome"], link
            )
            if enviado:
                flash(
                    "Enviamos um link de recuperação para o seu e-mail. "
                    "O link expira em 30 minutos.",
                    "success",
                )
            else:
                flash(
                    "Conta encontrada, mas não foi possível enviar o e-mail. "
                    "Configure SMTP no servidor ou use seu telefone cadastrado. "
                    "Em ambiente de desenvolvimento, verifique os logs do servidor.",
                    "warning",
                )
            return render_template("esqueci_senha.html")

        telefone_limpo = _limpar_telefone(identificador)
        telefone_exibicao = identificador
        texto = (
            f"Olá, esqueci minha senha e gostaria de redefinir. "
            f"Meu telefone é {telefone_exibicao}."
        )
        conn = get_connection()
        cursor = conn.cursor()
        wa_base = _whatsapp_suporte_url(cursor)
        conn.close()
        whatsapp_url = _url_whatsapp_com_texto(wa_base, texto)
        modo_whatsapp = True
        if not whatsapp_url:
            flash(
                "Encontramos seu cadastro, mas o WhatsApp de suporte ainda não está "
                "configurado. Entre em contato com o administrador da barbearia.",
                "warning",
            )
        return render_template(
            "esqueci_senha.html",
            modo_whatsapp=modo_whatsapp,
            whatsapp_url=whatsapp_url,
            telefone_exibicao=telefone_exibicao,
        )

    return render_template(
        "esqueci_senha.html",
        modo_whatsapp=modo_whatsapp,
        whatsapp_url=whatsapp_url,
        telefone_exibicao=telefone_exibicao,
    )


@app.route("/redefinir_senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    user_id = _validar_token_recuperacao(token)
    if not user_id:
        flash(
            "Link inválido ou expirado. Solicite uma nova recuperação de senha.",
            "error",
        )
        return redirect(url_for("esqueci_senha"))

    if request.method == "POST":
        nova = request.form.get("nova_senha", "")
        confirma = request.form.get("confirma_senha", "")
        if len(nova) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "error")
            return render_template("redefinir_senha.html", token=token)
        if nova != confirma:
            flash("As senhas não coincidem.", "error")
            return render_template("redefinir_senha.html", token=token)

        senha_hash = generate_password_hash(nova)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE usuarios SET senha = ? WHERE id = ?",
            (senha_hash, user_id),
        )
        conn.commit()
        conn.close()
        flash("Senha alterada com sucesso! Faça login com a nova senha.", "success")
        return redirect(url_for("login"))

    return render_template("redefinir_senha.html", token=token)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# -------------------------- AGENDA BARBEIRO --------------------------
@app.route("/agenda")
def agenda():
    if session.get("role") not in ("barbeiro", "profissional"):
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    
    # 🌟 ATUALIZAÇÃO: Busca a foto de capa para exibir no topo do HTML
    foto_capa = obter_foto_capa(cursor)

    barbearia_id = session.get("barbearia_id")
    if not barbearia_id:
        conn.close()
        return redirect(url_for("login"))
    horarios_negocio = _listar_horarios(cursor, barbearia_id)
    cursor.execute(
        """
        SELECT Nome, Dia, Hora, Servico, Whatsapp
        FROM Clientes
        WHERE barbeiro_id = ? AND barbearia_id = ?
        ORDER BY Dia, Hora
        """,
        (session["user_id"], barbearia_id),
    )
    registros = cursor.fetchall()
    conn.close()

    hoje = datetime.today()
    agenda_data = {
        (hoje + timedelta(days=i)).strftime("%Y-%m-%d"): {
            h: None for h in horarios_negocio
        }
        for i in range(28)
    }

    for r in registros:
        d_str = r.Dia.strftime("%Y-%m-%d") if isinstance(r.Dia, datetime) else str(r.Dia)
        h_str = str(r.Hora)[:5]
        if d_str in agenda_data and h_str in agenda_data[d_str]:
            agenda_data[d_str][h_str] = {
                "nome": r.Nome,
                "servico": r.Servico,
                "whatsapp": r.Whatsapp,
            }

    return render_template(
        "agenda.html",
        agenda=agenda_data,
        horarios=horarios_negocio,
        datetime=datetime,
        dias_pt=DIAS_PT,
        foto_capa=foto_capa,
    )

# -------------------------- AGENDA ADMIN --------------------------
@app.route("/admin")
@app.route("/admin_agenda")
@requer_plano
def admin_agenda():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        flash(_("Sessão inválida. Faça login novamente."), "error")
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    ensure_schema_migrations(cursor)

    foto_capa = obter_foto_capa(cursor)
    horarios_negocio = _listar_horarios(cursor, barbearia_id)

    cursor.execute(
        """
        SELECT c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp, u.nome AS barbeiro_nome,
               c.barbeiro_id, IFNULL(c.status, 'Agendado') AS status
        FROM Clientes c
        INNER JOIN usuarios u ON c.barbeiro_id = u.id
        WHERE c.barbearia_id = ?
          AND IFNULL(c.status, 'Agendado') <> 'Concluído'
        ORDER BY c.Dia, c.Hora
        """,
        (barbearia_id,),
    )
    registros = cursor.fetchall()

    hoje = datetime.today()
    agenda_data = {
        (hoje + timedelta(days=i)).strftime("%Y-%m-%d"): {
            h: None for h in horarios_negocio
        }
        for i in range(28)
    }

    for r in registros:
        d_str = r.Dia.strftime("%Y-%m-%d") if isinstance(r.Dia, datetime) else str(r.Dia)
        h_str = str(r.Hora)[:5]
        if d_str in agenda_data and h_str in agenda_data[d_str]:
            if agenda_data[d_str][h_str] is None:
                agenda_data[d_str][h_str] = []
            agenda_data[d_str][h_str].append({
                "nome": r.Nome,
                "servico": r.Servico,
                "whatsapp": getattr(r, "Whatsapp", ""),
                "barbeiro_nome": r.barbeiro_nome,
                "barbeiro_id": r.barbeiro_id,
            })

    barbeiros = _listar_profissionais(cursor, barbearia_id)
    barbearia = _obter_barbearia_por_id(cursor, barbearia_id)
    barbearia_slug = barbearia["slug"] if barbearia else session.get("barbearia_slug", "")
    conn.close()

    return render_template(
        "admin_agenda.html",
        agenda=agenda_data,
        horarios=horarios_negocio,
        datetime=datetime,
        dias_pt=DIAS_PT,
        barbeiros=barbeiros,
        foto_capa=foto_capa,
        barbearia_slug=barbearia_slug,
    )


@app.route("/admin/profissionais/novo", methods=["GET", "POST"])
@app.route("/admin_cadastrar_profissional", methods=["GET", "POST"])
@app.route("/admin/profissional/novo", methods=["GET", "POST"])
@requer_plano
def admin_cadastrar_profissional():
    bloqueio = _exigir_admin_ou_login()
    if bloqueio:
        return bloqueio

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip()
        celular_raw = (request.form.get("celular") or "").strip()
        especialidade = (request.form.get("especialidade") or "").strip()
        senha = request.form.get("senha") or ""
        foto = request.files.get("foto_perfil")

        erros = []
        if not nome:
            erros.append("Informe o nome completo.")
        if not email or "@" not in email:
            erros.append("Informe um e-mail válido.")
        if len(senha) < 6:
            erros.append("A senha inicial deve ter pelo menos 6 caracteres.")

        celular = _limpar_telefone(celular_raw) if celular_raw else None
        if celular_raw and celular and len(celular) < 10:
            erros.append("Celular inválido.")

        foto_nome = None
        if foto and foto.filename:
            foto_nome = _salvar_upload_foto_perfil(foto)
            if not foto_nome:
                erros.append(
                    "Foto inválida. Use JPG, PNG, WEBP ou GIF (máx. recomendado 5 MB)."
                )

        barbearia_id = _barbearia_id_admin_obrigatorio()
        if not barbearia_id:
            return redirect(url_for("login"))

        conn = get_connection()
        cursor = conn.cursor()

        if email and not erros and _email_ja_cadastrado(cursor, email):
            erros.append("Este e-mail já está cadastrado.")

        if erros:
            conn.close()
            for msg in erros:
                flash(msg, "error")
            return render_template(
                "admin_cadastrar_profissional.html",
                form={
                    "nome": nome,
                    "email": email,
                    "celular": celular_raw,
                    "especialidade": especialidade,
                },
            )

        senha_hash = generate_password_hash(senha)
        role = "profissional"

        tem_tel = _coluna_usuarios_existe(cursor, "telefone")
        tem_esp = _coluna_usuarios_existe(cursor, "especialidade")
        tem_foto = _coluna_usuarios_existe(cursor, "foto_perfil")

        if not (tem_tel and tem_esp and tem_foto):
            conn.close()
            flash(
                "Execute a migração do banco: python aplicar_migracao_profissional_perfil.py",
                "error",
            )
            return render_template("admin_cadastrar_profissional.html", form={})

        cursor.execute(
            """
            INSERT INTO usuarios (
                nome, email, telefone, senha, role, especialidade, foto_perfil, barbearia_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                email,
                celular or None,
                senha_hash,
                role,
                especialidade or None,
                foto_nome,
                barbearia_id,
            ),
        )
        conn.commit()
        conn.close()
        flash(f"Profissional {nome} cadastrado com sucesso!", "success")
        return redirect(url_for("admin_agenda"))

    return render_template("admin_cadastrar_profissional.html", form={})


@app.route("/admin/agenda/concluir", methods=["POST"])
@requer_plano
def admin_agenda_concluir():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    data = request.form.get("data")
    hora = request.form.get("hora")
    barbeiro_id = request.form.get("barbeiro_id")
    valor_raw = (request.form.get("valor") or "0").strip().replace(",", ".")
    tipo_feito = request.form.get("descricao_tipo", "Serviço")
    detalhe = request.form.get("descricao_detalhe", "")
    servico_agendado = request.form.get("servico_agendado", "")
    nome_cliente = request.form.get("nome_cliente", "")

    if not data or not hora or not barbeiro_id:
        flash("Dados do agendamento incompletos.", "danger")
        return redirect(url_for("admin_agenda"))

    try:
        valor = float(valor_raw) if valor_raw else 0.0
    except ValueError:
        flash("Valor inválido. Use apenas números (ex: 50 ou 50.00).", "danger")
        return redirect(url_for("admin_agenda"))

    if valor < 0:
        flash("O valor não pode ser negativo.", "warning")
        return redirect(url_for("admin_agenda"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT Nome, Servico, IFNULL(status, 'Agendado')
        FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
        """,
        (data, hora, barbeiro_id, barbearia_id),
    )
    agendamento = cursor.fetchone()
    if not agendamento:
        conn.close()
        flash("Agendamento não encontrado.", "danger")
        return redirect(url_for("admin_agenda"))
    if agendamento[2] == "Concluído":
        conn.close()
        flash("Este agendamento já foi concluído.", "warning")
        return redirect(url_for("admin_agenda"))

    try:
        cursor.execute(
            """
            UPDATE Clientes
            SET status = 'Concluído'
            WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
            """,
            (data, hora, barbeiro_id, barbearia_id),
        )
        if valor > 0:
            descricao_fin = _montar_descricao_atendimento(
                tipo_feito, detalhe, servico_agendado, nome_cliente
            )
            _inserir_receita_financeiro(
                cursor, descricao_fin, valor, int(barbeiro_id), barbearia_id
            )
            flash(
                f"Atendimento concluído. Receita de R$ {valor:.2f} registrada no financeiro.",
                "success",
            )
        else:
            flash(
                "Atendimento concluído sem lançamento financeiro (valor R$ 0,00).",
                "success",
            )
        conn.commit()
    except DbError as e:
        conn.rollback()
        flash(f"Erro ao concluir atendimento: {e}", "danger")
    finally:
        conn.close()

    return redirect(url_for("admin_agenda"))


# -------------------------- AGENDAR --------------------------
def _normalizar_id_inserido(valor):
    """Converte ID retornado pelo SQL Server (int, Decimal, None)."""
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        try:
            return int(float(valor))
        except (TypeError, ValueError):
            return None


def _inserir_agendamento_retornar_id(
    cursor, nome, data, hora, servico, whatsapp, barbeiro_id, barbearia_id
):
    """Insere agendamento e devolve o id gerado (SQLite: last_insert_rowid)."""
    cursor.execute(
        """
        INSERT INTO Clientes (
            Nome, Dia, Hora, Servico, Whatsapp, barbeiro_id, barbearia_id, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Agendado')
        """,
        (nome, data, hora, servico, whatsapp, barbeiro_id, barbearia_id),
    )
    return _normalizar_id_inserido(cursor.lastrowid)


def _buscar_agendamento_por_id(cursor, agendamento_id):
    """Retorna dict com dados do agendamento ou None."""
    cursor.execute(
        """
        SELECT c.id, c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp,
               c.barbeiro_id, u.nome AS profissional_nome
        FROM Clientes c
        LEFT JOIN usuarios u ON c.barbeiro_id = u.id
        WHERE c.id = ?
        """,
        (agendamento_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    dia = row[2]
    if hasattr(dia, "strftime"):
        data_fmt = dia.strftime("%Y-%m-%d")
        data_exib = dia.strftime("%d/%m/%Y")
    else:
        data_fmt = str(dia)[:10]
        data_exib = data_fmt

    hora = str(row[3])[:5] if row[3] else ""

    return {
        "id": row[0],
        "nome": row[1],
        "data": data_fmt,
        "data_exib": data_exib,
        "hora": hora,
        "servico": row[4],
        "whatsapp": row[5] or "",
        "barbeiro_id": row[6],
        "profissional": row[7] or "—",
    }


@app.route("/agendar", methods=["POST"])
def agendar():
    nome = request.form["nome"]
    data = request.form["data"]
    hora = request.form["hora"]
    servico = request.form["servico"]
    whatsapp = request.form.get("whatsapp", "")
    barbeiro_id = request.form["barbeiro_id"]
    barbearia_id = request.form.get("barbearia_id", type=int)
    slug_volta = (request.form.get("barbearia_slug") or "").strip()

    conn = get_connection()
    cursor = conn.cursor()
    ensure_schema_migrations(cursor)

    if not barbearia_id and slug_volta:
        barbearia = _obter_barbearia_por_slug(cursor, slug_volta)
        if barbearia:
            barbearia_id = barbearia["id"]

    if not barbearia_id:
        conn.close()
        flash(_("Estabelecimento inválido."), "error")
        return redirect(url_for("home"))

    if not _profissional_pertence_barbearia(cursor, int(barbeiro_id), barbearia_id):
        conn.close()
        flash(_("Profissional inválido para este estabelecimento."), "error")
        if slug_volta:
            return redirect(url_for("marcar_barbearia", slug=slug_volta))
        return redirect(url_for("home"))

    cursor.execute(
        """
        SELECT COUNT(*) FROM Clientes
        WHERE Dia = ? AND Hora = ? AND barbeiro_id = ? AND barbearia_id = ?
          AND IFNULL(status, 'Agendado') <> 'Concluído'
        """,
        (data, hora, barbeiro_id, barbearia_id),
    )

    if cursor.fetchone()[0] > 0:
        conn.close()
        flash(
            "Este horário já está ocupado com este profissional. Por favor, escolha outra opção!",
            "warning",
        )
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin_agenda"))
        if role in ("barbeiro", "profissional"):
            return redirect(url_for("agenda"))
        if slug_volta:
            return redirect(url_for("marcar_barbearia", slug=slug_volta))
        return redirect(url_for("home"))

    novo_id = _inserir_agendamento_retornar_id(
        cursor, nome, data, hora, servico, whatsapp, barbeiro_id, barbearia_id
    )
    conn.commit()
    conn.close()

    if not novo_id:
        flash(
            "Agendamento salvo, mas não foi possível abrir a tela de confirmação. "
            "Verifique se a coluna id existe em Clientes (migracao_clientes_id).",
            "warning",
        )
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin_agenda"))
        if role in ("barbeiro", "profissional"):
            return redirect(url_for("agenda"))
        if slug_volta:
            return redirect(url_for("marcar_barbearia", slug=slug_volta))
        return redirect(url_for("home"))

    return redirect(
        url_for(
            "sucesso_agendamento",
            agendamento_id=novo_id,
            slug=slug_volta or None,
        )
    )


@app.route("/sucesso/<int:agendamento_id>")
def sucesso_agendamento(agendamento_id):
    slug = request.args.get("slug")
    if not agendamento_id or agendamento_id < 1:
        flash("Link de confirmação inválido.", "warning")
        if slug:
            return redirect(url_for("barbearia_home", slug=slug))
        return redirect(url_for("home"))

    conn = get_connection()
    cursor = conn.cursor()
    try:
        ag = _buscar_agendamento_por_id(cursor, agendamento_id)
    except DbError:
        ag = None
    finally:
        conn.close()

    if not ag:
        flash("Agendamento não encontrado ou já removido.", "danger")
        if slug:
            return redirect(url_for("barbearia_home", slug=slug))
        return redirect(url_for("home"))

    is_admin_or_staff = (
        "role" in session and session["role"] in ("admin", "barbeiro", "profissional")
    )

    return render_template(
        "sucesso_agendamento.html",
        ag=ag,
        voltar_para_agenda=is_admin_or_staff,
        barbearia_slug=slug,
    )

# -------------------------- WHATSAPP / EDITAR / EXCLUIR --------------------------
@app.route("/editar/<string:data>/<string:hora>", methods=["GET", "POST"])
def editar(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        cursor.execute("UPDATE Clientes SET Nome=?, Servico=?, Whatsapp=? WHERE Dia=? AND Hora=? AND barbeiro_id=?",
                       (request.form["nome"], request.form["servico"], request.form.get("whatsapp",""), data, hora, barbeiro_id))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_agenda" if session["role"]=="admin" else "agenda"))
    cursor.execute("SELECT Nome, Servico, Whatsapp FROM Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    cliente = cursor.fetchone()
    conn.close()
    return render_template("editar.html", cliente=cliente, data=data, hora=hora, barbeiro_id=barbeiro_id)

@app.route("/excluir/<string:data>/<string:hora>")
def excluir(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_agenda" if session["role"]=="admin" else "agenda"))

@app.route("/whatsapp/<string:data>/<string:hora>")
def enviar_whatsapp(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Nome, Whatsapp FROM Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    cliente = cursor.fetchone()
    conn.close()
    if not cliente or not cliente[1]:
        return "⚠️ WhatsApp não cadastrado!"
    numero = "+55" + cliente[1].strip() if not cliente[1].startswith("+") else cliente[1].strip()
    try:
        import pywhatkit

        pywhatkit.sendwhatmsg_instantly(
            numero,
            f"Lembrete: {cliente[0]}, seu horário é {data} às {hora}.",
            wait_time=15,
        )
        return "✅ Enviado!"
    except Exception:
        return "❌ Erro ao enviar."

# -------------------------- MARCAR AGENDAMENTO CLIENTE --------------------------
@app.route("/marcar")
def marcar():
    """Legado: redireciona para a URL pública do estabelecimento."""
    slug_sessao = session.get("barbearia_slug")
    if slug_sessao:
        return redirect(url_for("marcar_barbearia", slug=slug_sessao, **request.args))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT slug FROM barbearias ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return redirect(url_for("marcar_barbearia", slug=row[0], **request.args))
    flash(_("Nenhum estabelecimento cadastrado ainda."), "warning")
    return redirect(url_for("home"))


@app.route("/b/<identificador>/marcar")
def marcar_barbearia(identificador):
    conn = get_connection()
    cursor = conn.cursor()
    ensure_schema_migrations(cursor)
    barbearia = _resolver_barbearia(cursor, identificador)
    if not barbearia:
        conn.close()
        flash(_("Estabelecimento não encontrado."), "warning")
        return redirect(url_for("home"))

    barbearia_id = barbearia["id"]
    slug_exib = barbearia["slug"] or str(barbearia["id"])
    configs = _carregar_configs_home(cursor, barbearia_id)
    barbeiros = _listar_profissionais(cursor, barbearia_id)
    horarios = _listar_horarios(cursor, barbearia_id)
    servicos = _listar_servicos(cursor, barbearia_id)
    conn.close()

    profissional_sugerido = None
    profissional_param = request.args.get("profissional")
    if profissional_param:
        try:
            pid = int(profissional_param)
            ids_validos = {b[0] for b in barbeiros}
            if pid in ids_validos:
                profissional_sugerido = pid
        except (TypeError, ValueError):
            pass

    return render_template(
        "marcar_agendamento.html",
        barbeiros=barbeiros,
        horarios=horarios,
        servicos=servicos,
        profissional_sugerido=profissional_sugerido,
        configs=configs,
        barbearia_id=barbearia_id,
        barbearia_slug=slug_exib,
    )

# -------------------------- EXPORTAR PDF / EXCEL --------------------------
@app.route("/exportar_excel")
def exportar_excel():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT Nome, Dia, Hora, Servico FROM Clientes WHERE barbearia_id = ?",
        (barbearia_id,),
    )
    dados = cursor.fetchall()
    conn.close()

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Nome", "Data", "Hora", "Serviço"])
    for d in dados:
        ws.append([d[0], str(d[1]), d[2], d[3]])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, download_name="agenda.xlsx", as_attachment=True)

@app.route("/pdf_hoje")
def pdf_hoje():
    return redirect(url_for("pdf_diario", data=datetime.today().strftime("%Y-%m-%d")))

@app.route("/pdf_diario/<string:data>")
def pdf_diario(data):
    barbearia_id = None
    if session.get("role") == "admin":
        barbearia_id = _barbearia_id_admin_obrigatorio()
    conn = get_connection()
    cursor = conn.cursor()
    if barbearia_id:
        cursor.execute(
            """
            SELECT c.Nome, c.Hora, c.Servico, u.nome
            FROM Clientes c
            JOIN usuarios u ON c.barbeiro_id = u.id
            WHERE c.Dia = ? AND c.barbearia_id = ?
            """,
            (data, barbearia_id),
        )
    else:
        cursor.execute(
            """
            SELECT c.Nome, c.Hora, c.Servico, u.nome
            FROM Clientes c
            JOIN usuarios u ON c.barbeiro_id = u.id
            WHERE c.Dia = ?
            """,
            (data,),
        )
    clientes = cursor.fetchall()
    conn.close()

    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=A4)
    c.drawString(50, 800, f"Agenda - {data}")
    y = 750
    for cli in clientes:
        c.drawString(50, y, f"{cli[1]} - {cli[0]} ({cli[2]}) - Barbeiro: {cli[3]}")
        y -= 20
    c.save()
    output.seek(0)
    return send_file(output, download_name=f"agenda_{data}.pdf", as_attachment=True)

# -------------------------- FINANCEIRO ADMIN --------------------------
def _buscar_transacoes_financeiro(cursor, barbearia_id):
    """Mesma consulta da tela financeira (com nome do profissional)."""
    cursor.execute(
        """
        SELECT f.descricao, f.valor, f.tipo_transacao,
               COALESCE(u.nome, f.barbeiro, 'Geral / Estabelecimento') AS profissional,
               f.data
        FROM financeiro f
        LEFT JOIN usuarios u ON f.profissional_id = u.id
        WHERE f.barbearia_id = ?
        ORDER BY f.data DESC
        """,
        (barbearia_id,),
    )
    return cursor.fetchall()


def _calcular_saldo_financeiro(transacoes):
    saldo = 0.0
    for t in transacoes:
        if t[2] == "Receita":
            saldo += float(t[1])
        else:
            saldo -= float(t[1])
    return saldo


def _buscar_faturamento_profissionais(cursor, barbearia_id):
    """Total de receitas agrupado por profissional (para comissões / folha)."""
    cursor.execute(
        """
        SELECT u.nome, SUM(f.valor) AS total_faturado
        FROM financeiro f
        INNER JOIN usuarios u ON f.profissional_id = u.id
        WHERE f.tipo_transacao = 'Receita' AND f.barbearia_id = ?
        GROUP BY u.id, u.nome
        ORDER BY total_faturado DESC
        """,
        (barbearia_id,),
    )
    return cursor.fetchall()


def _inserir_receita_financeiro(
    cursor, descricao, valor, profissional_id, barbearia_id
):
    """Registra receita na tabela financeiro (uso pela agenda e lançamentos manuais)."""
    nome_prof, pid = _nome_profissional_lancamento(
        cursor, profissional_id, barbearia_id
    )
    cursor.execute(
        """
        INSERT INTO financeiro (
            descricao, valor, tipo_transacao, barbeiro, profissional_id,
            barbearia_id, data
        )
        VALUES (?, ?, 'Receita', ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (descricao, valor, nome_prof, pid, barbearia_id),
    )


def _montar_descricao_atendimento(tipo, detalhe, servico_agendado, nome_cliente):
    detalhe = (detalhe or "").strip()
    servico_agendado = (servico_agendado or "").strip()
    if tipo == "Serviço":
        base = detalhe or servico_agendado or "Atendimento"
        return f"Serviço: {base}"
    if tipo == "Venda de Produto":
        base = detalhe or "Produto"
        return f"Venda: {base}"
    if detalhe:
        return detalhe
    if servico_agendado:
        return f"Atendimento — {servico_agendado} ({nome_cliente})"
    return f"Atendimento — {nome_cliente}"


def _nome_profissional_lancamento(cursor, profissional_id, barbearia_id):
    if not profissional_id:
        return "Geral / Estabelecimento", None
    cursor.execute(
        """
        SELECT nome FROM usuarios
        WHERE id = ? AND barbearia_id = ?
          AND role IN ('barbeiro', 'profissional')
        """,
        (profissional_id, barbearia_id),
    )
    row = cursor.fetchone()
    if row:
        return row[0], profissional_id
    return "Geral / Estabelecimento", None


def _formatar_data_transacao(data):
    if hasattr(data, "strftime"):
        return data.strftime("%d/%m/%Y %H:%M")
    return str(data)[:16] if data else ""


def _gerar_excel_financeiro(transacoes, saldo, faturamento_profissionais):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Financeiro"
    ws.append(["Data", "Descrição", "Profissional", "Tipo", "Valor"])
    for t in transacoes:
        ws.append([
            _formatar_data_transacao(t[4]),
            t[0],
            t[3],
            t[2],
            float(t[1]),
        ])
    ws.append([])
    ws.append(["", "", "", "Saldo em caixa", saldo])
    ws.append([])
    ws.append(["Faturamento por Profissional (Receitas)", ""])
    ws.append(["Profissional", "Total faturado (R$)"])
    for nome, total in faturamento_profissionais:
        ws.append([nome, float(total)])
    if not faturamento_profissionais:
        ws.append(["—", 0.0])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _gerar_pdf_financeiro(transacoes, saldo, titulo_estabelecimento, faturamento_profissionais):
    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=A4)
    largura, altura = A4
    y = altura - 2 * cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, titulo_estabelecimento)
    y -= 0.8 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Relatório Financeiro")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Saldo em caixa: R$ {saldo:.2f}")
    y -= 1 * cm

    colunas = ["Data", "Descrição", "Profissional", "Tipo", "Valor"]
    xs = [2 * cm, 4.2 * cm, 9.5 * cm, 14.5 * cm, 17 * cm]
    c.setFont("Helvetica-Bold", 9)
    for i, col in enumerate(colunas):
        c.drawString(xs[i], y, col)
    y -= 0.5 * cm
    c.line(2 * cm, y, largura - 2 * cm, y)
    y -= 0.4 * cm

    c.setFont("Helvetica", 8)
    for t in transacoes:
        if y < 2 * cm:
            c.showPage()
            y = altura - 2 * cm
            c.setFont("Helvetica", 8)
        linha = [
            _formatar_data_transacao(t[4])[:10],
            (t[0] or "")[:28],
            (t[3] or "")[:18],
            t[2] or "",
            f"R$ {float(t[1]):.2f}",
        ]
        for i, texto in enumerate(linha):
            c.drawString(xs[i], y, texto)
        y -= 0.45 * cm

    y -= 0.8 * cm
    if y < 4 * cm:
        c.showPage()
        y = altura - 2 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Faturamento por Profissional (Receitas)")
    y -= 0.6 * cm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(2 * cm, y, "Profissional")
    c.drawString(12 * cm, y, "Total (R$)")
    y -= 0.4 * cm
    c.line(2 * cm, y, largura - 2 * cm, y)
    y -= 0.35 * cm

    c.setFont("Helvetica", 9)
    if faturamento_profissionais:
        for nome, total in faturamento_profissionais:
            if y < 2 * cm:
                c.showPage()
                y = altura - 2 * cm
                c.setFont("Helvetica", 9)
            c.drawString(2 * cm, y, (nome or "")[:40])
            c.drawString(12 * cm, y, f"{float(total):.2f}")
            y -= 0.4 * cm
    else:
        c.drawString(2 * cm, y, "Nenhuma receita vinculada a profissionais.")
        y -= 0.4 * cm

    c.save()
    output.seek(0)
    return output


@app.route("/admin_financeiro")
@requer_plano
def admin_financeiro():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    profissionais = _listar_profissionais(cursor, barbearia_id)
    transacoes = _buscar_transacoes_financeiro(cursor, barbearia_id)
    saldo = _calcular_saldo_financeiro(transacoes)
    faturamento_profissionais = _buscar_faturamento_profissionais(
        cursor, barbearia_id
    )
    total_faturamento_equipe = sum(float(r[1]) for r in faturamento_profissionais)
    conn.close()

    return render_template(
        "admin_financeiro.html",
        transacoes=transacoes,
        saldo=saldo,
        profissionais=profissionais,
        faturamento_profissionais=faturamento_profissionais,
        total_faturamento_equipe=total_faturamento_equipe,
    )


@app.route("/lancar_transacao", methods=["POST"])
@requer_plano
def lancar_transacao():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    descricao = request.form["descricao"]
    valor = request.form["valor"]
    tipo = request.form["tipo_transacao"]
    profissional_id_raw = request.form.get("profissional_id", "").strip()
    profissional_id = int(profissional_id_raw) if profissional_id_raw else None

    conn = get_connection()
    cursor = conn.cursor()
    nome_profissional, profissional_id = _nome_profissional_lancamento(
        cursor, profissional_id, barbearia_id
    )
    cursor.execute(
        """
        INSERT INTO financeiro (
            descricao, valor, tipo_transacao, barbeiro, profissional_id,
            barbearia_id, data
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (descricao, valor, tipo, nome_profissional, profissional_id, barbearia_id),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("admin_financeiro"))


@app.route("/financeiro/exportar/excel")
@requer_plano
def financeiro_exportar_excel():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    transacoes = _buscar_transacoes_financeiro(cursor, barbearia_id)
    saldo = _calcular_saldo_financeiro(transacoes)
    faturamento_profissionais = _buscar_faturamento_profissionais(
        cursor, barbearia_id
    )
    conn.close()

    output = _gerar_excel_financeiro(transacoes, saldo, faturamento_profissionais)
    nome = f"financeiro_{datetime.today().strftime('%Y%m%d')}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nome,
    )


@app.route("/financeiro/exportar/pdf")
@requer_plano
def financeiro_exportar_pdf():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    titulo = session.get("nome_barbearia", "AgendaSimples")

    conn = get_connection()
    cursor = conn.cursor()
    transacoes = _buscar_transacoes_financeiro(cursor, barbearia_id)
    saldo = _calcular_saldo_financeiro(transacoes)
    faturamento_profissionais = _buscar_faturamento_profissionais(
        cursor, barbearia_id
    )
    conn.close()

    output = _gerar_pdf_financeiro(
        transacoes, saldo, titulo, faturamento_profissionais
    )
    nome = f"financeiro_{datetime.today().strftime('%Y%m%d')}.pdf"
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=nome,
    )

# -------------------------- DINÂMICA DA GALERIA --------------------------
@app.route("/admin/galeria", methods=["GET", "POST"])
@requer_plano
def admin_galeria():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        categoria = request.form.get("categoria")
        file = request.files.get("foto")

        if file and file.filename != '':
            upload_dir = 'static/uploads/galeria'
            if not os.path.exists(upload_dir):
                os.makedirs(upload_dir)
                
            extensao = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"foto_{categoria}_{timestamp}.{extensao}"
            
            file.save(os.path.join(upload_dir, filename))

            cursor.execute(
                "INSERT INTO tb_galeria (barbearia_id, categoria, caminho_foto) VALUES (?, ?, ?)",
                (barbearia_id, categoria, filename),
            )
            conn.commit()

    cursor.execute(
        "SELECT id, categoria, caminho_foto FROM tb_galeria WHERE barbearia_id = ? ORDER BY id DESC",
        (barbearia_id,),
    )
    todas_fotos = cursor.fetchall()
    conn.close()
    
    galeria = {"corte": [], "corte_barba": [], "sobrancelha": [], "outros": []}
    for f in todas_fotos:
        if f.categoria in galeria:
            galeria[f.categoria].append({"id": f.id, "foto": f.caminho_foto})
            
    return render_template("admin_galeria.html", galeria=galeria)

@app.route("/eliminar_foto/<int:foto_id>", methods=["POST", "GET"])
@requer_plano
def eliminar_foto(foto_id):
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    barbearia_id = _barbearia_id_admin_obrigatorio()
    if not barbearia_id:
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT caminho_foto FROM tb_galeria WHERE id = ? AND barbearia_id = ?",
        (foto_id, barbearia_id),
    )
    foto = cursor.fetchone()
    
    if foto:
        nome_arquivo = foto[0]
        caminho_completo = os.path.join('static/uploads/galeria', nome_arquivo)
        
        if os.path.exists(caminho_completo):
            try:
                os.remove(caminho_completo)
            except Exception as e:
                print(f"Erro ao deletar arquivo físico: {e}")
            
        cursor.execute(
            "DELETE FROM tb_galeria WHERE id = ? AND barbearia_id = ?",
            (foto_id, barbearia_id),
        )
        conn.commit()
        
    conn.close()
    return redirect(url_for("admin_galeria"))

# -------------------------- CONFIGURAÇÕES DO ADMIN (COM FOTO DE CAPA) --------------------------
@app.route("/admin/configuracoes", methods=["GET", "POST"])
@requer_plano
def admin_configuracoes():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))
        
    conn = get_connection()
    cursor = conn.cursor()
    
    barbearia_id_alvo = _barbearia_id_admin_obrigatorio()
    if not barbearia_id_alvo:
        conn.close()
        return redirect(url_for("login"))

    if request.method == "POST":
        novo_nome = request.form.get("nome_barbearia")
        file_capa = request.files.get("foto_capa")
        file_logotipo = request.files.get("logotipo")
        titulo1 = request.form.get("titulo_catalogo1", "").strip()
        titulo2 = request.form.get("titulo_catalogo2", "").strip()
        titulo3 = request.form.get("titulo_catalogo3", "").strip()
        titulo4 = request.form.get("titulo_catalogo4", "").strip()
        texto_marcar = request.form.get("texto_marcar_direito", "").strip()
        file_fundo_marcar = request.files.get("foto_fundo_marcar")
        link_instagram = _normalizar_link_url(request.form.get("link_instagram", ""))
        link_facebook = _normalizar_link_url(request.form.get("link_facebook", ""))
        link_whatsapp = _normalizar_link_whatsapp(request.form.get("link_whatsapp", ""))

        # 1. Salva nome e títulos dos catálogos da Home
        if novo_nome:
            cursor.execute("UPDATE barbearias SET nome = ? WHERE id = ?", (novo_nome, barbearia_id_alvo))
            session['nome_barbearia'] = novo_nome

        try:
            cursor.execute(
                """
                UPDATE barbearias SET
                    titulo_catalogo1 = ?,
                    titulo_catalogo2 = ?,
                    titulo_catalogo3 = ?,
                    titulo_catalogo4 = ?
                WHERE id = ?
                """,
                (
                    titulo1 or DEFAULT_TITULOS_CATALOGO[0],
                    titulo2 or DEFAULT_TITULOS_CATALOGO[1],
                    titulo3 or DEFAULT_TITULOS_CATALOGO[2],
                    titulo4 or DEFAULT_TITULOS_CATALOGO[3],
                    barbearia_id_alvo,
                ),
            )
        except DbError:
            pass

        _processar_upload_capas_catalogo(request, cursor, barbearia_id_alvo)

        try:
            cursor.execute(
                """
                UPDATE barbearias SET
                    texto_marcar_direito = ?,
                    link_instagram = ?,
                    link_facebook = ?,
                    link_whatsapp = ?
                WHERE id = ?
                """,
                (
                    texto_marcar or novo_nome or "AgendaSimples",
                    link_instagram,
                    link_facebook,
                    link_whatsapp,
                    barbearia_id_alvo,
                ),
            )
        except DbError:
            try:
                cursor.execute(
                    "UPDATE barbearias SET texto_marcar_direito = ? WHERE id = ?",
                    (texto_marcar or novo_nome or "AgendaSimples", barbearia_id_alvo),
                )
            except DbError:
                pass

        if file_fundo_marcar and file_fundo_marcar.filename:
            upload_banner_dir = "static/uploads/banners"
            os.makedirs(upload_banner_dir, exist_ok=True)
            ext = (
                file_fundo_marcar.filename.rsplit(".", 1)[1].lower()
                if "." in file_fundo_marcar.filename
                else "jpg"
            )
            banner_filename = f"fundo_direito_{barbearia_id_alvo}.{ext}"
            file_fundo_marcar.save(os.path.join(upload_banner_dir, banner_filename))
            try:
                cursor.execute(
                    """
                    UPDATE barbearias SET foto_fundo_direito_url = ? WHERE id = ?
                    """,
                    (banner_filename, barbearia_id_alvo),
                )
            except DbError:
                pass

        # 2. Upload do logotipo (Home)
        if file_logotipo and file_logotipo.filename:
            upload_logo_dir = "static/uploads/logo"
            os.makedirs(upload_logo_dir, exist_ok=True)
            ext = (
                file_logotipo.filename.rsplit(".", 1)[1].lower()
                if "." in file_logotipo.filename
                else "png"
            )
            logo_filename = f"logo_barbearia_{barbearia_id_alvo}.{ext}"
            file_logotipo.save(os.path.join(upload_logo_dir, logo_filename))
            try:
                cursor.execute(
                    "UPDATE barbearias SET logotipo_url = ? WHERE id = ?",
                    (logo_filename, barbearia_id_alvo),
                )
            except DbError:
                pass

        # 3. Processa o upload da Foto de Capa se o usuário escolheu um arquivo
        if file_capa and file_capa.filename != '':
            upload_dir = 'static/uploads/capa'
            if not os.path.exists(upload_dir):
                os.makedirs(upload_dir)
                
            extensao = file_capa.filename.rsplit('.', 1)[1].lower() if '.' in file_capa.filename else 'jpg'
            filename = f"capa_barbearia_{barbearia_id_alvo}.{extensao}"
            
            # Salva o arquivo físico na pasta
            file_capa.save(os.path.join(upload_dir, filename))
            
            # Verifica se já existe o registro 'foto_capa' na tb_configuracoes
            cursor.execute("SELECT COUNT(*) FROM tb_configuracoes WHERE chave = 'foto_capa'")
            existe_config = cursor.fetchone()[0]
            
            if existe_config > 0:
                cursor.execute("UPDATE tb_configuracoes SET valor = ? WHERE chave = 'foto_capa'", (filename,))
            else:
                cursor.execute("INSERT INTO tb_configuracoes (chave, valor) VALUES ('foto_capa', ?)", (filename,))
        
        conn.commit()
        conn.close()
        return redirect(url_for("admin_configuracoes"))
        
    # --- GERENCIAMENTO DO SINAL DO GET (CARREGAMENTO DA TELA) ---
    # Busca o nome atual da barbearia
    configs = _carregar_configs_home(cursor, barbearia_id_alvo)

    cursor.execute("SELECT valor FROM tb_configuracoes WHERE chave = 'foto_capa'")
    config_foto = cursor.fetchone()
    foto_capa = config_foto[0] if config_foto else None

    conn.close()
    return render_template(
        "admin_configuracoes.html",
        nome_atual=configs["nome_negocio"],
        foto_capa=foto_capa,
        logotipo_url=configs["logotipo_url"],
        titulo_catalogo1=configs["titulo_catalogo1"],
        titulo_catalogo2=configs["titulo_catalogo2"],
        titulo_catalogo3=configs["titulo_catalogo3"],
        titulo_catalogo4=configs["titulo_catalogo4"],
        texto_marcar_direito=configs.get("texto_marcar_direito") or configs["nome_negocio"],
        foto_fundo_direito_url=configs.get("foto_fundo_direito_url"),
        capa_catalogo1=configs.get("capa_catalogo1"),
        capa_catalogo2=configs.get("capa_catalogo2"),
        capa_catalogo3=configs.get("capa_catalogo3"),
        capa_catalogo4=configs.get("capa_catalogo4"),
        link_instagram=configs.get("link_instagram") or "",
        link_facebook=configs.get("link_facebook") or "",
        link_whatsapp=configs.get("link_whatsapp") or "",
    )

@app.route("/admin/clientes")
@requer_plano
def admin_clientes():
    return render_template("admin_clientes.html")

@app.route("/admin/assinatura")
@requer_plano
def admin_assinatura():
    """Status do plano (rota liberada pelo decorator quando trial/plano ativo)."""
    from subscriptions import obter_assinatura, assinatura_permite_acesso

    barbearia_id = session.get("barbearia_id") or session.get("user_id")
    conn = get_connection()
    cursor = conn.cursor()
    assinatura = obter_assinatura(cursor, barbearia_id)
    conn.close()
    return render_template(
        "admin_assinatura.html",
        assinatura=assinatura,
        acesso_ok=assinatura_permite_acesso(assinatura),
        trial_days=cfg.TRIAL_DAYS,
    )

# -------------------------- RODAR --------------------------
if __name__ == "__main__":
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"Acesse em: http://{local_ip}:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)