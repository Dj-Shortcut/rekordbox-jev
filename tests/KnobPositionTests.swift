import AppKit

@main
struct KnobPositionTests {
    static var checks = 0
    static let white = NSColor(deviceRed:1,green:1,blue:1,alpha:1)

    static func bitmap(width: Int = 1272, height: Int = 768) -> NSBitmapImageRep {
        let image = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:width,pixelsHigh:height,
            bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,
            colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
        image.bitmapData!.initialize(repeating:0,count:image.bytesPerRow*height)
        return image
    }

    static func center(_ deck: Int, _ band: String) -> (Int,Int) {
        (deck == 1 ? 613 : 659,["trim":195,"high":225,"mid":254,"low":283][band]!)
    }

    static func pointer(_ image: NSBitmapImageRep, deck: Int, band: String, degrees: Double) {
        let (cx,cy) = center(deck,band)
        let angle = degrees*Double.pi/180
        let direction = (sin(angle),-cos(angle))
        for x in (cx-8)...(cx+8) { for y in (cy-8)...(cy+8) {
            let dx = Double(x-cx), dy = Double(y-cy)
            let radius = hypot(dx,dy)
            let forward = dx*direction.0+dy*direction.1
            let across = abs(dx*direction.1-dy*direction.0)
            if radius >= 3 && radius <= 8 && forward > 0 && across <= 0.65 {
                image.setColor(white,atX:x,y:y)
            }
        }}
    }

    static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(),message)
        checks += 1
    }

    static func main() throws {
        for deck in 1...2 { for band in ["trim","high","mid","low"] {
            for degrees in [0.0,-45,-120,45,120,-135,135,-160,160] {
                let image = bitmap()
                pointer(image,deck:deck,band:band,degrees:degrees)
                let vision = MixerVision(bitmap:image)
                let measured = vision.eqPosition(deck,band)
                let expected = min(1,max(-1,degrees/135))
                check(measured != nil,"deck \(deck) \(band) \(degrees): pointer was not detected")
                check(abs(measured!-expected) < 0.04,"deck \(deck) \(band) \(degrees): wrong signed angle \(measured!)")
                check(vision.neutral(deck,band) == (degrees == 0),"existing neutral classification changed")
            }
            let empty = bitmap()
            check(MixerVision(bitmap:empty).eqPosition(deck,band) == nil,"empty pixels must be unknown")
            check(MixerVision(bitmap:empty).neutral(deck,band) == nil,"empty neutral classification changed")
            let (cx,cy) = center(deck,band)
            empty.setColor(white,atX:cx,y:cy-5)
            check(MixerVision(bitmap:empty).eqPosition(deck,band) == nil,"one pixel must be unknown")

            let ambiguous = bitmap()
            pointer(ambiguous,deck:deck,band:band,degrees:-45)
            pointer(ambiguous,deck:deck,band:band,degrees:45)
            check(MixerVision(bitmap:ambiguous).eqPosition(deck,band) == nil,"two pointer directions must be unknown")

            let ring = bitmap()
            for x in (cx-7)...(cx+7) { for y in (cy-7)...(cy+7) {
                if (4...6).contains(hypot(Double(x-cx),Double(y-cy))) { ring.setColor(white,atX:x,y:y) }
            }}
            check(MixerVision(bitmap:ring).eqPosition(deck,band) == nil,"white ring must be unknown")

            let isolated = bitmap()
            pointer(isolated,deck:deck,band:band,degrees:0)
            isolated.setColor(white,atX:cx+4,y:cy-4)
            check(MixerVision(bitmap:isolated).eqPosition(deck,band) == nil,"disconnected white mark must be unknown")
        }}
        let invalid = MixerVision(bitmap:bitmap(width:100,height:100))
        check(invalid.eqPosition(1,"low") == nil,"uncalibrated dimensions must be unknown")
        let valid = MixerVision(bitmap:bitmap())
        check(valid.eqPosition(3,"low") == nil,"invalid deck must be unknown")
        check(valid.eqPosition(1,"filter") == nil,"invalid band must be unknown")

        let image = bitmap()
        pointer(image,deck:1,band:"low",degrees:-45)
        pointer(image,deck:2,band:"low",degrees:45)
        let data = try JSONSerialization.data(withJSONObject:MixerVision(bitmap:image).json)
        let decoded = try JSONSerialization.jsonObject(with:data) as! [String:Any]
        let positions = decoded["eq_position"] as! [String:[String:Any]]
        check((positions["1"]!["low"] as! Double) < 0,"JSON reduced pointer must be negative")
        check((positions["2"]!["low"] as! Double) > 0,"JSON boosted pointer must be positive")
        check(positions["1"]!["trim"] is NSNull,"JSON unknown must be null")
        check((decoded["eq_position_measurement"] as! String).contains("Not dB"),"JSON must label visual-angle limits")
        print("PASS \(checks) knob position cases")
    }
}
