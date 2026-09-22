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
    @Published var sessionAction = ""
    @Published var sessionPhase = ""
    let root: URL
    private var timer: Timer?
    private var lastNewest = ""
    private var lastEventFiles = ""
    init(root: URL) {
        self.root = root
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval:0.2, repeats:true) { [weak self] _ in self?.refresh() }
    }
    var selected: Run? { runs.first(where:{$0.id == selectedID}) }
    func refresh() {
        let evidence = root.appendingPathComponent("evidence")
        if let status = readObject(evidence.appendingPathComponent("dj-session-status.json")) {
            let names = ["next_track_prepared":"Volgende track klaar", "aligned_start":"Rode dots gelijk · mix gestart",
                         "paired_bass_step":"Bass overdragen", "transition_completed":"Volgende overgang voorbereiden",
                         "session_stopped":"Gestopt", "session_blocked":"Bediening onderbroken"]
            var message = string(status["message"]).isEmpty ? (names[string(status["event"])] ?? "Voorbereiden") : string(status["message"])
            if let title = status["title"] as? String { message += " · " + title }
            if sessionAction != message { sessionAction = message }
            if sessionPhase != string(status["status"]) { sessionPhase = string(status["status"]) }
        }
        let events = evidence.appendingPathComponent("jev-events")
        let files = ((try? FileManager.default.contentsOfDirectory(at:events,includingPropertiesForKeys:nil)) ?? [])
            .filter{$0.pathExtension == "json"}.sorted{modified($0) > modified($1)}.prefix(40)
        // A fast refresh must not repeatedly decode the same answers and reshuffle
        // equal-probability rows. Only changed event files rebuild the questions.
        let signature = files.map { $0.lastPathComponent + ":" + String(modified($0)) }.joined(separator:"|")
            + String(modified(evidence.appendingPathComponent("jev-live-example.json")))
            + String(modified(evidence.appendingPathComponent("jev-reactive-preview.json")))
        if signature == lastEventFiles { return }
        lastEventFiles = signature
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
private let widgetBackground = Color(red:0.065,green:0.08,blue:0.095)
private let backgroundPhoto: NSImage? = {
    guard let url = Bundle.main.url(forResource:"dj-jev-background",withExtension:"png") else { return nil }
    return NSImage(contentsOf:url)
}()
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
    @StateObject private var probe = JevProbeControls()
    private func candidateTitle(_ choice: String, run: Run) -> String? {
        if let title = object(object(run.state["candidates"])[choice])["title"] as? String { return title }
        if let candidates = run.state["candidates"] as? [Object],
           let candidate = candidates.first(where: { "TRACK_" + string($0["id"]) == choice || string($0["id"]) == choice }) {
            return candidate["title"] as? String
        }
        return nil
    }
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
    private func answerLabel(_ id: String, run: Run) -> String {
        let answer = run.answer(id)
        let choice = string(answer["choice"])
        if choice.isEmpty { return run.status == "pending" ? "Jev denkt…" : "Nog geen antwoord" }
        if choice == "hold" && id == "transport" { return "Muziek laten lopen" }
        if let title = candidateTitle(choice,run:run) { return title }
        if id == "length", Int(choice) != nil { return choice + " maten" }
        let labels = ["none":"Geen wijziging", "hold":"Even wachten", "mix":"Overgang voortzetten", "start_A":"Deck A starten",
                      "start_B":"Deck B starten", "stop_A":"Deck A stoppen", "handover_to_B":"Bass overdragen naar B",
                      "stop_B":"Deck B stoppen", "load_A":"Nummer laden op A", "load_B":"Nummer laden op B",
                      "play_A":"Deck A starten", "play_B":"Deck B starten", "prepare_A":"Deck A voorbereiden", "prepare_B":"Deck B voorbereiden",
                      "reset_A":"EQ van A herstellen", "reset_B":"EQ van B herstellen", "align_A":"Beats van A gelijkzetten", "align_B":"Beats van B gelijkzetten",
                      "mix_A":"Overgang naar A", "mix_B":"Overgang naar B", "mix_center":"Beide decks mengen",
                      "bass_A":"Bass overdragen naar A", "bass_B":"Bass overdragen naar B", "bass_balanced":"Bass verdelen",
                      "beats2":"2 beats", "beats4":"4 beats", "beats8":"8 beats", "beats16":"16 beats",
                      "bring_in_B":"B inmixen", "remove_A":"A uitmixen", "wait":"Wachten"]
        return labels[choice] ?? optionLabel(choice,question:object(run.questions[id]),run:run)
    }
    private func compactQuestion(_ id: String, _ question: Object) -> String {
        let labels = ["bass":"Bass overdragen?", "levels":"Faders bewegen?", "transport":"Wat doet Jev nu?",
                      "track":"Welk nummer volgt?", "opening_track":"Welk nummer starten?", "next_track":"Welk nummer volgt?", "action":"Wat doet Jev nu?", "dj_action":"Wat doet Jev nu?", "gesture":"Hoe snel deze beweging?", "duration":"Hoe snel deze beweging?", "crossfader":"Waarheen met de fader?", "length":"Hoe lang mixen?"]
        let instructions = object(question["instructions"])
        let task = string(instructions["task"]).isEmpty ? string(question["instructions"]) : string(instructions["task"])
        return labels[id] ?? (task.isEmpty ? id : task.firstIndex(of:"?").map { String(task[...$0]) } ?? task)
    }
    private func choiceLabel(_ choice: String, id: String, run: Run) -> String {
        if let title = candidateTitle(choice,run:run) { return title }
        if id == "length", Int(choice) != nil { return choice + " maten" }
        let labels = ["none":"Geen wijziging", "hold":"Zo houden", "mix":"Overgang voortzetten", "start_A":"Deck A starten",
                      "start_B":"Deck B starten", "stop_A":"Deck A stoppen", "handover_to_B":"Bass naar B",
                      "bring_in_B":"B inmixen", "remove_A":"A uitmixen", "wait":"Wachten"]
        return labels[choice] ?? optionLabel(choice,question:object(run.questions[id]),run:run)
    }
    private func sortedChoices(_ probabilities: Object) -> [String] {
        probabilities.keys.sorted {
            let left = probabilities[$0] as? Double ?? 0
            let right = probabilities[$1] as? Double ?? 0
            return left == right ? $0 < $1 : left > right
        }
    }
    private func visibleChoices(_ probabilities: Object, choice: String) -> [String] {
        let sorted = sortedChoices(probabilities)
        // The chosen answer stays visible, even when many candidates tie.
        return choice.isEmpty ? Array(sorted.prefix(4)) : [choice] + Array(sorted.filter{$0 != choice}.prefix(3))
    }
    private func isMixBranch(_ id: String, run: Run) -> Bool {
        ["crossfader","bass","duration"].contains(id)
            && object(object(run.questions["transport"])["criteria"])["mix"] != nil
    }
    private func isLoadBranch(_ id: String, run: Run) -> Bool {
        let transport = object(object(run.questions["transport"])["criteria"])
        return id == "next_track" && (transport["load_A"] != nil || transport["load_B"] != nil)
    }
    private func orderedQuestions(_ run: Run) -> [String] {
        let order = ["transport":0,"next_track":1,"crossfader":2,"bass":3,"duration":4]
        return run.questions.keys.sorted {
            let a = order[$0] ?? 5, b = order[$1] ?? 5
            return a == b ? $0 < $1 : a < b
        }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack {
                Image(systemName:"waveform").foregroundStyle(mint)
                Text("DJ Jev").font(.system(size:15,weight:.semibold,design:.rounded))
                Spacer()
                if let run = monitor.selected {
                    // A timestamp distinguishes old answers from live activity without an extra status panel.
                    Text(timeLabel(run)).font(.system(size:10,design:.monospaced)).foregroundStyle(muted)
                }
            }
            if let run = monitor.selected {
                ScrollView {
                    VStack(alignment:.leading,spacing:10) {
                        HStack(spacing:6) {
                            Circle().fill(run.status == "pending" ? Color.orange : muted).frame(width:6,height:6)
                            Text(run.status == "pending" ? "Nieuwe vraag aan Jev…" : "Laatste antwoord · " + timeLabel(run))
                                .font(.system(size:10)).foregroundStyle(muted)
                            if let seconds = run.result["request_seconds"] as? Double {
                                Spacer()
                                Text(String(format:"%.2f s",seconds)).font(.system(size:10,design:.monospaced)).foregroundStyle(muted)
                            }
                        }
                        if run.questions.isEmpty || run.status == "error" {
                            HStack(alignment:.top,spacing:8) {
                                Image(systemName:run.status == "error" ? "exclamationmark.circle" : "info.circle").foregroundStyle(.orange)
                                Text(!run.error.isEmpty ? run.error : string(run.result["message"]))
                                    .font(.system(size:12)).foregroundStyle(muted)
                            }
                        }
                        ForEach(orderedQuestions(run),id:\.self) { id in
                            let question = object(run.questions[id])
                            let answer = run.answer(id)
                            let choice = string(answer["choice"])
                            let probabilities = object(answer["probabilities"])
                            let mixBranch = isMixBranch(id,run:run)
                            let loadBranch = isLoadBranch(id,run:run)
                            let transportChoice = string(run.answer("transport")["choice"])
                            let unused = !transportChoice.isEmpty &&
                                ((mixBranch && transportChoice != "mix") ||
                                 (loadBranch && !["load_A","load_B"].contains(transportChoice)))
                            let questionLabel = loadBranch ? "Bij laden: welk nummer?" :
                                (mixBranch ? "Bij mengen: " : "") + compactQuestion(id,question)
                            let answerTint = unused ? muted : mint
                            VStack(alignment:.leading,spacing:9) {
                                HStack(alignment:.top,spacing:8) {
                                    Image(systemName:choice.isEmpty ? "ellipsis.circle" : unused ? "minus.circle" : "checkmark.circle.fill")
                                        .foregroundStyle(choice.isEmpty ? Color.orange : answerTint)
                                    Text(questionLabel)
                                        .font(.system(size:13,weight:.medium)).foregroundStyle(unused ? muted : Color.white)
                                }
                                if unused {
                                    Text("Niet uitgevoerd · Jev koos " + answerLabel("transport",run:run))
                                        .font(.system(size:10)).foregroundStyle(muted)
                                } else if id == "next_track" && run.questions["dj_action"] != nil {
                                    Text("Gebruikt wanneer Jev kiest om te laden").font(.system(size:10)).foregroundStyle(muted)
                                } else if id == "gesture" {
                                    Text("Gebruikt bij een fader- of bassbeweging").font(.system(size:10)).foregroundStyle(muted)
                                }
                                Text(answerLabel(id,run:run))
                                    .font(.system(size:20,weight:.semibold,design:.rounded)).foregroundStyle(answerTint)
                                ForEach(visibleChoices(probabilities,choice:choice),id:\.self) { option in
                                    let probability = probabilities[option] as? Double ?? 0
                                    VStack(alignment:.leading,spacing:4) {
                                        HStack {
                                            Text(choiceLabel(option,id:id,run:run)).lineLimit(1)
                                            Spacer()
                                            Text(String(format:"%.0f%%",probability*100)).monospacedDigit()
                                        }.font(.system(size:10)).foregroundStyle(option == choice ? answerTint : muted)
                                        ProgressView(value:min(1,max(0,probability))).tint(option == choice ? answerTint : Color.white.opacity(0.22))
                                    }
                                }
                                if probabilities.count > 4 {
                                    Text("\(probabilities.count) mogelijkheden beoordeeld").font(.system(size:10)).foregroundStyle(muted)
                                }
                            }.padding(12).frame(maxWidth:.infinity,alignment:.leading)
                            .background(widgetBackground.opacity(0.72),in:RoundedRectangle(cornerRadius:12))
                            .overlay(RoundedRectangle(cornerRadius:12).stroke(choice.isEmpty ? Color.orange.opacity(0.3) : answerTint.opacity(0.22),lineWidth:1))
                        }
                    }
                }
            } else {
                HStack { Image(systemName:"ellipsis.circle").foregroundStyle(.orange); Text("Wachten op de eerste vraag…").font(.system(size:12)).foregroundStyle(muted) }
                Spacer(minLength:0)
            }
            VStack(alignment:.leading,spacing:5) {
                HStack(spacing:8) {
                    Button {
                        monitor.followLatest = true
                        probe.toggle()
                    } label: {
                        Label(probe.running ? "Stop" : "Start", systemImage:probe.running ? "stop.fill" : "play.fill")
                    }
                    .buttonStyle(.borderedProminent).tint(mint.opacity(0.8))
                    .disabled(probe.busy)
                    if probe.busy { ProgressView().controlSize(.small) }
                    Spacer(minLength:0)
                }
                if !monitor.sessionAction.isEmpty {
                    Text(monitor.sessionAction).font(.system(size:11)).foregroundStyle(["trial_blocked", "set_blocked", "provider_retry", "observation_retry"].contains(monitor.sessionPhase) ? Color.orange : mint)
                        .fixedSize(horizontal:false,vertical:true)
                }
                if !probe.status.isEmpty {
                    Text(probe.status).font(.system(size:10)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
                }
            }
        }
        .padding(12).frame(minWidth:276,minHeight:160)
        .background(alignment:.bottomTrailing) {
            if let backgroundPhoto {
                Image(nsImage:backgroundPhoto)
                    .resizable().scaledToFit()
                    .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.bottomTrailing)
                    .opacity(0.5).allowsHitTesting(false).accessibilityHidden(true)
            }
        }
        .background(widgetBackground).clipped().preferredColorScheme(.dark)
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
    // The current widget contains display and mouse buttons, no text entry.
    // Keep Rekordbox's keyboard focus while Start/Stop and scrolling work here.
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var panel: FloatingPanel?
    private var monitor: Monitor?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let editItem = NSMenuItem(title:"Wijzig",action:nil,keyEquivalent:"")
        let editMenu = NSMenu(title:"Wijzig")
        editMenu.addItem(withTitle:"Knip",action:#selector(NSText.cut(_:)),keyEquivalent:"x")
        editMenu.addItem(withTitle:"Kopieer",action:#selector(NSText.copy(_:)),keyEquivalent:"c")
        editMenu.addItem(withTitle:"Plak",action:#selector(NSText.paste(_:)),keyEquivalent:"v")
        editMenu.addItem(withTitle:"Selecteer alles",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a")
        editItem.submenu = editMenu; menu.addItem(editItem); NSApplication.shared.mainMenu = menu
        let root = URL(fileURLWithPath:"/private/tmp/rekordbox-bridge-\(getuid())/widget",isDirectory:true)
        let model = Monitor(root:root); monitor = model
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
        let height = min(490.0,visible.height-40)
        let panel = FloatingPanel(contentRect:NSRect(x:visible.maxX-350,y:visible.maxY-height-20,width:330,height:height),
                                  styleMask:[.titled,.closable,.resizable,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.title = "DJ Jev"
        panel.titleVisibility = .hidden; panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true; panel.level = .floating; panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true; panel.minSize = NSSize(width:300,height:230)
        panel.delegate = self; panel.isReleasedWhenClosed = false
        panel.contentView = NSHostingView(rootView:Inspector(monitor:model))
        self.panel = panel
        panel.orderFrontRegardless()
    }
    func windowWillClose(_ notification: Notification) { NSApplication.shared.terminate(nil) }
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
            let model = Monitor(root:URL(fileURLWithPath:"/private/tmp/rekordbox-bridge-\(getuid())/widget",isDirectory:true))
            let summary: Object = ["startButton":"Start proef","testMode":"contextual_start_trial","selectedID":model.selectedID,"records":model.runs.map { run -> Object in
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
