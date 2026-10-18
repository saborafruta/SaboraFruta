import SwiftUI

struct CheckoutView: View {
    @Environment(AppModel.self) private var model
    @State private var state: CheckoutState?
    @State private var selectedClient: Client?
    @State private var clientQuery = ""
    @State private var clientResults: [Client] = []
    @State private var productQuery = ""
    @State private var productResults: [Product] = []
    @State private var quantity = "1"
    @State private var lines: [CheckoutLine] = []
    @State private var payment: PaymentOption?
    @State private var received = ""
    @State private var discount: Decimal = 0
    @State private var showDiscount = false
    @State private var showDiscountAuthorization = false
    @State private var discountAuthorization: String?
    @State private var clientSearchTask: Task<Void, Never>?
    @State private var productSearchTask: Task<Void, Never>?

    private var subtotal: Decimal { lines.reduce(0) { $0 + $1.total } }
    private var total: Decimal { max(0, subtotal - discount) }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                statusHeader
                clientSection
                productSection
                cartSection
                totalsSection
            }.padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Checkout de venda")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { SyncButton() } }
        .task { await loadState() }
        .sheet(isPresented: $showDiscount) {
            DiscountSheet(subtotal: subtotal, initialDiscount: discount, percentages: state?.discountPercentages ?? []) {
                discount = min($0, subtotal)
            }
        }
        .sheet(isPresented: $showDiscountAuthorization) {
            DiscountAuthorizationSheet { email, password in
                await authorizeDiscount(email: email, password: password)
            }
        }
    }

    private var statusHeader: some View {
        HStack {
            Label(state?.sessionOpen == true ? "Caixa aberto" : "Caixa fechado", systemImage: "hourglass")
                .font(.caption.bold())
                .foregroundStyle(state?.sessionOpen == true ? .green : .red)
            Spacer()
            Text(state?.cashRegisterName ?? "Venda rápida").font(.caption).foregroundStyle(.secondary)
        }
    }

    private var clientSection: some View {
        ITEDCard {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Cliente").font(.headline)
                    Text(selectedClient?.nome ?? "Cliente balcão").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                NavigationLink("Cadastro completo") { ClientRegistrationView() }.font(.caption.bold())
            }
            HStack(spacing: 10) {
                Button {
                    selectedClient = nil; clientQuery = ""; clientResults = []
                } label: { Label("Balcão", systemImage: "person") }
                    .buttonStyle(.bordered).buttonBorderShape(.capsule)
                TextField("Nome ou CPF/CNPJ", text: $clientQuery)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
                if !clientQuery.isEmpty {
                    Button { clientQuery = ""; clientResults = [] } label: { Image(systemName: "xmark.circle.fill") }
                }
            }.padding(.top, 10)
            if !clientResults.isEmpty {
                SearchResultPanel {
                    LazyVStack(spacing: 0) {
                        ForEach(clientResults) { client in
                            Button {
                                selectedClient = client; clientQuery = client.nome; clientResults = []
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(client.nome).foregroundStyle(.primary)
                                    Text([client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • "))
                                        .font(.caption).foregroundStyle(.secondary)
                                }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                            }
                            Divider()
                        }
                    }
                }.padding(.top, 8)
            }
            if discountRequiresAuthorization {
                Button { showDiscountAuthorization = true } label: {
                    Label(
                        discountAuthorization == nil ? "Autorizar desconto" : "Desconto autorizado",
                        systemImage: discountAuthorization == nil ? "lock.trianglebadge.exclamationmark" : "checkmark.shield"
                    )
                }
                .buttonStyle(.bordered)
                .tint(discountAuthorization == nil ? .orange : .green)
                .padding(.top, 6)
            }
        }
    }

    private var productSection: some View {
        ITEDCard {
            Text("Adicionar produto").font(.headline)
            Text("Bipe ou pesquise pelo código e nome").font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 8) {
                TextField("Qtd.", text: $quantity).keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder).frame(width: 74)
                TextField("Código ou produto", text: $productQuery)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: productQuery) { _, value in searchProducts(value) }
                Button { searchProducts(productQuery, immediate: true) } label: { Image(systemName: "magnifyingglass") }
                    .buttonStyle(.borderedProminent).tint(ITEDStyle.blue)
            }.padding(.top, 10)
            if !productResults.isEmpty {
                SearchResultPanel {
                    LazyVStack(spacing: 0) {
                        ForEach(productResults) { product in
                            Button { add(product) } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(product.descricao).foregroundStyle(.primary).lineLimit(2)
                                        Text("\(product.codigo) • Estoque: \(product.estoqueDisponivel) \(product.unidade)")
                                            .font(.caption)
                                            .foregroundStyle(product.podeVender ? Color.secondary : Color.red)
                                        if !product.estoques.isEmpty {
                                            Text(product.estoques.map { "\($0.depositoNome): \($0.disponivel)" }.joined(separator: "  •  "))
                                                .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                                        }
                                    }
                                    Spacer()
                                    Text(Decimal.parse(product.preco).currency).bold().foregroundStyle(ITEDStyle.orange)
                                }.padding(12)
                            }.disabled(!product.podeVender)
                            Divider()
                        }
                    }
                }.padding(.top, 8)
            }
        }
    }

    private var cartSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("Produtos da venda").font(.headline); Spacer(); Text("\(lines.count) produto(s)").font(.caption).foregroundStyle(.secondary) }
            if lines.isEmpty {
                ContentUnavailableView("Venda vazia", systemImage: "barcode.viewfinder", description: Text("Bipe o primeiro produto para começar."))
                    .frame(minHeight: 150).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
            } else {
                ForEach($lines) { $line in
                    ITEDCard {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading) {
                                Text(line.product.descricao).font(.subheadline.bold())
                                Text("\(Decimal.parse(line.product.preco).currency)/\(line.product.unidade)").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(role: .destructive) { lines.removeAll { $0.id == line.id } } label: { Image(systemName: "trash") }
                        }
                        HStack {
                            Button { line.quantity = max(0.001, line.quantity - 1) } label: { Image(systemName: "minus") }
                            TextField("Quantidade", value: $line.quantity, format: .number.precision(.fractionLength(0...3)))
                                .keyboardType(.decimalPad).multilineTextAlignment(.center).frame(width: 88)
                            Button { line.quantity += 1 } label: { Image(systemName: "plus") }
                            Spacer()
                            Text(line.total.currency).bold()
                        }.buttonStyle(.bordered).padding(.top, 6)
                    }
                }
            }
        }
    }

    private var totalsSection: some View {
        ITEDCard {
            HStack(spacing: 10) {
                Button { showDiscount = true } label: { Label("Desconto", systemImage: "tag") }.buttonStyle(.bordered)
                Menu {
                    ForEach(state?.payments ?? []) { option in Button(option.descricao) { payment = option } }
                } label: { Label(payment?.descricao ?? "Forma de pagamento", systemImage: "banknote") }
                    .buttonStyle(.bordered).lineLimit(1).minimumScaleFactor(0.75)
            }
            if let payment { Text("Selecionada: \(payment.descricao)").font(.caption).foregroundStyle(.secondary).padding(.top, 4) }
            TextField("Valor recebido", text: $received).keyboardType(.decimalPad).textFieldStyle(.roundedBorder).padding(.top, 8)
            Divider().padding(.vertical, 8)
            HStack { Text("TOTAL A PAGAR").font(.caption); Spacer(); Text(total.currency).font(.title.bold()) }
            Button { Task { await savePending() } } label: {
                Label("Salvar em pendência", systemImage: "clock").frame(maxWidth: .infinity)
            }.buttonStyle(.bordered).controlSize(.large).disabled(lines.isEmpty || (discountRequiresAuthorization && discountAuthorization == nil))
            Button { Task { await finalize() } } label: {
                Label("Finalizar venda", systemImage: "cart.badge.checkmark").frame(maxWidth: .infinity)
            }.itedPrimaryButton().disabled(lines.isEmpty || payment == nil || state?.sessionOpen != true || (discountRequiresAuthorization && discountAuthorization == nil))
        }
    }

    private func loadState() async {
        guard let session = model.session else { return }
        await model.perform {
            state = try await model.api.checkoutState(session: session)
            model.lastSync = Date()
        }
    }

    private func searchClients(_ query: String) {
        clientSearchTask?.cancel()
        guard query.trimmingCharacters(in: .whitespaces).count >= 2, let session = model.session else {
            clientResults = []; return
        }
        clientSearchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            do { clientResults = try await model.api.checkoutClients(session: session, query: query) }
            catch { if !Task.isCancelled { model.errorMessage = error.localizedDescription } }
        }
    }

    private func searchProducts(_ query: String, immediate: Bool = false) {
        productSearchTask?.cancel()
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty, let session = model.session else {
            productResults = []; return
        }
        productSearchTask = Task {
            if !immediate { try? await Task.sleep(for: .milliseconds(350)) }
            guard !Task.isCancelled else { return }
            do { productResults = try await model.api.checkoutProducts(session: session, query: query, clientID: selectedClient?.id) }
            catch { if !Task.isCancelled { model.errorMessage = error.localizedDescription } }
        }
    }

    private func add(_ product: Product) {
        guard product.podeVender else {
            model.errorMessage = "\(product.descricao): \(product.bloqueios.joined(separator: ", "))"
            return
        }
        let parsed = max(Decimal(string: quantity.replacingOccurrences(of: ",", with: ".")) ?? 1, 0.001)
        if let index = lines.firstIndex(where: { $0.id == product.id }) { lines[index].quantity += parsed }
        else { lines.append(CheckoutLine(product: product, quantity: parsed)) }
        productQuery = ""; productResults = []; quantity = "1"
    }

    private func savePending() async {
        guard let session = model.session else { return }
        await model.perform {
            let pending = try await model.api.saveCheckoutPending(
                session: session, client: selectedClient, lines: lines, payment: payment,
                discount: discount, received: max(Decimal.parse(received), total),
                discountAuthorization: discountAuthorization
            )
            model.successMessage = "Venda #\(pending.numeroVenda) salva em pendências."
            resetSale()
        }
    }

    private func finalize() async {
        guard let session = model.session, let payment else { return }
        await model.perform {
            let number = try await model.api.finalizeCheckout(
                session: session, client: selectedClient, lines: lines, payment: payment,
                discount: discount, received: max(Decimal.parse(received), total),
                discountAuthorization: discountAuthorization
            )
            model.successMessage = "Venda #\(number) finalizada. O checkout já está pronto para a próxima venda."
            resetSale()
        }
    }

    private func resetSale() {
        selectedClient = nil; clientQuery = ""; clientResults = []
        productQuery = ""; productResults = []; lines = []; payment = nil; received = ""; discount = 0; discountAuthorization = nil
    }

    private var discountRequiresAuthorization: Bool {
        guard discount > 0, let state else { return false }
        let percentageLimit = subtotal * state.maxDiscountPercent / 100
        if state.maxDiscountPercent > 0 && discount <= percentageLimit { return false }
        let excess = max(0, discount - percentageLimit)
        let roundedTotal = total == total.rounded(0)
        return !(state.maxRoundingDiscount > 0 && roundedTotal && excess <= state.maxRoundingDiscount)
    }

    private func authorizeDiscount(email: String, password: String) async -> Bool {
        guard let session = model.session else { return false }
        do {
            discountAuthorization = try await model.api.authorizeCheckoutDiscount(session: session, email: email, password: password)
            return true
        } catch {
            model.errorMessage = error.localizedDescription
            return false
        }
    }
}

private struct DiscountSheet: View {
    @Environment(\.dismiss) private var dismiss
    let subtotal: Decimal
    let initialDiscount: Decimal
    let percentages: [String]
    let apply: (Decimal) -> Void
    @State private var mode = 0
    @State private var value = ""

    var body: some View {
        NavigationStack {
            Form {
                Picker("Tipo", selection: $mode) { Text("Valor (R$)").tag(0); Text("Porcentagem (%)").tag(1) }
                    .pickerStyle(.segmented)
                Section("Descontos rápidos") {
                    ScrollView(.horizontal) {
                        HStack { ForEach(percentages, id: \.self) { percent in Button("\(percent)%") { mode = 1; value = percent } } }
                    }
                }
                TextField(mode == 0 ? "Valor do desconto" : "Percentual do desconto", text: $value).keyboardType(.decimalPad)
                LabeledContent("Desconto calculado", value: calculated.currency)
                LabeledContent("Total", value: max(0, subtotal - calculated).currency)
            }
            .navigationTitle("Desconto da venda")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Aplicar") { apply(calculated); dismiss() } }
            }
            .onAppear { value = initialDiscount.apiString }
        }.presentationDetents([.medium, .large])
    }

    private var calculated: Decimal {
        let input = Decimal.parse(value)
        return mode == 0 ? input : subtotal * input / 100
    }
}

private struct DiscountAuthorizationSheet: View {
    @Environment(\.dismiss) private var dismiss
    let authorize: (String, String) async -> Bool
    @State private var email = ""
    @State private var password = ""
    @State private var isAuthorizing = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Use um usuário com permissão PDV → Aprovar.").font(.subheadline).foregroundStyle(.secondary)
                    TextField("E-mail do autorizador", text: $email).keyboardType(.emailAddress).textInputAutocapitalization(.never)
                    SecureField("Senha", text: $password)
                }
                Button {
                    Task {
                        isAuthorizing = true
                        if await authorize(email, password) { dismiss() }
                        isAuthorizing = false
                    }
                } label: {
                    if isAuthorizing { ProgressView().frame(maxWidth: .infinity) }
                    else { Text("Autorizar desconto").frame(maxWidth: .infinity) }
                }
                .itedPrimaryButton()
                .disabled(email.isEmpty || password.isEmpty || isAuthorizing)
            }
            .navigationTitle("Autorização")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } } }
        }.presentationDetents([.medium])
    }
}

struct PendingSalesView: View {
    @Environment(AppModel.self) private var model
    @State private var sales: [CheckoutPendingSummary] = []
    var body: some View {
        List(sales) { sale in
            VStack(alignment: .leading, spacing: 4) {
                HStack { Text("Venda #\(sale.numeroVenda)").font(.headline); Spacer(); Text(Decimal.parse(sale.valorTotal).currency).bold() }
                Text(sale.clienteNome)
                Text("\(sale.quantidadeItens) item(ns) • \(sale.dataVenda)").font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 4)
        }
        .overlay { if sales.isEmpty && !model.isBusy { ContentUnavailableView("Nenhuma pendência", systemImage: "checkmark.circle") } }
        .navigationTitle("Vendas pendentes")
        .task {
            guard let session = model.session else { return }
            await model.perform { sales = try await model.api.checkoutPendings(session: session) }
        }
    }
}
