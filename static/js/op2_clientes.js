/* Fluxo compartilhado entre nova OP/orçamento e detalhe. Métodos usam o
 * `this` reativo do Alpine, nunca uma referência vinculada ao estado bruto. */
function configurarClientesOp2(estado, urls) {
  return Object.assign(estado, {
    clienteAtual: estado.clienteAtual || null,
    clientes: estado.clientes || [],
    clienteBuscaErro: '', clienteBuscaTimer: null,
    modalCliente: false, clienteEditandoId: '', clienteCarregando: false,
    salvando: false, erro: '', erros: {},
    cancelarBuscaCliente() {
      clearTimeout(this.clienteBuscaTimer);
      this.clienteBuscaSeq++;
      this.clienteResultados = [];
      this.clienteBuscando = false;
      this.clienteBuscaConcluida = false;
      this.clienteBuscaErro = '';
    },
    digitarCliente() {
      this.cancelarBuscaCliente();
      if (!this.adicionandoCliente) this.clienteId = '';
      this.clienteBuscaTimer = setTimeout(() => this.buscarClientes(), 250);
    },
    async respostaCliente(resposta) {
      if (resposta.status === 401 || resposta.redirected) {
        throw new Error('Sessão expirada ou acesso não permitido. Entre novamente para continuar.');
      }
      if (resposta.status === 403) throw new Error('Você não tem permissão para esta ação.');
      if (!(resposta.headers.get('content-type') || '').includes('application/json')) {
        throw new Error('Não foi possível acessar os clientes. Tente novamente.');
      }
      const dados = await resposta.json();
      if (!resposta.ok && !dados.erros) throw new Error(dados.erro || dados.error || 'Não foi possível acessar os clientes.');
      return dados;
    },
    async buscarClientes() {
      const q = (this.buscaCliente || '').trim();
      const seq = ++this.clienteBuscaSeq;
      this.clienteBuscaErro = '';
      this.clienteBuscaConcluida = false;
      if (q.length < 2) { this.clienteResultados = []; this.clienteBuscando = false; return; }
      this.clienteBuscando = true;
      try {
        const resposta = await fetch(urls.buscar + '?q=' + encodeURIComponent(q), {
          headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
        });
        const dados = await this.respostaCliente(resposta);
        if (seq === this.clienteBuscaSeq) this.clienteResultados = dados.clientes || [];
      } catch (erro) {
        if (seq === this.clienteBuscaSeq) this.clienteBuscaErro = erro.message;
      } finally {
        if (seq === this.clienteBuscaSeq) { this.clienteBuscando = false; this.clienteBuscaConcluida = true; }
      }
    },
    clientesVisiveis() {
      const q = (this.buscaCliente || '').trim();
      if (q.length < 2) return [];
      const normalizar = valor => String(valor || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
      const termos = normalizar(q).split(/\s+/);
      const locais = this.clientes.filter(cliente => {
        const texto = normalizar([cliente.texto, cliente.nome, cliente.razao_social, cliente.documento, cliente.contato, cliente.telefone, cliente.cidade].filter(Boolean).join(' '));
        return termos.every(termo => texto.includes(termo) || (/^[\d()./+\-]+$/.test(termo) && texto.replace(/\D/g, '').includes(termo.replace(/\D/g, ''))));
      });
      const vistos = new Set(this.clientesAdicionais.map(cliente => String(cliente.id)));
      if (this.adicionandoCliente) vistos.add(String(this.clienteId));
      // O servidor já filtrou inclusive por campos não exibidos no resultado.
      return [...this.clienteResultados, ...locais].filter(cliente => {
        const id = String(cliente.id);
        if (vistos.has(id)) return false;
        vistos.add(id); return true;
      }).slice(0, 20);
    },
    selecionarCliente(cliente) {
      this.cancelarBuscaCliente();
      if (this.adicionandoCliente) {
        if (String(cliente.id) !== String(this.clienteId) && !this.clientesAdicionais.some(c => String(c.id) === String(cliente.id))) {
          this.clientesAdicionais.push(cliente);
        }
        this.adicionandoCliente = false;
        this.buscaCliente = this.clienteAtual?.nome || '';
        return;
      }
      this.clienteAtual = cliente;
      this.clienteId = String(cliente.id);
      this.buscaCliente = cliente.nome;
      this.contatoNome = cliente.contato || '';
      this.contatoTelefone = cliente.telefone || '';
      this.clientesAdicionais = this.clientesAdicionais.filter(c => String(c.id) !== String(cliente.id));
    },
    adicionarCliente() {
      this.cancelarBuscaCliente();
      if (this.clienteId) this.adicionandoCliente = true;
      this.buscaCliente = '';
      this.$nextTick(() => this.$refs.buscaCliente.focus());
    },
    cancelarClienteAdicional() {
      this.cancelarBuscaCliente();
      this.adicionandoCliente = false;
      this.buscaCliente = this.clienteAtual?.nome || '';
    },
    removerClienteAdicional(id) {
      this.clientesAdicionais = this.clientesAdicionais.filter(c => String(c.id) !== String(id));
    },
    async abrirCadastroCliente(id = '') {
      this.cancelarBuscaCliente();
      this.clienteEditandoId = String(id || '');
      this.erro = ''; this.erros = {}; this.modalCliente = true;
      this.clienteCarregando = true;
      await this.$nextTick();
      this.$refs.novo.reset();
      try {
        if (id) {
          const dados = await this.respostaCliente(await fetch(urls.editar.replace('/0/', '/' + encodeURIComponent(id) + '/'), {
            headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
          }));
          for (const [nome, valor] of Object.entries(dados.campos)) {
            const campo = this.$refs.novo.elements.namedItem(nome);
            if (!campo) continue;
            if (campo.type === 'checkbox') campo.checked = Boolean(valor);
            else campo.value = valor ?? '';
          }
        }
      } catch (erro) { this.erro = erro.message; return; }
      this.clienteCarregando = false;
    },
    async salvarCliente() {
      if (this.salvando || this.clienteCarregando) return;
      // Capture antes de o Alpine desabilitar o fieldset durante o envio.
      const body = new FormData(this.$refs.novo);
      this.salvando = true; this.erro = ''; this.erros = {};
      try {
        const url = this.clienteEditandoId ? urls.editar.replace('/0/', '/' + encodeURIComponent(this.clienteEditandoId) + '/') : urls.criar;
        const dados = await this.respostaCliente(await fetch(url, {
          method: 'POST', headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
          body
        }));
        if (!dados.ok) {
          this.erros = Object.fromEntries(Object.entries(dados.erros || {}).map(([k, v]) => [k, v.join(' ')]));
          this.erro = dados.erro || ''; return;
        }
        const cliente = dados.cliente;
        this.clientes = [cliente, ...this.clientes.filter(c => String(c.id) !== String(cliente.id))];
        if (this.clienteEditandoId && String(cliente.id) !== String(this.clienteId)) {
          this.clientesAdicionais = this.clientesAdicionais.map(c => String(c.id) === String(cliente.id) ? cliente : c);
        } else {
          if (this.clienteEditandoId) this.adicionandoCliente = false;
          this.selecionarCliente(cliente);
        }
        this.modalCliente = false;
      } catch (erro) { this.erro = erro.message; }
      finally { this.salvando = false; }
    }
  });
}
