import SwiftUI
import UIKit

enum ITEDStyle {
    static let blue = Color(red: 0.02, green: 0.48, blue: 0.90)
    static let orange = Color(red: 1.0, green: 0.38, blue: 0.05)
    static let orderPurple = Color(red: 0.34, green: 0.20, blue: 0.58)
    static let navy = Color(red: 0.02, green: 0.08, blue: 0.16)
    static let radius: CGFloat = 18
}

struct ITEDCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: ITEDStyle.radius))
    }
}

struct ITEDLogo: View {
    var compact = false

    var body: some View {
        Group {
            if let url = Bundle.module.url(forResource: "LogoITED", withExtension: "png"),
               let image = UIImage(contentsOfFile: url.path) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
            } else {
                Text("iTED")
                    .font(compact ? .headline.bold() : .largeTitle.bold())
                    .foregroundStyle(ITEDStyle.navy)
            }
        }
            .frame(height: compact ? 38 : 100)
            .padding(compact ? 4 : 10)
            .background(.white, in: RoundedRectangle(cornerRadius: compact ? 12 : 24))
            .overlay {
                RoundedRectangle(cornerRadius: compact ? 12 : 24)
                    .stroke(.white, lineWidth: 2)
            }
            .shadow(color: .black.opacity(0.12), radius: 12, y: 5)
            .accessibilityLabel("iTED")
    }
}

struct SearchResultPanel<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }
    var body: some View {
        ScrollView { content }
            .frame(maxHeight: 270)
            .background(.background, in: RoundedRectangle(cornerRadius: 14))
            .overlay { RoundedRectangle(cornerRadius: 14).stroke(.secondary.opacity(0.22)) }
    }
}

extension View {
    func itedPrimaryButton() -> some View {
        buttonStyle(.borderedProminent)
            .buttonBorderShape(.roundedRectangle(radius: 14))
            .tint(ITEDStyle.orange)
            .controlSize(.large)
    }
}
