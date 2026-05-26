"""Preenche msgstr EN/ES para strings da Home (rodar após pybabel update)."""
from pathlib import Path

TRADUCOES = {
    "en": {
        "Agendamento online profissional": "Professional online booking",
        "Bem-vindo": "Welcome",
        "Agende com": "Book with",
        "Qualidade, praticidade e o portfólio do seu profissional em um só lugar.": "Quality, convenience, and your professional's portfolio in one place.",
        "Marcar Horário": "Book appointment",
        "Marcar horário": "Book appointment",
        "Acesso ao painel da equipe": "Team dashboard access",
        "Nossos catálogos": "Our catalogs",
        "Toque em um card para ver o portfólio de fotos": "Tap a card to view the photo portfolio",
        "fotos": "photos",
        "%(num)d foto": "%(num)d photo",
        "%(num)d fotos": "%(num)d photos",
        "Ver portfólio": "View portfolio",
        "Nenhuma foto neste catálogo ainda.": "No photos in this catalog yet.",
        "Fechar": "Close",
        "Logotipo de %(nome)s": "Logo of %(nome)s",
        "Ícone do estabelecimento": "Business icon",
        "Como deseja continuar?": "How would you like to continue?",
        "Abrir portfólio: %(titulo)s": "Open portfolio: %(titulo)s",
        "Galeria de fotos": "Photo gallery",
        "Foto %(num)d de %(total)d — %(titulo)s": "Photo %(num)d of %(total)d — %(titulo)s",
        "Foto anterior": "Previous photo",
        "Próxima foto": "Next photo",
        "Instagram": "Instagram",
        "Facebook": "Facebook",
        "WhatsApp": "WhatsApp",
        "© %(nome)s · %(marca)s": "© %(nome)s · %(marca)s",
        "AgendaSimples": "AgendaSimples",
        "Catálogo 1": "Catalog 1",
        "Catálogo 2": "Catalog 2",
        "Catálogo 3": "Catalog 3",
        "Catálogo 4": "Catalog 4",
        "Agende seu horário": "Book your appointment",
        "Reserve seu horário em poucos cliques": "Book your appointment in a few clicks",
        "Agendar": "Book now",
        "Redes sociais": "Social media",
        "Siga-nos e fique por dentro das novidades": "Follow us for news and updates",
    },
    "es": {
        "Agendamento online profissional": "Reserva online profesional",
        "Bem-vindo": "Bienvenido",
        "Agende com": "Reserve con",
        "Qualidade, praticidade e o portfólio do seu profissional em um só lugar.": "Calidad, practicidad y el portafolio de su profesional en un solo lugar.",
        "Marcar Horário": "Reservar horario",
        "Marcar horário": "Reservar horario",
        "Acesso ao painel da equipe": "Acceso al panel del equipo",
        "Nossos catálogos": "Nuestros catálogos",
        "Toque em um card para ver o portfólio de fotos": "Toque una tarjeta para ver el portafolio de fotos",
        "fotos": "fotos",
        "%(num)d foto": "%(num)d foto",
        "%(num)d fotos": "%(num)d fotos",
        "Ver portfólio": "Ver portafolio",
        "Nenhuma foto neste catálogo ainda.": "Aún no hay fotos en este catálogo.",
        "Fechar": "Cerrar",
        "Logotipo de %(nome)s": "Logotipo de %(nome)s",
        "Ícone do estabelecimento": "Ícono del establecimiento",
        "Como deseja continuar?": "¿Cómo desea continuar?",
        "Abrir portfólio: %(titulo)s": "Abrir portafolio: %(titulo)s",
        "Galeria de fotos": "Galería de fotos",
        "Foto %(num)d de %(total)d — %(titulo)s": "Foto %(num)d de %(total)d — %(titulo)s",
        "Foto anterior": "Foto anterior",
        "Próxima foto": "Foto siguiente",
        "Instagram": "Instagram",
        "Facebook": "Facebook",
        "WhatsApp": "WhatsApp",
        "© %(nome)s · %(marca)s": "© %(nome)s · %(marca)s",
        "AgendaSimples": "AgendaSimples",
        "Catálogo 1": "Catálogo 1",
        "Catálogo 2": "Catálogo 2",
        "Catálogo 3": "Catálogo 3",
        "Catálogo 4": "Catálogo 4",
        "Agende seu horário": "Reserve su horario",
        "Reserve seu horário em poucos cliques": "Reserve su horario en pocos clics",
        "Agendar": "Reservar",
        "Redes sociais": "Redes sociales",
        "Siga-nos e fique por dentro das novidades": "Síganos y manténgase al día",
    },
}


def aplicar(po_path: Path, lang: str):
    texto = po_path.read_text(encoding="utf-8")
    for msgid, msgstr in TRADUCOES[lang].items():
        bloco_vazio = f'msgid "{msgid}"\nmsgstr ""'
        bloco_ok = f'msgid "{msgid}"\nmsgstr "{msgstr}"'
        if bloco_vazio in texto:
            texto = texto.replace(bloco_vazio, bloco_ok, 1)
        elif f'msgid "{msgid}"' in texto and f'msgstr "{msgstr}"' not in texto:
            import re
            texto = re.sub(
                rf'msgid "{re.escape(msgid)}"\n(?:#, fuzzy\n)?msgstr "[^"]*"',
                bloco_ok,
                texto,
                count=1,
            )
    po_path.write_text(texto, encoding="utf-8")
    print(f"OK — {po_path}")


def main():
    base = Path(__file__).parent / "translations"
    aplicar(base / "en" / "LC_MESSAGES" / "messages.po", "en")
    aplicar(base / "es" / "LC_MESSAGES" / "messages.po", "es")


if __name__ == "__main__":
    main()
