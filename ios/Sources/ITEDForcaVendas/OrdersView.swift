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
    var accent: Color = ITEDStyle.orderPurple

    var body: some View {
        HStack(spacing: 10) {
            Text(step)
                .font(.caption.bold())
                .foregroundStyle(.white)
                .frame(width: 30, height: 30)
                .background(accent, in: RoundedRectangle(cornerRadius: 10))
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

private struct OrderSectionCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            content
        }
        .padding(15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            Color(.secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: ITEDStyle.radius)
        )
        .overlay {
            RoundedRectangle(cornerRadius: ITEDStyle.radius)
                .stroke(Color.secondary.opacity(0.16), lineWidth: 1)
        }
    }
}

private struct OrderMenuLabel: View {
    let title: String
    let value: String
    let icon: String
    let accent: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.up.chevron.down")
                .font(.caption.weight(.semibold))
                .foregroundStyle(accent)
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .background(Color(.tertiarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.secondary.opacity(0.55), lineWidth: 1)
        }
    }
}

private struct OrderOutlinedGroup<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .padding(13)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.tertiarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Color.secondary.opacity(0.32), lineWidth: 1)
            }
    }
}

private struct StockBalanceChips: View {
    let product: Product

    var body: some View {
        if !product.estoques.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Estoque por depósito")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(product.estoques) { stock in
                            HStack(spacing: 4) {
                                if stock.origemPedido {
                                    Image(systemName: "checkmark")
                                        .font(.caption2.bold())
                                }
                                Text("\(stock.depositoNome)  \(stock.disponivel) \(product.unidade)")
                                    .font(.caption)
                            }
                            .foregroundStyle(stock.permiteVenda ? Color.primary : Color.secondary)
                            .padding(.horizontal, 9)
                            .frame(minHeight: 30)
                            .background(
                                stock.origemPedido
                                    ? ITEDStyle.orderPurple.opacity(0.26)
                                    : Color.secondary.opacity(0.13),
                                in: Capsule()
                            )
                        }
                    }
                }
            }
        }
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
        OrderSectionCard {
            OrderStepHeader(step: "1", title: "Cliente", caption: "Obrigatório")
            if let client {
                OrderOutlinedGroup {
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(client.nome)
                                .font(.headline)
                                .lineLimit(2)
                            let detail = [client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                            if !detail.isEmpty {
                                Text(detail)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
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
                        .tint(ITEDStyle.blue)
                    }
                    Divider()
                    Text("Crédito disponível: \(Decimal.parse(client.creditoDisponivel).currency)")
                        .font(.subheadline.weight(.semibold))
                    Text("Tabela: \(client.tabelaPreco)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                TextField("Buscar por nome, CPF ou CNPJ", text: $clientQuery)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
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
                }
            }
        }
    }

    private var productSection: some View {
        OrderSectionCard {
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
                    OrderMenuLabel(
                        title: "Categoria dos produtos",
                        value: selectedProductFilterLabel,
                        icon: "list.bullet",
                        accent: ITEDStyle.orderPurple
                    )
                }
                .buttonStyle(.plain)
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
                if !products.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(products) { product in
                                Button { add(product) } label: {
                                    HStack {
                                        ProductThumbnail(product: product)
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
                }
                Text("Escolha uma categoria ou digite para pesquisar.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var orderItemsSection: some View {
        OrderSectionCard {
            OrderStepHeader(step: "3", title: "Itens do pedido", caption: "\(lines.count) item(ns)")
            ForEach(lines) { line in
                OrderOutlinedGroup {
                    HStack(alignment: .top, spacing: 10) {
                        ProductThumbnail(product: line.product)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(line.product.descricao)
                                .font(.subheadline.bold())
                                .lineLimit(2)
                            Text("\(Decimal.parse(line.product.preco).currency)/\(line.product.unidade)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 6)
                        Button {
                            removalCandidateID = line.id
                        } label: {
                            Image(systemName: "trash")
                                .font(.subheadline.weight(.semibold))
                                .frame(width: 34, height: 34)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.circle)
                        .tint(.red)
                        .accessibilityLabel("Remover \(line.product.descricao)")
                    }
                    StockBalanceChips(product: line.product)
                    HStack(spacing: 7) {
                        Button {
                            lines.adjustQuantity(for: line.id, by: -1)
                        } label: {
                            Image(systemName: "minus")
                                .font(.caption.weight(.bold))
                                .frame(width: 24, height: 24)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.circle)
                        .accessibilityLabel("Diminuir quantidade de \(line.product.descricao)")
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Quantidade")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            HStack(spacing: 4) {
                                TextField(
                                    "Qtd.",
                                    value: quantityBinding(for: line.id),
                                    format: .number.precision(.fractionLength(0...3))
                                )
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.center)
                                .textFieldStyle(.plain)
                                .frame(minWidth: 44)
                                Text(line.product.unidade)
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal, 9)
                        .frame(minHeight: 48)
                        .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 9))
                        .overlay {
                            RoundedRectangle(cornerRadius: 9)
                                .stroke(Color.secondary.opacity(0.55), lineWidth: 1)
                        }
                        Button {
                            lines.adjustQuantity(for: line.id, by: 1)
                        } label: {
                            Image(systemName: "plus")
                                .font(.caption.weight(.bold))
                                .frame(width: 24, height: 24)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.circle)
                        .tint(ITEDStyle.orderPurple)
                        .accessibilityLabel("Aumentar quantidade de \(line.product.descricao)")
                        Spacer()
                        VStack(alignment: .trailing, spacing: 1) {
                            Text("Subtotal").font(.caption2).foregroundStyle(.secondary)
                            Text(line.total.currency).font(.subheadline.bold())
                        }
                    }
                }
            }
        }
    }

    private var commercialSection: some View {
        OrderSectionCard {
            OrderStepHeader(step: "4", title: "Condições comerciais")
            Menu {
                ForEach(options?.payments ?? []) { item in
                    Button(item.descricao) { payment = item }
                }
            } label: {
                OrderMenuLabel(
                    title: "Forma de pagamento",
                    value: payment?.descricao ?? "Selecione",
                    icon: "creditcard",
                    accent: ITEDStyle.orderPurple
                )
            }
            .buttonStyle(.plain)
            if !(options?.conditions.isEmpty ?? true) {
                Menu {
                    ForEach(options?.conditions ?? []) { item in
                        Button(item.descricao) { condition = item }
                    }
                } label: {
                    OrderMenuLabel(
                        title: "Condição de pagamento",
                        value: condition?.descricao ?? options?.conditions.first?.descricao ?? "Selecione",
                        icon: "calendar.badge.clock",
                        accent: ITEDStyle.orderPurple
                    )
                }
                .buttonStyle(.plain)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Prioridade")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(options?.priorities ?? []) { item in
                            Button {
                                priority = item
                            } label: {
                                Text(item.descricao)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(priority == item ? Color.white : Color.primary)
                                    .padding(.horizontal, 14)
                                    .frame(minHeight: 36)
                                    .background(
                                        priority == item ? ITEDStyle.orderPurple : Color.primary.opacity(0.07),
                                        in: Capsule()
                                    )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            VStack(alignment: .leading, spacing: 7) {
                DatePicker("Entrega prevista", selection: $deliveryDate, displayedComponents: .date)
                    .datePickerStyle(.compact)
                    .padding(.horizontal, 12)
                    .frame(maxWidth: .infinity, minHeight: 54)
                    .background(Color(.tertiarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.secondary.opacity(0.55), lineWidth: 1)
                    }
                Text("Atalhos de entrega")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    ForEach([5, 10, 15], id: \.self) { days in
                        Button("+\(days) dias") {
                            deliveryDate = Calendar.current.date(byAdding: .day, value: days, to: Date()) ?? Date()
                        }
                        .buttonStyle(.bordered)
                        .tint(ITEDStyle.orderPurple)
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
        OrderSectionCard {
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
            condition = options?.conditions.first
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
                priority: priority, deliveryDate: deliveryDate, notes: notes
            )
            switch result {
            case let .sent(order): model.successMessage = "Pedido #\(order.numeroPedido) enviado ao iTED."
            case .queued: model.successMessage = "Pedido salvo offline. Ele será enviado na próxima sincronização."
            }
            dismiss()
        }
    }
}

private struct ProductThumbnail: View {
    let product: Product

    var body: some View {
        if let value = product.fotoUrl?.trimmingCharacters(in: .whitespacesAndNewlines),
           !value.isEmpty,
           let url = URL(string: value) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFill()
                case .empty:
                    ZStack {
                        Color.secondary.opacity(0.08)
                        ProgressView().controlSize(.small)
                    }
                case .failure:
                    Color.secondary.opacity(0.08)
                @unknown default:
                    Color.secondary.opacity(0.08)
                }
            }
            .frame(width: 56, height: 56)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.secondary.opacity(0.18), lineWidth: 1)
            }
            .accessibilityLabel("Foto de \(product.descricao)")
        }
    }
}
