"""
A NF-e de remessa para venda fora do estabelecimento.

A EMPRESA É REMETENTE E DESTINATÁRIA — é a particularidade da operação, e a
razão de ela não caber no fluxo normal de venda. Sem comprador, a nota é
emitida pela empresa contra ela mesma, e é isso que ampara a mercadoria em
trânsito sem que exista uma venda.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import ItemCarga, Viagem
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class RemessaVendaForaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Remessa LTDA', nome_fantasia='Remessa',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz Natal',
            cnpj='63345678000272', uf='RN', cidade='Natal', is_matriz=True,
            endereco='Rua A', numero='100', bairro='Centro', cep='59000000',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='rem@fiscal.local', nome='Rem', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Caixa de polpa', codigo='CX1', ncm='20079900',
            preco_venda=Decimal('10'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

        cls.natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa_venda_fora',
            descricao='Remessa para venda fora do estabelecimento',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False, gera_financeiro=False,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=cls.natureza, cfop='5904', csosn='400',
            cst_pis='49', cst_cofins='49', aliquota_pis=Decimal('0'),
            informacoes_complementares='Mercadoria destinada a venda fora do estabelecimento.',
        )
        RegraNaturezaOperacao.objects.create(
            natureza=cls.natureza, cfop='6904', somente_interestadual=True,
            csosn='400',
        )

    def setUp(self):
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=125, motorista_nome='João',
            veiculo_placa='ABC1234', vendedor=self.usuario,
        )

    def _item(self, quantidade='200', valor='10', produto=None):
        return ViagemService.adicionar_item(self.viagem, {
            'natureza': self.natureza, 'produto': produto or self.produto,
            'quantidade': quantidade, 'valor_unitario': valor,
        })

    # ── A empresa nos dois lados ─────────────────────────────────────────

    def test_a_empresa_e_remetente_e_destinataria(self):
        """
        Sem comprador, o destinatário é o próprio emitente. Apontar para um
        cliente qualquer aqui produziria uma venda que não aconteceu.
        """
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['cnpj_emitente'], '63345678000272')
        self.assertEqual(payload['cnpj_destinatario'], '63345678000272')
        self.assertEqual(payload['nome_destinatario'], 'Matriz Natal')

    def test_o_documento_registra_a_propria_filial_como_destino(self):
        self._item()

        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertEqual(documento.destinatario_tipo, 'filial')
        self.assertEqual(documento.destinatario_id, self.filial.pk)
        self.assertIn('venda fora', documento.destinatario_snapshot['observacao'])

    # ── O fiscal vem da parametrização ───────────────────────────────────

    def test_o_cfop_vem_da_regra_e_nao_do_codigo(self):
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '5904')

    def test_mudar_a_regra_muda_a_nota(self):
        """
        É a prova de que o CFOP não está no código: trocar o cadastro troca o
        que sai na nota, sem deploy.
        """
        RegraNaturezaOperacao.objects.filter(cfop='5904').update(cfop='5905')
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '5905')

    def test_o_csosn_e_os_cst_vem_da_regra(self):
        self._item()

        item = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)['items'][0]

        self.assertEqual(item['icms_situacao_tributaria'], '400')
        self.assertEqual(item['pis_situacao_tributaria'], '49')
        self.assertEqual(item['cofins_situacao_tributaria'], '49')

    def test_cst_e_csosn_nao_vao_juntos(self):
        """Mandar os dois é rejeição certa na SEFAZ."""
        RegraNaturezaOperacao.objects.filter(cfop='5904').update(cst_icms='00')
        self._item()

        item = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)['items'][0]

        # A regra tem os dois, mas so' um vai -- e o CSOSN ganha, porque a
        # empresa e' do Simples.
        self.assertEqual(item['icms_situacao_tributaria'], '400')

    def test_a_natureza_da_nota_vem_do_cadastro(self):
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(
            payload['natureza_operacao'],
            'Remessa para venda fora do estabelecimento',
        )

    def test_a_informacao_complementar_explica_a_nota(self):
        """
        Uma nota da empresa para ela mesma parece erro para quem a lê na
        estrada — fiscal de barreira inclusive.
        """
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        info = payload['informacoes_adicionais_contribuinte']
        self.assertIn('Remessa para venda fora do estabelecimento', info)
        self.assertIn('000125', info)
        self.assertIn('ABC1234', info)
        self.assertIn('venda fora do estabelecimento', info)

    # ── O que a nota leva ────────────────────────────────────────────────

    def test_a_nota_leva_so_a_mercadoria_de_venda_fora(self):
        """Venda e bonificação têm documento próprio."""
        venda = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=venda, cfop='5102')
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial)
        self._item(quantidade='200')
        ViagemService.adicionar_item(self.viagem, {
            'natureza': venda, 'produto': self.produto, 'quantidade': '150',
            'cliente': cliente, 'valor_unitario': '10',
        })

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['items'][0]['quantidade_comercial'], 200.0)

    def test_o_total_e_a_soma_dos_itens(self):
        self._item(quantidade='200', valor='10')

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['valor_total'], 2000.0)

    def test_a_remessa_nao_cobra_de_ninguem(self):
        self._item()

        payload = RemessaVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['formas_pagamento'][0]['forma_pagamento'], '90')

    # ── O que impede a emissão ───────────────────────────────────────────

    def test_sem_ncm_a_emissao_para(self):
        sem_ncm = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao='Sem NCM', codigo='SN1', ncm='',
        )
        ProdutoFilial.objects.create(produto=sem_ncm, filial=self.filial)
        self._item(produto=sem_ncm)

        problemas = RemessaVendaForaService.conferir(self.viagem)

        self.assertTrue(any('sem NCM' in p for p in problemas), problemas)

    def test_sem_valor_a_emissao_para(self):
        """A nota precisa declarar quanto sai."""
        self._item(valor='0')

        problemas = RemessaVendaForaService.conferir(self.viagem)

        self.assertTrue(any('sem valor' in p for p in problemas), problemas)

    def test_sem_regra_a_emissao_para(self):
        RegraNaturezaOperacao.objects.all().delete()
        self._item()

        with self.assertRaises(DadosInvalidosError) as erro:
            RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertIn('Cadastre a regra', str(erro.exception))

    def test_carga_sem_venda_fora_nao_emite_remessa(self):
        problemas = RemessaVendaForaService.conferir(self.viagem)

        self.assertTrue(
            any('não tem mercadoria' in p for p in problemas), problemas,
        )

    def test_emissao_que_falha_nao_deixa_buraco_na_numeracao(self):
        """
        Número reservado e não usado vira buraco na numeração, que a SEFAZ
        cobra depois com inutilização.

        Quem garante isso é a transação: a emissão inteira é atômica, então a
        reserva volta atrás junto com o resto. A conferência antes de reservar
        é cinto e suspensório -- ela deixa a falha mais clara, mas não é o que
        segura a numeração.
        """
        from apps.core.models.parametros import (
            ParametroDocumentoFiscal, ParametrosSistema,
        )

        parametros, _ = ParametrosSistema.objects.get_or_create(filial=self.filial)
        config = ParametroDocumentoFiscal.objects.create(
            parametros=parametros, tipo_documento='nfe', proximo_numero=41, serie=1,
        )
        self._item(valor='0')

        with self.assertRaises(DadosInvalidosError):
            RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        config.refresh_from_db()
        self.assertEqual(config.proximo_numero, 41)

    def test_nao_emite_duas_remessas_para_a_mesma_viagem(self):
        self._item()
        RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        with self.assertRaises(DadosInvalidosError) as erro:
            RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertIn('já tem a remessa', str(erro.exception))

    def test_remessa_cancelada_libera_nova_emissao(self):
        self._item()
        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)
        documento.status = StatusDocumentoFiscal.CANCELADA
        documento.save(update_fields=['status'])

        nova = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertNotEqual(nova.pk, documento.pk)

    # ── O elo até a carga ────────────────────────────────────────────────

    def test_as_linhas_da_carga_apontam_para_a_nota(self):
        """Viagem → carga → documento fiscal, para a fiscalização responder."""
        self._item()

        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        item = ItemCarga.objects.get(viagem=self.viagem)
        self.assertEqual(item.documento_fiscal, documento)

    def test_o_documento_nasce_pendente_de_transmissao(self):
        self._item()

        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertEqual(documento.status, StatusDocumentoFiscal.PENDENTE)
        self.assertEqual(documento.origem_tipo, 'viagem_remessa')
        self.assertEqual(documento.origem_id, self.viagem.pk)

    def test_o_numero_sai_da_numeracao_da_filial(self):
        from apps.core.models.parametros import (
            ParametroDocumentoFiscal, ParametrosSistema,
        )

        parametros, _ = ParametrosSistema.objects.get_or_create(filial=self.filial)
        config = ParametroDocumentoFiscal.objects.create(
            parametros=parametros, tipo_documento='nfe', proximo_numero=41, serie=2,
        )
        self._item()

        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        config.refresh_from_db()
        self.assertEqual(documento.numero, 41)
        self.assertEqual(documento.serie, 2)
        self.assertEqual(config.proximo_numero, 42)
