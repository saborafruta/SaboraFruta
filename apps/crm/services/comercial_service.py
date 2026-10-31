"""Regras transacionais da Central Comercial."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.crm.models import AtividadeCRM, EtapaPipelineCRM, OportunidadeCRM, PipelineCRM
from apps.crm.services.carteira_service import CarteiraService


class EtapaAutomaticaCRM:
    CONVERSAS = 'Conversas'
    ORCANDO = 'Orçando'
    AGUARDANDO_APROVACAO = 'Aguardando aprovação'
    APROVADOS = 'Aprovados'
    RECUSADOS = 'Recusados'


ETAPAS_PADRAO = (
    (EtapaAutomaticaCRM.CONVERSAS, '#3b82f6', 10, EtapaPipelineCRM.Tipo.ABERTA),
    (EtapaAutomaticaCRM.ORCANDO, '#8b5cf6', 35, EtapaPipelineCRM.Tipo.ABERTA),
    (
        EtapaAutomaticaCRM.AGUARDANDO_APROVACAO,
        '#f59e0b', 65, EtapaPipelineCRM.Tipo.ABERTA,
    ),
    (EtapaAutomaticaCRM.APROVADOS, '#22c55e', 100, EtapaPipelineCRM.Tipo.GANHA),
    (EtapaAutomaticaCRM.RECUSADOS, '#ef4444', 0, EtapaPipelineCRM.Tipo.PERDIDA),
)


PROXIMAS_ACOES_AUTOMATICAS = {
    EtapaAutomaticaCRM.CONVERSAS: 'Atender a conversa no WhatsApp',
    EtapaAutomaticaCRM.ORCANDO: 'Finalizar e enviar o orçamento',
    EtapaAutomaticaCRM.AGUARDANDO_APROVACAO: 'Acompanhar a resposta do cliente',
    EtapaAutomaticaCRM.APROVADOS: 'Acompanhar a separação do pedido',
    EtapaAutomaticaCRM.RECUSADOS: 'Revisar o motivo da recusa com o cliente',
}


class ComercialService:
    @staticmethod
    def _normalizar_etapas_automaticas(pipeline):
        etapas = list(pipeline.etapas.select_for_update().order_by('ordem', 'pk'))
        esperado = [
            (indice, nome, cor, probabilidade, tipo, True)
            for indice, (nome, cor, probabilidade, tipo) in enumerate(ETAPAS_PADRAO, 1)
        ]
        atual = [
            (
                etapa.ordem, etapa.nome, etapa.cor, etapa.probabilidade,
                etapa.tipo, etapa.ativo,
            )
            for etapa in etapas
        ]
        if atual[:len(esperado)] == esperado and all(
            not item[-1] for item in atual[len(esperado):]
        ):
            return

        # Libera temporariamente as restrições únicas de nome e ordem para
        # reaproveitar as etapas e manter as oportunidades já existentes.
        for indice, etapa in enumerate(etapas, 1):
            etapa.nome = f'Etapa temporária {etapa.pk}'
            etapa.ordem = 1000 + indice
            etapa.save(update_fields=['nome', 'ordem', 'updated_at'])

        for indice, (nome, cor, probabilidade, tipo) in enumerate(ETAPAS_PADRAO, 1):
            if indice <= len(etapas):
                etapa = etapas[indice - 1]
                etapa.nome = nome
                etapa.cor = cor
                etapa.ordem = indice
                etapa.probabilidade = probabilidade
                etapa.tipo = tipo
                etapa.ativo = True
                etapa.save(update_fields=[
                    'nome', 'cor', 'ordem', 'probabilidade', 'tipo', 'ativo', 'updated_at',
                ])
            else:
                EtapaPipelineCRM.objects.create(
                    pipeline=pipeline, nome=nome, cor=cor, ordem=indice,
                    probabilidade=probabilidade, tipo=tipo,
                )

        for etapa in etapas[len(ETAPAS_PADRAO):]:
            etapa.ativo = False
            etapa.save(update_fields=['ativo', 'updated_at'])

    @staticmethod
    @tenant_atomic
    def obter_pipeline_padrao(filial):
        # Serializa a criação preguiçosa por filial. Sem esse lock, duas
        # primeiras visitas simultâneas poderiam disputar o funil padrão.
        filial.__class__.objects.select_for_update().get(pk=filial.pk)
        pipeline = (
            PipelineCRM.objects.select_for_update()
            .filter(filial=filial, padrao=True)
            .first()
        )
        if pipeline is None:
            pipeline = PipelineCRM.objects.create(
                filial=filial, nome='Funil comercial', padrao=True,
                descricao='Jornada automática das conversas e orçamentos do WhatsApp.',
            )
        ComercialService._normalizar_etapas_automaticas(pipeline)
        return pipeline

    @classmethod
    @tenant_atomic
    def sincronizar_conversa_whatsapp(
        cls, *, conversa_id, filial, cliente, responsavel, etapa_nome,
        valor_estimado=None, motivo_perda='', autor=None,
    ):
        pipeline = cls.obter_pipeline_padrao(filial)
        etapa = pipeline.etapas.get(nome=etapa_nome, ativo=True)
        oportunidade = (
            OportunidadeCRM.objects.select_for_update()
            .filter(filial=filial, conversa_whatsapp_id=conversa_id)
            .order_by('pk')
            .first()
        )
        criada = oportunidade is None
        etapa_anterior = oportunidade.etapa if oportunidade else None
        if criada:
            oportunidade = OportunidadeCRM(
                filial=filial,
                cliente=cliente,
                pipeline=pipeline,
                etapa=etapa,
                titulo=f'WhatsApp — {cliente.nome_display}',
                origem=OportunidadeCRM.Origem.WHATSAPP,
                responsavel=responsavel,
                conversa_whatsapp_id=conversa_id,
                criado_por=autor or responsavel,
            )
        else:
            oportunidade.cliente = cliente
            oportunidade.pipeline = pipeline
            oportunidade.etapa = etapa
            oportunidade.titulo = f'WhatsApp — {cliente.nome_display}'
            oportunidade.origem = OportunidadeCRM.Origem.WHATSAPP
            oportunidade.responsavel = responsavel or oportunidade.responsavel

        if valor_estimado is not None:
            oportunidade.valor_estimado = valor_estimado
        oportunidade.ultimo_contato_em = timezone.now()
        oportunidade.proxima_acao = PROXIMAS_ACOES_AUTOMATICAS[etapa_nome]
        oportunidade.motivo_perda = (
            (motivo_perda or '').strip()
            if etapa.tipo == EtapaPipelineCRM.Tipo.PERDIDA else ''
        )
        oportunidade.fechado_em = (
            timezone.now()
            if etapa.tipo in {EtapaPipelineCRM.Tipo.GANHA, EtapaPipelineCRM.Tipo.PERDIDA}
            else None
        )
        oportunidade.full_clean(exclude=['tags'])
        oportunidade.save()

        if criada:
            descricao = f'Conversa adicionada automaticamente em {etapa.nome}.'
            tipo_atividade = AtividadeCRM.Tipo.CRIACAO
        elif etapa_anterior_id := getattr(etapa_anterior, 'pk', None):
            if etapa_anterior_id == etapa.pk:
                return oportunidade
            descricao = f'Etapa atualizada automaticamente de {etapa_anterior.nome} para {etapa.nome}.'
            tipo_atividade = AtividadeCRM.Tipo.SISTEMA
        else:
            descricao = f'Etapa atualizada automaticamente para {etapa.nome}.'
            tipo_atividade = AtividadeCRM.Tipo.SISTEMA
        AtividadeCRM.objects.create(
            oportunidade=oportunidade,
            tipo=tipo_atividade,
            descricao=descricao,
            autor=autor,
            metadados={'origem': 'whatsapp', 'etapa': etapa_nome},
        )
        return oportunidade

    @classmethod
    @tenant_atomic
    def criar_oportunidade(cls, *, filial, usuario, dados, tags=None):
        dados = dict(dados)
        dados['responsavel'] = CarteiraService.validar_responsavel(
            cliente=dados['cliente'],
            responsavel=dados.get('responsavel'),
            usuario=usuario,
        )
        oportunidade = OportunidadeCRM(filial=filial, criado_por=usuario, **dados)
        oportunidade.full_clean(exclude=['tags'])
        oportunidade.save()
        if tags is not None:
            oportunidade.tags.set(tags)
        AtividadeCRM.objects.create(
            oportunidade=oportunidade, tipo=AtividadeCRM.Tipo.CRIACAO,
            descricao=f'Oportunidade criada na etapa {oportunidade.etapa.nome}.', autor=usuario,
        )
        return oportunidade

    @staticmethod
    @tenant_atomic
    def mover_etapa(*, oportunidade, etapa, usuario, motivo_perda=''):
        oportunidade = OportunidadeCRM.objects.select_for_update().select_related(
            'etapa', 'pipeline', 'filial', 'responsavel',
        ).get(pk=oportunidade.pk)
        if etapa.pipeline_id != oportunidade.pipeline_id:
            raise ValidationError('A etapa de destino não pertence ao funil da oportunidade.')
        if etapa.pipeline.filial_id != oportunidade.filial_id:
            raise ValidationError('A etapa de destino pertence a outra filial.')
        anterior = oportunidade.etapa
        oportunidade.etapa = etapa
        if etapa.tipo == EtapaPipelineCRM.Tipo.PERDIDA:
            oportunidade.motivo_perda = (motivo_perda or '').strip()
        elif anterior.tipo == EtapaPipelineCRM.Tipo.PERDIDA:
            oportunidade.motivo_perda = ''
        oportunidade.fechado_em = (
            timezone.now() if etapa.tipo in {
                EtapaPipelineCRM.Tipo.GANHA, EtapaPipelineCRM.Tipo.PERDIDA,
            } else None
        )
        oportunidade.full_clean(exclude=['tags'])
        oportunidade.save(update_fields=[
            'etapa', 'motivo_perda', 'fechado_em', 'updated_at',
        ])
        AtividadeCRM.objects.create(
            oportunidade=oportunidade, tipo=AtividadeCRM.Tipo.MOVIMENTACAO,
            descricao=f'Etapa alterada de {anterior.nome} para {etapa.nome}.',
            autor=usuario,
            metadados={'etapa_anterior_id': anterior.pk, 'etapa_nova_id': etapa.pk},
        )
        return oportunidade

    @staticmethod
    @tenant_atomic
    def registrar_nota(*, oportunidade, texto, usuario):
        texto = (texto or '').strip()
        if not texto:
            raise ValidationError('Escreva uma nota antes de salvar.')
        return AtividadeCRM.objects.create(
            oportunidade=oportunidade, tipo=AtividadeCRM.Tipo.NOTA,
            descricao=texto, autor=usuario,
        )
