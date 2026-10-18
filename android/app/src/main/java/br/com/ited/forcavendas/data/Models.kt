package br.com.ited.forcavendas.data

data class AuthTokens(
    val access: String,
    val refresh: String,
)

data class WorkContext(
    val tenantAlias: String,
    val empresaId: Long,
    val empresaNome: String,
    val filialId: Long,
    val filialNome: String,
    val filialCnpj: String,
    val podeCriarPedido: Boolean,
    val podeSeparar: Boolean,
    val podeCheckout: Boolean = false,
)

data class UserSession(
    val tokens: AuthTokens,
    val usuarioNome: String,
    val usuarioEmail: String,
    val context: WorkContext,
)

data class LoginResult(
    val tokens: AuthTokens,
    val usuarioNome: String,
    val usuarioEmail: String,
    val contexts: List<WorkContext>,
)

data class Client(
    val id: Long,
    val nome: String,
    val documento: String,
    val cidadeUf: String,
    val creditoDisponivel: String,
    val tabelaPreco: String,
    val tabelaPrecoId: Long,
    val prazoPagamentoDias: Int,
    val bloqueado: Boolean,
    val motivoBloqueio: String,
)

data class ClientRegistration(
    val personType: String = "F",
    val customerType: String = "varejo",
    val legalName: String = "",
    val tradeName: String = "",
    val document: String = "",
    val identity: String = "",
    val municipalRegistration: String = "",
    val stateRegistration: String = "",
    val birthDate: String = "",
    val gender: String = "",
    val phone: String = "",
    val mobile: String = "",
    val email: String = "",
    val invoiceEmail: String = "",
    val contactName: String = "",
    val postalCode: String = "",
    val street: String = "",
    val number: String = "",
    val complement: String = "",
    val referencePoint: String = "",
    val neighborhood: String = "",
    val city: String = "",
    val state: String = "",
    val cityCode: String = "",
    val creditLimit: String = "0",
    val paymentTermDays: String = "0",
    val finalConsumer: Boolean = true,
    val icmsTaxpayer: Boolean = false,
    val simpleTaxpayer: Boolean = false,
    val blocked: Boolean = false,
    val blockReason: String = "",
    val notes: String = "",
)

data class CepAddress(
    val postalCode: String,
    val street: String,
    val complement: String,
    val neighborhood: String,
    val city: String,
    val state: String,
    val cityCode: String,
)

data class CompanyLookup(
    val document: String,
    val legalName: String,
    val tradeName: String,
    val stateRegistration: String,
    val street: String,
    val number: String,
    val complement: String,
    val neighborhood: String,
    val city: String,
    val state: String,
    val postalCode: String,
    val cityCode: String,
    val phone: String,
    val email: String,
    val registrationStatus: String,
)

data class StockBalance(
    val depositoId: Long,
    val depositoNome: String,
    val disponivel: String,
    val permiteVenda: Boolean,
    val origemPedido: Boolean,
)

data class Product(
    val id: Long,
    val descricao: String,
    val codigo: String,
    val unidade: String,
    val preco: String,
    val precoOrigem: String,
    val categoria: String,
    val categoriaId: Long?,
    val destaque: Boolean,
    val estoqueDisponivel: String,
    val controlaLote: Boolean,
    val podeVender: Boolean,
    val bloqueios: List<String>,
    val estoques: List<StockBalance>,
)

data class ProductCategory(
    val id: Long,
    val nome: String,
)

data class PaymentOption(
    val id: Long,
    val descricao: String,
    val tipo: String = "",
)

data class CheckoutState(
    val sessionOpen: Boolean,
    val cashRegisterName: String?,
    val payments: List<PaymentOption>,
    val canApplyDiscount: Boolean,
    val discountPercentages: List<String>,
    val maxDiscountPercent: String,
    val maxRoundingDiscount: String,
)

data class CheckoutLine(
    val product: Product,
    val quantity: String,
)

data class CheckoutSale(
    val client: Client?,
    val lines: List<CheckoutLine>,
    val payment: PaymentOption,
    val discount: String,
    val received: String,
    val pendingId: Long? = null,
    val discountAuthorization: String = "",
)

data class CheckoutResult(
    val saleId: Long,
    val number: String,
    val total: String,
    val change: String,
)

data class CheckoutPendingSummary(
    val id: Long,
    val number: String,
    val clientName: String,
    val total: String,
    val discount: String,
    val createdAt: String,
    val itemCount: Int,
)

data class CheckoutPendingDraft(
    val id: Long,
    val number: String,
    val client: Client?,
    val lines: List<CheckoutLine>,
    val discount: String,
    val paymentId: Long?,
)

data class PaymentCondition(
    val id: Long,
    val descricao: String,
)

data class OrderOptions(
    val payments: List<PaymentOption>,
    val conditions: List<PaymentCondition>,
    val categories: List<ProductCategory> = emptyList(),
)

data class OrderLineDraft(
    val product: Product,
    val quantity: String,
)

data class OrderDraft(
    val client: Client,
    val paymentId: Long,
    val conditionId: Long?,
    val prazoDias: Int,
    val prioridade: String,
    val dataEntrega: String?,
    val observacao: String,
    val lines: List<OrderLineDraft>,
)

data class SavedOrderDraft(
    val client: Client,
    val paymentId: Long?,
    val conditionId: Long?,
    val priority: String,
    val deliveryDate: String,
    val observation: String,
    val savedAt: String = "",
    val lines: List<OrderLineDraft>,
)

data class SubmitOrderResult(
    val orderId: Long?,
    val officialNumber: String?,
    val queued: Boolean,
)

data class OrderSummary(
    val id: Long,
    val numero: String,
    val cliente: String,
    val status: String,
    val statusDescricao: String,
    val valorTotal: String,
    val dataEmissao: String,
)

data class BatchSuggestion(
    val id: Long,
    val numero: String,
    val quantidade: String,
    val validade: String?,
)

data class SeparationDeposit(
    val id: Long,
    val nome: String,
    val disponivel: String,
    val padrao: Boolean,
)

data class SeparationLine(
    val id: Long,
    val numeroItem: Int,
    val produtoDescricao: String,
    val produtoCodigo: String,
    val unidade: String,
    val quantidadeSolicitada: String,
    val controlaLote: Boolean,
    val lotesSugeridos: List<BatchSuggestion>,
    val depositos: List<SeparationDeposit>,
)

data class SeparationOrder(
    val id: Long,
    val numero: String,
    val cliente: String,
    val prioridade: String,
    val dataEntrega: String?,
    val status: String,
    val lines: List<SeparationLine>,
)

data class BatchAllocation(
    val batchId: Long,
    val quantity: String,
)

data class SeparationInput(
    val itemId: Long,
    val depositoId: Long,
    val quantidade: String,
    val batchAllocations: List<BatchAllocation> = emptyList(),
    val observacao: String = "",
)

data class SyncSummary(
    val pendingBefore: Int,
    val pendingAfter: Int,
    val sentOrders: Int,
    val refreshedResources: List<String>,
    val errors: List<String>,
)

data class PendingOrderInfo(
    val key: String,
    val clientName: String,
    val createdAt: String,
    val attempts: Int,
    val lastError: String?,
)

data class SyncDashboard(
    val lastSuccessAt: String?,
    val lastError: String?,
    val hasOrderDraft: Boolean,
    val pendingOrders: List<PendingOrderInfo>,
    val cachedResources: List<String>,
)
