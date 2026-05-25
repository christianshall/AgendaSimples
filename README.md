# AgendaSimples 📅 • Christian Shall Barber Shop

O **AgendaSimples** é uma aplicação web completa de gestão e agendamento em tempo real desenvolvida para a **Christian Shall Barber Shop**. O sistema automatiza o fluxo de marcações de clientes, otimiza a organização das agendas dos barbeiros e centraliza o controle financeiro e a auditoria para o administrador.

---

## 🚀 Tecnologias Utilizadas

- **Backend:** Python 3.11 & Flask (Micro-framework)
- **Frontend:** HTML5, CSS3 (Custom Dark Mode) & Jinja2 Templates
- **Banco de Dados:** SQL Estruturado com transações via cursores Python
- **Ambiente:** VS Code & Isolamento de dependências via Virtual Environment (`.venv`)

---

## 🛠️ Arquitetura e Módulos do Sistema

1. **Módulo de Apresentação (Frontend):** Templates dinâmicos (`marcar.html`, `admin_agenda.html`) integrados à estilização global `style.css`.
2. **Módulo de Controle (Core Backend):** Arquivo centralizado `app.py` responsável pelas regras de negócio, tratamento de rotas, middlewares de segurança (`session["role"]`) e geração de relatórios (`pdf_diario`).
3. **Módulo de Dados (Persistência):** Arquivo de migração estrutural `AgendaSimples.sql`.

---

## ⚙️ Como Executar o Projeto Localmente

### 1. Clonar o Repositório
```bash
git clone [https://github.com/christianshall/AgendaSimples.git](https://github.com/christianshall/AgendaSimples.git)
cd AgendaSimples
