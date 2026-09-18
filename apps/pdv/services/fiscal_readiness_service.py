"""Validacao amigavel dos dados obrigatorios antes da emissao fiscal."""
from dataclasses import asdict, dataclass
from decimal import Decimal
import re

from django.urls import reverse
from django.utils import timezone

from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema
from apps.fiscal.models import AliquotaIBPT


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


@dataclass(frozen=True)
class FiscalIssue:
    code: str
    title: str
    message: str
    action_label: str
    action_url: str
    severity: str = "error"

    def as_dict(self):
        return asdict(self)


class FiscalReadinessService:
    """Retorna pendencias com linguagem de operacao e destino para correcao."""

    def __init__(self, venda, tipo):
        self.venda = venda
        self.tipo = str(tipo or "").lower()
        self.issues = []
        self.params_url = reverse("core:admin_parametros")

    def add(self, code, title, message, label, url, severity="error"):
        self.issues.append(FiscalIssue(code, title, message, label, url, severity))

    def verificar(self):
        if self.tipo not in {"nfe", "nfce"}:
            self.add("tipo", "Tipo de nota nao reconhecido", "Escolha NF-e ou NFC-e para continuar.", "Voltar ao PDV", reverse("pdv:home"))
            return self.resultado()
        self._filial()
        self._documento()
        self._venda()
        self._cliente()
        self._produtos()
        self._pagamentos()
        return self.resultado()

    def resultado(self):
        erros = [item.as_dict() for item in self.issues if item.severity == "error"]
        avisos = [item.as_dict() for item in self.issues if item.severity != "error"]
        return {"ok": not erros, "tipo": self.tipo, "pendencias": erros, "avisos": avisos}

    def _filial(self):
        filial = self.venda.filial
        faltando = []
        checks = {
            "CNPJ": len(_digits(filial.cnpj)) == 14,
            "inscricao estadual": bool((filial.inscricao_estadual or "").strip()),
            "logradouro": bool((filial.endereco or "").strip()),
            "numero": bool((filial.numero or "").strip()),
            "bairro": bool((filial.bairro or "").strip()),
            "cidade": bool((filial.cidade or "").strip()),
            "UF": len((filial.uf or "").strip()) == 2,
            "CEP": len(_digits(filial.cep)) == 8,
            "codigo IBGE da cidade": len(_digits(filial.codigo_municipio_ibge)) == 7,
            "regime tributario": bool(filial.codigo_regime_tributario or filial.empresa.codigo_regime_tributario),
            "token de emissao Focus": bool((filial.focusnfe_token or "").strip()),
        }
        faltando.extend(label for label, ok in checks.items() if not ok)
        if faltando:
            self.add("filial", "Complete os dados fiscais da empresa", "Falta informar: " + ", ".join(faltando) + ".", "Abrir parametros fiscais", self.params_url)

    def _documento(self):
        try:
            params = ParametrosSistema.objects.get(filial=self.venda.filial)
        except ParametrosSistema.DoesNotExist:
            self.add("parametros", "Configure a emissao fiscal", "A empresa ainda nao possui a configuracao fiscal inicial.", "Configurar agora", self.params_url)
            return
        doc = ParametroDocumentoFiscal.objects.filter(parametros=params, tipo_documento=self.tipo).first()
        if not doc:
            self.add("documento", f"Configure a {self.tipo.upper()}", "Informe serie, proximo numero e ambiente de emissao.", "Abrir configuracao", self.params_url)
        elif not doc.habilitado:
            self.add("documento_desabilitado", f"Ative a emissao de {self.tipo.upper()}", "A emissao deste documento esta desativada nos parametros.", "Ativar emissao", self.params_url)
        if self.tipo == "nfce" and (not (params.nfce_csc_id or "").strip() or not (params.nfce_csc_token or "").strip()):
            self.add("csc", "Configure o CSC da NFC-e", "Faltam o identificador ou o token CSC usado no QR Code do cupom.", "Abrir configuracao da NFC-e", self.params_url)

    def _venda(self):
        if self.venda.status not in {"finalizada", "orcamento"}:
            self.add("venda_status", "Finalize a venda primeiro", "A nota so pode ser emitida depois que a venda estiver concluida.", "Voltar ao PDV", reverse("pdv:home"))
        if not self.venda.itens.exists():
            self.add("itens", "Adicione ao menos um produto", "Nao existe item para colocar na nota.", "Voltar ao PDV", reverse("pdv:home"))

    def _cliente(self):
        cliente = self.venda.cliente
        if self.tipo == "nfce":
            if cliente and cliente.cpf_cnpj and len(_digits(cliente.cpf_cnpj)) not in {11, 14}:
                self.add("cliente_documento", "Corrija o CPF ou CNPJ do cliente", "O documento informado esta incompleto.", "Abrir cliente", reverse("cadastros:cliente-update", args=[cliente.pk]))
            return
        if not cliente:
            self.add("cliente", "Selecione o cliente da NF-e", "A NF-e precisa identificar o destinatario e seu endereco.", "Voltar e selecionar cliente", reverse("pdv:home"))
            return
        endereco = {
            "logradouro": cliente.endereco, "numero": cliente.numero, "bairro": cliente.bairro,
            "cidade": cliente.cidade, "uf": cliente.uf, "cep": cliente.cep,
            "ibge": cliente.codigo_municipio_ibge,
        }
        extra = cliente.enderecos.filter(ativo=True).order_by("-padrao", "id").first()
        if extra:
            endereco.update({
                "logradouro": extra.endereco or endereco["logradouro"],
                "numero": extra.numero or endereco["numero"],
                "bairro": extra.bairro or endereco["bairro"],
                "cidade": extra.cidade or endereco["cidade"],
                "uf": extra.uf or endereco["uf"],
                "cep": extra.cep or endereco["cep"],
                "ibge": extra.codigo_municipio_ibge or endereco["ibge"],
            })
        faltando = []
        if len(_digits(cliente.cpf_cnpj)) not in {11, 14}:
            faltando.append("CPF ou CNPJ valido")
        for label, value in {
            "logradouro": endereco["logradouro"], "numero": endereco["numero"],
            "bairro": endereco["bairro"], "cidade": endereco["cidade"], "UF": endereco["uf"],
        }.items():
            if not (value or "").strip():
                faltando.append(label)
        if len(_digits(endereco["cep"])) != 8:
            faltando.append("CEP")
        if len(_digits(endereco["ibge"])) != 7:
            faltando.append("codigo IBGE da cidade")
        ie = (cliente.inscricao_estadual or cliente.rg_ie or "").strip()
        if cliente.contribuinte_icms and (not ie or ie.upper() == "ISENTO"):
            faltando.append("inscricao estadual")
        if faltando:
            self.add("cliente", "Complete o cadastro do cliente", "Falta informar: " + ", ".join(faltando) + ".", "Abrir cliente", reverse("cadastros:cliente-update", args=[cliente.pk]))

    def _produtos(self):
        regime = self.venda.filial.codigo_regime_tributario or self.venda.filial.empresa.codigo_regime_tributario
        simples = regime in {1, 4}
        for item in self.venda.itens.select_related("produto__unidade_medida"):
            produto = item.produto
            faltando = []
            if len(_digits(produto.ncm)) != 8:
                faltando.append("NCM com 8 numeros")
            if not produto.unidade_medida_id:
                faltando.append("unidade de medida")
            if not (produto.cst_csosn or "").strip():
                faltando.append("CSOSN" if simples else "CST de ICMS")
            elif simples and len(_digits(produto.cst_csosn)) != 3:
                faltando.append("CSOSN com 3 numeros")
            elif not simples and len(_digits(produto.cst_csosn)) != 2:
                faltando.append("CST de ICMS com 2 numeros")
            if len(_digits(produto.cst_pis)) != 2:
                faltando.append("CST de PIS")
            if len(_digits(produto.cst_cofins)) != 2:
                faltando.append("CST de COFINS")
            cfop = produto.cfop_venda_interna or item.cfop
            if len(_digits(cfop)) != 4:
                faltando.append("CFOP de venda")
            if faltando:
                self.add(f"produto_{produto.pk}", f"Revise o produto {produto.descricao}", "Falta informar: " + ", ".join(faltando) + ".", "Abrir produto", reverse("produtos:produto-update", args=[produto.pk]) + "?step=3")
                continue
            data_emissao = timezone.localdate(self.venda.data_venda) if self.venda.data_venda else timezone.localdate()
            tem_ibpt = AliquotaIBPT.objects.filter(
                uf=self.venda.filial.uf,
                ncm=_digits(produto.ncm),
                vigencia_inicio__lte=data_emissao,
                vigencia_fim__gte=data_emissao,
            ).exists()
            if not tem_ibpt:
                # A estimativa IBPT (Lei 12.741/2012 — valor aproximado dos
                # tributos) NAO e obrigatoria para a SEFAZ autorizar a nota.
                # Fica como aviso para nao bloquear a emissao.
                self.add(
                    f"ibpt_{produto.pk}",
                    f"Atualize os tributos de {produto.descricao}",
                    "A estimativa tributaria vigente deste NCM ainda nao esta disponivel. Isso evita emitir sem o valor aproximado dos impostos.",
                    "Revisar produto",
                    reverse("produtos:produto-update", args=[produto.pk]) + "?step=3",
                    severity="aviso",
                )

    def _pagamentos(self):
        pagamentos = list(self.venda.pagamentos.select_related("forma_pagamento", "tef_transacao"))
        if not pagamentos:
            self.add("pagamento", "Informe a forma de pagamento", "A nota precisa mostrar como a venda foi paga.", "Voltar ao PDV", reverse("pdv:home"))
            return
        liquido = sum((Decimal(p.valor) - Decimal(p.troco or 0) for p in pagamentos), Decimal("0"))
        if abs(liquido - Decimal(self.venda.valor_total)) > Decimal("0.01"):
            self.add("pagamento_total", "Confira o valor recebido", "Pagamentos menos o troco nao fecham com o total da venda.", "Voltar ao PDV", reverse("pdv:home"))
        for pagamento in pagamentos:
            forma = pagamento.forma_pagamento
            if not ((forma.codigo_sefaz or "").strip() or (forma.tipo or "").strip()):
                self.add(f"forma_{forma.pk}", f"Configure a forma {forma.descricao}", "Falta o codigo fiscal da forma de pagamento.", "Abrir parametros fiscais", self.params_url)
            if forma.requer_tef and not pagamento.tef_transacao_id:
                self.add(f"tef_{pagamento.pk}", "Finalize o pagamento no cartao", "A venda indica cartao integrado, mas nao possui autorizacao da operadora.", "Voltar ao PDV", reverse("pdv:home"))


def verificar_prontidao_fiscal(venda, tipo):
    return FiscalReadinessService(venda, tipo).verificar()
