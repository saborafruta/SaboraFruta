"""Remessa e retorno de cobrança em arquivo posicional CNAB.

O fluxo segue o mesmo conceito do TOTVS Gestão Financeira: a conta bancária
define o convênio/layout, títulos são agrupados em uma remessa e o retorno
produz ocorrências auditáveis antes de atualizar o Contas a Receber.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import (
    Boleto, ContaBancaria, ContaReceber, FormaPagamento, RemessaBancaria,
    RetornoBancario, RetornoBancarioItem,
)
from apps.financeiro.services.receber_service import ContaReceberService


OCORRENCIAS = {
    "02": "Entrada confirmada",
    "03": "Entrada rejeitada",
    "06": "Liquidação normal",
    "09": "Baixa",
    "17": "Liquidação após baixa",
    "19": "Confirmação de instrução de protesto",
    "23": "Entrada em cartório",
    "28": "Tarifa",
}
OCORRENCIAS_PAGAS = {"06", "17"}
OCORRENCIAS_REJEITADAS = {"03"}
OCORRENCIAS_BAIXADAS = {"09"}


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _ascii(valor) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).upper()


def _campo(linha: list[str], inicio: int, fim: int, valor, numerico=False):
    tamanho = fim - inicio + 1
    texto = _digitos(valor) if numerico else _ascii(valor)
    texto = texto[-tamanho:].rjust(tamanho, "0") if numerico else texto[:tamanho].ljust(tamanho)
    linha[inicio - 1:fim] = texto


def _linha() -> list[str]:
    return list(" " * 240)


def _valor_cnab(valor: Decimal, tamanho=15) -> str:
    return str(int((Decimal(valor or 0) * 100).quantize(Decimal("1")))).zfill(tamanho)


class RemessaCNAB240Service:
    @staticmethod
    def _validar_conta(conta: ContaBancaria):
        faltantes = []
        for campo, rotulo in (
            (conta.banco_codigo, "código do banco"),
            (conta.agencia, "agência"),
            (conta.conta, "conta"),
            (conta.codigo_transmissao or conta.convenio, "código de transmissão/convênio"),
        ):
            if not _digitos(campo):
                faltantes.append(rotulo)
        if conta.layout_cobranca != ContaBancaria.LayoutCobranca.CNAB240:
            raise DomainError("Esta versão gera remessa somente no padrão CNAB 240.")
        if faltantes:
            raise DomainError("Complete a conta bancária: " + ", ".join(faltantes) + ".")

    @classmethod
    @tenant_atomic
    def gerar(cls, *, conta_bancaria: ContaBancaria, titulos, usuario) -> RemessaBancaria:
        conta = ContaBancaria.objects.select_for_update().select_related("filial").get(pk=conta_bancaria.pk)
        cls._validar_conta(conta)
        titulos = list(
            ContaReceber.objects.select_for_update().select_related("cliente", "filial")
            .filter(
                pk__in=[t.pk if hasattr(t, "pk") else t for t in titulos],
                filial=conta.filial,
                status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO, StatusContaReceber.NEGOCIADO],
            ).order_by("data_vencimento", "pk")
        )
        if not titulos:
            raise DomainError("Selecione ao menos um título em aberto.")
        ja_remetidos = Boleto.objects.filter(
            conta_receber__in=titulos, remessa__isnull=False,
        ).exclude(status__in=["rejeitado", "cancelado"]).values_list("conta_receber_id", flat=True)
        if ja_remetidos:
            raise DomainError("Há título selecionado que já pertence a uma remessa ativa.")

        ultimo = RemessaBancaria.objects.filter(
            conta_bancaria=conta,
        ).order_by("-numero_remessa").values_list("numero_remessa", flat=True).first() or 0
        numero_remessa = ultimo + 1
        boletos = []
        for titulo in titulos:
            cliente = titulo.cliente
            faltantes = [
                rotulo for valor, rotulo in (
                    (cliente.cpf_cnpj, "CPF/CNPJ"), (cliente.razao_social, "nome"),
                    (cliente.endereco, "endereço"), (cliente.cidade, "cidade"),
                    (cliente.uf, "UF"), (cliente.cep, "CEP"),
                ) if not str(valor or "").strip()
            ]
            if faltantes:
                raise DomainError(f"Cliente {cliente}: complete " + ", ".join(faltantes) + ".")
            boleto = Boleto.objects.filter(conta_receber=titulo).order_by("-pk").first()
            if boleto and boleto.conta_bancaria_id != conta.pk:
                raise DomainError(f"O título {titulo.pk} já possui boleto vinculado a outra conta bancária.")
            if boleto is None:
                nosso_numero = str(conta.nosso_numero_atual)
                conta.nosso_numero_atual += 1
                boleto = Boleto.objects.create(
                    conta_receber=titulo, conta_bancaria=conta, filial=conta.filial,
                    nosso_numero=nosso_numero, seu_numero=(titulo.documento_numero or str(titulo.pk))[:20],
                    carteira=conta.carteira_cobranca, data_emissao=titulo.data_emissao,
                    data_vencimento=titulo.data_vencimento, valor=titulo.valor_saldo,
                    status="gerado",
                )
            boletos.append(boleto)
        conta.save(update_fields=["nosso_numero_atual", "updated_at"])

        conteudo = cls._montar(conta, boletos, numero_remessa)
        nome = f"CB{_digitos(conta.banco_codigo).zfill(3)}{numero_remessa:06d}.REM"
        remessa = RemessaBancaria.objects.create(
            conta_bancaria=conta, filial=conta.filial, numero_remessa=numero_remessa,
            arquivo_nome=nome, arquivo_cnab=conteudo, quantidade_boletos=len(boletos),
            valor_total=sum((b.valor for b in boletos), Decimal("0")), status="gerado",
            layout="cnab240", arquivo_hash=hashlib.sha256(conteudo.encode("ascii")).hexdigest(),
            usuario=usuario,
        )
        Boleto.objects.filter(pk__in=[b.pk for b in boletos]).update(remessa=remessa, status="remetido")
        return remessa

    @classmethod
    def _montar(cls, conta, boletos, numero_remessa):
        banco = _digitos(conta.banco_codigo).zfill(3)[-3:]
        filial = conta.filial
        empresa_nome = filial.razao_social
        cnpj = _digitos(filial.cnpj).zfill(14)
        convenio = conta.codigo_transmissao or conta.convenio
        agora = timezone.localtime()
        linhas = []

        h = _linha()
        _campo(h, 1, 3, banco, True); _campo(h, 4, 7, 0, True); _campo(h, 8, 8, 0, True)
        _campo(h, 18, 18, 2, True); _campo(h, 19, 32, cnpj, True); _campo(h, 33, 52, convenio)
        _campo(h, 53, 57, conta.agencia, True); _campo(h, 58, 58, conta.agencia_digito)
        _campo(h, 59, 70, conta.conta, True); _campo(h, 71, 71, conta.conta_digito)
        _campo(h, 73, 102, empresa_nome); _campo(h, 103, 132, conta.banco_nome or f"BANCO {banco}")
        _campo(h, 143, 150, agora.strftime("%d%m%Y"), True); _campo(h, 151, 156, agora.strftime("%H%M%S"), True)
        _campo(h, 158, 163, numero_remessa, True); _campo(h, 164, 166, 87, True); _campo(h, 167, 171, 0, True)
        linhas.append("".join(h))

        lote = _linha()
        _campo(lote, 1, 3, banco, True); _campo(lote, 4, 7, 1, True); _campo(lote, 8, 8, 1, True)
        _campo(lote, 9, 9, "R"); _campo(lote, 10, 11, 1, True); _campo(lote, 12, 13, 40, True)
        _campo(lote, 18, 18, 2, True); _campo(lote, 19, 33, cnpj, True); _campo(lote, 34, 53, convenio)
        _campo(lote, 54, 58, conta.agencia, True); _campo(lote, 59, 59, conta.agencia_digito)
        _campo(lote, 60, 71, conta.conta, True); _campo(lote, 72, 72, conta.conta_digito)
        _campo(lote, 74, 103, empresa_nome); _campo(lote, 184, 191, numero_remessa, True)
        linhas.append("".join(lote))

        sequencia = 0
        for boleto in boletos:
            sequencia += 1
            titulo = boleto.conta_receber
            p = _linha()
            _campo(p, 1, 3, banco, True); _campo(p, 4, 7, 1, True); _campo(p, 8, 8, 3, True)
            _campo(p, 9, 13, sequencia, True); _campo(p, 14, 14, "P"); _campo(p, 16, 17, 1, True)
            _campo(p, 18, 22, conta.agencia, True); _campo(p, 23, 23, conta.agencia_digito)
            _campo(p, 24, 35, conta.conta, True); _campo(p, 36, 36, conta.conta_digito)
            _campo(p, 38, 57, boleto.nosso_numero); _campo(p, 58, 58, 1, True)
            _campo(p, 59, 59, 1, True); _campo(p, 60, 60, 2, True); _campo(p, 61, 61, 2, True)
            _campo(p, 63, 77, boleto.seu_numero or titulo.pk)
            _campo(p, 78, 85, boleto.data_vencimento.strftime("%d%m%Y"), True)
            _campo(p, 86, 100, _valor_cnab(boleto.valor), True); _campo(p, 108, 109, 2, True)
            _campo(p, 110, 110, "N"); _campo(p, 111, 118, boleto.data_emissao.strftime("%d%m%Y"), True)
            linhas.append("".join(p))

            sequencia += 1
            cliente = titulo.cliente
            q = _linha()
            _campo(q, 1, 3, banco, True); _campo(q, 4, 7, 1, True); _campo(q, 8, 8, 3, True)
            _campo(q, 9, 13, sequencia, True); _campo(q, 14, 14, "Q"); _campo(q, 16, 17, 1, True)
            _campo(q, 18, 18, 1 if len(_digitos(cliente.cpf_cnpj)) == 11 else 2, True)
            _campo(q, 19, 33, cliente.cpf_cnpj, True); _campo(q, 34, 73, cliente.razao_social)
            endereco = " ".join(filter(None, [cliente.endereco, cliente.numero, cliente.complemento]))
            _campo(q, 74, 113, endereco); _campo(q, 114, 128, cliente.bairro)
            _campo(q, 129, 136, cliente.cep, True); _campo(q, 137, 151, cliente.cidade); _campo(q, 152, 153, cliente.uf)
            linhas.append("".join(q))

        trailer_lote = _linha()
        _campo(trailer_lote, 1, 3, banco, True); _campo(trailer_lote, 4, 7, 1, True); _campo(trailer_lote, 8, 8, 5, True)
        _campo(trailer_lote, 18, 23, len(linhas), True); _campo(trailer_lote, 24, 29, len(boletos), True)
        _campo(trailer_lote, 30, 46, _valor_cnab(sum((b.valor for b in boletos), Decimal("0")), 17), True)
        linhas.append("".join(trailer_lote))
        trailer = _linha()
        _campo(trailer, 1, 3, banco, True); _campo(trailer, 4, 7, 9999, True); _campo(trailer, 8, 8, 9, True)
        _campo(trailer, 18, 23, 1, True); _campo(trailer, 24, 29, len(linhas) + 1, True)
        linhas.append("".join(trailer))
        return "\r\n".join(linhas) + "\r\n"


@dataclass
class OcorrenciaRetorno:
    numero_linha: int
    nosso_numero: str
    codigo: str
    data: object
    valor_titulo: Decimal
    valor_pago: Decimal
    tarifa: Decimal


class RetornoCNABService:
    @staticmethod
    def _decimal(texto):
        return Decimal(_digitos(texto) or "0") / Decimal("100")

    @staticmethod
    def _data(texto):
        texto = _digitos(texto)
        if len(texto) != 8 or texto == "00000000":
            return None
        try:
            return datetime.strptime(texto, "%d%m%Y").date()
        except ValueError:
            return None

    @classmethod
    def ler(cls, conteudo: str):
        linhas = [linha.rstrip("\r") for linha in conteudo.splitlines() if linha.strip()]
        tamanhos = {len(linha) for linha in linhas}
        if not linhas or tamanhos != {240}:
            if tamanhos == {400}:
                raise DomainError("CNAB 400 detectado. O layout precisa ser homologado por banco antes da importação.")
            raise DomainError("Arquivo inválido: todas as linhas devem ter exatamente 240 posições.")
        if linhas[0][7:8] != "0" or linhas[-1][7:8] != "9":
            raise DomainError("Arquivo CNAB 240 sem header/trailer válidos.")
        ocorrencias = []
        pendente = None
        for numero, linha in enumerate(linhas, 1):
            if linha[7:8] != "3":
                continue
            segmento = linha[13:14]
            if segmento == "T":
                if pendente:
                    ocorrencias.append(pendente)
                pendente = OcorrenciaRetorno(
                    numero_linha=numero, nosso_numero=linha[37:57].strip(),
                    codigo=linha[15:17], data=None,
                    valor_titulo=cls._decimal(linha[81:96]), valor_pago=Decimal("0"),
                    tarifa=Decimal("0"),
                )
            elif segmento == "U" and pendente:
                pendente.valor_pago = cls._decimal(linha[77:92])
                pendente.tarifa = cls._decimal(linha[17:32])
                pendente.data = cls._data(linha[137:145]) or cls._data(linha[145:153])
                ocorrencias.append(pendente)
                pendente = None
        if pendente:
            ocorrencias.append(pendente)
        if not ocorrencias:
            raise DomainError("Nenhuma ocorrência de cobrança (segmento T) foi encontrada.")
        return ocorrencias

    @classmethod
    @tenant_atomic
    def importar(cls, *, conta_bancaria, nome, dados: bytes, usuario):
        if len(dados) > 10 * 1024 * 1024:
            raise DomainError("O arquivo excede o limite de 10 MB.")
        try:
            conteudo = dados.decode("ascii")
        except UnicodeDecodeError:
            try:
                conteudo = dados.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise DomainError("Não foi possível ler o arquivo como texto CNAB.") from exc
        arquivo_hash = hashlib.sha256(dados).hexdigest()
        if RetornoBancario.objects.filter(filial=conta_bancaria.filial, arquivo_hash=arquivo_hash).exists():
            raise DomainError("Este arquivo de retorno já foi processado.")
        ocorrencias = cls.ler(conteudo)
        banco_arquivo = _digitos(conteudo.splitlines()[0][:3]).zfill(3)
        banco_conta = _digitos(conta_bancaria.banco_codigo).zfill(3)
        if banco_arquivo != banco_conta:
            raise DomainError(
                f"O retorno pertence ao banco {banco_arquivo}, mas a conta selecionada é do banco {banco_conta}."
            )
        retorno = RetornoBancario.objects.create(
            conta_bancaria=conta_bancaria, filial=conta_bancaria.filial,
            arquivo_nome=(nome or "retorno.ret")[:80], arquivo_cnab=conteudo,
            arquivo_hash=arquivo_hash, layout="cnab240", quantidade_registros=len(ocorrencias),
            usuario=usuario,
        )
        totais = {"processados": 0, "pagos": 0, "baixados": 0, "rejeitados": 0, "nao_encontrados": 0, "ignorados": 0}
        forma_boleto = FormaPagamento.objects.filter(
            Q(filial=conta_bancaria.filial) | Q(filial__isnull=True),
            empresa=conta_bancaria.filial.empresa,
            tipo=TipoFormaPagamento.BOLETO, ativo=True,
        ).order_by("-filial_id").first()
        boletos_por_numero = {}
        for candidato in Boleto.objects.filter(
            filial=conta_bancaria.filial, conta_bancaria=conta_bancaria,
        ).select_related("conta_receber"):
            chave = _digitos(candidato.nosso_numero).lstrip("0") or "0"
            boletos_por_numero[chave] = candidato
        for ocorrencia in ocorrencias:
            chave = _digitos(ocorrencia.nosso_numero).lstrip("0") or "0"
            boleto = boletos_por_numero.get(chave)
            status, mensagem = "ignorado", "Ocorrência registrada sem alteração financeira."
            if not boleto:
                status, mensagem = "nao_encontrado", "Nosso número não localizado nesta conta bancária."
                totais["nao_encontrados"] += 1
            elif ocorrencia.codigo in OCORRENCIAS_PAGAS:
                titulo = boleto.conta_receber
                if titulo.status == StatusContaReceber.PAGO or boleto.status == "pago":
                    status, mensagem = "ignorado", "Boleto já estava liquidado; baixa não duplicada."
                    totais["ignorados"] += 1
                elif not forma_boleto:
                    status, mensagem = "erro", "Cadastre uma forma de pagamento ativa do tipo Boleto."
                    totais["ignorados"] += 1
                else:
                    valor = ocorrencia.valor_pago or titulo.valor_saldo
                    titulo = ContaReceberService.registrar_baixa(
                        conta=titulo, data_pagamento=ocorrencia.data or timezone.localdate(),
                        valor_pago=min(valor, titulo.valor_saldo), forma_pagamento=forma_boleto,
                        usuario=usuario, conta_bancaria=conta_bancaria,
                        observacao=f"Baixa automática via retorno CNAB, ocorrência {ocorrencia.codigo}.",
                    )
                    boleto.status = (
                        "pago" if titulo.status == StatusContaReceber.PAGO else "pago_parcial"
                    )
                    boleto.data_pagamento = ocorrencia.data or timezone.localdate()
                    boleto.valor_pago = valor; boleto.banco_codigo_retorno = ocorrencia.codigo; boleto.retorno = retorno
                    boleto.save(update_fields=["status", "data_pagamento", "valor_pago", "banco_codigo_retorno", "retorno", "updated_at"])
                    status = boleto.status
                    mensagem = "Baixa aplicada ao Contas a Receber."
                    totais["pagos"] += 1
                    retorno.valor_total_pago += valor
            elif boleto and ocorrencia.codigo in OCORRENCIAS_REJEITADAS:
                boleto.status = "rejeitado"; boleto.banco_codigo_retorno = ocorrencia.codigo; boleto.retorno = retorno
                boleto.save(update_fields=["status", "banco_codigo_retorno", "retorno", "updated_at"])
                status, mensagem = "rejeitado", "Registro rejeitado pelo banco."
                totais["rejeitados"] += 1
            elif boleto and ocorrencia.codigo in OCORRENCIAS_BAIXADAS:
                boleto.status = "baixado"; boleto.banco_codigo_retorno = ocorrencia.codigo; boleto.retorno = retorno
                boleto.save(update_fields=["status", "banco_codigo_retorno", "retorno", "updated_at"])
                status, mensagem = "baixado", "Baixa de carteira confirmada pelo banco."
                totais["baixados"] += 1
            else:
                totais["ignorados"] += 1
            totais["processados"] += 1
            RetornoBancarioItem.objects.create(
                retorno=retorno, boleto=boleto, numero_linha=ocorrencia.numero_linha,
                nosso_numero=ocorrencia.nosso_numero, codigo_ocorrencia=ocorrencia.codigo,
                descricao_ocorrencia=OCORRENCIAS.get(ocorrencia.codigo, "Ocorrência bancária"),
                data_ocorrencia=ocorrencia.data, valor_titulo=ocorrencia.valor_titulo,
                valor_pago=ocorrencia.valor_pago, valor_tarifa=ocorrencia.tarifa,
                status=status, mensagem=mensagem,
            )
        retorno.quantidade_pagos = totais["pagos"]
        retorno.quantidade_baixados = totais["baixados"]
        retorno.quantidade_rejeitados = totais["rejeitados"]
        retorno.resumo_processamento = totais
        retorno.processado = True
        retorno.data_processamento = timezone.now()
        retorno.save(update_fields=[
            "quantidade_pagos", "quantidade_baixados", "quantidade_rejeitados",
            "valor_total_pago", "resumo_processamento", "processado", "data_processamento",
        ])
        return retorno
