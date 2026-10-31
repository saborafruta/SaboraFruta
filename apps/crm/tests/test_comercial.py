from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.crm.forms import OportunidadeCRMForm
from apps.crm.models import (
    AtividadeCRM, CarteiraCliente, EtapaPipelineCRM, OportunidadeCRM, PipelineCRM,
)
from apps.crm.services import ComercialService
from apps.crm.services.comercial_service import EtapaAutomaticaCRM


class ComercialServiceTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa CRM', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz CRM', nome_fantasia='Matriz CRM',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        self.perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Comercial', ativo=True,
        )
        self.usuario = Usuario.objects.create_user(
            email='vendas@example.com', nome='Vendas', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil,
        )
        self.cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente CRM', cpf_cnpj='12345678901',
            tipo_pessoa='F', ativo=True,
        )
        CarteiraCliente.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vendedor=self.usuario,
            atribuido_por=self.usuario,
        )

    def _criar_oportunidade(self):
        pipeline = ComercialService.obter_pipeline_padrao(self.filial)
        etapa = pipeline.etapas.order_by('ordem').first()
        return ComercialService.criar_oportunidade(
            filial=self.filial,
            usuario=self.usuario,
            dados={
                'cliente': self.cliente,
                'pipeline': pipeline,
                'etapa': etapa,
                'titulo': 'Venda recorrente',
                'valor_estimado': Decimal('1500.00'),
                'prioridade': OportunidadeCRM.Prioridade.ALTA,
                'origem': OportunidadeCRM.Origem.RECOMPRA,
                'responsavel': self.usuario,
                'descricao': '',
                'previsao_fechamento': None,
                'proxima_acao': 'Telefonar para o cliente',
                'proxima_acao_em': None,
            },
        )

    def test_pipeline_padrao_e_criado_uma_unica_vez_com_cinco_etapas(self):
        primeiro = ComercialService.obter_pipeline_padrao(self.filial)
        segundo = ComercialService.obter_pipeline_padrao(self.filial)

        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(PipelineCRM.objects.filter(filial=self.filial).count(), 1)
        self.assertEqual(primeiro.etapas.count(), 5)
        self.assertEqual(
            list(primeiro.etapas.order_by('ordem').values_list('nome', flat=True)),
            [
                'Conversas', 'Orçando', 'Aguardando aprovação',
                'Aprovados', 'Recusados',
            ],
        )

    def test_conversa_whatsapp_cria_um_cartao_e_move_o_mesmo_cartao(self):
        criada = ComercialService.sincronizar_conversa_whatsapp(
            conversa_id=123,
            filial=self.filial,
            cliente=self.cliente,
            responsavel=self.usuario,
            etapa_nome=EtapaAutomaticaCRM.CONVERSAS,
            autor=self.usuario,
        )
        movida = ComercialService.sincronizar_conversa_whatsapp(
            conversa_id=123,
            filial=self.filial,
            cliente=self.cliente,
            responsavel=self.usuario,
            etapa_nome=EtapaAutomaticaCRM.AGUARDANDO_APROVACAO,
            valor_estimado=Decimal('350.00'),
            autor=self.usuario,
        )

        self.assertEqual(criada.pk, movida.pk)
        self.assertEqual(OportunidadeCRM.objects.count(), 1)
        self.assertEqual(movida.etapa.nome, 'Aguardando aprovação')
        self.assertEqual(movida.valor_estimado, Decimal('350.00'))
        self.assertEqual(movida.origem, OportunidadeCRM.Origem.WHATSAPP)
        self.assertEqual(movida.atividades.count(), 2)

    def test_criacao_registra_atividade_auditavel(self):
        oportunidade = self._criar_oportunidade()

        atividade = oportunidade.atividades.get()
        self.assertEqual(atividade.tipo, AtividadeCRM.Tipo.CRIACAO)
        self.assertEqual(atividade.autor, self.usuario)
        self.assertIn('Conversas', atividade.descricao)

    def test_movimentacao_para_ganho_fecha_oportunidade_e_registra_historico(self):
        oportunidade = self._criar_oportunidade()
        etapa = oportunidade.pipeline.etapas.get(tipo=EtapaPipelineCRM.Tipo.GANHA)

        atualizada = ComercialService.mover_etapa(
            oportunidade=oportunidade, etapa=etapa, usuario=self.usuario,
        )

        self.assertEqual(atualizada.etapa, etapa)
        self.assertIsNotNone(atualizada.fechado_em)
        self.assertEqual(
            atualizada.atividades.filter(tipo=AtividadeCRM.Tipo.MOVIMENTACAO).count(), 1,
        )

    def test_movimentacao_recusa_etapa_de_outro_funil(self):
        oportunidade = self._criar_oportunidade()
        outro = PipelineCRM.objects.create(filial=self.filial, nome='Outro funil')
        etapa = EtapaPipelineCRM.objects.create(
            pipeline=outro, nome='Outra etapa', ordem=1,
        )

        with self.assertRaisesMessage(ValidationError, 'não pertence ao funil'):
            ComercialService.mover_etapa(
                oportunidade=oportunidade, etapa=etapa, usuario=self.usuario,
            )

        oportunidade.refresh_from_db()
        self.assertNotEqual(oportunidade.etapa, etapa)

    def test_formulario_nao_aceita_cliente_de_outra_filial(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial B', nome_fantasia='Filial B',
            cnpj='11222333000262', uf='RN', is_matriz=False,
        )
        cliente_de_fora = Cliente.objects.create(
            filial=outra, razao_social='Cliente de fora', cpf_cnpj='98765432100',
            tipo_pessoa='F', ativo=True,
        )
        pipeline = ComercialService.obter_pipeline_padrao(self.filial)
        etapa = pipeline.etapas.order_by('ordem').first()
        form = OportunidadeCRMForm(data={
            'titulo': 'Tentativa fora do escopo',
            'cliente': cliente_de_fora.pk,
            'pipeline': pipeline.pk,
            'etapa': etapa.pk,
            'valor_estimado': '100.00',
            'prioridade': OportunidadeCRM.Prioridade.NORMAL,
            'origem': OportunidadeCRM.Origem.MANUAL,
        }, filial=self.filial, usuario=self.usuario)

        self.assertFalse(form.is_valid())
        self.assertIn('cliente', form.errors)
