"""
Cadastro rápido de cliente, de dentro da confecção.

O FORMULÁRIO COMPLETO CONTINUA SENDO O DO ERP. Este aqui tem só o que o
comercial precisa para começar um pedido: quem é, o documento e como falar
com a pessoa. Endereço, tabela de preço, limite de crédito e o resto são
preenchidos no cadastro geral, quando fizer falta.

A ESCOLHA É DELIBERADA. Um formulário com trinta campos no meio da tela de
pedidos faz o vendedor desistir e digitar "CLIENTE NOVO" no lugar do nome —
e aí o cadastro vira lixo. Poucos campos agora, completos depois, é o que
mantém a base limpa.

GRAVAR É COM O SERVIÇO DO ERP (`ClienteService.criar`), não com o `save()`
do form: é ele que barra CPF/CNPJ repetido e replica o cadastro para as
outras filiais. Duplicar essa regra aqui seria criar a segunda verdade sobre
quem é cliente.
"""
from django import forms

from apps.cadastros.models import Cliente
from apps.core.constants.choices import TipoPessoa


class ClienteRapidoForm(forms.ModelForm):
    """O mínimo para o cliente existir e o pedido começar."""

    # Declarado à parte por causa do `max_length=14` do modelo: o campo
    # guarda só dígitos, mas quem digita cola "12.640.991/0001-56", que tem
    # 18 caracteres. Sem afrouxar aqui, o formulário recusaria o valor ANTES
    # de o `clean` ter chance de tirar a pontuação -- e o erro que aparece
    # ("no máximo 14 caracteres") não faz sentido nenhum para o usuário.
    cpf_cnpj = forms.CharField(
        max_length=18, required=False, label='CPF / CNPJ',
        widget=forms.TextInput(attrs={'maxlength': '18'}),
    )

    class Meta:
        model = Cliente
        fields = [
            'tipo_pessoa', 'razao_social', 'cpf_cnpj',
            'inscricao_estadual', 'contribuinte_icms',
            'contato_nome', 'celular', 'telefone', 'email', 'cidade', 'uf',
        ]
        labels = {
            'razao_social': 'Nome do cliente / Razão social',
            'cpf_cnpj': 'CPF / CNPJ',
            'inscricao_estadual': 'Inscrição estadual',
            'contribuinte_icms': 'Contribuinte de ICMS',
            'celular': 'WhatsApp',
            'contato_nome': 'Contato',
        }
        help_texts = {
            # A tela do pedido manda o link e o PDF por aqui: sem número, o
            # botão de WhatsApp fica pedindo para digitar toda vez.
            'celular': 'É por aqui que o pedido é enviado ao cliente.',
            'inscricao_estadual': 'Deixe em branco se for isento ou não contribuinte.',
            'contribuinte_icms': 'Marque só quem tem inscrição estadual ativa.',
            # Sem este campo, todo cliente criado por aqui nascia sem
            # contato -- e o pedido puxava o telefone do cadastro sem saber
            # com quem falar, que é a metade que importa numa confecção.
            'contato_nome': 'Quem acompanha o pedido do lado do cliente.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['tipo_pessoa'].choices = TipoPessoa.choices

        # NENHUM CAMPO OBRIGATÓRIO AQUI. Este formulário existe para o
        # cliente novo entrar no meio de um pedido, com o vendedor no
        # telefone -- e campo obrigatório nessa hora é o que faz a pessoa
        # desistir e digitar o nome do cliente na observação. O que
        # faltar é completado depois, no cadastro geral.
        #
        # A ÚNICA COISA COBRADA é não gravar um cliente COMPLETAMENTE
        # vazio (ver `clean`). Não é campo obrigatório: é a diferença
        # entre um cadastro incompleto e um registro que não é ninguém.
        for campo in self.fields.values():
            campo.required = False

        for nome, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            classes = campo.widget.attrs.get('class', '')
            campo.widget.attrs['class'] = (classes + ' form-input w-full').strip()

        self.fields['cpf_cnpj'].widget.attrs['placeholder'] = 'Só números'
        self.fields['celular'].widget.attrs['placeholder'] = '(84) 99999-0000'
        self.fields['razao_social'].widget.attrs['placeholder'] = (
            'Nome da pessoa ou da empresa'
        )

    def clean_cpf_cnpj(self):
        """
        Guarda só dígitos.

        O cadastro geral, o fiscal e a busca comparam sem pontuação — deixar
        o que o usuário digitou faria o mesmo CNPJ entrar duas vezes, uma
        com pontos e outra sem.
        """
        bruto = self.cleaned_data.get('cpf_cnpj') or ''
        digitos = ''.join(c for c in bruto if c.isdigit())

        # EM BRANCO PASSA: o documento deixou de ser obrigatório aqui.
        # PREENCHIDO continua conferido -- documento pela metade não
        # ajuda ninguém e volta como rejeição na primeira nota fiscal.
        if not digitos:
            return ''
        if len(digitos) not in (11, 14):
            raise forms.ValidationError(
                'Informe um CPF (11 dígitos) ou CNPJ (14 dígitos), '
                'ou deixe em branco para completar depois.'
            )
        return digitos

    def clean(self):
        dados = super().clean()
        # Coerência com a regra da NF-e: contribuinte sem inscrição estadual
        # numérica volta da SEFAZ como rejeição, e o lugar de descobrir isso
        # é aqui, não na emissão.
        ie = ''.join(c for c in (dados.get('inscricao_estadual') or '') if c.isdigit())
        if dados.get('contribuinte_icms') and not ie:
            self.add_error(
                'inscricao_estadual',
                'Contribuinte de ICMS precisa de inscrição estadual — ou '
                'desmarque a opção acima.',
            )

        # A ÚNICA TRAVA que sobrou. Cliente sem nome E sem documento não
        # é um cadastro incompleto: é uma linha que não é ninguém, que
        # aparece em branco na carteira, no pedido e na nota -- e que
        # ninguém consegue nem procurar depois para corrigir.
        if not (dados.get('razao_social') or '').strip() and not dados.get('cpf_cnpj'):
            raise forms.ValidationError(
                'Informe ao menos o nome ou o CPF/CNPJ do cliente. '
                'O resto pode ficar para depois.'
            )
        return dados
