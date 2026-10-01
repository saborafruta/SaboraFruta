from django.db import transaction

from .models import MenuWhatsApp, OpcaoMenuWhatsApp


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
    OpcaoMenuWhatsApp.objects.bulk_create([
        OpcaoMenuWhatsApp(
            menu=menu, chave='1', titulo='Fazer um agendamento',
            acao=OpcaoMenuWhatsApp.Acao.AGENDA, ordem=0,
        ),
        OpcaoMenuWhatsApp(
            menu=menu, chave='2', titulo='Falar com um atendente',
            acao=OpcaoMenuWhatsApp.Acao.ATENDIMENTO_HUMANO,
            palavras_chave='atendente, humano, pessoa, falar com atendente', ordem=1,
        ),
        OpcaoMenuWhatsApp(
            menu=menu, chave='3', titulo='Encerrar a conversa',
            acao=OpcaoMenuWhatsApp.Acao.ENCERRAR,
            palavras_chave='encerrar, finalizar, sair', ordem=2,
        ),
    ])
    return menu
