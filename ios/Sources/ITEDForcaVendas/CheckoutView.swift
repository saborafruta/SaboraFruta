import SwiftUI
import UIKit

private enum CheckoutField: Hashable {
    case client
    case quantity
    case product
    case received
    case lineQuantity(Int)
}

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
    @State private var showQuickClient = false
    @State private var discountAuthorization: String?
    @State private var clientSearchTask: Task<Void, Never>?
    @State private var productSearchTask: Task<Void, Never>?
    @FocusState private var focusedField: CheckoutField?
    @State private var pendingID: Int?
    private let pendingPaymentID: Int?

    private var subtotal: Decimal { lines.reduce(0) { $0 + $1.total } }
    private var total: Decimal { max(0, subtotal - discount) }
    private var change: Decimal { max(0, Decimal.parse(received) - total) }
    private var isCashPayment: Bool {
        payment?.tipo.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "dinheiro"
    }

    init(initialPending: CheckoutPendingDetail? = nil) {
        _pendingID = State(initialValue: initialPending?.id)
        pendingPaymentID = initialPending?.formaPagamentoId
        _selectedClient = State(initialValue: initialPending?.cliente)
        _lines = State(initialValue: initialPending?.itens.map {
            CheckoutLine(product: $0.produto, quantity: .parse($0.quantidade))
        } ?? [])
        _discount = State(initialValue: .parse(initialPending?.desconto ?? "0"))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                statusHeader
                if let pendingID {
                    Label("Venda pendente #\(pendingID). Revise o pagamento e finalize para dar baixa.", systemImage: "hourglass")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(ITEDStyle.blue)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                        .background(ITEDStyle.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
                }
                clientSection
                productSection
                cartSection
                totalsSection
                checkoutActions
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .dismissKeyboardOnBackgroundTap()
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Checkout de venda")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                NavigationLink { PendingSalesView() } label: { Image(systemName: "hourglass") }
                    .accessibilityLabel("Vendas pendentes")
                SyncButton()
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Concluir") { dismissKeyboard() }
            }
        }
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
        .sheet(isPresented: $showQuickClient) {
            QuickClientSheet { client in
                selectedClient = client
                clientQuery = ""
                clientResults = []
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
                    Text(selectedClient == nil ? "Cliente balcão ou identificado" : "Cliente selecionado")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Cadastro rápido") { showQuickClient = true }
                    .font(.caption.bold())
            }
            if let selectedClient {
                selectedClientRow(selectedClient)
                    .padding(.top, 10)
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { clientSelectionButton; clientSearchField }
                    VStack(spacing: 10) { clientSearchField; clientSelectionButton }
                }
                .padding(.top, 10)
            }
            if selectedClient == nil, !clientResults.isEmpty {
                SearchResultPanel {
                    LazyVStack(spacing: 0) {
                        ForEach(clientResults) { client in
                            Button {
                                selectedClient = client; clientQuery = ""; clientResults = []; focusedField = nil
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

    private func selectedClientRow(_ client: Client) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "person.crop.circle.fill")
                .font(.title2)
                .foregroundStyle(ITEDStyle.blue)
            VStack(alignment: .leading, spacing: 3) {
                Text(client.nome)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                let detail = [client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 8)
            Button("Trocar") {
                selectedClient = nil
                clientQuery = ""
                clientResults = []
                focusedField = .client
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private var clientSelectionButton: some View {
        Button {
            selectedClient = nil; clientQuery = ""; clientResults = []; dismissKeyboard()
        } label: {
            Label("Balcão", systemImage: "person")
                .lineLimit(1)
        }
        .buttonStyle(.bordered)
        .buttonBorderShape(.capsule)
    }

    private var clientSearchField: some View {
        HStack(spacing: 8) {
            TextField("Nome ou CPF/CNPJ", text: $clientQuery)
                .focused($focusedField, equals: .client)
                .textFieldStyle(.roundedBorder)
                .onChange(of: clientQuery) { _, value in
                    selectedClient = nil
                    searchClients(value)
                }
                .submitLabel(.search)
            if !clientQuery.isEmpty {
                Button {
                    clientQuery = ""; clientResults = []
                } label: { Image(systemName: "xmark.circle.fill") }
                    .accessibilityLabel("Limpar cliente")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var productSection: some View {
        ITEDCard {
            HStack(alignment: .center, spacing: 8) {
                quantityField
                productSearchField
            }
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

    private var quantityField: some View {
        TextField("Qtd.", text: $quantity)
            .focused($focusedField, equals: .quantity)
            .keyboardType(.decimalPad)
            .textFieldStyle(.roundedBorder)
            .frame(minWidth: 74, maxWidth: 92)
    }

    private var productSearchField: some View {
        HStack(spacing: 8) {
            TextField("Adicionar produto", text: $productQuery, prompt: Text("Adicionar produto: código ou nome"))
                .focused($focusedField, equals: .product)
                .textFieldStyle(.roundedBorder)
                .onChange(of: productQuery) { _, value in searchProducts(value) }
                .submitLabel(.search)
                .onSubmit { searchProducts(productQuery, immediate: true) }
            Button { searchProducts(productQuery, immediate: true) } label: {
                Image(systemName: "magnifyingglass")
                    .frame(minWidth: 28, minHeight: 28)
            }
            .buttonStyle(.borderedProminent)
            .tint(ITEDStyle.orange)
            .disabled(productQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .frame(maxWidth: .infinity)
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
                                .focused($focusedField, equals: .lineQuantity(line.id))
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
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { discountButton; paymentMenu }
                VStack(spacing: 10) { discountButton; paymentMenu }
            }
            if let payment { Text("Selecionada: \(payment.descricao)").font(.caption).foregroundStyle(.secondary).padding(.top, 4) }
            TextField("Valor recebido", text: $received)
                .focused($focusedField, equals: .received)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
                .padding(.top, 8)
            if isCashPayment, !received.isEmpty {
                LabeledContent("Troco", value: change.currency)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(change > 0 ? Color.green : Color.secondary)
                    .padding(.top, 4)
            }
        }
    }

    private var discountButton: some View {
        Button { showDiscount = true } label: {
            Label(discount > 0 ? "Desconto \(discount.currency)" : "Desconto", systemImage: "tag")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .disabled(state?.canApplyDiscount != true || subtotal <= 0)
    }

    private var paymentMenu: some View {
        Menu {
            ForEach(state?.payments ?? []) { option in
                Button(option.descricao) {
                    payment = option
                    let cashPayment = option.tipo
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .lowercased() == "dinheiro"
                    if !cashPayment || received.isEmpty { received = total.apiString }
                }
            }
        } label: {
            Label(payment?.descricao ?? "Forma de pagamento", systemImage: "banknote")
                .lineLimit(2)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
    }

    private var checkoutActions: some View {
        VStack(spacing: 8) {
            HStack {
                Text("TOTAL A PAGAR").font(.caption.weight(.semibold))
                Spacer()
                Text(total.currency).font(.title2.bold())
            }
            HStack(spacing: 10) {
                Button { Task { await savePending() } } label: {
                    Label("Salvar pendência", systemImage: "clock")
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(maxWidth: .infinity, minHeight: 28)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.roundedRectangle(radius: 14))
                .controlSize(.large)
                .frame(maxWidth: .infinity, minHeight: 52)
                .disabled(lines.isEmpty || pendingID != nil || (discountRequiresAuthorization && discountAuthorization == nil))
                Button { Task { await finalize() } } label: {
                    Label("Finalizar", systemImage: "cart.badge.checkmark")
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(maxWidth: .infinity, minHeight: 28)
                }
                .itedPrimaryButton()
                .frame(maxWidth: .infinity, minHeight: 52)
                .disabled(lines.isEmpty || payment == nil || state?.sessionOpen != true || (discountRequiresAuthorization && discountAuthorization == nil))
            }
        }
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: ITEDStyle.radius))
    }

    private func loadState() async {
        guard let session = model.session else { return }
        await model.perform {
            state = try await model.api.checkoutState(session: session)
            if let paymentID = pendingPaymentID {
                payment = state?.payments.first { $0.id == paymentID }
            }
            if pendingID != nil, received.isEmpty { received = total.apiString }
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
                pendingID: pendingID,
                discountAuthorization: discountAuthorization
            )
            model.successMessage = "Venda #\(number) finalizada. O checkout já está pronto para a próxima venda."
            resetSale()
        }
    }

    private func resetSale() {
        selectedClient = nil; clientQuery = ""; clientResults = []
        productQuery = ""; productResults = []; lines = []; payment = nil; received = ""; discount = 0; discountAuthorization = nil
        pendingID = nil
    }

    private func dismissKeyboard() {
        focusedField = nil
        UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder),
            to: nil,
            from: nil,
            for: nil
        )
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
    @FocusState private var valueIsFocused: Bool

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
                TextField(mode == 0 ? "Valor do desconto" : "Percentual do desconto", text: $value)
                    .focused($valueIsFocused)
                    .keyboardType(.decimalPad)
                LabeledContent("Desconto calculado", value: calculated.currency)
                LabeledContent("Total", value: max(0, subtotal - calculated).currency)
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Desconto da venda")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Aplicar") {
                        valueIsFocused = false
                        apply(calculated)
                        dismiss()
                    }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Concluir") {
                        valueIsFocused = false
                        UIApplication.shared.sendAction(
                            #selector(UIResponder.resignFirstResponder),
                            to: nil,
                            from: nil,
                            for: nil
                        )
                    }
                }
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

private struct QuickClientSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let onCreated: (Client) -> Void
    @State private var name = ""
    @State private var document = ""
    @State private var phone = ""
    @State private var isSaving = false
    @FocusState private var focusedField: Field?

    private enum Field: Hashable { case name, document, phone }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Cadastre somente os dados necessários para esta venda. O cadastro completo continua disponível na área Clientes.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Section("Cliente") {
                    TextField("Nome / Razão social", text: $name)
                        .focused($focusedField, equals: .name)
                        .textContentType(.name)
                    TextField("CPF/CNPJ (opcional)", text: $document)
                        .focused($focusedField, equals: .document)
                        .keyboardType(.numberPad)
                    TextField("Celular (opcional)", text: $phone)
                        .focused($focusedField, equals: .phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Cadastro rápido")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }.disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Cadastrar") { Task { await save() } }
                        .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Concluir") { focusedField = nil }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func save() async {
        guard let session = model.session else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            let client = try await model.api.createCheckoutClient(
                session: session,
                name: name,
                document: document,
                phone: phone
            )
            onCreated(client)
            dismiss()
        } catch {
            model.errorMessage = error.localizedDescription
        }
    }
}

struct PendingSalesView: View {
    @Environment(AppModel.self) private var model
    @State private var sales: [CheckoutPendingSummary] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        List {
            if let errorMessage {
                ContentUnavailableView {
                    Label("Não foi possível carregar", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(errorMessage)
                } actions: {
                    Button("Tentar novamente") { Task { await load() } }
                }
            } else {
                ForEach(sales) { sale in
                    NavigationLink {
                        PendingCheckoutLoader(pendingID: sale.id)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "hourglass")
                                .foregroundStyle(ITEDStyle.orange)
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Venda #\(sale.numeroVenda)").font(.headline)
                                Text(sale.clienteNome).lineLimit(1)
                                Text("\(sale.quantidadeItens) item(ns) • \(sale.dataVenda)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 8)
                            Text(Decimal.parse(sale.valorTotal).currency)
                                .font(.subheadline.bold())
                        }
                        .padding(.vertical, 6)
                    }
                }
            }
        }
        .overlay {
            if isLoading { ProgressView("Carregando pendências…") }
            else if sales.isEmpty && errorMessage == nil {
                ContentUnavailableView(
                    "Nenhuma venda pendente",
                    systemImage: "checkmark.circle",
                    description: Text("As vendas salvas no checkout aparecerão aqui para recebimento.")
                )
            }
        }
        .navigationTitle("Vendas pendentes")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await load() } } label: { Image(systemName: "arrow.clockwise") }
                    .disabled(isLoading)
            }
        }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        isLoading = true
        errorMessage = nil
        do { sales = try await model.api.checkoutPendings(session: session) }
        catch { errorMessage = error.localizedDescription }
        isLoading = false
    }
}

private struct PendingCheckoutLoader: View {
    @Environment(AppModel.self) private var model
    let pendingID: Int
    @State private var pending: CheckoutPendingDetail?
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let pending {
                CheckoutView(initialPending: pending)
            } else if isLoading {
                ProgressView("Abrindo venda…")
            } else {
                ContentUnavailableView {
                    Label("Não foi possível abrir", systemImage: "exclamationmark.triangle")
                } description: {
                    Text(errorMessage ?? "Tente novamente.")
                } actions: {
                    Button("Tentar novamente") { Task { await load() } }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        isLoading = true
        errorMessage = nil
        do { pending = try await model.api.checkoutPending(session: session, id: pendingID) }
        catch { errorMessage = error.localizedDescription }
        isLoading = false
    }
}
