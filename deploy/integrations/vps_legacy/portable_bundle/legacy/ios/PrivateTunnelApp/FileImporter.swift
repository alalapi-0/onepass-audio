//
//  FileImporter.swift
//  PrivateTunnel
//
//  Purpose: Wraps UIDocumentPickerViewController to load JSON configurations from the Files app.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      FileImporter { result in
//          // Handle parsed TunnelConfig
//      }
//
import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct FileImporter: UIViewControllerRepresentable {
    typealias UIViewControllerType = UIDocumentPickerViewController

    let completion: (Result<TunnelConfig, Error>) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [UTType.json], asCopy: true)
        picker.delegate = context.coordinator
        picker.allowsMultipleSelection = false
        picker.shouldShowFileExtensions = true
        return picker
    }

    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(completion: completion)
    }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        private let completion: (Result<TunnelConfig, Error>) -> Void

        init(completion: @escaping (Result<TunnelConfig, Error>) -> Void) {
            self.completion = completion
        }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            guard let url = urls.first else { return }

            do {
                let data = try Data(contentsOf: url)
                let config = try TunnelConfig.from(jsonData: data)
                try config.isValid()
                completion(.success(config))
            } catch {
                completion(.failure(error))
            }
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            // User cancelled the picker; no result is propagated to keep the current UI state.
        }
    }
}
