//
//  TunnelProtocol.swift
//  PacketTunnelProvider
//
//  Defines the minimal framing used by the toy UDP tunnel. The format is
//  intentionally tiny and carries raw IPv4 packets without encryption. It is
//  suitable for short-lived development testing only.
//

import Foundation

enum TunnelFrameType: UInt8 {
    case dataIP = 0x00
    case ping = 0x01
    case pong = 0x02
}

struct TunnelFrame {
    let type: TunnelFrameType
    let payload: Data
}

enum TunnelProtocolError: Error {
    case payloadTooLarge
    case invalidMagic
    case invalidVersion
    case malformedFrame
    case unknownType(UInt8)
}

enum TunnelProtocol {
    private static let magic: UInt16 = 0x5459
    private static let version: UInt8 = 0x01
    static let headerLength = 8
    static let maxPayloadLength = 1_600

    static func encodeFrame(type: TunnelFrameType, payload: Data) throws -> Data {
        guard payload.count <= maxPayloadLength else {
            throw TunnelProtocolError.payloadTooLarge
        }

        var data = Data(capacity: headerLength + payload.count)

        var magicLE = magic.littleEndian
        withUnsafeBytes(of: &magicLE) { data.append(contentsOf: $0) }
        data.append(version)
        data.append(type.rawValue)

        var lengthLE = UInt32(payload.count).littleEndian
        withUnsafeBytes(of: &lengthLE) { data.append(contentsOf: $0) }

        data.append(payload)
        return data
    }

    static func parseFrame(_ data: Data) throws -> TunnelFrame {
        guard data.count >= headerLength else {
            throw TunnelProtocolError.malformedFrame
        }

        let magicValue = UInt16(data[0]) | (UInt16(data[1]) << 8)
        guard magicValue == magic else {
            throw TunnelProtocolError.invalidMagic
        }

        let versionByte = data[2]
        guard versionByte == version else {
            throw TunnelProtocolError.invalidVersion
        }

        let typeByte = data[3]
        guard let frameType = TunnelFrameType(rawValue: typeByte) else {
            throw TunnelProtocolError.unknownType(typeByte)
        }

        let lengthBytes = data[4..<8]
        var lengthValue: UInt32 = 0
        for (index, byte) in lengthBytes.enumerated() {
            lengthValue |= UInt32(byte) << (8 * index)
        }
        let payloadLength = Int(lengthValue)
        guard payloadLength >= 0,
              payloadLength <= data.count - headerLength,
              payloadLength <= maxPayloadLength else {
            throw TunnelProtocolError.malformedFrame
        }

        let payload = data.subdata(in: headerLength..<(headerLength + payloadLength))
        return TunnelFrame(type: frameType, payload: payload)
    }
}
