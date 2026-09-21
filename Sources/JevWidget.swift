import AppKit
import SwiftUI

private typealias Object = [String: Any]
private func object(_ value: Any?) -> Object { value as? Object ?? [:] }
private func string(_ value: Any?) -> String { value as? String ?? "" }
private func pretty(_ value: Any) -> String {
    guard JSONSerialization.isValidJSONObject(value),
          let data = try? JSONSerialization.data(withJSONObject:value, options:[.prettyPrinted,.sortedKeys,.fragmentsAllowed]),
          let text = String(data:data,encoding:.utf8) else { return String(describing:value) }
    return text
}
private func readObject(_ url: URL) -> Object? {
    guard let data = try? Data(contentsOf:url), data.count < 4_000_000,
          let json = try? JSONSerialization.jsonObject(with:data) as? Object else { return nil }
    return json
}
private func modified(_ url: URL) -> Double {
    ((try? FileManager.default.attributesOfItem(atPath:url.path)[.modificationDate]) as? Date)?.timeIntervalSince1970 ?? 0
}

private struct Run: Identifiable {
    var id: String
    var started: Double
    var status: String
    var request: Object
    var result: Object
    var error: String
    var label: String
    var payload: Object { object(request["payload"]) }
    var questions: Object { object(payload["questions"]) }
    var state: Object { object(payload["state"]) }
    var synthetic: Bool { string(request["provenance"]) == "synthetic_example" }
    var version: Int { object(state["tutorial_guidance"])["version"] as? Int ?? 1 }
    var statusText: String {
        switch status {
        case "preview": return "Nog niet aan Jev verstuurd"
        case "answered": return "Echt Jev-antwoord"
        case "pending": return Date().timeIntervalSince1970-started > 60 ? "Antwoord nog niet bevestigd" : "Jev wordt bevraagd…"
        case "not_sent": return "Geen aanvraag verstuurd"
        case "cancelled": return "Proef gestopt"
        default: return "Aanvraag niet gelukt"
        }
    }
    func answer(_ id: String) -> Object {
        if let answers = result["answers"] as? Object { return object(answers[id]) }
        if id == "mix_plan", result["choice"] != nil { return result }
        return [:]
    }
}

private final class Monitor: ObservableObject {
    @Published var runs: [Run] = []
    @Published var selectedID = ""
    @Published var followLatest = true
    @Published var loadIssue = ""
    @Published var tab = 0
    @Published var pinned = true
    let root: URL
    private var timer: Timer?
    private var lastNewest = ""
    init(root: URL) {
        self.root = root
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval:1, repeats:true) { [weak self] _ in self?.refresh() }
    }
    var selected: Run? { runs.first(where:{$0.id == selectedID}) }
    func refresh() {
        let evidence = root.appendingPathComponent("evidence")
        let events = evidence.appendingPathComponent("jev-events")
        let files = ((try? FileManager.default.contentsOfDirectory(at:events,includingPropertiesForKeys:nil)) ?? [])
            .filter{$0.pathExtension == "json"}.sorted{modified($0) > modified($1)}.prefix(40)
        var values: [Run] = []
        for file in files {
            guard let event = readObject(file), let request = event["request"] as? Object else { continue }
            let result = object(event["result"])
            let status = string(event["status"])
            // Never attach an answer to a different question snapshot.
            if status == "answered" && string(result["payload_id"]) != string(request["payload_id"]) { continue }
            values.append(Run(id:string(event["id"]),started:event["started_at"] as? Double ?? modified(file),
                              status:status,request:request,result:result,error:string(event["error"]),label:string(request["kind"]) == "jev_reactive_request" ? "DJ-beslissing" : "Aanroep"))
        }
        let legacy = evidence.appendingPathComponent("jev-live-example.json")
        if let result = readObject(legacy), let request = readObject(evidence.appendingPathComponent("jev-live-example.request.json")),
           !string(result["payload_id"]).isEmpty,
           string(result["payload_id"]) == string(request["payload_id"]),
           !values.contains(where:{string($0.result["payload_id"]) == string(result["payload_id"]) && $0.status == "answered"}) {
            values.append(Run(id:"first-live-test",started:modified(legacy),status:"answered",request:request,
                              result:result,error:"",label:"Eerste verbindingstest"))
        }
        values.sort{$0.started > $1.started}
        let newest = values.first?.id ?? ""
        let reactivePreview = evidence.appendingPathComponent("jev-reactive-preview.json")
        if let request = readObject(reactivePreview) {
            values.insert(Run(id:"reactive-preview-"+string(request["payload_id"]),started:modified(reactivePreview),status:"preview",
                              request:request,result:[:],error:"",label:"Actuele DJ-vraag"),at:values.isEmpty || modified(reactivePreview) > values[0].started ? 0 : values.count)
        }
        let preview = root.appendingPathComponent("examples/jev-request.json")
        if let request = readObject(preview) {
            values.append(Run(id:"preview-"+string(request["payload_id"]),started:modified(preview),status:"preview",
                              request:request,result:[:],error:"",label:"Voorbereide vraag"))
        }
        if selectedID.isEmpty || !values.contains(where:{$0.id == selectedID}) || (followLatest && newest != lastNewest && !newest.isEmpty) {
            selectedID = values.first?.id ?? ""
        }
        lastNewest = newest
        runs = values
        loadIssue = values.isEmpty ? "Er zijn nog geen leesbare vragen of antwoorden beschikbaar." : ""
    }
}

private let mint = Color(red:0.35,green:0.9,blue:0.73)
private let muted = Color.white.opacity(0.57)
private struct Pill: View {
    var text: String
    var tint: Color = mint
    var body: some View {
        Text(text).font(.system(size:10,weight:.semibold)).foregroundStyle(tint)
            .padding(.horizontal,8).padding(.vertical,4)
            .background(tint.opacity(0.12),in:Capsule())
    }
}
private struct Card<Content: View>: View {
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(alignment:.leading,spacing:10,content:content).frame(maxWidth:.infinity,alignment:.leading)
            .padding(14).background(Color.white.opacity(0.045),in:RoundedRectangle(cornerRadius:13))
            .overlay(RoundedRectangle(cornerRadius:13).stroke(Color.white.opacity(0.07),lineWidth:1))
    }
}
private struct Inspector: View {
    @ObservedObject var monitor: Monitor
    @ObservedObject var controls: JevControls
    let pin: (Bool) -> Void
    private func optionLabel(_ id: String, question: Object, run: Run) -> String {
        if id == "defer" { return "Uitstellen" }
        let option = object(object(question["criteria"])[id])
        let label = string(option["label"])
        if !label.isEmpty { return label }
        let proposal = object(option["proposal"])
        let tid = string(proposal["technique"])
        let embedded = object(option["technique"])
        let name = string(embedded["name"])
        let currentName = string(object(object(object(run.state["tutorial_guidance"])["techniques"])[tid])["name"])
        return name.isEmpty ? (currentName.isEmpty ? id : currentName) : name
    }
    private func timeLabel(_ run: Run) -> String {
        let formatter = DateFormatter(); formatter.locale = Locale(identifier:"nl_BE"); formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from:Date(timeIntervalSince1970:run.started))
    }
    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            HStack(spacing:10) {
                Image(systemName:"waveform.path").font(.system(size:23,weight:.medium)).foregroundStyle(mint)
                    .frame(width:38,height:38).background(mint.opacity(0.10),in:RoundedRectangle(cornerRadius:10))
                VStack(alignment:.leading,spacing:2) {
                    Text("Jev / DJ").font(.system(size:18,weight:.semibold,design:.rounded))
                    Text("Vraag & antwoord").font(.system(size:11)).foregroundStyle(muted)
                }
                Spacer()
                Button { monitor.pinned.toggle(); pin(monitor.pinned) } label: {
                    Image(systemName:monitor.pinned ? "pin.fill" : "pin.slash").foregroundStyle(monitor.pinned ? mint : muted)
                }.buttonStyle(.plain).help(monitor.pinned ? "Altijd bovenop uitschakelen" : "Altijd bovenop inschakelen")
            }
            JevControlView(controls:controls,onStart:{ monitor.followLatest = true; monitor.tab = 0 })
            if let run = monitor.selected {
                Picker("Aanvraag",selection:Binding(get:{monitor.selectedID},set:{monitor.selectedID=$0;monitor.followLatest=false})) {
                    ForEach(monitor.runs) { item in
                        Text("\(item.label) · v\(item.version) · \(timeLabel(item))").tag(item.id)
                    }
                }.labelsHidden()
                HStack {
                    Pill(text:run.statusText,tint:run.status == "error" ? .orange : mint)
                    if run.synthetic { Pill(text:"Voorbeeldgegevens",tint:.orange) }
                    Spacer(minLength:0)
                }
                Picker("Inhoud",selection:$monitor.tab) {
                    Text("Vragen").tag(0); Text("Muziek").tag(1); Text("Alle invoer").tag(2)
                }.pickerStyle(.segmented)
                ScrollView {
                    VStack(alignment:.leading,spacing:12) {
                        if !run.error.isEmpty { Card { Text(run.error).foregroundStyle(.orange).textSelection(.enabled) } }
                        if run.status == "not_sent" { Card { Text(string(run.result["message"])).textSelection(.enabled) } }
                        if monitor.tab == 0 {
                            if let seconds = object(run.result["timing"])["cycle_seconds"] as? Double {
                                Card {
                                    Text(String(format:"Hele cyclus: %.3f s",seconds)).font(.system(size:13,weight:.semibold))
                                    Text(string(object(run.result["execution"])["message"])).font(.system(size:11)).foregroundStyle(muted)
                                    DisclosureGroup("Tijden en uitvoering") {
                                        Text(pretty(["timing":object(run.result["timing"]),"execution":object(run.result["execution"])]))
                                            .font(.system(size:10,design:.monospaced)).textSelection(.enabled)
                                    }.font(.system(size:11))
                                }
                            }
                            ForEach(run.questions.keys.sorted(),id:\.self) { id in
                                questionView(id,object(run.questions[id]),run)
                            }
                        } else if monitor.tab == 1 {
                            musicView(run)
                        } else {
                            Card {
                                Text("Exact meegestuurd / voorbereid").font(.system(size:13,weight:.semibold))
                                Text(run.status == "preview" ? "Dit verzoek is nog niet verstuurd." : "Dit hoort bij deze specifieke aanroep.")
                                    .foregroundStyle(muted).font(.system(size:11))
                                Button("Kopieer volledige invoer") {
                                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(pretty(run.payload),forType:.string)
                                }.controlSize(.small)
                                Text(pretty(run.payload)).font(.system(size:10,design:.monospaced)).textSelection(.enabled)
                            }
                        }
                    }.padding(.bottom,8)
                }
                HStack {
                    Toggle("Volg nieuwste",isOn:$monitor.followLatest).toggleStyle(.checkbox).font(.system(size:11))
                    Spacer()
                    Text("\(run.questions.count) \(run.questions.count == 1 ? "vraag" : "vragen")").font(.system(size:11)).foregroundStyle(muted)
                }
                Text(string(run.request["kind"]) == "jev_reactive_request"
                     ? "DJ-proef: één handeling, daarna opnieuw waarnemen."
                     : "Historische mixplan-test; geen actieve DJ-bediening.")
                    .font(.system(size:10)).foregroundStyle(muted)
            } else {
                Spacer(); Text(monitor.loadIssue).foregroundStyle(muted); Spacer()
            }
        }
        .padding(18).frame(minWidth:380,minHeight:440)
        .background(Color(red:0.065,green:0.08,blue:0.095)).preferredColorScheme(.dark)
    }
    @ViewBuilder private func questionView(_ id: String, _ question: Object, _ run: Run) -> some View {
        let instructions = object(question["instructions"])
        let task = string(instructions["task"]).isEmpty ? string(question["instructions"]) : string(instructions["task"])
        let answer = run.answer(id)
        let choice = string(answer["choice"])
        let probabilities = object(answer["probabilities"])
        let options = object(question["criteria"])
        Card {
            Text("VRAAG AAN JEV").font(.system(size:10,weight:.bold)).foregroundStyle(mint)
            Text(task.isEmpty ? id : task).font(.system(size:13,weight:.medium)).textSelection(.enabled)
            if !choice.isEmpty {
                Divider().overlay(Color.white.opacity(0.08))
                Text(optionLabel(choice,question:question,run:run)).font(.system(size:22,weight:.semibold,design:.rounded))
                HStack {
                    Text(string(run.result["model"])).foregroundStyle(muted)
                    Spacer()
                    if let seconds = run.result["request_seconds"] as? Double { Text(String(format:"%.3f s",seconds)).monospacedDigit().foregroundStyle(mint) }
                }.font(.system(size:11))
            } else {
                Text(run.status == "pending" ? "Wachten op antwoord…" : "Nog geen antwoord op deze vraag.")
                    .foregroundStyle(muted).font(.system(size:12))
            }
        }
        Card {
            Text(choice.isEmpty ? "Antwoordopties" : "Kansen per antwoordoptie").font(.system(size:12,weight:.semibold))
            ForEach(options.keys.sorted(),id:\.self) { option in
                VStack(alignment:.leading,spacing:5) {
                    HStack {
                        if option == choice { Image(systemName:"checkmark.circle.fill").foregroundStyle(mint) }
                        Text(optionLabel(option,question:question,run:run)).font(.system(size:12))
                        Spacer()
                        if let value = probabilities[option] as? Double { Text(String(format:"%.0f%%",value*100)).font(.system(size:12,weight:.semibold)).monospacedDigit() }
                    }
                    if let value = probabilities[option] as? Double { ProgressView(value:min(1,max(0,value))).tint(option == choice ? mint : Color.white.opacity(0.35)) }
                    DisclosureGroup("Beschrijving") {
                        Text(pretty(options[option] ?? "")).font(.system(size:10,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                    }.font(.system(size:10)).foregroundStyle(muted)
                }.padding(.vertical,3)
            }
            if let confidence = answer["confidence"] as? Double {
                Divider()
                Text(String(format:"Confidence %.2f · spreiding van de modelkeuze",confidence)).font(.system(size:10)).foregroundStyle(muted)
                Text("Jev geeft bij deze vraag geen geschreven toelichting.").font(.system(size:10)).foregroundStyle(muted)
            }
        }
        Card {
            DisclosureGroup("Volledige vraaginstructies") {
                Text(pretty(question["instructions"] ?? "")).font(.system(size:11)).textSelection(.enabled).padding(.top,6)
            }.font(.system(size:12,weight:.medium))
        }
    }
    @ViewBuilder private func musicView(_ run: Run) -> some View {
        let music = object(run.state["music"])
        let tracks = object(music["tracks"])
        if run.synthetic {
            Card { Text("Dit zijn verzonnen voorbeeldtracks, geen live aflezing van Rekordbox.").foregroundStyle(.orange).font(.system(size:12)) }
        }
        ForEach(tracks.keys.sorted(),id:\.self) { id in
            let track = object(tracks[id])
            Card {
                Text(string(track["title"]).isEmpty ? id : string(track["title"])).font(.system(size:14,weight:.semibold))
                HStack {
                    if let bpm = track["bpm"] as? Double { Pill(text:String(format:"%.0f BPM",bpm)) }
                    let key = string(track["key"])
                    if !key.isEmpty { Pill(text:key,tint:.white.opacity(0.7)) }
                }
                Text(string(track["description"])).font(.system(size:12)).foregroundStyle(muted).textSelection(.enabled)
            }
        }
        Card {
            Text("Muziekmarkeringen en context").font(.system(size:12,weight:.semibold))
            Text(pretty(music)).font(.system(size:10,design:.monospaced)).textSelection(.enabled)
        }
    }
}

private final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var panel: FloatingPanel?
    private var monitor: Monitor?
    private var controls: JevControls?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let editItem = NSMenuItem(title:"Wijzig",action:nil,keyEquivalent:"")
        let editMenu = NSMenu(title:"Wijzig")
        editMenu.addItem(withTitle:"Knip",action:#selector(NSText.cut(_:)),keyEquivalent:"x")
        editMenu.addItem(withTitle:"Kopieer",action:#selector(NSText.copy(_:)),keyEquivalent:"c")
        editMenu.addItem(withTitle:"Plak",action:#selector(NSText.paste(_:)),keyEquivalent:"v")
        editMenu.addItem(withTitle:"Selecteer alles",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a")
        editItem.submenu = editMenu; menu.addItem(editItem); NSApplication.shared.mainMenu = menu
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        let model = Monitor(root:root); monitor = model
        let controls = JevControls(root:root); self.controls = controls
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
        let height = min(800,visible.height-40)
        let panel = FloatingPanel(contentRect:NSRect(x:visible.maxX-440,y:visible.maxY-height-20,width:420,height:height),
                                  styleMask:[.titled,.closable,.resizable,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.title = "Jev — Vraag & antwoord"
        panel.titleVisibility = .hidden; panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true; panel.level = .floating; panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true; panel.minSize = NSSize(width:380,height:480)
        panel.delegate = self; panel.isReleasedWhenClosed = false
        panel.contentView = NSHostingView(rootView:Inspector(monitor:model,controls:controls,pin:{ [weak panel] pinned in panel?.level = pinned ? .floating : .normal }))
        self.panel = panel
        panel.orderFrontRegardless()
    }
    func windowWillClose(_ notification: Notification) { controls?.stop(); NSApplication.shared.terminate(nil) }
}
@main private enum WidgetMain {
    static func main() {
        if CommandLine.arguments.contains("--check-controls") {
            precondition(JevControls.validSecret("example-test-token"))
            precondition(!JevControls.validSecret(""))
            precondition(!JevControls.validSecret("x\ny"))
            precondition(!JevControls.validSecret(String(repeating:"x",count:4097)))
            precondition(JevControls.summary("{\"error\":\"voorbeeldfout\"}",code:1) == "voorbeeldfout")
            precondition(JevControls.summary("",code:130).contains("gestopt"))
            precondition(JevControls.summary("{\"execution\":{\"status\":\"expired\",\"message\":\"Te laat\"}}",code:0).contains("Te laat"))
            print("Widget controls: invoer- en resultaatcontroles geslaagd; geen Sleutelhanger of API benaderd.")
            return
        }
        if CommandLine.arguments.contains("--inspect") {
            let model = Monitor(root:Bundle.main.bundleURL.deletingLastPathComponent())
            let summary: Object = ["selectedID":model.selectedID,"records":model.runs.map { run -> Object in
                ["id":run.id,"status":run.status,"version":run.version,"synthetic":run.synthetic,
                 "questionCount":run.questions.count,"hasAnswer":run.questions.keys.contains{!run.answer($0).isEmpty}]
            }]
            print(pretty(summary)); return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate(); app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
