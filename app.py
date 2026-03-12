from flask import Flask, render_template, request, redirect, url_for, send_file, session
import pyodbc
from datetime import datetime, timedelta
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import pywhatkit
import socket

app = Flask(__name__)
app.secret_key = "CHRISTIAN_BARBESHOP_KEY_2025"

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

# -------------------------- LOGIN / LOGOUT --------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session.clear()
        email = request.form.get("email")
        senha = request.form.get("password")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome, role FROM usuarios WHERE email=? AND senha=?", (email, senha))
        user = cursor.fetchone()
        conn.close()
        if user:
            session["user_id"] = user.id
            session["user_name"] = user.nome
            session["role"] = user.role
            if user.role == "admin":
                return redirect(url_for("admin_agenda"))
            else:
                return redirect(url_for("agenda"))
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
        d_str = r.Dia.strftime("%Y-%m-%d")
        if d_str in agenda_data and r.Hora in agenda_data[d_str]:
            agenda_data[d_str][r.Hora] = {
                "nome": r.Nome,
                "servico": r.Servico,
                "whatsapp": r.Whatsapp
            }

    return render_template("agenda.html", agenda=agenda_data, horarios=HORARIOS, datetime=datetime, dias_pt=DIAS_PT)

# -------------------------- AGENDA ADMIN --------------------------
@app.route("/admin_agenda")
def admin_agenda():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.Nome, c.Dia, c.Hora, c.Servico, c.Whatsapp, u.nome as barbeiro_nome, c.barbeiro_id
        FROM dbo.Clientes c INNER JOIN usuarios u ON c.barbeiro_id = u.id
        ORDER BY c.Dia, c.Hora
    """)
    registros = cursor.fetchall()

    hoje = datetime.today()
    agenda_data = {(hoje + timedelta(days=i)).strftime("%Y-%m-%d"): {h: None for h in HORARIOS} for i in range(28)}

    for r in registros:
        d_str = r.Dia.strftime("%Y-%m-%d")
        if d_str in agenda_data and r.Hora in agenda_data[d_str]:
            if agenda_data[d_str][r.Hora] is None:
                agenda_data[d_str][r.Hora] = []
            agenda_data[d_str][r.Hora].append({
                "nome": r.Nome,
                "servico": r.Servico,
                "whatsapp": getattr(r, "Whatsapp", ""),
                "barbeiro_nome": r.barbeiro_nome,
                "barbeiro_id": r.barbeiro_id
            })

    cursor.execute("SELECT id, nome FROM usuarios WHERE role = 'barbeiro' ORDER BY nome")
    barbeiros = cursor.fetchall()
    conn.close()

    return render_template("admin_agenda.html", agenda=agenda_data, horarios=HORARIOS, datetime=datetime, dias_pt=DIAS_PT, barbeiros=barbeiros)

# -------------------------- AGENDAR --------------------------
# ---------------- AGENDAR ----------------

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

    cursor.execute(
        "SELECT nome FROM usuarios WHERE id=?",
        (barbeiro_id,)
    )

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
    path = "agenda.xlsx"
    wb.save(path)
    return send_file(path, as_attachment=True)

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

    path = f"agenda_{data}.pdf"
    c = canvas.Canvas(path, pagesize=A4)
    c.drawString(50, 800, f"Agenda - {data}")
    y = 750
    for cli in clientes:
        c.drawString(50, y, f"{cli[1]} - {cli[0]} ({cli[2]}) - Barbeiro: {cli[3]}")
        y -= 20
    c.save()
    return send_file(path, as_attachment=True)

# -------------------------- RODAR --------------------------
if __name__ == "__main__":
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"Acesse em: http://{local_ip}:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)