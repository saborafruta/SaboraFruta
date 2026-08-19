"""
Cria os sete perfis da confecção numa empresa.

Idempotente: rodar duas vezes não duplica nem reescreve o que alguém
ajustou à mão depois. Sem `--forcar`, perfil que já existe é deixado em paz
— o ajuste manual do cliente vale mais que o padrão da fábrica.

Não roda sozinho em migration porque criar perfil é decisão de quem
administra a empresa, não efeito colateral de um deploy: cada empresa tem a
sua estrutura, e algumas já montaram perfis próprios com outros nomes.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Empresa
from apps.core.models.usuario import Permissao, PerfilAcesso
from apps.moda.permissoes import PERFIS, TODAS, permissoes_do_perfil


class Command(BaseCommand):
    help = 'Cria os perfis Comercial, PCP, Corte, Produção, Qualidade, Expedição e Gestão.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--empresa', type=int, required=True,
            help='Id da empresa que vai receber os perfis.',
        )
        parser.add_argument(
            '--perfil', action='append', default=None,
            help='Cria só este perfil (pode repetir). Sem isto, cria os sete.',
        )
        parser.add_argument(
            '--forcar', action='store_true',
            help='Reescreve as permissões de perfil que já existe.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Mostra o que seria criado, sem gravar.',
        )

    def handle(self, *args, **opcoes):
        try:
            empresa = Empresa.objects.get(pk=opcoes['empresa'])
        except Empresa.DoesNotExist:
            raise CommandError(f'Empresa {opcoes["empresa"]} não existe.')

        nomes = opcoes['perfil'] or list(PERFIS)
        desconhecidos = [n for n in nomes if n not in PERFIS]
        if desconhecidos:
            raise CommandError(
                f'Perfil desconhecido: {", ".join(desconhecidos)}. '
                f'Os disponíveis são: {", ".join(PERFIS)}.'
            )

        for nome in nomes:
            self._criar(empresa, nome, opcoes['forcar'], opcoes['dry_run'])

    @transaction.atomic
    def _criar(self, empresa, nome, forcar, simular):
        permissoes = permissoes_do_perfil(nome)
        existente = PerfilAcesso.objects.filter(empresa=empresa, nome=nome).first()

        if existente and not forcar:
            self.stdout.write(
                f'{nome}: já existe, mantido como está (use --forcar para reescrever)'
            )
            return

        if simular:
            self.stdout.write(self.style.SUCCESS(f'{nome}:'))
            for modulo, acoes in sorted(permissoes.items()):
                self.stdout.write(f'    {modulo}: {", ".join(acoes)}')
            return

        perfil = existente or PerfilAcesso.objects.create(
            empresa=empresa, nome=nome,
            descricao=PERFIS[nome]['descricao'],
        )
        if existente:
            perfil.descricao = PERFIS[nome]['descricao']
            perfil.save(update_fields=['descricao'])
            # Só as permissões que ESTE comando governa. Apagar todas
            # removeria um módulo que o cliente concedeu à mão e que não faz
            # parte do padrão.
            perfil.permissoes.filter(modulo__in=permissoes).delete()

        Permissao.objects.bulk_create([
            Permissao(
                perfil=perfil, modulo=modulo,
                **{f'pode_{acao}': acao in acoes for acao in TODAS},
            )
            for modulo, acoes in permissoes.items()
        ])
        verbo = 'atualizado' if existente else 'criado'
        self.stdout.write(self.style.SUCCESS(
            f'{nome}: {verbo} com {len(permissoes)} módulos'
        ))
