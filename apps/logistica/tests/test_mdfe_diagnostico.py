"""
Diagnostico do MDF-e: separar "falta dado" de "tem bug".

A tela mostrava tres bloqueios ao mesmo tempo -- NF-e sem chave autorizada,
municipio de descarregamento vazio e peso zero. Sao sintomas encadeados ou
falhas independentes? Estes testes montam o cenario COMPLETO e vao tirando
uma peca de cada vez, para saber qual causa qual.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusDocumentoFiscal


class BaseMDFe(TestCase):
    _seq = 0

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        # Emitente completo: CNPJ, IE e endereco com IBGE, senao a validacao
        # para na filial antes de chegar ao que se quer medir.
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='SABORAFRUTA LTDA',
            nome_fantasia='SABORAFRUTA', cnpj='11222333000181',
            inscricao_estadual='200000000', uf='RN', cidade='Natal',
            endereco='Av Principal', numero='100', bairro='Centro',
            cep='59000000', codigo_municipio_ibge='2408102', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True)
        self.usuario = Usuario.objects.create_user(
            email='u@teste.local', nome='U', password='senha-de-teste-123',
            empresa=self.empresa, perfil=perfil, filial=self.filial)

    def _mdfe(self, **kw):
        from apps.logistica.models import MDFe

        BaseMDFe._seq += 1
        dados = dict(
            filial=self.filial, numero=BaseMDFe._seq, serie=1,
            data_emissao=timezone.localtime(),
            motorista_nome='Victor Trindade', motorista_cpf='07812214152',
            veiculo_placa='IYG5E68',
            uf_carregamento='RN', municipio_carregamento='Natal',
            codigo_municipio_carregamento='2408102',
            uf_descarregamento='RN', municipio_descarregamento='Parnamirim',
            codigo_municipio_descarregamento='2403251',
            peso_total_kg=Decimal('150'),
            # As chaves sao exatamente estas: `uf_placa` (nao `uf`), e sem
            # tipo_rodado/tipo_carroceria a validacao do veiculo barra antes
            # de qualquer checagem de rota.
            transporte_metadados={
                'tara': '1000', 'uf_placa': 'RN',
                'tipo_rodado': '01', 'tipo_carroceria': '00',
            },
        )
        dados.update(kw)
        return MDFe.objects.create(**dados)

    def _nfe(self, *, autorizada=True, chave=None):
        from apps.financeiro.models import DocumentoFiscal

        BaseMDFe._seq += 1
        chave = chave if chave is not None else '5' * 44
        return DocumentoFiscal.objects.create(
            filial=self.filial, tipo_documento='nfe',
            numero=BaseMDFe._seq, serie=1, chave=chave,
            emitente_cnpj=self.filial.cnpj, data_emissao=timezone.localtime(),
            usuario=self.usuario,
            status=(StatusDocumentoFiscal.AUTORIZADA if autorizada
                    else StatusDocumentoFiscal.PENDENTE),
            valor_total=Decimal('1550'),
            destinatario_snapshot={
                'nome': 'CLIENTE DESTINO', 'cidade': 'Parnamirim', 'uf': 'RN',
                'codigo_municipio': '2403251', 'cep': '59140000',
                'logradouro': 'Rua do Destino', 'numero': '50',
                'bairro': 'Centro',
            },
        )

    def _vincular(self, mdfe, nfe, **kw):
        from apps.logistica.models import DocumentoMDFe

        dados = dict(
            mdfe=mdfe, documento_fiscal=nfe, tipo_documento='nfe',
            chave_acesso=nfe.chave or '', numero_documento=str(nfe.numero),
            serie=str(nfe.serie), emitente_nome=self.filial.razao_social,
            emitente_documento=self.filial.cnpj,
            municipio_descarga='Parnamirim', uf_descarga='RN',
            peso_kg=Decimal('150'), valor=Decimal('1550'),
        )
        dados.update(kw)
        return DocumentoMDFe.objects.create(**dados)

    def _payload(self, mdfe):
        from apps.logistica.services.mdfe_focusnfe import construir_payload_mdfe

        return construir_payload_mdfe(mdfe)


class CenarioCompletoTests(BaseMDFe):
    """Com tudo preenchido, o payload sai. E a referencia dos demais testes."""

    def test_mdfe_completo_gera_payload(self):
        mdfe = self._mdfe()
        self._vincular(mdfe, self._nfe())

        payload = self._payload(mdfe)

        self.assertEqual(payload['uf_inicio'], 'RN')
        self.assertEqual(payload['uf_fim'], 'RN')
        self.assertEqual(payload['cnpj_emitente'], '11222333000181')

    def test_payload_leva_a_chave_da_nfe(self):
        mdfe = self._mdfe()
        self._vincular(mdfe, self._nfe(chave='1' * 44))

        payload = self._payload(mdfe)
        texto = str(payload)

        self.assertIn('1' * 44, texto)

    def test_payload_leva_o_peso_bruto(self):
        mdfe = self._mdfe(peso_total_kg=Decimal('275.5'))
        self._vincular(mdfe, self._nfe())

        self.assertEqual(self._payload(mdfe)['peso_bruto'], '275.5000')


class BloqueiosTests(BaseMDFe):
    """Cada peca que falta, isolada, para saber o que cada mensagem significa."""

    def test_sem_nfe_vinculada_recusa(self):
        with self.assertRaises(DomainError) as ctx:
            self._payload(self._mdfe())
        self.assertIn('ao menos uma NF-e', str(ctx.exception))

    def test_nfe_nao_autorizada_recusa_pelo_status(self):
        """
        E regra da SEFAZ, nao capricho do sistema: NF-e nao autorizada nao
        pode entrar em manifesto.
        """
        mdfe = self._mdfe()
        self._vincular(mdfe, self._nfe(autorizada=False))

        with self.assertRaises(DomainError) as ctx:
            self._payload(mdfe)
        self.assertIn('ainda não foi autorizada', str(ctx.exception))

    def test_nfe_autorizada_sem_chave_recusa(self):
        """Foi este o caso da tela: autorizada no status, mas sem a chave."""
        mdfe = self._mdfe()
        self._vincular(mdfe, self._nfe(chave=''), chave_acesso='')

        with self.assertRaises(DomainError) as ctx:
            self._payload(mdfe)
        self.assertIn('chave de acesso', str(ctx.exception))

    def test_sem_municipio_de_descarregamento_recusa(self):
        mdfe = self._mdfe(municipio_descarregamento='',
                          codigo_municipio_descarregamento='')
        self._vincular(mdfe, self._nfe())

        with self.assertRaises(DomainError) as ctx:
            self._payload(mdfe)
        self.assertIn('descarregamento', str(ctx.exception))

    def test_peso_zero_recusa(self):
        mdfe = self._mdfe(peso_total_kg=Decimal('0'))
        self._vincular(mdfe, self._nfe())

        with self.assertRaises(DomainError) as ctx:
            self._payload(mdfe)
        self.assertIn('peso bruto', str(ctx.exception))

    def test_erros_de_rota_saem_todos_de_uma_vez(self):
        """
        Uma mensagem por vez faria o usuario corrigir, tentar, descobrir a
        proxima -- tres idas ate a SEFAZ para o mesmo manifesto.
        """
        mdfe = self._mdfe(
            municipio_descarregamento='', codigo_municipio_descarregamento='',
            peso_total_kg=Decimal('0'), uf_descarregamento='',
        )
        self._vincular(mdfe, self._nfe())

        with self.assertRaises(DomainError) as ctx:
            self._payload(mdfe)

        texto = str(ctx.exception)
        self.assertIn('descarregamento', texto)
        self.assertIn('peso bruto', texto)


class ChaveDaNFeTests(BaseMDFe):
    """
    De onde a chave e lida.

    O `_chave_nfe_vinculada` tem varias fontes em cascata. Se a ordem quebrar,
    um MDF-e com NF-e perfeitamente autorizada passa a ser recusado -- e a
    mensagem culpa a NF-e, nao o codigo.
    """

    def _chave(self, vinculo):
        from apps.logistica.services.mdfe_focusnfe import _chave_nfe_vinculada

        return _chave_nfe_vinculada(vinculo)

    def test_le_do_documento_fiscal(self):
        mdfe = self._mdfe()
        vinculo = self._vincular(mdfe, self._nfe(chave='7' * 44))
        self.assertEqual(self._chave(vinculo), '7' * 44)

    def test_cai_para_a_chave_do_vinculo(self):
        mdfe = self._mdfe()
        vinculo = self._vincular(mdfe, self._nfe(chave=''), chave_acesso='9' * 44)
        self.assertEqual(self._chave(vinculo), '9' * 44)

    def test_extrai_do_xml_quando_o_campo_esta_vazio(self):
        """NF-e importada por XML pode nao ter o campo `chave` preenchido."""
        from apps.financeiro.models import DocumentoFiscal

        mdfe = self._mdfe()
        nfe = self._nfe(chave='')
        DocumentoFiscal.objects.filter(pk=nfe.pk).update(
            xml_retorno=f'<nfeProc><protNFe><chNFe>{"3" * 44}</chNFe></protNFe></nfeProc>'
        )
        nfe.refresh_from_db()
        vinculo = self._vincular(mdfe, nfe, chave_acesso='')

        self.assertEqual(self._chave(vinculo), '3' * 44)

    def test_chave_com_tamanho_errado_nao_e_aceita(self):
        """43 digitos passariam adiante e a SEFAZ rejeitaria o manifesto."""
        mdfe = self._mdfe()
        vinculo = self._vincular(mdfe, self._nfe(chave='2' * 43), chave_acesso='')

        self.assertEqual(self._chave(vinculo), '')


class CascataDaNFeTests(BaseMDFe):
    """
    Vincular uma NF-e autorizada preenche rota e peso sozinho.

    E o ponto que decide se um MDF-e travado e bug ou dado faltando: se o
    vinculo preenche, entao municipio vazio e peso zero sao SINTOMA de NF-e
    sem autorizacao -- nao tres problemas para resolver um a um.
    """

    def _vincular_pela_view(self, mdfe, nfe):
        from apps.logistica.views import _vincular_nfe_ao_mdfe

        return _vincular_nfe_ao_mdfe(mdfe, nfe, atualizar_rota=True)

    def test_vinculo_preenche_o_municipio_de_descarregamento(self):
        mdfe = self._mdfe(municipio_descarregamento='',
                          codigo_municipio_descarregamento='',
                          uf_descarregamento='')

        self._vincular_pela_view(mdfe, self._nfe())
        mdfe.refresh_from_db()

        self.assertEqual(mdfe.municipio_descarregamento, 'Parnamirim')
        self.assertEqual(mdfe.codigo_municipio_descarregamento, '2403251')
        self.assertEqual(mdfe.uf_descarregamento, 'RN')

    def test_vinculo_preenche_o_carregamento_pela_filial(self):
        mdfe = self._mdfe(municipio_carregamento='',
                          codigo_municipio_carregamento='')

        self._vincular_pela_view(mdfe, self._nfe())
        mdfe.refresh_from_db()

        self.assertEqual(mdfe.municipio_carregamento, 'Natal')
        self.assertEqual(mdfe.codigo_municipio_carregamento, '2408102')

    def test_destino_sai_do_snapshot_do_destinatario(self):
        """
        E do snapshot que a rota vem. NF-e ainda nao autorizada costuma estar
        sem ele -- por isso o municipio aparece vazio na tela.
        """
        from apps.financeiro.models import DocumentoFiscal

        mdfe = self._mdfe(municipio_descarregamento='',
                          codigo_municipio_descarregamento='')
        nfe = self._nfe()
        DocumentoFiscal.objects.filter(pk=nfe.pk).update(destinatario_snapshot={})
        nfe.refresh_from_db()

        self._vincular_pela_view(mdfe, nfe)
        mdfe.refresh_from_db()

        self.assertEqual(mdfe.municipio_descarregamento, '')

    def test_depois_de_vincular_o_payload_sai(self):
        """O teste que fecha o diagnostico: NF-e autorizada = MDF-e emissivel."""
        mdfe = self._mdfe(municipio_descarregamento='',
                          codigo_municipio_descarregamento='',
                          uf_descarregamento='')

        self._vincular_pela_view(mdfe, self._nfe())
        mdfe.refresh_from_db()

        payload = self._payload(mdfe)
        self.assertEqual(payload['uf_fim'], 'RN')


class EntregaParaClienteTests(BaseMDFe):
    """
    Entrega para CLIENTE, nao para filial.

    O caminho original so montava o endereco de destino quando o destinatario
    era uma filial. Numa entrega para cliente o campo ficava vazio e a tela
    mandava conferir "o cadastro da filial de destino" -- um cadastro que nao
    tem nada a ver com o problema.
    """

    def _endereco_destino(self, nfe):
        from apps.logistica.views import _endereco_destino_nfe

        return _endereco_destino_nfe(nfe)

    def test_endereco_do_cliente_sai_do_snapshot_da_nfe(self):
        endereco = self._endereco_destino(self._nfe())

        self.assertIn('Rua do Destino', endereco)
        self.assertIn('50', endereco)
        self.assertIn('Parnamirim', endereco)
        self.assertIn('RN', endereco)

    def test_rota_para_cliente_preenche_o_descarregamento(self):
        """
        O municipio ja vinha certo pelo snapshot -- so o endereco e que nao.
        Este teste garante que a correcao nao mexeu no que ja funcionava.
        """
        from apps.logistica.views import _rota_filiais_nfe

        rota = _rota_filiais_nfe(self._nfe())

        self.assertEqual(rota['municipio_descarregamento'], 'Parnamirim')
        self.assertEqual(rota['codigo_municipio_descarregamento'], '2403251')
        self.assertEqual(rota['uf_descarregamento'], 'RN')
        # Sem filial de destino: e uma entrega para cliente.
        self.assertIsNone(rota['destino'])

    def test_snapshot_vazio_devolve_endereco_vazio(self):
        """Aí sim o dado falta de verdade — e a tela precisa dizer isso."""
        from apps.financeiro.models import DocumentoFiscal

        nfe = self._nfe()
        DocumentoFiscal.objects.filter(pk=nfe.pk).update(destinatario_snapshot={})
        nfe.refresh_from_db()

        self.assertEqual(self._endereco_destino(nfe), '')

    def test_mdfe_para_cliente_emite_normalmente(self):
        """O que fecha o caso: entrega para cliente gera o payload."""
        from apps.logistica.views import _vincular_nfe_ao_mdfe

        mdfe = self._mdfe(municipio_descarregamento='',
                          codigo_municipio_descarregamento='',
                          uf_descarregamento='')
        _vincular_nfe_ao_mdfe(mdfe, self._nfe(), atualizar_rota=True)
        mdfe.refresh_from_db()

        payload = self._payload(mdfe)

        self.assertEqual(payload['uf_fim'], 'RN')
        self.assertEqual(mdfe.municipio_descarregamento, 'Parnamirim')
