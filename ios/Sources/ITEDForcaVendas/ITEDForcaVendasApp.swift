import SwiftUI

@main
struct ITEDForcaVendasApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .preferredColorScheme(preferredScheme)
                .dynamicTypeSize(.small ... .xxLarge)
        }
    }

    private var preferredScheme: ColorScheme? {
        switch model.colorScheme {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}
