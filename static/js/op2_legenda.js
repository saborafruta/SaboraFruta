// Salva em sequência: uma resposta lenta nunca sobrescreve o texto mais recente.
function op2LegendaAutomatica() {
  return {
    salvo: '', estado: '', salvando: false, timer: null,
    init() { this.salvo = this.$refs.texto.value; },
    destroy() { clearTimeout(this.timer); },
    agendar() {
      clearTimeout(this.timer);
      this.estado = 'pendente';
      this.timer = setTimeout(() => this.salvar(), 700);
    },
    avisarSaida(evento) {
      if (this.salvando || this.$refs.texto.value.trim() !== this.salvo.trim()) {
        evento.preventDefault();
        evento.returnValue = '';
      }
    },
    sincronizar(dados) {
      if (String(dados.id) === this.$refs.form.elements.visual_id.value &&
          !this.salvando && this.$refs.texto.value.trim() === this.salvo.trim()) {
        this.$refs.texto.value = this.salvo = dados.texto;
      }
    },
    async salvar() {
      clearTimeout(this.timer);
      if (this.salvando) return;
      const enviado = this.$refs.texto.value;
      if (enviado.trim() === this.salvo.trim()) {
        this.estado = '';
        return;
      }
      this.salvando = true;
      this.estado = 'salvando';
      const form = this.$refs.form;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const resposta = await fetch(form.action, {
          method: 'POST', credentials: 'same-origin', keepalive: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          signal: controller.signal,
          body: new URLSearchParams({
            acao: 'descricao_visual', visual_id: form.elements.visual_id.value,
            descricao: enviado, csrfmiddlewaretoken: form.elements.csrfmiddlewaretoken.value,
          }),
        });
        if (!resposta.ok || resposta.redirected) throw new Error('Falha ao salvar');
        const dados = await resposta.json();
        if (dados.ok !== true || typeof dados.descricao !== 'string') throw new Error('Resposta inválida');
        this.salvo = dados.descricao;
        if (this.$refs.texto.value === enviado) this.$refs.texto.value = this.salvo;
        this.estado = 'salvo';
        this.$dispatch('op2-legenda-salva', { id: form.elements.visual_id.value, texto: this.salvo });
      } catch (erro) {
        this.estado = 'erro';
      } finally {
        clearTimeout(timeout);
        this.salvando = false;
        // Se houve nova digitação durante o envio, persiste a última versão.
        if (this.$refs.texto.value !== enviado && this.$refs.texto.value.trim() !== this.salvo.trim()) {
          this.agendar();
        }
      }
    },
  };
}
