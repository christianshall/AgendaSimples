# Tradução multi-idioma (Flask-Babel)

Idiomas: **PT** (padrão), **EN**, **ES**.

## Estrutura do projeto

```
AgendaSimples/
├── babel.cfg              # Onde o Babel extrai textos (.py e templates)
├── messages.pot           # Catálogo mestre (gerado)
├── translations/
│   ├── en/LC_MESSAGES/messages.po
│   ├── en/LC_MESSAGES/messages.mo  (compilado)
│   └── es/LC_MESSAGES/messages.po
└── templates/
    └── _lang_selector.html   # Seletor PT | EN | ES
```

## Sintaxe nos templates HTML

Texto simples:

```html
<h1>{{ _('Marcar horário') }}</h1>
```

Com variável:

```html
<p>{{ _('Agende com') }} {{ configs.nome_negocio }}</p>
```

Bloco longo (opcional):

```html
{% trans %}Texto com <strong>HTML</strong>{% endtrans %}
```

No Python (`app.py`):

```python
from flask_babel import _
flash(_("Acesso negado!"), "error")
```

## Comandos no terminal (Windows)

Ative o venv e instale o Babel CLI (já vem com `flask-babel`):

```powershell
cd C:\MinGW\AgendaSimples
.\.venv\Scripts\Activate.ps1
pip install flask-babel babel
```

### 1. Extrair textos para `messages.pot`

```powershell
pybabel extract -F babel.cfg -o messages.pot .
```

### 2. Criar idioma novo (só na primeira vez)

```powershell
pybabel init -i messages.pot -d translations -l pt
pybabel init -i messages.pot -d translations -l en
pybabel init -i messages.pot -d translations -l es
```

### 3. Atualizar `.po` após mudar HTML/Python

```powershell
pybabel update -i messages.pot -d translations
```

Edite os arquivos `translations/en/LC_MESSAGES/messages.po` e `translations/es/LC_MESSAGES/messages.po` preenchendo `msgstr` de cada `msgid`.

### 4. Compilar para o app usar

```powershell
pybabel compile -d translations
```

Reinicie o Flask após compilar.

### Atualizar só a Home (atalho)

Depois de editar `templates/home.html`:

```powershell
pybabel extract -F babel.cfg -o messages.pot .
pybabel update -i messages.pot -d translations
python preencher_traducoes_home.py
pybabel compile -d translations
```

Edite manualmente qualquer `msgstr` vazio em `translations/en/...` e `translations/es/...` antes do `compile`, se precisar de textos novos não cobertos pelo script.

## Trocar idioma na interface

- URLs: `/mudar_idioma/pt`, `/mudar_idioma/en`, `/mudar_idioma/es`
- O seletor **PT | EN | ES** está na Home e no painel Admin.
- O idioma fica em `session['lang']`; se vazio, usa o navegador.

## Incluir o seletor em outro template

```html
<link rel="stylesheet" href="{{ url_for('static', filename='lang-switcher.css') }}">
{% include '_lang_selector.html' %}
```
