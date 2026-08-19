"""
O que cada código de QR é, e para onde ele leva.

Esta tabela é a única fonte da verdade do assunto: prefixo, modelo, tela de
destino, rótulo e permissão. Acrescentar um quinto documento com QR é uma
linha aqui e o mixin `ComCodigoQr` no modelo — nem a view do leitor nem a
etiqueta precisam saber que ele existe.

O CÓDIGO NÃO É A AUTORIZAÇÃO. É a diferença entre este QR e o do cardápio
digital: lá o token É a chave, porque o cliente não tem login. Aqui quem
escaneia é gente da casa, e o leitor só redireciona para a tela de sempre —
que continua exigindo login, permissão de módulo e filial. Um código
fotografado por quem não deveria não abre nada.
"""
from dataclasses import dataclass

from apps.moda.models import (
    FichaTecnica, OrdemProducao, PedidoProducao, RegistroCorte,
)


@dataclass(frozen=True)
class Documento:
    prefixo: str
    modelo: type
    rota: str
    label: str
    # A ação exigida para ABRIR a tela. Hoje é 'ver' nas quatro, e está
    # explícito para o dia em que uma delas for mais restrita — assim a
    # regra fica na tabela, e não escondida na view do leitor.
    acao: str = 'ver'

    def url(self, objeto) -> tuple[str, list]:
        return self.rota, [objeto.pk]


DOCUMENTOS = (
    Documento('PED', PedidoProducao, 'moda:pedido-detail', 'Pedido'),
    Documento('OP', OrdemProducao, 'moda:ordem-detail', 'Ordem de produção'),
    Documento('FT', FichaTecnica, 'moda:ficha-detail', 'Ficha de produção'),
    Documento('LT', RegistroCorte, 'moda:corte-detail', 'Lote de corte'),
)

POR_PREFIXO = {d.prefixo: d for d in DOCUMENTOS}
POR_MODELO = {d.modelo: d for d in DOCUMENTOS}


def documento_de(objeto) -> Documento | None:
    """A entrada da tabela para um objeto já carregado."""
    return POR_MODELO.get(type(objeto))


def limpar(codigo: str) -> str:
    """
    Normaliza o que veio do leitor.

    Leitor de código de barras às vezes entrega com espaço em volta, e quem
    digita à mão escreve minúsculo. O prefixo é maiúsculo; o token, não —
    `token_urlsafe` diferencia maiúscula de minúscula, e passar tudo para
    maiúscula transformaria um código válido em inexistente.
    """
    codigo = (codigo or '').strip()
    # O QR guarda a URL inteira; um leitor configurado como teclado entrega
    # ela toda. Ficar só com o último trecho aceita os dois formatos.
    if '/' in codigo:
        codigo = codigo.rstrip('/').rsplit('/', 1)[-1]
    prefixo, _, resto = codigo.partition('-')
    return f'{prefixo.upper()}-{resto}' if resto else codigo


def resolver(codigo: str):
    """
    `(Documento, objeto)` do código, ou `(None, None)`.

    Busca em `all_objects`: o filtro de filial é decidido por quem chamou,
    que é quem sabe se a resposta certa é "troque de filial" ou 404. Aqui
    dentro, filtrar já jogaria fora a informação necessária para escolher.
    """
    codigo = limpar(codigo)
    prefixo = codigo.partition('-')[0]

    documento = POR_PREFIXO.get(prefixo)
    if documento is None:
        return None, None

    objeto = documento.modelo.all_objects.filter(codigo_qr=codigo).first()
    if objeto is None:
        return None, None
    return documento, objeto
