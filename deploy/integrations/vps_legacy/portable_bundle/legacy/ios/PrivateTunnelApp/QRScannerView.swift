//
//  QRScannerView.swift
//  PrivateTunnel
//
//  Purpose: Presents an AVCaptureSession-backed QR scanner to import tunnel configuration JSON payloads.
//  Author: OpenAI Assistant
//  Created: 2024-05-15
//
//  Example:
//      QRScannerView { result in
//          // Handle Result<TunnelConfig, Error>
//      }
//
import AVFoundation
import SwiftUI

struct QRScannerView: UIViewControllerRepresentable {
    typealias UIViewControllerType = ScannerViewController

    let completion: (Result<TunnelConfig, Error>) -> Void

    func makeUIViewController(context: Context) -> ScannerViewController {
        let controller = ScannerViewController()
        controller.completion = completion
        controller.metadataDelegate = context.coordinator
        context.coordinator.controller = controller
        return controller
    }

    func updateUIViewController(_ uiViewController: ScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    final class ScannerViewController: UIViewController {
        var completion: ((Result<TunnelConfig, Error>) -> Void)?
        weak var metadataDelegate: AVCaptureMetadataOutputObjectsDelegate?

        fileprivate let captureSession = AVCaptureSession()
        fileprivate let metadataOutput = AVCaptureMetadataOutput()
        private var previewLayer: AVCaptureVideoPreviewLayer?
        private var hasCompleted = false

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            configureSession()
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            startSessionIfNeeded()
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            captureSession.stopRunning()
        }

        private func configureSession() {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                    DispatchQueue.main.async {
                        guard granted else {
                            self?.complete(with: ScannerError.cameraDenied)
                            return
                        }
                        self?.setupCamera()
                    }
                }
            case .authorized:
                setupCamera()
            default:
                complete(with: ScannerError.cameraDenied)
            }
        }

        private func setupCamera() {
            guard let videoDevice = AVCaptureDevice.default(for: .video) else {
                complete(with: ScannerError.cameraUnavailable)
                return
            }

            captureSession.beginConfiguration()
            captureSession.sessionPreset = .high

            do {
                let input = try AVCaptureDeviceInput(device: videoDevice)
                if captureSession.canAddInput(input) {
                    captureSession.addInput(input)
                }

                if captureSession.canAddOutput(metadataOutput) {
                    captureSession.addOutput(metadataOutput)
                    metadataOutput.metadataObjectTypes = [.qr]
                    metadataOutput.setMetadataObjectsDelegate(metadataDelegate, queue: DispatchQueue.main)
                }
            } catch {
                complete(with: error)
                captureSession.commitConfiguration()
                return
            }

            captureSession.commitConfiguration()

            let previewLayer = AVCaptureVideoPreviewLayer(session: captureSession)
            previewLayer.videoGravity = .resizeAspectFill
            previewLayer.frame = view.layer.bounds
            view.layer.addSublayer(previewLayer)
            self.previewLayer = previewLayer

            let guideLabel = UILabel()
            guideLabel.text = "将二维码置于框内"
            guideLabel.textColor = .white
            guideLabel.font = .preferredFont(forTextStyle: .headline)
            guideLabel.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(guideLabel)

            NSLayoutConstraint.activate([
                guideLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
                guideLabel.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -40)
            ])
        }

        private func startSessionIfNeeded() {
            guard !captureSession.isRunning, !hasCompleted else { return }
            DispatchQueue.global(qos: .userInitiated).async {
                self.captureSession.startRunning()
            }
        }

        fileprivate func process(metadataObjects: [AVMetadataObject]) {
            guard !hasCompleted,
                  let metadata = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
                  metadata.type == .qr,
                  let stringValue = metadata.stringValue else {
                return
            }

            hasCompleted = true
            captureSession.stopRunning()

            do {
                let config = try TunnelConfig.from(jsonString: stringValue)
                try config.isValid()
                complete(with: .success(config))
            } catch {
                complete(with: .failure(error))
            }
        }

        private func complete(with result: Result<TunnelConfig, Error>) {
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                completion?(result)
            }
        }

        private func complete(with error: Error) {
            complete(with: .failure(error))
        }
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        weak var controller: ScannerViewController?

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
            controller?.process(metadataObjects: metadataObjects)
        }
    }

    enum ScannerError: LocalizedError {
        case cameraDenied
        case cameraUnavailable

        var errorDescription: String? {
            switch self {
            case .cameraDenied:
                return "未获得相机权限，请在设置中启用相机访问。"
            case .cameraUnavailable:
                return "无法访问相机设备。"
            }
        }
    }
}
