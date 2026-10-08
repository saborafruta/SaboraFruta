from django.db import transaction

from .models import ConfiguracaoWhatsApp, MenuWhatsApp, OpcaoMenuWhatsApp


@transaction.atomic
def garantir_fluxo_padrao(configuracao):
    """Cria o fluxo inicial sem sobrescrever personalizações existentes."""
    menu = (
        configuracao.menus.filter(principal=True, ativo=True).first()
        or configuracao.menus.filter(ativo=True).first()
    )
    if menu:
        return menu

    menu, criado = MenuWhatsApp.objects.get_or_create(
        configuracao=configuracao,
        codigo='principal',
        defaults={
            'filial': configuracao.filial,
            'nome': 'Menu principal',
            'mensagem': 'Como posso ajudar?',
            'principal': True,
        },
    )
    if not criado:
        menu.principal = True
        menu.ativo = True
        menu.save(update_fields=['principal', 'ativo', 'updated_at'])
    if menu.opcoes.exists():
        return menu
    opcoes = []
    if configuracao.modo_atendimento in {
        ConfiguracaoWhatsApp.ModoAtendimento.AGENDA,
        ConfiguracaoWhatsApp.ModoAtendimento.AMBOS,
    }:
        opcoes.append(OpcaoMenuWhatsApp(
            menu=menu, chave=str(len(opcoes) + 1), titulo='Fazer um agendamento',
            acao=OpcaoMenuWhatsApp.Acao.AGENDA,
            palavras_chave='agendar, agenda, marcar horário', ordem=len(opcoes),
        ))
    if configuracao.modo_atendimento in {
        ConfiguracaoWhatsApp.ModoAtendimento.CATALOGO,
        ConfiguracaoWhatsApp.ModoAtendimento.AMBOS,
    }:
        opcoes.append(OpcaoMenuWhatsApp(
            menu=menu, chave=str(len(opcoes) + 1), titulo='Fazer um pedido',
            acao=OpcaoMenuWhatsApp.Acao.CATALOGO,
            palavras_chave='comprar, catálogo, catalogo, pedido', ordem=len(opcoes),
        ))
    opcoes.extend([
        OpcaoMenuWhatsApp(
            menu=menu, chave=str(len(opcoes) + 1), titulo='Falar com um atendente',
            acao=OpcaoMenuWhatsApp.Acao.ATENDIMENTO_HUMANO,
            palavras_chave='atendente, humano, pessoa, falar com atendente', ordem=len(opcoes),
        ),
        OpcaoMenuWhatsApp(
            menu=menu, chave=str(len(opcoes) + 2), titulo='Encerrar a conversa',
            acao=OpcaoMenuWhatsApp.Acao.ENCERRAR,
            palavras_chave='encerrar, finalizar, sair', ordem=len(opcoes) + 1,
        ),
    ])
    OpcaoMenuWhatsApp.objects.bulk_create(opcoes)
    return menu
