from flask import Flask, render_template, request, redirect, url_for, send_file, session
import pyodbc
from datetime import datetime, timedelta
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import pywhatkit
import socket
import io
import re
import os

# 1. IMPORTAR O FLASK-BABEL
from flask_babel import Babel, _

app = Flask(__name__)
app.secret_key = "CHRISTIAN_BARBESHOP_KEY_2025"

# 2. CONFIGURAR O BABEL
app.config['BABEL_DEFAULT_LOCALE'] = 'pt'
app.config['BABEL_SUPPORTED_LOCALES'] = ['pt', 'en', 'es']

def get_locale():
    # Verifica o idioma salvo na sessão pelo botão
    lang = session.get('lang')
    if lang:
        print(f"--> [BABEL] Idioma carregado da sessão: {lang}")
        return lang
    
    # Se não tiver na sessão, tenta o navegador
    match = request.accept_languages.best_match(app.config['BABEL_SUPPORTED_LOCALES'])
    print(f"--> [BABEL] Idioma padrão do navegador detectado: {match or 'pt'}")
    return match or 'pt'

babel = Babel(app, locale_selector=get_locale)

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

# -------------------------- CONEXÃO SQL SERVER --------------------------
def get_connection():
    return pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=DESKTOP-V1OSISF;"
        "Database=AgendaSimples;"
        "Trusted_Connection=yes;"
    )

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

# 3. ROTA PARA O ADMIN MUDAR O IDIOMA VIA BOTÃO
@app.route("/mudar_idioma/<string:codigo_idioma>")
def mudar_idioma(codigo_idioma):
    if codigo_idioma in app.config['BABEL_SUPPORTED_LOCALES']:
        session['lang'] = codigo_idioma
        print(f"--> [ROTA] Sessão alterada com sucesso para: {codigo_idioma}")
    
    proxima_pagina = request.args.get("next") or request.referrer or url_for('home')
    return redirect(proxima_pagina)

# -------------------------- ROTAS DO SAAS / CADASTRO --------------------------

@app.route("/cadastro_barbearia", methods=["GET", "POST"])
def cadastro_barbearia():
    if request.method == "POST":
        nome = request.form.get("nome_barbearia")
        email = request.form.get("email")
        senha = request.form.get("password")
        slug = slugify(nome)
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Insere a nova barbearia no ecossistema
        cursor.execute("""
            INSERT INTO barbearias (nome, slug, email, senha, plano_ativo)
            VALUES (?, ?, ?, ?, 1)
        """, (nome, slug, email, senha))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
    return render_template("cadastro_barbearia.html")

# -------------------------- LOGIN / LOGOUT --------------------------
@app.route("/")
def home():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Busca as fotos gerais da galeria para exibir na Home principal
    cursor.execute("SELECT id, categoria, caminho_foto FROM tb_galeria ORDER BY id DESC")
    todas_fotos = cursor.fetchall()
    conn.close()
    
    galeria = {"corte": [], "corte_barba": [], "sobrancelha": [], "outros": []}
    for f in todas_fotos:
        if f.categoria in galeria:
            galeria[f.categoria].append({"id": f.id, "foto": f.caminho_foto})
            
    return render_template("home.html", galeria=galeria)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session.clear()
        email = request.form.get("email")
        senha = request.form.get("password")
        conn = get_connection()
        cursor = conn.cursor()
        
        # Busca na tabela de usuários original
        cursor.execute("SELECT id, nome, role FROM usuarios WHERE email=? AND senha=?", (email, senha))
        user = cursor.fetchone()
        
        if user:
            session["user_id"] = user.id
            session["user_name"] = user.nome
            session["role"] = user.role
            
            # BUSCA O NOME DA BARBEARIA AUTOMATICAMENTE NO LOGIN
            cursor.execute("SELECT nome FROM barbearias WHERE id = ?", (user.id,))
            b_nome = cursor.fetchone()
            if b_nome:
                session["nome_barbearia"] = b_nome[0]
            else:
                cursor.execute("SELECT TOP 1 nome FROM barbearias")
                primeira = cursor.fetchone()
                session["nome_barbearia"] = primeira[0] if primeira else "Christian Shall Barber Shop"
            
            conn.close()
            if user.role == "admin":
                return redirect(url_for("admin_agenda"))
            else:
                return redirect(url_for("agenda"))
                
        conn.close()
        return "Usuário ou senha incorretos!"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# -------------------------- AGENDA BARBEIRO --------------------------
@app.route("/agenda")
def agenda():
    if "role" not in session or session["role"] != "barbeiro":
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    
    # 🌟 ATUALIZAÇÃO: Busca a foto de capa para exibir no topo do HTML
    foto_capa = obter_foto_capa(cursor)

    cursor.execute("""
        SELECT Nome, Dia, Hora, Servico, Whatsapp
        FROM dbo.Clientes
        WHERE barbeiro_id = ?
        ORDER BY Dia, Hora
    """, (session["user_id"],))
    registros = cursor.fetchall()
    conn.close()

    hoje = datetime.today()
    agenda_data = {(hoje + timedelta(days=i)).strftime("%Y-%m-%d"): {h: None for h in HORARIOS} for i in range(28)}

    for r in registros:
        d_str = r.Dia.strftime("%Y-%m-%d") if isinstance(r.Dia, datetime) else str(r.Dia)
        h_str = str(r.Hora)[:5]
        if d_str in agenda_data and h_str in agenda_data[d_str]:
            agenda_data[d_str][h_str] = {
                "nome": r.Nome,
                "servico": r.Servico,
                "whatsapp": r.Whatsapp
            }

    # 🌟 ATUALIZAÇÃO: Enviando foto_capa para o HTML
    return render_template("agenda.html", agenda=agenda_data, horarios=HORARIOS, datetime=datetime, dias_pt=DIAS_PT, foto_capa=foto_capa)

# -------------------------- AGENDA ADMIN --------------------------
@app.route("/admin_agenda")
def admin_agenda():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    
    # 🌟 ATUALIZAÇÃO: Busca a foto de capa para exibir no topo do HTML
    foto_capa = obter_foto_capa(cursor)

    cursor.execute("""
        SELECT c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp, u.nome as barbeiro_nome, c.barbeiro_id
        FROM dbo.Clientes c INNER JOIN usuarios u ON c.barbeiro_id = u.id
        ORDER BY c.Dia, c.Hora
    """)
    registros = cursor.fetchall()

    hoje = datetime.today()
    agenda_data = {(hoje + timedelta(days=i)).strftime("%Y-%m-%d"): {h: None for h in HORARIOS} for i in range(28)}

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
                "barbeiro_id": r.barbeiro_id
            })

    cursor.execute("SELECT id, nome FROM usuarios WHERE role = 'barbeiro' ORDER BY nome")
    barbeiros = cursor.fetchall()
    conn.close()

    # 🌟 ATUALIZAÇÃO: Enviando foto_capa para o HTML
    return render_template("admin_agenda.html", agenda=agenda_data, horarios=HORARIOS, datetime=datetime, dias_pt=DIAS_PT, barbeiros=barbeiros, foto_capa=foto_capa)

# -------------------------- AGENDAR --------------------------
@app.route("/agendar", methods=["POST"])
def agendar():
    nome = request.form["nome"]
    data = request.form["data"]
    hora = request.form["hora"]
    servico = request.form["servico"]
    whatsapp = request.form.get("whatsapp","")
    barbeiro_id = request.form["barbeiro_id"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT COUNT(*) FROM Clientes
    WHERE Dia=? AND Hora=? AND barbeiro_id=?
    """,(data,hora,barbeiro_id))

    if cursor.fetchone()[0] > 0:
        conn.close()
        return "Horário ocupado"

    cursor.execute("""
    INSERT INTO Clientes (Nome,Dia,Hora,Servico,Whatsapp,barbeiro_id)
    VALUES (?,?,?,?,?,?)
    """,(nome,data,hora,servico,whatsapp,barbeiro_id))
    conn.commit()

    cursor.execute("SELECT nome FROM usuarios WHERE id=?", (barbeiro_id,))
    barbeiro = cursor.fetchone()[0]
    conn.close()

    return render_template(
        "sucesso_agendamento.html",
        nome=nome,
        barbeiro=barbeiro,
        data=data,
        hora=hora,
        servico=servico
    )

# -------------------------- WHATSAPP / EDITAR / EXCLUIR --------------------------
@app.route("/editar/<string:data>/<string:hora>", methods=["GET", "POST"])
def editar(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    if request.method == "POST":
        cursor.execute("UPDATE dbo.Clientes SET Nome=?, Servico=?, Whatsapp=? WHERE Dia=? AND Hora=? AND barbeiro_id=?",
                       (request.form["nome"], request.form["servico"], request.form.get("whatsapp",""), data, hora, barbeiro_id))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_agenda" if session["role"]=="admin" else "agenda"))
    cursor.execute("SELECT Nome, Servico, Whatsapp FROM dbo.Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    cliente = cursor.fetchone()
    conn.close()
    return render_template("editar.html", cliente=cliente, data=data, hora=hora, barbeiro_id=barbeiro_id)

@app.route("/excluir/<string:data>/<string:hora>")
def excluir(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM dbo.Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_agenda" if session["role"]=="admin" else "agenda"))

@app.route("/whatsapp/<string:data>/<string:hora>")
def enviar_whatsapp(data, hora):
    barbeiro_id = request.args.get("barbeiro_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Nome, Whatsapp FROM dbo.Clientes WHERE Dia=? AND Hora=? AND barbeiro_id=?", (data, hora, barbeiro_id))
    cliente = cursor.fetchone()
    conn.close()
    if not cliente or not cliente[1]:
        return "⚠️ WhatsApp não cadastrado!"
    numero = "+55" + cliente[1].strip() if not cliente[1].startswith("+") else cliente[1].strip()
    try:
        pywhatkit.sendwhatmsg_instantly(numero, f"Lembrete: {cliente[0]}, seu horário é {data} às {hora}.", wait_time=15)
        return "✅ Enviado!"
    except:
        return "❌ Erro ao enviar."

# -------------------------- MARCAR AGENDAMENTO CLIENTE --------------------------
@app.route("/marcar")
def marcar():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM usuarios WHERE role = 'barbeiro' ORDER BY nome")
    barbeiros = cursor.fetchall()
    conn.close()
    return render_template("marcar_agendamento.html", barbeiros=barbeiros, horarios=HORARIOS)

# -------------------------- EXPORTAR PDF / EXCEL --------------------------
@app.route("/exportar_excel")
def exportar_excel():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Nome, Dia, Hora, Servico FROM dbo.Clientes")
    dados = cursor.fetchall()
    conn.close()

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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT c.Nome, c.Hora, c.Servico, u.nome FROM dbo.Clientes c JOIN usuarios u ON c.barbeiro_id = u.id WHERE c.Dia=?", (data,))
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
@app.route("/admin_financeiro")
def admin_financeiro():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT descricao, valor, tipo_transacao, barbeiro, data
        FROM financeiro
        ORDER BY data DESC
    """)
    transacoes = cursor.fetchall()

    saldo = 0
    for t in transacoes:
        if t[2] == "Receita":
            saldo += float(t[1])
        else:
            saldo -= float(t[1])
    conn.close()

    return render_template("admin_financeiro.html", transacoes=transacoes, saldo=saldo)

@app.route("/lancar_transacao", methods=["POST"])
def lancar_transacao():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    descricao = request.form["descricao"]
    valor = request.form["valor"]
    tipo = request.form["tipo_transacao"]
    barbeiro = session.get("user_name")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO financeiro (descricao, valor, tipo_transacao, barbeiro, data)
        VALUES (?, ?, ?, ?, GETDATE())
    """, (descricao, valor, tipo, barbeiro))
    conn.commit()
    conn.close()

    return redirect(url_for("admin_financeiro"))

# -------------------------- DINÂMICA DA GALERIA --------------------------
@app.route("/admin/galeria", methods=["GET", "POST"])
def admin_galeria():
    if "role" not in session or session["role"] != "admin":
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

            cursor.execute("INSERT INTO tb_galeria (barbearia_id, categoria, caminho_foto) VALUES (?, ?, ?)", 
                           (session['user_id'], categoria, filename))
            conn.commit()
    
    cursor.execute("SELECT id, categoria, caminho_foto FROM tb_galeria WHERE barbearia_id = ? ORDER BY id DESC", (session['user_id'],))
    todas_fotos = cursor.fetchall()
    conn.close()
    
    galeria = {"corte": [], "corte_barba": [], "sobrancelha": [], "outros": []}
    for f in todas_fotos:
        if f.categoria in galeria:
            galeria[f.categoria].append({"id": f.id, "foto": f.caminho_foto})
            
    return render_template("admin_galeria.html", galeria=galeria)

@app.route("/eliminar_foto/<int:foto_id>", methods=["POST", "GET"])
def eliminar_foto(foto_id):
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))
        
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT caminho_foto FROM tb_galeria WHERE id = ?", (foto_id,))
    foto = cursor.fetchone()
    
    if foto:
        nome_arquivo = foto[0]
        caminho_completo = os.path.join('static/uploads/galeria', nome_arquivo)
        
        if os.path.exists(caminho_completo):
            try:
                os.remove(caminho_completo)
            except Exception as e:
                print(f"Erro ao deletar arquivo físico: {e}")
            
        cursor.execute("DELETE FROM tb_galeria WHERE id = ?", (foto_id,))
        conn.commit()
        
    conn.close()
    return redirect(url_for("admin_galeria"))

# -------------------------- CONFIGURAÇÕES DO ADMIN (COM FOTO DE CAPA) --------------------------
@app.route("/admin/configuracoes", methods=["GET", "POST"])
def admin_configuracoes():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # Busca a barbearia alvo (seu ID de admin)
    cursor.execute("SELECT id FROM barbearias WHERE id = ?", (session["user_id"],))
    barbearia_existe = cursor.fetchone()
    barbearia_id_alvo = session["user_id"] if barbearia_existe else 1

    if request.method == "POST":
        novo_nome = request.form.get("nome_barbearia")
        file_capa = request.files.get("foto_capa")
        
        # 1. Salva o novo nome na tabela barbearias (Lógica que já funcionava)
        if novo_nome:
            cursor.execute("UPDATE barbearias SET nome = ? WHERE id = ?", (novo_nome, barbearia_id_alvo))
            session['nome_barbearia'] = novo_nome

        # 2. Processa o upload da Foto de Capa se o usuário escolheu um arquivo
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
    cursor.execute("SELECT nome FROM barbearias WHERE id = ?", (barbearia_id_alvo,))
    barbearia = cursor.fetchone()
    nome_atual = barbearia[0] if barbearia else "Minha Barbearia"
    
    # Busca o nome do arquivo da foto de capa na tb_configuracoes
    cursor.execute("SELECT valor FROM tb_configuracoes WHERE chave = 'foto_capa'")
    config_foto = cursor.fetchone()
    foto_capa = config_foto[0] if config_foto else None
    
    conn.close()
    return render_template("admin_configuracoes.html", nome_atual=nome_atual, foto_capa=foto_capa)

@app.route("/admin/clientes")
def admin_clientes(): return render_template("admin_clientes.html")

# -------------------------- RODAR --------------------------
if __name__ == "__main__":
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"Acesse em: http://{local_ip}:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)