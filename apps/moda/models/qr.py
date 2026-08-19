"""
Código de QR dos documentos do vertical.

UM campo, um mixin, quatro documentos: pedido, ordem de produção, ficha de
produção e lote de corte. Cada um ganha um código opaco com prefixo —
`OP-7hK2mQ...` — que é o que vai impresso no papel que circula na fábrica.

Por que prefixo e não só o token: o leitor precisa saber PARA ONDE ir sem
consultar quatro tabelas. Com o prefixo, uma única consulta resolve. E
quando alguém digita o código à mão, o prefixo já denuncia o engano ("isso
é um lote, você está na tela de pedidos") em vez de devolver um 404 mudo.

Por que campo gravado e não código assinado com a SECRET_KEY: o papel já
está na parede da fábrica. Trocar a SECRET_KEY é uma operação normal de
segurança, e ela não pode invalidar etiqueta impressa. O mesmo desenho do
`Mesa.qr_token` e do `PedidoProducao.token_publico`.

O código NÃO é a autorização. Quem escaneia cai na tela de sempre, que
continua exigindo login, permissão de módulo e filial — o QR é atalho para
a mesma porta, não uma porta nova. Ver `services/qr.py`.
"""
import secrets

from django.db import models


class ComCodigoQr(models.Model):
    """Dá ao documento um código de QR estável, gerado na primeira gravação."""

    # Cada modelo concreto define o seu. Ver `services/qr.py`, que amarra
    # prefixo → modelo → tela.
    PREFIXO_QR = ''

    codigo_qr = models.CharField(
        max_length=24, unique=True, editable=False, blank=True, db_index=True,
        verbose_name='Código do QR',
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.codigo_qr:
            self.codigo_qr = self.gerar_codigo_qr()
        super().save(*args, **kwargs)

    @classmethod
    def gerar_codigo_qr(cls) -> str:
        # 9 bytes = 12 caracteres. Curto de propósito: o QR fica menos denso
        # e um celular de chão de fábrica, sujo e com câmera ruim, lê de
        # mais longe. Adivinhar não leva a lugar nenhum — a tela de destino
        # exige login e permissão de qualquer jeito.
        return f'{cls.PREFIXO_QR}-{secrets.token_urlsafe(9)}'
