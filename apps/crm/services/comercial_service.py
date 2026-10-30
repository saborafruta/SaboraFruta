"""Regras transacionais da Central Comercial."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.crm.models import AtividadeCRM, EtapaPipelineCRM, OportunidadeCRM, PipelineCRM
from apps.crm.services.carteira_service import CarteiraService


ETAPAS_PADRAO = (
    ('Novo contato', '#3b82f6', 10, EtapaPipelineCRM.Tipo.ABERTA),
    ('Qualificação', '#8b5cf6', 30, EtapaPipelineCRM.Tipo.ABERTA),
    ('Proposta', '#f59e0b', 60, EtapaPipelineCRM.Tipo.ABERTA),
    ('Ganho', '#22c55e', 100, EtapaPipelineCRM.Tipo.GANHA),
    ('Perdido', '#ef4444', 0, EtapaPipelineCRM.Tipo.PERDIDA),
)


class ComercialService:
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
                descricao='Jornada padrão de relacionamento e venda.',
            )
        if not pipeline.etapas.exists():
            EtapaPipelineCRM.objects.bulk_create([
                EtapaPipelineCRM(
                    pipeline=pipeline, nome=nome, cor=cor, ordem=ordem,
                    probabilidade=probabilidade, tipo=tipo,
                )
                for ordem, (nome, cor, probabilidade, tipo) in enumerate(ETAPAS_PADRAO, 1)
            ])
        return pipeline

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
