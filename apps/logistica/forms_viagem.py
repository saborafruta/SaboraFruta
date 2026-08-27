"""
O formulário da viagem.

O NÚMERO NÃO É PEDIDO. Ele é gerado pela filial: número repetido bate na
unique depois de a pessoa já ter preenchido tudo, e inventar numeração à mão
não é trabalho de quem monta carga. A tela mostra o número que saiu.

A FILIAL TAMBÉM NÃO É CAMPO. Ela vem da filial ativa da sessão — deixar
escolher abriria a porta para criar viagem na unidade errada, e o erro só
aparece quando a carga não bate com o estoque de lá.
"""
from django import forms

from apps.cadastros.models import Motorista, Transportadora, Veiculo
from apps.core.models import Usuario
from apps.logistica.models import Viagem

BASE_INPUT_CLASS = 'form-input w-full'


class ViagemForm(forms.ModelForm):

    class Meta:
        model = Viagem
        fields = [
            'data_saida', 'hora_saida', 'previsao_retorno',
            'motorista', 'motorista_nome', 'motorista_documento',
            'veiculo', 'veiculo_placa', 'veiculo_descricao',
            'transportadora', 'responsavel', 'vendedor',
            'uf_origem', 'uf_destino', 'rota', 'percurso_ufs',
            'status', 'observacao',
        ]
        widgets = {
            # `format` EXPLICITO: com pt-br o Django renderiza 27/08/2026, e
            # `<input type="date">` so' aceita ISO -- descarta o resto e mostra
            # o campo vazio, perdendo a data que ja' estava la'.
            'data_saida': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'previsao_retorno': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'hora_saida': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    PLACEHOLDERS = {
        'motorista_nome': 'Nome de quem dirige',
        'motorista_documento': 'CPF ou CNH',
        'veiculo_placa': 'ABC1D23',
        'veiculo_descricao': 'Marca e modelo',
        'rota': 'Zona Norte, litoral sul…',
        'percurso_ufs': 'RN, PB, PE',
        'uf_origem': 'RN',
        'uf_destino': 'RN',
        'observacao': 'O que mais precisa constar nesta viagem',
    }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial

        # Tudo escopado na filial: cadastro de outra unidade nao aparece aqui.
        self.fields['motorista'].queryset = Motorista.objects.for_filial(filial)
        self.fields['veiculo'].queryset = Veiculo.objects.for_filial(filial)
        self.fields['transportadora'].queryset = (
            Transportadora.objects.for_filial(filial).filter(ativo=True)
        )
        pessoas = Usuario.objects.filter(
            empresa=getattr(filial, 'empresa_id', None), ativo=True,
        ).order_by('nome')
        self.fields['responsavel'].queryset = pessoas
        self.fields['vendedor'].queryset = pessoas

        self.fields['motorista'].empty_label = '— escolher do cadastro —'
        self.fields['veiculo'].empty_label = '— escolher do cadastro —'
        self.fields['transportadora'].empty_label = '— veículo da casa —'
        self.fields['responsavel'].empty_label = '— sem responsável —'
        self.fields['vendedor'].empty_label = '— ninguém vende na rua —'

        for nome, campo in self.fields.items():
            campo.widget.attrs['class'] = BASE_INPUT_CLASS
            if nome in self.PLACEHOLDERS:
                campo.widget.attrs['placeholder'] = self.PLACEHOLDERS[nome]
            if nome not in ('data_saida',):
                campo.required = False

        for nome in ('uf_origem', 'uf_destino', 'veiculo_placa', 'percurso_ufs'):
            self.fields[nome].widget.attrs['style'] = 'text-transform:uppercase;'

        # O STATUS SO' ANDA PELO CICLO. Numa viagem que ja' existe, a lista
        # mostra apenas para onde ela pode ir de fato -- caso contrario alguem
        # marca "Finalizada" numa viagem que nunca saiu, e a prestacao de
        # contas deixa de significar coisa alguma.
        if self.instance and self.instance.pk:
            permitidos = [(self.instance.status, self.instance.get_status_display())]
            permitidos += self.instance.proximos_status()
            self.fields['status'].choices = permitidos
        else:
            self.fields['status'].choices = [
                (Viagem.Status.RASCUNHO, Viagem.Status.RASCUNHO.label),
                (Viagem.Status.EM_PREPARACAO, Viagem.Status.EM_PREPARACAO.label),
            ]
            self.fields['status'].initial = Viagem.Status.RASCUNHO

    # ── Limpezas ─────────────────────────────────────────────────────────

    def _maiuscula(self, campo):
        return (self.cleaned_data.get(campo) or '').strip().upper()

    def clean_uf_origem(self):
        return self._maiuscula('uf_origem')

    def clean_uf_destino(self):
        return self._maiuscula('uf_destino')

    def clean_veiculo_placa(self):
        return self._maiuscula('veiculo_placa')

    def clean_percurso_ufs(self):
        return self._maiuscula('percurso_ufs')

    def clean(self):
        dados = super().clean()
        saida, previsao = dados.get('data_saida'), dados.get('previsao_retorno')
        if saida and previsao and previsao < saida:
            self.add_error('previsao_retorno', 'A volta não pode ser antes da saída.')

        # QUEM LEVOU PRECISA ESTAR DITO. Sem motorista nem placa a viagem sai
        # sem identificar o veiculo, e o MDF-e nao tem o que declarar.
        if not any((
            dados.get('motorista'), (dados.get('motorista_nome') or '').strip(),
            dados.get('veiculo'), (dados.get('veiculo_placa') or '').strip(),
        )):
            self.add_error(
                'motorista_nome',
                'Informe o motorista ou o veículo — a viagem precisa dizer quem leva.',
            )
        return dados

    def save(self, commit=True):
        """
        O cadastro preenche o texto que ficou em branco.

        Guardar o texto junto é o que faz a viagem de dois anos atrás continuar
        dizendo quem levou, mesmo que o motorista saia da empresa e o cadastro
        mude. Quem digitou por cima teve um motivo — placa de reboque,
        motorista substituto — e não pode ser sobrescrito.
        """
        viagem = super().save(commit=False)
        if viagem.motorista_id and not (viagem.motorista_nome or '').strip():
            viagem.motorista_nome = viagem.motorista.nome
        if viagem.motorista_id and not (viagem.motorista_documento or '').strip():
            viagem.motorista_documento = viagem.motorista.cpf or ''
        if viagem.veiculo_id and not (viagem.veiculo_placa or '').strip():
            viagem.veiculo_placa = (viagem.veiculo.placa or '').upper()
        if viagem.veiculo_id and not (viagem.veiculo_descricao or '').strip():
            viagem.veiculo_descricao = viagem.veiculo.descricao or ' '.join(
                parte for parte in (viagem.veiculo.marca, viagem.veiculo.modelo) if parte
            )
        if not viagem.uf_origem and self.filial:
            viagem.uf_origem = (getattr(self.filial, 'uf', '') or '').upper()
        if commit:
            viagem.save()
        return viagem
