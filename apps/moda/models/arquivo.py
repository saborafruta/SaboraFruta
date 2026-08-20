"""
Arquivos do pedido — a arte e tudo que chega junto com ela.

POR QUE NÃO BASTAVA A PERSONALIZAÇÃO. A `Personalizacao` é a arte APLICADA
num item: técnica, local e arquivo daquele escudo, naquela camisa. É a
informação que o chão de fábrica precisa.

O que faltava é o que chega ANTES disso: o layout que o cliente mandou por
WhatsApp, a planilha de nomes, o PDF do contrato, a foto da camisa do ano
passado. Esses são do PEDIDO, não de um item — e enquanto não existiam,
ficavam no celular de quem atendeu. Pedido sem produto lançado não tinha
nem onde guardar a arte, que é justamente o primeiro arquivo a chegar.

NÃO SUBSTITUI a personalização: quando a arte vira aplicação numa peça, ela
é lançada no item, com técnica e local. Aqui é o acervo do pedido.
"""
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

# Aberto de propósito: aqui entra o que o cliente mandou, e o cliente manda
# o que tem. Restringir demais faria a pessoa renomear a extensão para
# conseguir anexar -- que é pior do que aceitar.
EXTENSOES = [
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
    'pdf', 'cdr', 'ai', 'eps', 'psd',
    'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt', 'zip', 'rar',
]

# Só estas o navegador desenha numa <img>. Para as outras a tela oferece o
# arquivo, em vez de fingir uma prévia quebrada.
COM_PREVIA = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}


class ArquivoPedido(models.Model):
    """Um arquivo pendurado no pedido inteiro."""

    class Tipo(models.TextChoices):
        ARTE = 'arte', 'Arte / layout'
        REFERENCIA = 'referencia', 'Referência do cliente'
        DOCUMENTO = 'documento', 'Documento'
        OUTRO = 'outro', 'Outro'

    pedido = models.ForeignKey(
        'moda.PedidoProducao', on_delete=models.CASCADE, related_name='arquivos',
    )
    arquivo = models.FileField(
        upload_to='moda/pedidos/', validators=[FileExtensionValidator(EXTENSOES)],
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.ARTE)
    descricao = models.CharField(
        max_length=160, blank=True,
        help_text='O que é este arquivo. Ex.: escudo em curva, planilha de nomes.',
    )

    enviado_em = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='arquivos_moda',
    )

    class Meta:
        db_table = 'moda_pedido_arquivos'
        ordering = ['-enviado_em']
        verbose_name = 'Arquivo do pedido'
        verbose_name_plural = 'Arquivos do pedido'

    def __str__(self):
        return self.descricao or self.nome_arquivo

    @property
    def nome_arquivo(self) -> str:
        return self.arquivo.name.rsplit('/', 1)[-1] if self.arquivo else ''

    @property
    def extensao(self) -> str:
        nome = self.arquivo.name if self.arquivo else ''
        return nome.rsplit('.', 1)[-1].lower() if '.' in nome else ''

    @property
    def pode_pre_visualizar(self) -> bool:
        return self.extensao in COM_PREVIA

    @property
    def tamanho_legivel(self) -> str:
        """
        O tamanho em KB/MB.

        Aparece na tela porque arquivo de arte costuma ser grande, e saber
        que aquele PDF tem 40 MB explica por que o WhatsApp do cliente não
        abriu — sem isso, a conclusão é que o sistema perdeu o arquivo.
        """
        try:
            bytes_ = self.arquivo.size
        except (OSError, ValueError):
            return ''
        if bytes_ < 1024:
            return f'{bytes_} B'
        if bytes_ < 1024 * 1024:
            return f'{bytes_ / 1024:.0f} KB'
        return f'{bytes_ / (1024 * 1024):.1f} MB'
