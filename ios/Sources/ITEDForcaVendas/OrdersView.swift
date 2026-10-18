import SwiftUI

struct OrdersView: View {
    @Environment(AppModel.self) private var model
    @State private var orders: [OrderSummary] = []

    var body: some View {
        List(orders) { order in
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("Pedido #\(order.numeroPedido)").font(.headline)
                    Spacer()
                    Text(order.statusDescricao).font(.caption.bold()).foregroundStyle(ITEDStyle.blue)
                }
                Text(order.clienteNome)
                HStack { Text(order.dataEmissao).font(.caption).foregroundStyle(.secondary); Spacer(); Text(Decimal.parse(order.valorTotal).currency).bold() }
            }.padding(.vertical, 5)
        }
        .overlay { if orders.isEmpty && !model.isBusy { ContentUnavailableView("Nenhum pedido", systemImage: "doc.text") } }
        .navigationTitle("Pedidos")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink { NewOrderView() } label: { Image(systemName: "plus") } } }
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        await model.perform { orders = try await model.api.orders(session: session); model.lastSync = Date() }
    }
}

private struct OrderStepHeader: View {
    let step: String
    let title: String
    var caption: String? = nil

    var body: some View {
        HStack(spacing: 10) {
            Text(step)
                .font(.caption.bold())
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(ITEDStyle.blue, in: Circle())
                .accessibilityHidden(true)
            Text(title)
                .font(.headline)
            Spacer(minLength: 8)
            if let caption {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Etapa \(step), \(title)\(caption.map { ", \($0)" } ?? "")")
    }
}

struct NewOrderView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var options: OrderOptions?
    @State private var client: Client?
    @State private var clientQuery = ""
    @State private var clientResults: [Client] = []
    @State private var category: ProductCategory?
    @State private var featuredOnly = false
    @State private var productQuery = ""
    @State private var products: [Product] = []
    @State private var lines: [OrderDraftLine] = []
    @State private var payment: PaymentOption?
    @State private var condition: PaymentCondition?
    @State private var priority: OrderPriority?
    @State private var hasDeliveryDate = true
    @State private var deliveryDate = Calendar.current.date(byAdding: .day, value: 5, to: Date()) ?? Date()
    @State private var notes = ""
    @State private var searchTask: Task<Void, Never>?
    @State private var removalCandidateID: Int?

    private var total: Decimal { lines.reduce(0) { $0 + $1.total } }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                clientSection
                productSection
                if !lines.isEmpty {
                    orderItemsSection
                    commercialSection
                    submitSection
                }
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Novo pedido")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadOptions() }
        .confirmationDialog(
            "Remover produto do pedido?",
            isPresented: removalConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Remover produto", role: .destructive) {
                if let removalCandidateID { lines.removeProduct(id: removalCandidateID) }
                removalCandidateID = nil
            }
            Button("Cancelar", role: .cancel) { removalCandidateID = nil }
        } message: {
            Text("O produto só será removido depois desta confirmação.")
        }
    }

    private var clientSection: some View {
        ITEDCard {
            OrderStepHeader(step: "1", title: "Cliente", caption: "Obrigatório")
            if let client {
                HStack(spacing: 12) {
                    Image(systemName: "person.crop.circle.fill")
                        .font(.title2)
                        .foregroundStyle(ITEDStyle.blue)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(client.nome).font(.subheadline.weight(.semibold)).lineLimit(2)
                        let detail = [client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                        Text("Crédito disponível: \(Decimal.parse(client.creditoDisponivel).currency)")
                            .font(.caption.weight(.semibold))
                        Text("Tabela: \(client.tabelaPreco)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    Button("Trocar") {
                        self.client = nil
                        clientQuery = ""
                        clientResults = []
                        products = []
                        category = nil
                        featuredOnly = false
                        lines.removeAll()
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                }
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
                .padding(.top, 10)
            } else {
                TextField("Buscar por nome, CPF ou CNPJ", text: $clientQuery)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
                    .padding(.top, 10)
                if !clientResults.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(clientResults.prefix(5)) { item in
                                Button {
                                    client = item
                                    clientQuery = ""
                                    clientResults = []
                                    products = []
                                    category = nil
                                    featuredOnly = false
                                    lines.removeAll()
                                } label: {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(item.nome).foregroundStyle(.primary)
                                        let detail = [item.cpfCnpj, item.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(12)
                                }
                                Divider()
                            }
                        }
                    }
                    .padding(.top, 8)
                }
            }
        }
    }

    private var productSection: some View {
        ITEDCard {
            OrderStepHeader(
                step: "2",
                title: "Produtos",
                caption: client == nil ? "Selecione o cliente" : "\(lines.count) no pedido"
            )
            if client == nil {
                Text("A busca de produtos será liberada depois que você selecionar o cliente.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.top, 10)
            } else {
                Menu {
                    Button("Produtos em destaque", systemImage: "star") {
                        category = nil
                        featuredOnly = true
                        Task { await searchProducts() }
                    }
                    Button("Todas as categorias", systemImage: "square.grid.2x2") {
                        category = nil
                        featuredOnly = false
                        Task { await searchProducts() }
                    }
                    ForEach(options?.categories ?? []) { item in
                        Button(item.nome) {
                            category = item
                            featuredOnly = false
                            Task { await searchProducts() }
                        }
                    }
                } label: {
                    HStack {
                        Label(selectedProductFilterLabel, systemImage: "list.bullet")
                            .lineLimit(1)
                        Spacer()
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.bordered)
                .padding(.top, 10)
                HStack(spacing: 8) {
                    TextField("Nome, código ou código de barras", text: $productQuery)
                        .textFieldStyle(.roundedBorder)
                        .submitLabel(.search)
                        .onSubmit { Task { await searchProducts() } }
                    Button { Task { await searchProducts() } } label: {
                        Image(systemName: "magnifyingglass")
                            .frame(minWidth: 28, minHeight: 28)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(ITEDStyle.orange)
                }
                .padding(.top, 10)
                if !products.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(products) { product in
                                Button { add(product) } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(product.descricao).foregroundStyle(.primary).lineLimit(2)
                                            Text("\(product.categoria) • Estoque: \(product.estoqueDisponivel) \(product.unidade)")
                                                .font(.caption)
                                                .foregroundStyle(product.podeVender ? Color.secondary : Color.red)
                                        }
                                        Spacer(minLength: 8)
                                        Text(Decimal.parse(product.preco).currency)
                                            .fontWeight(.semibold)
                                            .foregroundStyle(ITEDStyle.orange)
                                    }
                                    .padding(12)
                                }
                                .disabled(!product.podeVender)
                                Divider()
                            }
                        }
                    }
                    .padding(.top, 8)
                }
                Text("Escolha uma categoria ou digite para pesquisar.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 6)
            }
        }
    }

    private var orderItemsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrderStepHeader(step: "3", title: "Itens do pedido", caption: "\(lines.count) item(ns)")
            ForEach(lines) { line in
                ITEDCard {
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(line.product.descricao).font(.subheadline.bold()).lineLimit(2)
                            Text("\(Decimal.parse(line.product.preco).currency)/\(line.product.unidade)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 8)
                        Menu {
                            Button("Remover produto", systemImage: "trash", role: .destructive) {
                                removalCandidateID = line.id
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                                .frame(width: 36, height: 36)
                        }
                        .accessibilityLabel("Opções de \(line.product.descricao)")
                    }
                    HStack(spacing: 10) {
                        Button {
                            lines.adjustQuantity(for: line.id, by: -1)
                        } label: {
                            Image(systemName: "minus")
                                .font(.body.weight(.bold))
                                .frame(width: 28, height: 28)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.circle)
                        .accessibilityLabel("Diminuir quantidade de \(line.product.descricao)")

                        VStack(spacing: 3) {
                            Text("Quantidade")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            TextField(
                                "Quantidade",
                                value: quantityBinding(for: line.id),
                                format: .number.precision(.fractionLength(0...3))
                            )
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.center)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 76, maxWidth: 96)
                        }

                        Button {
                            lines.adjustQuantity(for: line.id, by: 1)
                        } label: {
                            Image(systemName: "plus")
                                .font(.body.weight(.bold))
                                .frame(width: 28, height: 28)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.circle)
                        .tint(ITEDStyle.orange)
                        .accessibilityLabel("Aumentar quantidade de \(line.product.descricao)")
                        Spacer(minLength: 0)
                    }
                    .padding(.top, 8)
                    HStack {
                        Text("Subtotal").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text(line.total.currency).fontWeight(.bold)
                    }
                    .padding(.top, 4)
                }
            }
        }
    }

    private var commercialSection: some View {
        ITEDCard {
            OrderStepHeader(step: "4", title: "Condições comerciais")
            Picker("Forma de pagamento", selection: $payment) {
                Text("Selecione").tag(nil as PaymentOption?)
                ForEach(options?.payments ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
            .padding(.top, 6)
            Picker("Condição", selection: $condition) {
                Text("Sem condição").tag(nil as PaymentCondition?)
                ForEach(options?.conditions ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
            Picker("Prioridade", selection: $priority) {
                ForEach(options?.priorities ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
            Divider().padding(.vertical, 4)
            Toggle("Informar entrega prevista", isOn: $hasDeliveryDate)
            if hasDeliveryDate {
                DatePicker("Data", selection: $deliveryDate, displayedComponents: .date)
                    .padding(.top, 4)
                HStack(spacing: 8) {
                    ForEach([5, 10, 15], id: \.self) { days in
                        Button("+\(days) dias") {
                            deliveryDate = Calendar.current.date(byAdding: .day, value: days, to: Date()) ?? Date()
                        }
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                    }
                }
            }
            TextField("Observações", text: $notes, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(3...6)
                .padding(.top, 6)
        }
    }

    private var submitSection: some View {
        ITEDCard {
            HStack {
                Text("TOTAL DO PEDIDO").font(.caption.weight(.semibold))
                Spacer()
                Text(total.currency).font(.title2.bold())
            }
            Button { Task { await submit() } } label: {
                Label("Enviar pedido", systemImage: "paperplane.fill")
                    .frame(maxWidth: .infinity, minHeight: 28)
            }
            .itedPrimaryButton()
            .disabled(client == nil || payment == nil || priority == nil || lines.isEmpty)
            .padding(.top, 6)
        }
    }

    private func loadOptions() async {
        await model.perform {
            options = try await model.orderOptions()
            priority = options?.priorities.first
        }
    }

    private func searchClients(_ query: String) {
        searchTask?.cancel()
        guard query.count >= 2 else { clientResults = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            do { clientResults = try await model.clients(query: query) }
            catch { model.errorMessage = error.localizedDescription }
        }
    }

    private func searchProducts() async {
        guard let client else {
            model.errorMessage = "Selecione o cliente antes de buscar produtos."; return
        }
        await model.perform {
            products = try await model.orderProducts(
                client: client, query: productQuery,
                categoryID: category?.id, featured: featuredOnly
            )
        }
    }

    private func add(_ product: Product) {
        guard product.podeVender else {
            model.errorMessage = "\(product.descricao): \(product.bloqueios.joined(separator: ", "))"; return
        }
        if let index = lines.firstIndex(where: { $0.id == product.id }) { lines[index].quantity += 1 }
        else { lines.append(OrderDraftLine(product: product, quantity: 1)) }
        productQuery = ""
        products = []
        category = nil
        featuredOnly = false
    }

    private var selectedProductFilterLabel: String {
        if featuredOnly { return "Produtos em destaque" }
        return category?.nome ?? "Categoria dos produtos"
    }

    private func quantityBinding(for productID: Int) -> Binding<Decimal> {
        Binding(
            get: { lines.first { $0.id == productID }?.quantity ?? 0.001 },
            set: { lines.updateQuantity(for: productID, to: $0) }
        )
    }

    private var removalConfirmationPresented: Binding<Bool> {
        Binding(
            get: { removalCandidateID != nil },
            set: { if !$0 { removalCandidateID = nil } }
        )
    }

    private func submit() async {
        guard let client, let payment, let priority else { return }
        await model.perform {
            let result = try await model.submitOrder(
                client: client, lines: lines, payment: payment, condition: condition,
                priority: priority, deliveryDate: hasDeliveryDate ? deliveryDate : nil, notes: notes
            )
            switch result {
            case let .sent(order): model.successMessage = "Pedido #\(order.numeroPedido) enviado ao iTED."
            case .queued: model.successMessage = "Pedido salvo offline. Ele será enviado na próxima sincronização."
            }
            dismiss()
        }
    }
}
