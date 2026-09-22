import AppKit

// Fixed Rekordbox 6.8.7 / 2Deck Horizontal layout. Unknown measurements fail closed.
struct MixerVision {
    let bitmap: NSBitmapImageRep
    var calibrated: Bool { bitmap.pixelsWide == 1272 && bitmap.pixelsHigh == 768 }
    func rgb(_ x: Int, _ y: Int) -> NSColor? {
        guard calibrated else { return nil }
        return bitmap.colorAt(x:x,y:y)?.usingColorSpace(.deviceRGB)
    }
    func markers(_ deck: Int) -> [Double] {
        guard calibrated else { return [] }
        let top = deck == 1 ? 49 : 110
        var groups: [[Int]] = []
        for x in 20...1265 where !(632...640).contains(x) {
            let count = (top...(top+8)).filter { y in
                guard let c = rgb(x,y) else { return false }
                return c.redComponent > 0.55 && c.greenComponent < 0.30 && c.blueComponent < 0.35
            }.count
            if count >= 2 {
                if let last = groups.last?.last, x-last <= 4 { groups[groups.count-1].append(x) }
                else { groups.append([x]) }
            }
        }
        // Reject long red lines, e.g. the stopped playhead, rather than treating them as dots.
        return groups.filter { (2...8).contains($0.count) }.map { Double($0.reduce(0,+))/Double($0.count) }
    }
    var crossfader: Double? {
        guard calibrated else { return nil }
        let scores = (584...688).map { x -> (Int,Double) in
            (x,(377...393).reduce(0.0) { sum,y in
                guard let c = rgb(x,y) else { return sum }
                return sum+Double(min(c.redComponent,c.greenComponent,c.blueComponent))
            })
        }
        guard let best = scores.max(by:{$0.1 < $1.1}), best.1 > 5 else { return nil }
        return min(1,max(0,Double(best.0-585)/101))
    }
    var alignmentError: Double? {
        Self.alignmentError(markersA: markers(1), markersB: markers(2))
    }
    // Missing detections are not phase errors. Only disregard a missing marker when
    // its neighbours establish a regular gap and both neighbours match the other
    // deck. Every remaining correspondence must still be within the same 3 px limit.
    static func alignmentError(markersA a: [Double], markersB b: [Double]) -> Double? {
        guard let periodA = regularMarkerPeriod(a), let periodB = regularMarkerPeriod(b) else { return nil }
        let tolerance = 3.0
        let low = max(a[0], b[0]), high = min(a.last!, b.last!)
        guard high-low >= 2*min(periodA, periodB) else { return nil }
        let visibleA = a.filter { $0 >= low-tolerance && $0 <= high+tolerance }
        let visibleB = b.filter { $0 >= low-tolerance && $0 <= high+tolerance }
        guard visibleA.count >= 3, visibleB.count >= 3 else { return nil }
        let expected = max(max(visibleA.count, visibleB.count), Int(((high-low)/min(periodA, periodB)+0.05).rounded(.down))+1)
        guard Double(min(visibleA.count, visibleB.count))/Double(expected) >= 0.75 else { return nil }

        func distance(_ x: Double, to markers: [Double]) -> Double {
            markers.map { abs($0-x) }.min()!
        }
        func demonstratedMissing(_ x: Double, own: [Double], other: [Double], period: Double) -> Bool {
            guard let rightIndex = other.firstIndex(where: { $0 > x }), rightIndex > 0 else { return false }
            let left = other[rightIndex-1], right = other[rightIndex]
            let intervals = ((right-left)/period).rounded()
            guard intervals >= 2, intervals <= 3,
                  distance(left, to: own) <= tolerance, distance(right, to: own) <= tolerance else { return false }
            let slot = ((x-left)/(right-left)*intervals).rounded()
            return slot >= 1 && slot < intervals && abs(x-(left+(right-left)*slot/intervals)) <= tolerance
        }
        func offsets(_ visible: [Double], own: [Double], other: [Double], period: Double) -> [Double] {
            visible.compactMap { x in
                let offset = distance(x, to: other)
                if offset > tolerance && demonstratedMissing(x, own: own, other: other, period: period) { return nil }
                return offset
            }
        }
        let offsetsA = offsets(visibleA, own: a, other: b, period: periodB)
        let offsetsB = offsets(visibleB, own: b, other: a, period: periodA)
        guard let worst = (offsetsA+offsetsB).max() else { return nil }
        // A visible mismatch must never disappear into a median or missing-data allowance.
        if worst > tolerance { return worst }
        let matches = min(offsetsA.count, offsetsB.count)
        guard matches >= 4, Double(matches)/Double(expected) >= 0.75 else { return nil }
        return worst
    }
    private static func regularMarkerPeriod(_ markers: [Double]) -> Double? {
        guard markers.count >= 4, markers.allSatisfy({ $0.isFinite }) else { return nil }
        let gaps = zip(markers.dropFirst(), markers).map { $0-$1 }
        guard let shortest = gaps.min(), shortest >= 12 else { return nil }
        let unitGaps = gaps.filter { $0 <= shortest+3 }.sorted()
        guard unitGaps.count >= 2, unitGaps.count*2 >= gaps.count else { return nil }
        let middle = unitGaps.count/2
        let period = unitGaps.count % 2 == 0 ? (unitGaps[middle-1]+unitGaps[middle])/2 : unitGaps[middle]
        guard gaps.allSatisfy({ gap in
            let intervals = (gap/period).rounded()
            return intervals >= 1 && intervals <= 3 && abs(gap-intervals*period) <= 3
        }) else { return nil }
        return period
    }
    var aligned: Bool? { alignmentError.map { $0 <= 3.0 } }
    private func pointerPoints(_ deck: Int, _ band: String) -> (cx:Int, cy:Int, points:[(Int,Int)])? {
        guard calibrated, [1,2].contains(deck),
              let cy = ["trim":195,"high":225,"mid":254,"low":283][band] else { return nil }
        let cx = deck == 1 ? 613 : 659
        var points: [(Int,Int)] = []
        for x in (cx-7)...(cx+7) { for y in (cy-8)...(cy+7) {
            let radius = hypot(Double(x-cx),Double(y-cy))
            guard radius >= 3, radius <= 8, let c = rgb(x,y),
                  min(c.redComponent,c.greenComponent,c.blueComponent) > 0.65 else { continue }
            points.append((x,y))
        }}
        return (cx,cy,points)
    }
    func neutral(_ deck: Int, _ band: String) -> Bool? {
        guard let pointer = pointerPoints(deck,band) else { return nil }
        let (cx,cy,points) = pointer
        guard points.count >= 2 else { return nil }
        let x = Double(points.reduce(0){$0+$1.0}) / Double(points.count)
        let y = Double(points.reduce(0){$0+$1.1}) / Double(points.count)
        return abs(x-Double(cx)) <= 1.0 && y <= Double(cy-3)
    }
    // Signed visual pointer angle, not an EQ gain or loudness measurement.
    // Counter-clockwise/left is negative; clockwise/right is positive. White
    // marks must form one compact radial pointer, otherwise the result is nil.
    func eqPosition(_ deck: Int, _ band: String) -> Double? {
        guard let (cx,cy,points) = pointerPoints(deck,band), points.count >= 2 else { return nil }
        var unvisited = Set(points.map { $0.1*1272+$0.0 })
        var pending = [points[0]]
        unvisited.remove(points[0].1*1272+points[0].0)
        while let point = pending.popLast() {
            for dx in -1...1 { for dy in -1...1 where dx != 0 || dy != 0 {
                let next = (point.0+dx,point.1+dy)
                if unvisited.remove(next.1*1272+next.0) != nil { pending.append(next) }
            }}
        }
        guard unvisited.isEmpty else { return nil }
        let dx = Double(points.reduce(0){$0+$1.0})/Double(points.count)-Double(cx)
        let dy = Double(points.reduce(0){$0+$1.1})/Double(points.count)-Double(cy)
        let radius = hypot(dx,dy)
        guard radius >= 3 else { return nil }
        var unitX = 0.0, unitY = 0.0
        for point in points {
            let x = Double(point.0-cx), y = Double(point.1-cy), length = hypot(x,y)
            unitX += x/length; unitY += y/length
            // More than 30 degrees of deviation from the centroid is not one
            // confidently resolved pointer at this calibrated pixel scale.
            guard (x*dx+y*dy)/(length*radius) >= cos(Double.pi/6) else { return nil }
        }
        guard hypot(unitX,unitY)/Double(points.count) >= 0.90 else { return nil }
        let angle = atan2(dx,-dy)
        return min(1,max(-1,angle/(135*Double.pi/180)))
    }
    var json: [String:Any] {
        var eq: [String:[String:Any]] = [:]
        var positions: [String:[String:Any]] = [:]
        for deck in 1...2 {
            eq[String(deck)] = Dictionary(uniqueKeysWithValues: ["trim","high","mid","low"].map {
                ($0, neutral(deck,$0) as Any? ?? NSNull())
            })
            positions[String(deck)] = Dictionary(uniqueKeysWithValues: ["trim","high","mid","low"].map {
                ($0, eqPosition(deck,$0) as Any? ?? NSNull())
            })
        }
        return ["red_bar_markers_A":markers(1), "red_bar_markers_B":markers(2),
                "red_bar_alignment_error_px":alignmentError as Any? ?? NSNull(),
                "red_bar_aligned":aligned as Any? ?? NSNull(), "eq_neutral":eq,"eq_position":positions,
                "eq_position_measurement":"Signed visual knob pointer angle divided by 135 degrees, clamped to [-1,1]. Left is negative, right positive. Use eq_neutral for neutral classification; small nonzero angles can be pixel quantization. Unknown or ambiguous pixels are null. Not dB, EQ gain or audio loudness.",
                "eq_measurement":"Knob pointer at neutral; exact dB inferred only after native reset, not from reverse drag."]
    }
}
