"""
Provisiona uma instalação nova: Empresa + Filial matriz + perfil
Administrador + super admin.

Diferente do `seed`, que é demonstração e usa senha fixa (admin123): este
comando é para conta real. Por isso a senha NÃO é argumento de linha de
comando -- argumento aparece na lista de processos do servidor e no
histórico do shell. Ela vem da variável de ambiente ADMIN_SENHA, que no
Railway é uma variável do serviço, e nunca é impressa na saída.

Uso (local):
    ADMIN_SENHA='...' python manage.py criar_empresa_admin \\
        --cnpj 12345678000199 \\
        --razao-social "MINHA EMPRESA LTDA" \\
        --nome-fantasia "Minha Empresa" \\
        --cidade Natal --uf RN \\
        --admin-email admin@minhaempresa.com.br \\
        --admin-nome "Nome do Admin"

No Railway: defina ADMIN_SENHA nas variáveis do serviço e rode o mesmo
comando pelo terminal do serviço (ou `railway run`).

Reexecutar é seguro: nada é duplicado e a senha de um admin que já existe
não é sobrescrita (a menos que --resetar-senha seja passado).
"""
import os
import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

VAR_SENHA = 'ADMIN_SENHA'


class Command(BaseCommand):
    help = 'Cria Empresa + Filial matriz + super admin de uma instalação nova.'

    def add_arguments(self, parser):
        parser.add_argument('--cnpj', required=True,
                            help='CNPJ da empresa (só dígitos ou formatado).')
        parser.add_argument('--razao-social', required=True)
        parser.add_argument('--nome-fantasia', default='')
        parser.add_argument('--uf', required=True, help='Sigla do estado, ex.: RN')
        parser.add_argument('--cidade', default='')
        parser.add_argument('--admin-email', required=True)
        parser.add_argument('--admin-nome', required=True)
        parser.add_argument('--cnpj-filial', default='',
                            help='CNPJ da matriz, se diferente do da empresa.')
        parser.add_argument('--resetar-senha', action='store_true',
                            help='Troca a senha de um admin que já existe.')

    @transaction.atomic
    def handle(self, *args, **op):
        senha = os.environ.get(VAR_SENHA, '')
        if not senha:
            raise CommandError(
                f'Defina a variável de ambiente {VAR_SENHA} com a senha do admin.\n'
                f'Ela não é argumento de propósito: argumento de linha de comando '
                f'fica visível na lista de processos e no histórico do shell.'
            )

        cnpj = self._so_digitos(op['cnpj'])
        cnpj_filial = self._so_digitos(op['cnpj_filial']) or cnpj
        uf = op['uf'].strip().upper()
        email = op['admin_email'].strip().lower()

        if len(cnpj) != 14:
            raise CommandError(f'CNPJ deve ter 14 dígitos; recebi {len(cnpj)}.')
        if len(uf) != 2:
            raise CommandError('UF deve ter 2 letras, ex.: RN.')

        # Valida a senha com as mesmas regras do resto do sistema, antes de
        # criar qualquer coisa -- falhar depois de criar a empresa deixaria
        # o banco pela metade (a transação cobre, mas o erro fica confuso).
        try:
            validate_password(senha)
        except ValidationError as exc:
            raise CommandError('Senha recusada:\n  - ' + '\n  - '.join(exc.messages))

        empresa, criou_empresa = Empresa.objects.get_or_create(
            cnpj=cnpj,
            defaults={
                'razao_social': op['razao_social'],
                'nome_fantasia': op['nome_fantasia'] or op['razao_social'],
                'cidade': op['cidade'],
                'uf': uf,
            },
        )
        self.stdout.write(f'  Empresa: {empresa} ({"criada" if criou_empresa else "já existia"})')

        matriz, criou_matriz = Filial.objects.get_or_create(
            cnpj=cnpj_filial,
            defaults={
                'empresa': empresa,
                'razao_social': empresa.razao_social,
                'nome_fantasia': empresa.nome_fantasia or empresa.razao_social,
                'is_matriz': True,
                'cidade': op['cidade'],
                'uf': uf,
            },
        )
        self.stdout.write(f'  Filial matriz: {matriz} ({"criada" if criou_matriz else "já existia"})')

        # is_admin=True já libera tudo em Usuario.tem_permissao, então não é
        # preciso criar uma linha de Permissao por módulo aqui.
        perfil, criou_perfil = PerfilAcesso.objects.get_or_create(
            empresa=empresa, nome='Administrador',
            defaults={'is_admin': True, 'descricao': 'Acesso total ao sistema.'},
        )
        self.stdout.write(f'  Perfil: {perfil.nome} ({"criado" if criou_perfil else "já existia"})')

        usuario = Usuario.objects.filter(email=email).first()
        if usuario is None:
            Usuario.objects.create_superuser(
                email=email, nome=op['admin_nome'], password=senha,
                empresa=empresa, filial=matriz, perfil=perfil,
            )
            self.stdout.write(self.style.SUCCESS(f'  Super admin criado: {email}'))
        elif op['resetar_senha']:
            usuario.set_password(senha)
            usuario.is_superuser = True
            usuario.is_staff = True
            usuario.ativo = True
            usuario.save()
            self.stdout.write(self.style.SUCCESS(f'  Senha redefinida para: {email}'))
        else:
            self.stdout.write(
                f'  Usuário {email} já existe — senha mantida. '
                f'Use --resetar-senha para trocá-la.'
            )

        # A senha nunca é impressa: a saída deste comando costuma ir para o
        # log do deploy, que fica guardado.
        self.stdout.write(self.style.SUCCESS('\n✓ Pronto. Acesse /auth/login/ com o e-mail acima.'))

    @staticmethod
    def _so_digitos(valor: str) -> str:
        return re.sub(r'\D', '', valor or '')
