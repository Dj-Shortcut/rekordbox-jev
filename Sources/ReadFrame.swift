import AppKit

@main enum ReadFrame {
    static func main() throws {
        guard CommandLine.arguments.count == 2,
              let bitmap = NSBitmapImageRep(data:try Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1]))),
              bitmap.pixelsWide == 1272, bitmap.pixelsHigh == 768 else {
            throw NSError(domain:"ReadFrame",code:1,userInfo:[NSLocalizedDescriptionKey:"Geen gekalibreerde opname."])
        }
        func rgb(_ x:Int,_ y:Int) -> NSColor { bitmap.colorAt(x:x,y:y)!.usingColorSpace(.deviceRGB)! }
        func white(_ x:Int,_ y:Int) -> Double {
            let c = rgb(x,y); return Double(min(c.redComponent,c.greenComponent,c.blueComponent))
        }
        func blue(_ x:Int,_ y:Int) -> Bool {
            let c = rgb(x,y)
            return c.blueComponent > 0.3 && c.blueComponent > c.redComponent*1.7 && c.greenComponent > 0.2
        }
        var scores:[(Int,Double)] = []
        for x in 584...688 {
            let score = (377...393).reduce(0.0) { $0+white(x,$1) }
            scores.append((x,score))
        }
        scores.sort{$0.1 > $1.1}
        let best = scores[0]
        var assignments:[String:String] = [:]
        var sync:[String:Bool] = [:]
        var master:[String:Bool] = [:]
        for deck in 1...2 {
            let left = blue(deck == 1 ? 531 : 553,379)
            let right = blue(deck == 1 ? 709 : 730,379)
            assignments[String(deck)] = left && !right ? "left" : right && !left ? "right" : !left && !right ? "unassigned" : "unknown"
            let origin = deck == 1 ? 535 : 1220
            var blueCount = 0, orangeCount = 0, whiteCount = 0
            for x in origin...min(origin+40,1271) {
                for y in 177...195 { if blue(x,y) { blueCount += 1 } }
                // Rekordbox 6.8.7 Dark uses white BEAT SYNC text when enabled.
                // Exclude the x1 multiplier on the right and MASTER below.
                if x <= origin+25 {
                    for y in 178...194 { if white(x,y) > 0.70 { whiteCount += 1 } }
                }
                for y in 198...209 {
                    let c = rgb(x,y)
                    if c.redComponent > 0.4 && c.greenComponent > 0.2 && c.redComponent > c.blueComponent*2 { orangeCount += 1 }
                }
            }
            sync[String(deck)] = blueCount > 8 || whiteCount > 8
            master[String(deck)] = orangeCount > 6
        }
        var result:[String:Any] = [
            "source":"Rekordbox window pixels, same captured frame as OCR",
            "crossfader_position":best.1 > 5 ? min(1,max(0,Double(best.0-585)/101)) : NSNull(),
            "crossfader_pixel_score":best.1,
            "deck_assignments":assignments,
            "beat_sync_lit":sync,"master_lit":master,
            "note":"Position and assignment are visual measurements; crossfader curve and actual audible output are not measured."
        ]
        result.merge(MixerVision(bitmap:bitmap).json) { _,new in new }
        let data = try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys])
        print(String(data:data,encoding:.utf8)!)
    }
}
