import Foundation

@main
struct MarkerAlignmentTests {
    enum Expected { case aligned, misaligned, unknown, blocked }
    static var checks = 0

    static func check(_ name: String, _ a: [Double], _ b: [Double], _ expected: Expected) {
        let error = MixerVision.alignmentError(markersA: a, markersB: b)
        let passed: Bool
        switch expected {
        case .aligned: passed = error.map { $0.isFinite && $0 <= 3 } ?? false
        case .misaligned: passed = error.map { $0.isFinite && $0 > 3 } ?? false
        case .unknown: passed = error == nil
        case .blocked: passed = error.map { $0.isFinite && $0 > 3 } ?? true
        }
        precondition(passed, "\(name): unexpected error \(String(describing: error))")
        checks += 1
    }

    static func main() {
        let regular = (0...10).map { 22.0+Double($0)*102.5 }
        let recordedA = [22.0,124.5,227,329.5,431.5,534.5,740.5,842.5,945,1047.5]
        let recordedB = [22.0,124.5,227,329.5,432.5,535.5,740.5,842.5,945,1047.5,1150,1253.5]
        check("identical", regular, regular, .aligned)
        check("recorded aligned frame with playhead gap", recordedA, recordedB, .aligned)
        check("reported single missing B detection", recordedA, recordedB.filter { $0 != 124.5 }, .aligned)
        check("reported single missing A detection", recordedA.filter { $0 != 124.5 }, recordedB, .aligned)
        check("minimum four pairs", [0,100,200,300], [0,100,200,300], .aligned)
        check("one pixel shift", regular, regular.map { $0+1 }, .aligned)
        check("three pixel tolerance", regular, regular.map { $0+3 }, .aligned)
        check("four pixels rejected", regular, regular.map { $0+4 }, .misaligned)
        check("one beat offset", regular, regular.map { $0+25.625 }, .misaligned)
        check("one beat offset reverse decks", regular.map { $0+25.625 }, regular, .misaligned)
        check("half bar offset", regular, regular.map { $0+51.25 }, .misaligned)
        check("progressive drift", regular, regular.enumerated().map { $0.element+Double($0.offset)*0.7 }, .misaligned)
        check("different tempo", regular, (0...10).map { 22.0+Double($0)*110 }, .misaligned)
        check("too few both decks", [0,100,200], [0,100,200], .unknown)
        check("too little shared evidence", [0,100,200,300], [200,300,400,500], .unknown)
        check("disjoint windows", regular, regular.map { $0+1500 }, .unknown)
        check("empty", regular, [], .unknown)
        check("nonfinite", regular, [0,100,200,.nan,400], .unknown)
        check("duplicate detection", regular, [22,124.5,124.5,227,329.5], .unknown)
        check("unsorted detection", regular, [22,227,124.5,329.5], .unknown)
        check("irregular spacing", regular, [22,120,227,345,440,534.5,635,745,853,940,1047], .unknown)
        check("isolated shifted marker", regular, regular.enumerated().map { $0.element+($0.offset == 5 ? 8 : 0) }, .blocked)
        check("extra red outlier", regular, (regular+[280]).sorted(), .unknown)
        check("too many common omissions", regular.enumerated().filter { ![2,3,6,7].contains($0.offset) }.map(\.element), regular.enumerated().filter { ![2,3,6,7].contains($0.offset) }.map(\.element), .unknown)
        check("too sparse one deck", regular, regular.enumerated().filter { $0.offset % 2 == 0 }.map(\.element), .unknown)

        // A single omitted detection anywhere in either deck must not turn correct
        // alignment into a one-bar error. The opposite test preserves genuine phase
        // errors even when the same missing-data pattern is present.
        for index in regular.indices {
            let omitted = regular.enumerated().filter { $0.offset != index }.map(\.element)
            check("omission A \(index)", omitted, regular, .aligned)
            check("omission B \(index)", regular, omitted, .aligned)
            for shift in [-25.625, -6, 6, 25.625] {
                check("omission plus offset \(index)/\(shift)", regular, omitted.map { $0+shift }, .misaligned)
            }
        }
        print("PASS \(checks) marker alignment cases")
    }
}
