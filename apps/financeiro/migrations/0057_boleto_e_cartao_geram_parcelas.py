"""
Boleto e cartão de crédito passam a gerar parcelas.

O QUE ESTAVA ERRADO NO CADASTRO

As formas nasceram todas com `gera_parcelas` desmarcado, boleto e cartão de
crédito incluídos. É esse campo que separa "recebi agora" de "vou receber
depois" em todo o ERP — e, desmarcado neles, venda a prazo não abria conta a
receber em lugar nenhum: nem no PDV, nem na viagem, nem na expedição. A
mercadoria saía, o cliente ficava devendo, e o contas a receber não sabia.

BOLETO E CARTÃO DE CRÉDITO SÃO A PRAZO POR DEFINIÇÃO. Boleto é promessa de
pagamento com vencimento; cartão de crédito é a operadora pagando depois.
Nenhum dos dois é dinheiro na mão no momento da entrega.

CARTÃO DE DÉBITO E PIX FICAM COMO ESTÃO: são liquidação imediata, e marcá-los
encheria o contas a receber de títulos que nascem quitados.

QUEM JÁ DECIDIU O CONTRÁRIO NÃO É CONTRARIADO: a migration só toca as formas
que estão com o campo desmarcado E sem prazo de liquidação configurado — quem
ajustou o cadastro à mão tinha um motivo, e a migration não sabe qual é.
"""
from django.db import migrations

# As duas que são a prazo por definição.
A_PRAZO = ('boleto', 'cartao_credito')


def marcar(apps, schema_editor):
    FormaPagamento = apps.get_model('financeiro', 'FormaPagamento')
    FormaPagamento.objects.filter(
        tipo__in=A_PRAZO,
        gera_parcelas=False,
        prazo_liquidacao_dias=0,
    ).update(gera_parcelas=True)


def desmarcar(apps, schema_editor):
    """
    A volta desmarca as mesmas formas.

    NÃO É PERFEITA, e não tem como ser: depois de aplicada, não há como
    distinguir a forma que a migration marcou daquela que alguém marcou à mão
    no dia seguinte. A volta assume o estado anterior, que é o que uma
    reversão significa.
    """
    FormaPagamento = apps.get_model('financeiro', 'FormaPagamento')
    FormaPagamento.objects.filter(
        tipo__in=A_PRAZO,
        gera_parcelas=True,
        prazo_liquidacao_dias=0,
    ).update(gera_parcelas=False)


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0056_payload_do_documento'),
    ]

    operations = [
        migrations.RunPython(marcar, desmarcar),
    ]
