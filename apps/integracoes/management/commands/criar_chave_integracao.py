from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Empresa, Filial
from apps.integracoes.models import CredencialIntegracao, ESCOPOS_DISPONIVEIS


class Command(BaseCommand):
    help = 'Cria uma chave de API para um sistema externo. O segredo é exibido uma única vez.'

    def add_arguments(self, parser):
        parser.add_argument('--empresa-cnpj', required=True)
        parser.add_argument('--nome', required=True)
        parser.add_argument(
            '--escopos',
            default=','.join(ESCOPOS_DISPONIVEIS),
            help='Lista separada por vírgulas ou * para acesso total.',
        )
        parser.add_argument(
            '--filial-cnpj', action='append', default=[],
            help='Restringe a uma filial; pode ser informado mais de uma vez.',
        )

    def handle(self, *args, **options):
        try:
            empresa = Empresa.objects.using('default').get(
                cnpj=options['empresa_cnpj'], ativo=True,
            )
        except Empresa.DoesNotExist as exc:
            raise CommandError('Empresa ativa não encontrada.') from exc

        escopos = [item.strip() for item in options['escopos'].split(',') if item.strip()]
        invalidos = set(escopos) - set(ESCOPOS_DISPONIVEIS) - {'*'}
        if invalidos:
            raise CommandError(f'Escopos inválidos: {", ".join(sorted(invalidos))}.')

        filiais = Filial.objects.using('default').filter(
            empresa=empresa, ativo=True, cnpj__in=options['filial_cnpj'],
        )
        if options['filial_cnpj'] and filiais.count() != len(set(options['filial_cnpj'])):
            raise CommandError('Uma ou mais filiais não pertencem à empresa ou estão inativas.')

        try:
            credencial, token = CredencialIntegracao.criar(
                empresa=empresa,
                nome=options['nome'],
                escopos=escopos,
            )
        except Exception as exc:
            raise CommandError(f'Não foi possível criar a credencial: {exc}') from exc
        if options['filial_cnpj']:
            credencial.filiais.set(filiais)

        self.stdout.write(self.style.SUCCESS('Credencial criada.'))
        self.stdout.write(f'ID: {credencial.pk}')
        self.stdout.write(f'Empresa: {empresa.razao_social}')
        self.stdout.write(f'Prefixo: {credencial.prefixo}')
        self.stdout.write('CHAVE (copie agora; ela não poderá ser consultada depois):')
        self.stdout.write(token)
