import SwiftUI

struct ClientsView: View {
    @Environment(AppModel.self) private var model
    @State private var query = ""
    @State private var clients: [Client] = []
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        List(clients) { client in
            VStack(alignment: .leading, spacing: 4) {
                HStack { Text(client.nome).font(.headline); if client.bloqueado { Image(systemName: "lock.fill").foregroundStyle(.red) } }
                Text([client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • "))
                    .font(.caption).foregroundStyle(.secondary)
                Text("Crédito: \(Decimal.parse(client.creditoDisponivel).currency) • \(client.tabelaPreco)")
                    .font(.caption2).foregroundStyle(.secondary)
            }.padding(.vertical, 3)
        }
        .searchable(text: $query, prompt: "Nome, CPF/CNPJ ou celular")
        .onChange(of: query) { _, value in scheduleSearch(value) }
        .overlay { if clients.isEmpty && !model.isBusy { ContentUnavailableView("Busque um cliente", systemImage: "person.text.rectangle") } }
        .navigationTitle("Clientes")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink { ClientRegistrationView() } label: { Image(systemName: "person.badge.plus") } } }
        .task { await search("") }
    }

    private func scheduleSearch(_ text: String) {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            await search(text)
        }
    }

    private func search(_ text: String) async {
        guard let session = model.session else { return }
        do { clients = try await model.api.clients(session: session, query: text) }
        catch { model.errorMessage = error.localizedDescription }
    }
}

struct ClientRegistrationView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var form = ClientRegistration()
    @State private var lastLookedUpCep = ""

    var body: some View {
        Form {
            Section("Tipo de pessoa") {
                Picker("Pessoa", selection: $form.tipoPessoa) {
                    Text("Física").tag("F")
                    Text("Jurídica").tag("J")
                }.pickerStyle(.segmented)
            }

            if form.tipoPessoa == "J" {
                Section("Consulta da empresa") {
                    HStack {
                        TextField("CNPJ", text: $form.cpfCnpj).keyboardType(.numberPad)
                        Button("Consultar") { Task { await lookupCompany() } }
                            .disabled(digits(form.cpfCnpj).count != 14)
                    }
                }
            }

            Section("Identificação") {
                TextField(form.tipoPessoa == "J" ? "Razão social" : "Nome completo", text: $form.razaoSocial)
                TextField("Nome fantasia", text: $form.nomeFantasia)
                if form.tipoPessoa == "F" { TextField("CPF", text: $form.cpfCnpj).keyboardType(.numberPad) }
                TextField(form.tipoPessoa == "J" ? "RG/IE" : "RG", text: $form.rgIe)
                if form.tipoPessoa == "J" { TextField("Inscrição estadual", text: $form.inscricaoEstadual) }
            }

            Section("Endereço") {
                TextField("CEP", text: $form.cep).keyboardType(.numberPad)
                    .onChange(of: form.cep) { _, value in
                        let clean = digits(value)
                        if clean.count == 8 && clean != lastLookedUpCep { Task { await lookupCep(clean) } }
                    }
                TextField("Endereço", text: $form.endereco)
                TextField("Número", text: $form.numero)
                TextField("Complemento", text: $form.complemento)
                TextField("Ponto de referência", text: $form.pontoReferencia)
                TextField("Bairro", text: $form.bairro)
                HStack { TextField("Cidade", text: $form.cidade); TextField("UF", text: $form.uf).frame(width: 60).textInputAutocapitalization(.characters) }
            }

            Section("Contato") {
                TextField("Celular", text: $form.celular).keyboardType(.phonePad)
                TextField("Telefone", text: $form.telefone).keyboardType(.phonePad)
                TextField("E-mail", text: $form.email).keyboardType(.emailAddress).textInputAutocapitalization(.never)
                TextField("Contato", text: $form.contatoNome)
            }

            Section("Comercial") {
                TextField("Limite de crédito", text: $form.limiteCredito).keyboardType(.decimalPad)
                Stepper("Prazo: \(form.prazoPagamentoDias) dia(s)", value: $form.prazoPagamentoDias, in: 0...3650)
                Toggle("Consumidor final", isOn: $form.consumidorFinal)
                Toggle("Contribuinte de ICMS", isOn: $form.contribuinteIcms)
                Toggle("Optante do Simples", isOn: $form.optanteSimples)
                TextField("Observações", text: $form.observacao, axis: .vertical).lineLimit(3...6)
            }

            Button { Task { await save() } } label: { Text("Salvar cliente").frame(maxWidth: .infinity) }
                .itedPrimaryButton().disabled(form.razaoSocial.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .navigationTitle("Novo cliente")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func lookupCompany() async {
        guard let session = model.session else { return }
        await model.perform {
            let company = try await model.api.lookupCompany(session: session, cnpj: digits(form.cpfCnpj))
            form.cpfCnpj = company.cnpj
            form.razaoSocial = company.razaoSocial
            form.nomeFantasia = company.nomeFantasia
            form.inscricaoEstadual = company.inscricaoEstadual
            form.rgIe = company.inscricaoEstadual
            form.endereco = company.endereco
            form.numero = company.numero
            form.complemento = company.complemento
            form.bairro = company.bairro
            form.cidade = company.cidade
            form.uf = company.uf
            form.cep = company.cep
            form.codigoMunicipioIbge = company.codigoMunicipioIbge
            form.telefone = company.telefone
            form.email = company.email
            lastLookedUpCep = digits(company.cep)
        }
    }

    private func lookupCep(_ cep: String) async {
        guard let session = model.session else { return }
        lastLookedUpCep = cep
        await model.perform {
            let address = try await model.api.lookupCep(session: session, cep: cep)
            form.cep = address.cep
            form.endereco = address.endereco
            form.complemento = address.complemento
            form.bairro = address.bairro
            form.cidade = address.cidade
            form.uf = address.uf
            form.codigoMunicipioIbge = address.codigoMunicipioIbge
        }
    }

    private func save() async {
        guard let session = model.session else { return }
        await model.perform {
            _ = try await model.api.createClient(session: session, registration: form)
            model.successMessage = "Cliente cadastrado e disponível para vendas."
            dismiss()
        }
    }

    private func digits(_ value: String) -> String { value.filter(\.isNumber) }
}
