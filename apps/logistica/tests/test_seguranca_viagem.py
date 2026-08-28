"""
As regras de segurança da viagem, e o log que explica o que passou por elas.

AS TRAVAS IMPEDEM O IMPOSSÍVEL; O LOG EXPLICA O POSSÍVEL. Uma viagem que sai
com 300 e volta com 170 está certa pela conta e pode estar errada pela
história — e a diferença entre as duas só aparece quando se consegue ler o que
aconteceu, na ordem, com quem e por quê.

Cada teste aqui corresponde a uma das onze regras da especificação: elas estão
espalhadas pelos serviços que as executam, e este arquivo é o lugar onde se
confere que TODAS continuam de pé.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import ItemCarga, VendaViagem, Viagem
from apps.logistica.services.log_viagem import LogViagemService
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

E = NaturezaOperacao.Especie


class SegurancaBase(TestCase):
    """Só as fixtures — herdar classe com testes faria a subclasse repeti-los."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Seguranca LTDA', nome_fantasia='Seg',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True, endereco='Rua A',
            numero='100', bairro='Centro', cep='59000000',
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
            email='seg@viagem.local', nome='Seg', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.naturezas = {}
        for codigo, especie, cfop in (
            ('venda', E.VENDA, '5102'),
            ('remessa', E.REMESSA_VENDA_FORA, '5904'),
            ('bonificacao', E.BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=codigo.title(),
                especie=especie, exige_destinatario=especie != E.REMESSA_VENDA_FORA,
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
            cls.naturezas[especie] = natureza

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('CX1', '1000')
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
            responsavel=self.usuario,
        )

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'), preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _carregar(self, especie=E.REMESSA_VENDA_FORA, quantidade='300',
                  valor='10', cliente=None):
        return ViagemService.adicionar_item(self.viagem, {
            'natureza': self.naturezas[especie], 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': valor, 'cliente': cliente,
        })

    def _documento(self, numero=1, status=None):
        from apps.financeiro.constants.enums import (
            StatusDocumentoFiscal, TipoDocumentoFiscal,
        )
        from apps.financeiro.models import DocumentoFiscal

        return DocumentoFiscal.objects.create(
            filial=self.filial, tipo_documento=TipoDocumentoFiscal.NFE,
            numero=numero, serie=1, emitente_cnpj=self.filial.cnpj,
            destinatario_snapshot={'nome': 'Cliente A'},
            data_emissao=timezone.now(), valor_total=Decimal('500'),
            usuario=self.usuario,
            status=status or StatusDocumentoFiscal.AUTORIZADA,
        )

    def _na_rua(self):
        ViagemService.fechar_carga(self.viagem, usuario=self.usuario)
        self.viagem.status = Viagem.Status.EM_VENDAS
        self.viagem.save(update_fields=['status'])

    def _entregar(self, quantidade, tipo=VendaViagem.Tipo.VENDA, **extras):
        dados = {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente, 'tipo': tipo,
        }
        if tipo == VendaViagem.Tipo.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.values[0]
        dados.update(extras)
        return VendaViagemService.registrar(self.viagem, dados, usuario=self.usuario)


class OnzeRegrasTests(SegurancaBase):
    """Uma por regra da especificação, na mesma ordem."""

    # 1 ─────────────────────────────────────────────────────────────────
    def test_1_venda_superior_a_quantidade_disponivel_e_recusada(self):
        self._carregar(quantidade='300')
        self._na_rua()

        with self.assertRaises(DadosInvalidosError):
            self._entregar('301')

    # 2 ─────────────────────────────────────────────────────────────────
    def test_2_bonificacao_superior_ao_disponivel_e_recusada(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('280')

        with self.assertRaises(DadosInvalidosError):
            self._entregar('30', tipo=VendaViagem.Tipo.BONIFICACAO)

    # 3 ─────────────────────────────────────────────────────────────────
    def test_3_retorno_superior_ao_saldo_e_recusado(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('250')

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.registrar_retorno(
                self.viagem, self.produto, Decimal('100'), usuario=self.usuario,
            )

        self.assertIn('só há', str(erro.exception))

    # 4 ─────────────────────────────────────────────────────────────────
    def test_4_finalizacao_sem_conciliacao_e_recusada(self):
        """Encerrar com sobra é perder o rastro do que está na rua."""
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('100')

        with self.assertRaises(DadosInvalidosError):
            ViagemService.encerrar(self.viagem)

    # 5 e 6 ─────────────────────────────────────────────────────────────
    def test_5_produto_vendido_sem_vinculo_com_a_viagem_e_recusado(self):
        outro = self._produto('CX2', '500')
        self._carregar(quantidade='300')
        self._na_rua()

        with self.assertRaises(DadosInvalidosError) as erro:
            self._entregar('10', produto=outro)

        self.assertIn('não está nesta viagem', str(erro.exception))

    def test_6_produto_bonificado_sem_vinculo_com_a_viagem_e_recusado(self):
        outro = self._produto('CX2', '500')
        self._carregar(quantidade='300')
        self._na_rua()

        with self.assertRaises(DadosInvalidosError):
            self._entregar('10', produto=outro, tipo=VendaViagem.Tipo.BONIFICACAO)

    # 7 ─────────────────────────────────────────────────────────────────
    def test_7_emissao_duplicada_de_nfe_e_recusada(self):
        self._carregar(quantidade='300')
        self._na_rua()
        RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        with self.assertRaises(DadosInvalidosError) as erro:
            RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        self.assertIn('já tem a remessa', str(erro.exception))

    # 8 ─────────────────────────────────────────────────────────────────
    def test_8_emissao_duplicada_de_mdfe_e_recusada(self):
        """
        Dois manifestos vivos para a mesma carga declaram a mesma mercadoria
        duas vezes à SEFAZ.
        """
        from apps.logistica.models import MDFe

        MDFe.objects.create(
            filial=self.filial, viagem=self.viagem, numero=1, serie=1,
            data_emissao=timezone.now(),
        )

        with self.assertRaises(DadosInvalidosError) as erro:
            MDFeViagemService.exigir_sem_mdfe(self.viagem)

        self.assertIn('já tem o MDF-e', str(erro.exception))

    def test_8b_viagem_sem_mdfe_passa(self):
        MDFeViagemService.exigir_sem_mdfe(self.viagem)

    # 9 ─────────────────────────────────────────────────────────────────
    def test_9_alteracao_de_carga_apos_a_saida_e_recusada(self):
        """
        Alterar depois reescreveria o que o documento fiscal já declarou. A
        correção é por baixa, retorno ou cancelamento -- e todas ficam no log.
        """
        self._carregar(quantidade='300')
        self._na_rua()

        with self.assertRaises(DadosInvalidosError) as erro:
            self._carregar(quantidade='10')

        self.assertIn('já saiu', str(erro.exception))

    def test_9b_a_correcao_permitida_fica_registrada(self):
        self._carregar(quantidade='300')
        self._na_rua()

        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('5'), 'quantidade_baixada',
            usuario=self.usuario, motivo='Quebra na estrada',
        )

        registro = LogViagemService.historico(self.viagem).last()
        self.assertEqual(registro.acao, LogViagemService.BAIXA_REGISTRADA)
        self.assertEqual(registro.justificativa, 'Quebra na estrada')

    # 10 ────────────────────────────────────────────────────────────────
    def test_10_venda_com_nota_emitida_nao_e_cancelada_direto(self):
        """
        Cancelar a venda sem cancelar a nota deixaria um documento autorizado
        apontando para uma operação que o sistema diz não ter existido.
        """
        self._carregar(quantidade='300')
        self._na_rua()
        venda = self._entregar('50')
        venda.documento_fiscal = self._documento()
        venda.save(update_fields=['documento_fiscal'])

        with self.assertRaises(DadosInvalidosError) as erro:
            VendaViagemService.cancelar(venda)

        self.assertIn('Cancele o documento fiscal', str(erro.exception))

    def test_10b_documento_autorizado_nao_e_excluido(self):
        """
        Uma NF-e autorizada existe nos registros do Fisco. Apagar a linha aqui
        não a desfaz lá.
        """
        from apps.financeiro.models import DocumentoFiscalProtegidoError

        documento = self._documento()

        with self.assertRaises(DocumentoFiscalProtegidoError):
            documento.delete()

    def test_10c_exclusao_em_massa_tambem_e_recusada(self):
        """
        O Django não chama `Model.delete()` num `queryset.delete()`, então sem
        um guarda no queryset um `.filter(...).delete()` passaria por cima da
        regra sem nem tocá-la.
        """
        from apps.financeiro.models import DocumentoFiscal, DocumentoFiscalProtegidoError

        self._documento()

        with self.assertRaises(DocumentoFiscalProtegidoError):
            DocumentoFiscal.objects.all().delete()
        self.assertEqual(DocumentoFiscal.objects.count(), 1)

    def test_10d_documento_que_nunca_chegou_a_sefaz_pode_sair(self):
        """
        Rascunho e rejeitada não existem no Fisco; travá-los deixaria lixo
        insuportável na tela sem proteger nada.
        """
        from apps.financeiro.constants.enums import StatusDocumentoFiscal
        from apps.financeiro.models import DocumentoFiscal

        pendente = self._documento(numero=2, status=StatusDocumentoFiscal.PENDENTE)
        rejeitada = self._documento(numero=3, status=StatusDocumentoFiscal.REJEITADA)

        pendente.delete()
        DocumentoFiscal.objects.filter(pk=rejeitada.pk).delete()

        self.assertEqual(DocumentoFiscal.objects.count(), 0)

    def test_10e_cancelada_e_inutilizada_tambem_ficam(self):
        """
        Cancelar não apaga na SEFAZ, e apagar aqui liberaria o número para ser
        reusado -- que é justamente o que esses dois status impedem.
        """
        from apps.financeiro.constants.enums import StatusDocumentoFiscal
        from apps.financeiro.models import DocumentoFiscalProtegidoError

        for numero, status in (
            (4, StatusDocumentoFiscal.CANCELADA),
            (5, StatusDocumentoFiscal.INUTILIZADA),
            (6, StatusDocumentoFiscal.DENEGADA),
        ):
            with self.subTest(status=status):
                documento = self._documento(numero=numero, status=status)
                with self.assertRaises(DocumentoFiscalProtegidoError):
                    documento.delete()

    # 11 ────────────────────────────────────────────────────────────────
    def test_11_venda_de_produto_esgotado_na_carga_e_recusada(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('300')

        with self.assertRaises(DadosInvalidosError):
            self._entregar('1')


class LogTests(SegurancaBase):
    """
    O log com os sete campos da especificação.

    REGISTRA A QUANTIDADE ANTES E DEPOIS: guardar só a movimentação obriga quem
    lê a somar tudo desde o começo para saber onde o saldo estava.
    """

    def _linhas(self):
        return LogViagemService.linhas(self.viagem)

    def test_a_venda_grava_os_sete_campos(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('50')

        linha = [l for l in self._linhas() if 'Venda' in l['operacao']][-1]

        self.assertEqual(linha['usuario'], self.usuario)
        self.assertIsNotNone(linha['quando'])
        self.assertEqual(linha['operacao'], 'Venda registrada')
        self.assertEqual(linha['quantidade_anterior'], '300.000')
        self.assertEqual(linha['quantidade_nova'], '250.000')
        self.assertIn('Produto CX1', linha['produto'])
        self.assertIn('Cliente A', linha['motivo'])

    def test_cada_destino_do_saldo_tem_nome_proprio(self):
        """
        "Baixa" sem dizer se foi venda, bonificação ou perda obriga a abrir
        outra tela para saber.
        """
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('50')
        self._entregar('10', tipo=VendaViagem.Tipo.BONIFICACAO)
        ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal('40'), usuario=self.usuario,
        )

        operacoes = [l['operacao'] for l in self._linhas()]

        self.assertIn('Venda registrada', operacoes)
        self.assertIn('Bonificação registrada', operacoes)
        self.assertIn('Retorno registrado', operacoes)

    def test_o_depois_de_uma_linha_e_o_antes_da_seguinte(self):
        """
        É o que denuncia buraco: se não bater, alguém mexeu por fora do
        sistema.
        """
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('50')
        self._entregar('80')

        # So' as linhas que mexem no SALDO. A inclusao na carga tambem tem
        # quantidade, mas de outra dimensao -- ela mede o item, nao o saldo,
        # e misturar as duas tornaria a corrente sem sentido.
        do_saldo = {'Venda registrada', 'Bonificação registrada',
                    'Retorno registrado', 'Baixa registrada'}
        movimentos = [l for l in self._linhas() if l['operacao'] in do_saldo]
        self.assertGreater(len(movimentos), 1)
        for anterior, seguinte in zip(movimentos, movimentos[1:]):
            self.assertEqual(
                anterior['quantidade_nova'], seguinte['quantidade_anterior'],
                'a corrente do saldo tem um buraco',
            )

    def test_operacao_sem_quantidade_nao_grava_zero(self):
        """
        Gravar zero ali faria o histórico parecer ter zerado o saldo.
        """
        self._carregar(quantidade='300')
        self._na_rua()

        fechamento = [
            l for l in self._linhas() if l['operacao'] == 'Carga fechada'
        ][0]

        self.assertIsNone(fechamento['quantidade_anterior'])
        self.assertIsNone(fechamento['quantidade_nova'])

    def test_o_cancelamento_registra_o_motivo(self):
        self._carregar(quantidade='300')
        self._na_rua()
        venda = self._entregar('50')

        VendaViagemService.cancelar(venda, motivo='Cliente desistiu')

        linha = [l for l in self._linhas() if 'cancelada' in l['operacao'].lower()][0]
        self.assertEqual(linha['motivo'], 'Cliente desistiu')

    def test_cancelamento_sem_motivo_diz_que_nao_teve(self):
        """
        Campo vazio some na leitura; "sem motivo informado" é uma informação.
        """
        self._carregar(quantidade='300')
        self._na_rua()
        venda = self._entregar('50')

        VendaViagemService.cancelar(venda)

        linha = [l for l in self._linhas() if 'cancelada' in l['operacao'].lower()][0]
        self.assertEqual(linha['motivo'], 'sem motivo informado')

    def test_o_historico_vem_do_mais_antigo_para_o_mais_novo(self):
        """Ele se lê como narrativa."""
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('50')

        linhas = self._linhas()

        self.assertEqual(linhas[0]['operacao'], 'Item incluído na carga')
        self.assertEqual(linhas[1]['operacao'], 'Carga fechada')
        for anterior, seguinte in zip(linhas, linhas[1:]):
            self.assertLessEqual(anterior['quando'], seguinte['quando'])

    def test_o_log_e_da_filial_da_viagem(self):
        self._carregar(quantidade='300')
        self._na_rua()

        registro = LogViagemService.historico(self.viagem).first()

        self.assertEqual(registro.filial, self.filial)
        self.assertEqual(registro.modulo, 'logistica')

    def test_uma_falha_no_log_nao_derruba_a_operacao(self):
        """
        Um log que derruba a operação que deveria registrar troca um problema
        de auditoria por um de produção -- o caminhão não pode ficar parado
        porque a escrita do histórico falhou.
        """
        self._carregar(quantidade='300')
        self._na_rua()

        # Um objeto que nao e' viagem quebra a escrita; o retorno e' None e
        # nada estoura para quem chamou.
        class Quebrado:
            filial = None
            numero = 'nao e numero'
            status = 'x'
            pk = None

        self.assertIsNone(
            LogViagemService.registrar(Quebrado(), 'x', usuario=self.usuario),
        )

    def test_o_log_nao_mistura_viagens(self):
        outra = Viagem.objects.create(
            filial=self.filial, numero=9, motorista_nome='Outro',
            vendedor=self.usuario,
        )
        self._carregar(quantidade='300')
        self._na_rua()

        self.assertEqual(LogViagemService.historico(outra).count(), 0)
        self.assertGreater(LogViagemService.historico(self.viagem).count(), 0)

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_tela_mostra_os_sete_campos(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('50')

        html = self.client.get(
            reverse('logistica:viagem-historico', args=[self.viagem.pk]),
        ).content.decode()

        for coluna in ('Quando', 'Usuário', 'Operação', 'Produto',
                       'Antes', 'Depois', 'Documento', 'Motivo'):
            self.assertIn(coluna, html, f'a coluna {coluna} sumiu')
        self.assertIn('Venda registrada', html)
        self.assertIn('250.000', html)

    def test_historico_de_outra_filial_nao_abre(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(filial=outra, numero=1, motorista_nome='Zé')

        resposta = self.client.get(
            reverse('logistica:viagem-historico', args=[alheia.pk]),
        )

        self.assertEqual(resposta.status_code, 404)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._carregar(quantidade='300')

        html = self.client.get(
            reverse('logistica:viagem-historico', args=[self.viagem.pk]),
        ).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')


class ManifestoDaViagemTests(SegurancaBase):
    """
    O manifesto que a viagem cria precisa ficar ligado a ela.

    SEM O VÍNCULO O BOTÃO MENTE: cria um MDF-e solto, a tela continua dizendo
    "sem manifesto", e o próximo clique cria mais um — a duplicidade que a
    regra 8 existe para impedir passaria justamente pelo caminho normal.
    """

    def _post(self, **extras):
        agora = timezone.localtime().replace(second=0, microsecond=0)
        dados = {
            'numero': 1, 'serie': '1',
            'data_emissao': timezone.localdate().isoformat(),
            'modal': 'rodoviario',
            'motorista_nome': 'Seu Zé', 'motorista_cpf': '11144477735',
            'veiculo_placa': 'ABC1D23',
            'uf_carregamento': 'RN', 'municipio_carregamento': 'Natal',
            'codigo_municipio_carregamento': '2408102',
            'uf_descarregamento': 'PB', 'municipio_descarregamento': 'João Pessoa',
            'codigo_municipio_descarregamento': '2507507',
            'peso_carga_kg': '1200',
            'inicio_viagem': agora.strftime('%Y-%m-%dT%H:%M'),
            'previsao_chegada': (agora + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M'),
        }
        dados.update(extras)
        return self.client.post(reverse('logistica:mdfe-create'), dados)

    def test_o_manifesto_nasce_ligado_a_viagem(self):
        from apps.logistica.models import MDFe

        self._carregar(quantidade='300')
        self._na_rua()

        resposta = self._post(viagem=self.viagem.pk)

        criado = MDFe.objects.filter(viagem=self.viagem).first()
        self.assertIsNotNone(criado, f'MDF-e nasceu solto (HTTP {resposta.status_code})')
        self.assertEqual(MDFeViagemService.mdfe_da_viagem(self.viagem), criado)

    def test_o_segundo_manifesto_da_mesma_viagem_e_recusado(self):
        from apps.logistica.models import MDFe

        self._carregar(quantidade='300')
        self._na_rua()
        self._post(viagem=self.viagem.pk)

        resposta = self._post(viagem=self.viagem.pk)

        self.assertEqual(MDFe.objects.filter(viagem=self.viagem).count(), 1)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('já tem o MDF-e', ' '.join(
            str(m) for m in resposta.wsgi_request._messages
        ))

    def test_manifesto_sem_viagem_continua_podendo_nascer(self):
        """Transferência entre filiais emite MDF-e sem viagem nenhuma."""
        from apps.logistica.models import MDFe

        self._post()

        self.assertEqual(MDFe.objects.filter(viagem__isnull=True).count(), 1)

    def test_a_emissao_do_manifesto_entra_no_historico(self):
        self._carregar(quantidade='300')
        self._na_rua()

        self._post(viagem=self.viagem.pk)

        linha = [
            l for l in LogViagemService.linhas(self.viagem)
            if l['operacao'] == 'Documento fiscal emitido'
        ][-1]
        self.assertIn('MDF-e', linha['motivo'])
        self.assertEqual(linha['usuario'], self.usuario)

    def test_viagem_de_outra_filial_nao_amarra_o_manifesto(self):
        from apps.logistica.models import MDFe

        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(filial=outra, numero=7, motorista_nome='Zé')

        self._post(viagem=alheia.pk)

        self.assertEqual(MDFe.objects.filter(viagem=alheia).count(), 0)


class HistoricoDasEmissoesTests(SegurancaBase):
    """As emissões e as mudanças de etapa também entram no histórico."""

    def test_a_remessa_emitida_entra_no_historico(self):
        self._carregar(quantidade='300')
        self._na_rua()

        documento = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        linha = [
            l for l in LogViagemService.linhas(self.viagem)
            if l['operacao'] == 'Documento fiscal emitido'
        ][-1]
        self.assertEqual(linha['documento'], f'{documento.numero}/{documento.serie}')
        self.assertIn('remessa', linha['motivo'])

    def test_a_mudanca_de_etapa_diz_de_onde_para_onde(self):
        """
        "Etapa alterada" sozinho não responde nada; o par de rótulos responde.
        """
        self._carregar(quantidade='300')
        ViagemService.fechar_carga(self.viagem, usuario=self.usuario)

        ViagemService.mudar_status(
            self.viagem, Viagem.Status.DOCUMENTOS_EMITIDOS, usuario=self.usuario,
        )

        linha = [
            l for l in LogViagemService.linhas(self.viagem)
            if l['operacao'] == 'Etapa alterada'
        ][-1]
        self.assertIn('de "Aguardando documentos fiscais"', linha['motivo'])
        self.assertIn('para "Documentos emitidos"', linha['motivo'])

    def test_o_encerramento_entra_no_historico(self):
        self._carregar(quantidade='300')
        self._na_rua()
        self._entregar('300')

        ViagemService.encerrar(self.viagem, usuario=self.usuario)

        operacoes = [l['operacao'] for l in LogViagemService.linhas(self.viagem)]
        self.assertIn('Viagem encerrada', operacoes)

    def test_o_item_removido_da_carga_fica_registrado(self):
        """Sair da carga é uma mudança de quantidade como qualquer outra."""
        item = self._carregar(quantidade='300')

        ViagemService.remover_item(self.viagem, item, usuario=self.usuario)

        linha = [
            l for l in LogViagemService.linhas(self.viagem)
            if l['operacao'] == 'Item removido da carga'
        ][0]
        self.assertEqual(linha['quantidade_anterior'], '300.000')
        self.assertIn('Produto CX1', linha['produto'])

    def test_a_viagem_leva_ao_historico(self):
        """Log que não se alcança pela tela é log que ninguém lê."""
        self._carregar(quantidade='300')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

        self.assertIn(
            reverse('logistica:viagem-historico', args=[self.viagem.pk]), html,
        )
