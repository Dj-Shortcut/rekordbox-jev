import AppKit
import SwiftUI
import CoreFoundation

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
    var execution: Object = [:]
    var payload: Object { object(request["payload"]) }
    var questions: Object { object(payload["questions"]) }
    var state: Object { object(payload["state"]) }
    var synthetic: Bool { string(request["provenance"]) == "synthetic_example" }
    var version: Int { object(state["tutorial_guidance"])["version"] as? Int ?? 1 }
    var statusText: String {
        if synthetic { return "Voorbeeldgegevens · geen live Jev-antwoord" }
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
    @Published var sessionRunID = ""
    let root: URL
    private var timer: Timer?
    private var lastNewest = ""
    private var lastEventFiles = ""
    init(root: URL) {
        self.root = root
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval:0.2, repeats:true) { [weak self] _ in self?.refresh() }
    }
    var selected: Run? {
        let run = runs.first(where:{$0.id == selectedID})
        // History stays inspectable when frozen, but an earlier session's
        // answer must not appear as the current session's decision.
        if followLatest && !sessionRunID.isEmpty && run?.id.hasPrefix(sessionRunID+"-") != true { return nil }
        return run
    }
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
            if sessionRunID != string(status["run_id"]) { sessionRunID = string(status["run_id"]) }
        }
        let events = evidence.appendingPathComponent("jev-events")
        let files = ((try? FileManager.default.contentsOfDirectory(at:events,includingPropertiesForKeys:nil)) ?? [])
            .filter{$0.pathExtension == "json"}.sorted{modified($0) > modified($1)}.prefix(200)
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
                              status:status,request:request,result:result,error:string(event["error"]),label:string(request["kind"]) == "jev_reactive_request" ? "DJ-beslissing" : "Aanroep", execution:object(event["execution"])))
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
        // Keep the latest complete answer on screen while the next request is
        // in flight. Otherwise rapid polling replaces readable answers with
        // spinners for most of the set.
        let newest = values.first(where:{$0.status == "answered"})?.id ?? values.first?.id ?? ""
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
        // Keep a frozen request readable even after it leaves the live history window.
        if !followLatest, let frozen = selected, !values.contains(where:{$0.id == frozen.id}) {
            values.append(frozen)
        }
        if selectedID.isEmpty || !values.contains(where:{$0.id == selectedID}) || (followLatest && newest != lastNewest && !newest.isEmpty) {
            selectedID = newest.isEmpty ? (values.first?.id ?? "") : newest
        }
        lastNewest = newest
        runs = values
        loadIssue = values.isEmpty ? "Er zijn nog geen leesbare vragen of antwoorden beschikbaar." : ""
    }
}

private let mint = Color(red:0.45,green:1.0,blue:0.8)
private let muted = Color(red:0.74,green:0.77,blue:0.8)
private let widgetBackground = Color(red:0.035,green:0.045,blue:0.055)
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
private struct WidgetHeightKey: PreferenceKey {
    static var defaultValue: [String: CGFloat] = [:]
    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue(),uniquingKeysWith: { _, latest in latest })
    }
}
private extension View {
    func measureWidgetHeight(_ name: String) -> some View {
        background(GeometryReader { geometry in
            Color.clear.preference(key:WidgetHeightKey.self,value:[name:geometry.size.height])
        })
    }
}

private struct Inspector: View {
    @ObservedObject var monitor: Monitor
    var readOnly = false
    var fitContent: (CGFloat) -> Void = { _ in }
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
    private func eqChoiceLabel(_ choice: String, id: String) -> String? {
        if id == "bass" {
            return ["hold":"Bassbalans zo houden", "A":"Bass naar A", "B":"Bass naar B",
                    "balanced":"Bass verdelen", "A_gentle":"Lichte basswissel naar A",
                    "B_gentle":"Lichte basswissel naar B", "A_deep":"Diepe basswissel naar A",
                    "B_deep":"Diepe basswissel naar B"][choice]
        }
        guard id == "mid" || id == "high" else { return nil }
        let band = id == "mid" ? "Midden" : "Hoog"
        return ["hold":band + " zo houden", "neutral":band + " herstellen",
                "A_soft":band + " A licht terug", "B_soft":band + " B licht terug",
                "A_cut":band + " A verder terug", "B_cut":band + " B verder terug"][choice]
    }
    private func timingChoiceLabel(_ choice: String, id: String) -> String? {
        if id == "entry_slot" {
            return choice == "fallback" ? "Ander passend inzetmoment" : "Inzet op " + choice.uppercased()
        }
        if id == "arrangement_fit" {
            return ["supported":"Naar mogelijke drop opbouwen", "alternative":"Andere overgang nodig",
                    "unknown":"Arrangement onzeker"][choice]
        }
        guard ["kick_pattern","last_section","post_peak","entry_fit"].contains(id) else { return nil }
        return ["plausible":"Kickpatroon aannemelijk", "unclear":"Patroon onzeker",
                "supported":"Laatste kicksectie aannemelijk", "unsuitable":"Geen geschikt moment",
                "unknown":"Onvoldoende informatie", "past":"Hoogtepunt vermoedelijk voorbij",
                "ahead":"Nog ontwikkeling verwacht", "suitable":"Geschikt om in te mixen",
                "protect":"Passage nog laten spelen"][choice]
    }
    private func clockLabel(_ seconds: Double) -> String {
        let value = max(0, Int(seconds.rounded()))
        return String(format:"%d:%02d",value / 60,value % 60)
    }
    private func answerLabel(_ id: String, run: Run) -> String {
        let answer = run.answer(id)
        let choice = string(answer["choice"])
        if choice.isEmpty { return run.status == "pending" ? "Jev denkt…" : "Nog geen antwoord" }
        if id == "crossfader" { return ["A":"Deck A laten horen","center":"Beide decks mengen","B":"Deck B laten horen","hold":"Fader zo houden"][choice] ?? choice }
        if let label = eqChoiceLabel(choice, id:id) { return label }
        if let label = timingChoiceLabel(choice, id:id) { return label }
        if choice == "hold" && id == "transport" { return "Muziek laten lopen" }
        if let title = candidateTitle(choice,run:run) { return title }
        if id == "length", Int(choice) != nil { return choice + " maten" }
        let labels = ["none":"Geen wijziging", "hold":"Even wachten", "echo_A":"Kort echo-accent op A", "echo_B":"Kort echo-accent op B", "mix":"Overgang voortzetten", "start_A":"Deck A starten",
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
        if id == "entry_slot" { return "Bij starten: welk inzetpunt?" }
        let labels = ["arrangement_fit":"Past opbouwen naar de inkomende drop?", "kick_pattern":"Waar keren de kicks terug?", "last_section":"Is dit de laatste kicksectie?", "post_peak":"Is het hoogtepunt voorbij?", "entry_fit":"Past inmixen op dit moment?", "mid":"Ruimte maken in het midden?", "high":"Hoe helder mag het klinken?", "bass":"Hoe wisselt de bass?", "levels":"Faders bewegen?", "transport":"Wat doet Jev nu?",
                      "track":"Welk nummer volgt?", "opening_track":"Welk nummer starten?", "next_track":"Welk nummer volgt?", "action":"Wat doet Jev nu?", "dj_action":"Wat doet Jev nu?", "gesture":"Hoe snel deze beweging?", "duration":"Hoe snel deze beweging?", "crossfader":"Waarheen met de fader?", "length":"Hoe lang mixen?"]
        let instructions = object(question["instructions"])
        let task = string(instructions["task"]).isEmpty ? string(question["instructions"]) : string(instructions["task"])
        return labels[id] ?? (task.isEmpty ? id : task.firstIndex(of:"?").map { String(task[...$0]) } ?? task)
    }
    private func choiceLabel(_ choice: String, id: String, run: Run) -> String {
        if id == "crossfader" { return ["A":"Deck A laten horen","center":"Beide decks mengen","B":"Deck B laten horen","hold":"Fader zo houden"][choice] ?? choice }
        if let label = eqChoiceLabel(choice, id:id) { return label }
        if let label = timingChoiceLabel(choice, id:id) { return label }
        if let title = candidateTitle(choice,run:run) { return title }
        if id == "length", Int(choice) != nil { return choice + " maten" }
        let labels = ["none":"Geen wijziging", "hold":"Zo houden", "echo_A":"Kort echo-accent op A", "echo_B":"Kort echo-accent op B", "mix":"Overgang voortzetten", "start_A":"Deck A starten",
                      "start_B":"Deck B starten", "stop_A":"Deck A stoppen", "stop_B":"Deck B stoppen", "handover_to_B":"Bass naar B",
                      "bring_in_B":"B inmixen", "remove_A":"A uitmixen", "wait":"Wachten",
                      "load_A":"Nummer laden op A", "load_B":"Nummer laden op B",
                      "play_A":"Deck A starten", "play_B":"Deck B starten", "prepare_A":"Deck A voorbereiden", "prepare_B":"Deck B voorbereiden",
                      "reset_A":"EQ van A herstellen", "reset_B":"EQ van B herstellen", "align_A":"Beats van A gelijkzetten", "align_B":"Beats van B gelijkzetten",
                      "beats2":"2 beats", "beats4":"4 beats", "beats8":"8 beats", "beats16":"16 beats"]
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
        ["crossfader","bass","mid","high","duration"].contains(id)
            && object(object(run.questions["transport"])["criteria"])["mix"] != nil
    }
    private func isLoadBranch(_ id: String, run: Run) -> Bool {
        let transport = object(object(run.questions["transport"])["criteria"])
        return id == "next_track" && (transport["load_A"] != nil || transport["load_B"] != nil)
    }
    private func orderedQuestions(_ run: Run) -> [String] {
        let order = ["kick_pattern":0,"last_section":1,"post_peak":2,"entry_fit":3,"arrangement_fit":4,"entry_slot":5,"transport":6,"next_track":7,"crossfader":8,"bass":9,"mid":10,"high":11,"duration":12]
        return run.questions.keys.sorted {
            let a = order[$0] ?? 13, b = order[$1] ?? 13
            return a == b ? $0 < $1 : a < b
        }
    }
    private func executionLabel(_ run: Run) -> String {
        if run.status == "pending" { return "Jev kiest…" }
        if run.status == "preview" { return "Nog niet verstuurd" }
        switch string(run.execution["status"]) {
        case "dispatch": return "Wordt uitgevoerd"
        case "verified":
            guard run.execution["verified"] as? Bool == true else { return "Bediening niet bevestigd" }
            return object(run.execution["decision"])["transport"] as? String == "hold"
            ? "Muziek blijft lopen" : "Bediening bevestigd"
        case "ignored": return "Niet uitgevoerd · verworpen"
        case "execution_deferred": return "Uitgesteld"
        case "execution_reconciling": return "Mixer opnieuw controleren"
        case "execution_reconciled": return "Hersteld · nieuwe beslissing"
        case "error": return "Uitvoering niet bevestigd"
        default: return "Geen uitvoeringsgegevens"
        }
    }
    private func readable(_ value: Any?) -> String {
        guard let value, !(value is NSNull) else { return "Onbekend" }
        if let boolean = value as? NSNumber, CFGetTypeID(boolean) == CFBooleanGetTypeID() {
            return boolean.boolValue ? "Ja" : "Nee"
        }
        return String(describing:value)
    }
    private func freeze() { monitor.followLatest = false }

    private var liveLabel: String {
        if readOnly { return "Voorbeeld" }
        if !monitor.followLatest { return "Beeldpauze" }
        if monitor.sessionPhase == "preparing" && probe.running { return "Voorbereiden" }
        if monitor.sessionPhase.contains("blocked") { return "Onderbroken" }
        return probe.running ? "Live" : probe.connected ? "Verbonden" : "Niet verbonden"
    }
    private var liveTint: Color {
        monitor.sessionPhase.contains("blocked") ? .orange : probe.connected && monitor.followLatest ? mint : muted
    }
    private func resumeFollowing() {
        monitor.followLatest = true
        if let latest = monitor.runs.first(where:{$0.status == "answered"}) {
            monitor.selectedID = latest.id
        }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:10) {
            HStack(spacing:10) {
                Text("Jev").font(.system(size:20,weight:.semibold,design:.rounded))
                HStack(spacing:5) {
                    Circle().fill(liveTint).frame(width:5,height:5)
                    Text(liveLabel).font(.system(size:10,weight:.medium)).foregroundStyle(liveTint)
                }
                Spacer()
                Button {
                    if monitor.followLatest { freeze() } else { resumeFollowing() }
                } label: { Image(systemName:monitor.followLatest ? "pause" : "play") }
                .buttonStyle(.plain)
                .accessibilityLabel(monitor.followLatest ? "Beeld pauzeren" : "Live volgen")
                .help("Alleen het beeld pauzeren; de DJ-set blijft doorgaan.")
                Button {
                    NSApp.windows.first(where:{$0.title == "DJ Jev"})?.miniaturize(nil)
                } label: { Image(systemName:"minus") }
                .buttonStyle(.plain).accessibilityLabel("Minimaliseren")
                .help("Verberg het venster; een draaiende DJ-set blijft doorgaan.")
                Menu {
                    Button("Live vragen") { monitor.tab = 0; resumeFollowing() }
                    Button("Context") { monitor.tab = 1 }
                    Button("DJ-checklist") { monitor.tab = 2 }
                    Button("Volledige gegevens") { monitor.tab = 3 }
                    Menu("Geschiedenis") {
                        ForEach(Array(monitor.runs.prefix(20))) { run in
                            Button(timeLabel(run)) { monitor.selectedID = run.id; monitor.tab = 0; freeze() }
                        }
                    }
                    Divider()
                    Button("Vergroten of herstellen") {
                        NSApp.windows.first(where:{$0.title == "DJ Jev"})?.zoom(nil)
                    }
                } label: { Image(systemName:"ellipsis") }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Meer weergaven")
            }
            if monitor.tab != 0 {
                HStack {
                    Button { monitor.tab = 0 } label: { Label("Vragen",systemImage:"chevron.left") }
                        .buttonStyle(.plain)
                    Spacer()
                    Text([1:"Context",2:"DJ-checklist",3:"Volledige gegevens"][monitor.tab] ?? "")
                        .font(.system(size:12)).foregroundStyle(muted)
                }
            }
            ScrollView(.vertical) {
                VStack(alignment:.leading,spacing:12) {
                    if probe.running && monitor.sessionPhase == "preparing" {
                        Text(monitor.sessionAction).font(.system(size:15,weight:.medium)).foregroundStyle(mint)
                            .fixedSize(horizontal:false,vertical:true)
                    } else if let run = monitor.selected {
                        if monitor.tab == 0 {
                            let ids = orderedQuestions(run)
                            VStack(alignment:.leading,spacing:12) {
                                ForEach(Array(stride(from:0,to:ids.count,by:2)),id:\.self) { index in
                                    HStack(alignment:.top,spacing:16) {
                                        answerRow(ids[index],run:run).frame(maxWidth:.infinity,alignment:.leading)
                                        if index+1 < ids.count {
                                            answerRow(ids[index+1],run:run).frame(maxWidth:.infinity,alignment:.leading)
                                        } else { Color.clear.frame(height:1).frame(maxWidth:.infinity) }
                                    }
                                }
                            }
                        } else if monitor.tab == 1 {
                            requestSummary(run)
                            contextView(run)
                        } else if monitor.tab == 2 {
                            checklistView(run)
                        } else {
                            Card {
                                Text("Exacte aanvraag aan Jev").font(.headline)
                                Text(pretty(run.payload)).font(.system(size:11,design:.monospaced))
                                    .textSelection(.enabled).fixedSize(horizontal:false,vertical:true)
                            }
                            Card {
                                Text("Exact antwoord van Jev").font(.headline)
                                Text(pretty(run.result)).font(.system(size:11,design:.monospaced))
                                    .textSelection(.enabled).fixedSize(horizontal:false,vertical:true)
                                Text("Uitvoering").font(.headline)
                                Text(pretty(run.execution)).font(.system(size:11,design:.monospaced))
                                    .textSelection(.enabled).fixedSize(horizontal:false,vertical:true)
                            }
                        }
                    } else if monitor.tab == 2 {
                        checklistView(nil)
                    } else {
                        Text("Wachten op Jevs eerste antwoord…").foregroundStyle(muted)
                    }
                }.padding(.trailing,4).padding(.vertical,4)
                    .measureWidgetHeight("content")
            }
            .scrollIndicators(.visible)
            .frame(maxWidth:.infinity,maxHeight:.infinity)
            .measureWidgetHeight("viewport")
            Text(verbatim:buildLabel)
                .font(.system(size:9)).foregroundStyle(muted)
            HStack(alignment:.center,spacing:12) {
                Button {
                    resumeFollowing()
                    probe.toggle()
                } label: {
                    Label(probe.running ? "Stop set" : "Start set",systemImage:probe.running ? "stop.fill" : "play.fill")
                }.buttonStyle(.borderedProminent).tint(mint.opacity(0.8)).disabled(probe.busy || readOnly)
                Spacer(minLength:0)
                if monitor.sessionPhase != "preparing", let run = monitor.selected {
                    Text(probe.running ? executionLabel(run) : "Laatste antwoord · " + timeLabel(run))
                        .font(.system(size:10)).foregroundStyle(muted)
                        .fixedSize(horizontal:false,vertical:true)
                }
            }
            if monitor.sessionPhase.contains("blocked") || (!probe.status.isEmpty && probe.status != "DJ Jev gestopt · muziek blijft spelen") {
                Text(monitor.sessionPhase.contains("blocked") && !probe.busy ? monitor.sessionAction :
                     probe.status.isEmpty ? monitor.sessionAction : probe.status)
                    .font(.system(size:11)).foregroundStyle(.orange).fixedSize(horizontal:false,vertical:true)
            }
        }
        .padding(12).frame(minWidth:352,minHeight:230,maxHeight:.infinity)
        .measureWidgetHeight("total")
        .onPreferenceChange(WidgetHeightKey.self) { heights in
            if let content = heights["content"], let viewport = heights["viewport"], let total = heights["total"], content > 0 {
                fitContent(ceil(content + total - viewport))
            }
        }
        .background(alignment:.bottomTrailing) {
            if let backgroundPhoto {
                Image(nsImage:backgroundPhoto).resizable().scaledToFit()
                    .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.bottomTrailing)
                    .opacity(0.32)
                    .overlay {
                        LinearGradient(colors:[widgetBackground.opacity(0.65), .clear],
                                       startPoint:.topLeading,endPoint:.bottomTrailing)
                    }
                    .allowsHitTesting(false).accessibilityHidden(true)
            }
        }
        .background(widgetBackground).preferredColorScheme(.dark)
    }

    @ViewBuilder private func requestSummary(_ run: Run) -> some View {
        VStack(alignment:.leading,spacing:5) {
            HStack(alignment:.top) {
                Text("\(run.questions.count) vragen · \(run.questions.keys.filter{!run.answer($0).isEmpty}.count) antwoorden")
                Spacer()
                Text(timeLabel(run)).monospacedDigit()
            }.font(.system(size:11)).foregroundStyle(muted)
            let entry = object(object(run.state["musical_timing"])["entry_preference"])
            let candidate = object(entry["candidate"])
            if let start = candidate["start_seconds"] as? Double {
                Text("Mogelijke laatste kicksectie · " + clockLabel(start))
                    .font(.system(size:14,weight:.semibold)).fixedSize(horizontal:false,vertical:true)
                if let wait = candidate["seconds_until_start"] as? Double,
                   let budget = candidate["suggested_overlap_seconds"] as? Double {
                    Text((string(candidate["position"]) == "after" ? "Sectie voorbij" : wait > 0 ? "Nog " + clockLabel(wait) : "Sectie bereikt")
                         + " · mixtijd tot " + clockLabel(budget))
                        .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
                }
                Text("Geschat uit bronmetingen · exacte frase onbekend")
                    .font(.system(size:10)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
            } else if run.questions["last_section"] != nil {
                Text("Geen duidelijke laatste kicksectie · terugval op resterende tijd")
                    .font(.system(size:12)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
            }
            let timing = object(run.state["musical_timing"])
            let grid = object(timing["entry_grid"])
            if let preferred = grid["selected_preference"] as? String {
                Text("Voorkeur " + preferred.uppercased() + " · 8 beats / 2 maten per punt")
                    .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
            }
            let arrangement = object(timing["arrangement"])
            if let seconds = arrangement["seconds_to_incoming_drop"] as? Double, seconds > 0 {
                Text("Mogelijke inkomende drop over " + clockLabel(seconds) + " · schatting")
                    .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
            }
            Text(executionLabel(run)).font(.system(size:12,weight:.medium)).foregroundStyle(mint)
                .fixedSize(horizontal:false,vertical:true)
            if !run.error.isEmpty { Text(run.error).font(.system(size:11)).foregroundStyle(.orange).fixedSize(horizontal:false,vertical:true) }
        }
    }

    @ViewBuilder private func answerRow(_ id: String, run: Run) -> some View {
        let question = object(run.questions[id])
        let answer = run.answer(id)
        let choice = string(answer["choice"])
        let probability = object(answer["probabilities"])[choice] as? Double
        let transport = string(run.answer("transport")["choice"])
        let unused = !transport.isEmpty && ((isMixBranch(id,run:run) && transport != "mix") ||
            (isLoadBranch(id,run:run) && !["load_A","load_B"].contains(transport)) ||
            (id == "entry_slot" && !["play_A","play_B"].contains(transport)))
        let tint = unused ? muted : mint
        VStack(alignment:.leading,spacing:5) {
            VStack(alignment:.leading,spacing:4) {
                Text(compactQuestion(id,question))
                    .font(.system(size:11,weight:.medium)).foregroundStyle(Color.white.opacity(0.94))
                    .fixedSize(horizontal:false,vertical:true)
                HStack(alignment:.firstTextBaseline,spacing:5) {
                    Text(answerLabel(id,run:run))
                        .font(.system(size:14,weight:.semibold,design:.rounded))
                        .foregroundStyle(tint).fixedSize(horizontal:false,vertical:true).textSelection(.enabled)
                    if unused {
                        Image(systemName:"minus.circle").font(.system(size:11)).foregroundStyle(muted)
                            .help("Dit antwoord is niet gebruikt bij de gekozen actie.")
                            .accessibilityLabel("Niet gebruikt")
                    }
                }
            }.frame(maxWidth:.infinity,alignment:.leading)
            if let probability, probability.isFinite, (0...1).contains(probability) {
                HStack(spacing:5) {
                    Text(String(format:"%.0f%%",probability * 100))
                        .font(.system(size:11,weight:.semibold,design:.rounded)).monospacedDigit().foregroundStyle(tint)
                    GeometryReader { geometry in
                        Capsule().fill(Color.white.opacity(0.16))
                            .overlay(alignment:.leading) {
                                Capsule().fill(tint).frame(width:geometry.size.width * probability)
                            }
                    }.frame(width:36,height:3)
                }
                .accessibilityElement(children:.ignore)
                .accessibilityLabel("Waarschijnlijkheid van het gekozen antwoord")
                .accessibilityValue(String(format:"%.0f procent",probability * 100))
                .help("Jevs waarschijnlijkheid voor dit antwoord binnen de aangeboden opties; geen garantie dat het muzikaal juist is.")
            } else {
                Text("—").foregroundStyle(muted).frame(width:50)
                    .accessibilityLabel("Waarschijnlijkheid niet beschikbaar")
            }
        }
        .padding(.vertical,1)
    }

    private var buildLabel: String {
        let version = Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "?"
        let commit = Bundle.main.object(forInfoDictionaryKey:"JevSourceCommit") as? String ?? "onbekende build"
        let protocolVersion = Bundle.main.object(forInfoDictionaryKey:"JevProtocolVersion") as? Int ?? 0
        return "DJ Jev \(version) · \(commit.prefix(8)) · protocol \(protocolVersion)"
    }

    @ViewBuilder private func contextView(_ run: Run) -> some View {
        let context = object(run.state["dj_context"])
        let situation = string(context["situation"])
        let names = ["opening_or_recovery":"Openen of hervatten", "select_or_prepare":"Opvolger kiezen of voorbereiden",
            "prepared_successor":"Opvolger klaar · instapmoment kiezen", "alignment_needed":"Beats uitlijnen",
            "overlap":"Nummers mengen", "handoff_cleanup":"Overgang afronden en opruimen", "executing":"Bediening bezig"]
        Card {
            Text("Wat Jev bij deze aanvraag wist").font(.headline)
            Text(names[situation] ?? "Geen situatiebeschrijving in deze oudere aanvraag")
                .foregroundStyle(mint).fixedSize(horizontal:false,vertical:true)
            Text("Context bij deze aanvraag")
                .font(.system(size:11)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
        }
        let decks = object(run.state["decks"])
        ForEach(decks.keys.sorted(),id:\.self) { name in
            let deck = object(decks[name])
            let details = object(object(context["decks"])[name])
            let playback: String = "Speelt: \(readable(deck["playing"])) · Positie: \(readable(deck["elapsed"])) s · Resterend: \(readable(deck["remaining"])) s"
            Card {
                Text("Deck " + name + " · " + readable(deck["title"])).font(.headline).fixedSize(horizontal:false,vertical:true)
                Text("Genre: " + readable(details["genre"]) + " · Tempo: " + readable(deck["bpm"]) + " · Toonaard: " + readable(deck["key"]))
                    .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
                Text(verbatim:playback)
                    .font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
                Text("Audioanalyse: " + readable(details["audio_evidence"]) + " · Zang: " + readable(details["vocal_activity"]))
                    .font(.system(size:11)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
            }
        }
        ForEach(["musical_timing","transition","audio_context","continuity","recent_meaningful_actions","candidates"],id:\.self) { key in
            let labels = ["musical_timing":"Timing en overlap", "transition":"Inkomend en uitgaand nummer",
                "audio_context":"Gemeten bass en energie per passage", "continuity":"Continuïteit en gereedheid",
                "recent_meaningful_actions":"Eerder bevestigde acties", "candidates":"Beschikbare opvolgers"]
            Card {
                DisclosureGroup(labels[key] ?? key) {
                    Text(pretty(run.state[key] ?? "Niet beschikbaar in deze aanvraag"))
                        .font(.system(size:11,design:.monospaced)).textSelection(.enabled)
                        .fixedSize(horizontal:false,vertical:true).padding(.top,8)
                }.font(.system(size:13)).tint(mint)
            }
        }
    }

    @ViewBuilder private func checklistView(_ run: Run?) -> some View {
        let context = object(run?.state["dj_context"])
        let active = Set((context["checklist"] as? [Object] ?? []).compactMap{$0["order"] as? Int})
        let url = Bundle.main.url(forResource:"dj-questions",withExtension:"json")
        let data = url.flatMap{try? Data(contentsOf:$0)}
        let questions = data.flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [Object] ?? []
        Card {
            Text("De 18 menselijke DJ-vragen").font(.headline)
            Text("Gemarkeerd = relevant voor deze aanvraag. Dit is een checklist, geen lijst modelantwoorden.")
                .font(.system(size:12)).foregroundStyle(muted).fixedSize(horizontal:false,vertical:true)
        }
        ForEach(questions.indices,id:\.self) { index in
            let item = questions[index]
            let order = item["order"] as? Int ?? index+1
            let statuses = ["active":"Aangesloten op huidige keuzes", "partial":"Gedeeltelijk ondersteund",
                "planned":"Nog te bouwen als aparte keuze", "observation":"Informatie uit de tools", "unsupported":"Bediening nog niet beschikbaar"]
            Card {
                Text("\(order). " + string(item["question"])).font(.system(size:14,weight:.semibold))
                    .foregroundStyle(active.contains(order) ? mint : .white).fixedSize(horizontal:false,vertical:true)
                if active.contains(order) { Pill(text:"Relevant voor deze aanvraag") }
                Text("Nodig: " + string(item["needs"])).font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
                Text("Mogelijkheden: " + string(item["answers"])).font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
                Text(statuses[string(item["status"])] ?? "Onbekend").font(.system(size:11)).foregroundStyle(muted)
            }
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
    private var resizeWork: DispatchWorkItem?
    private var requestedHeight: CGFloat = 0
    private func fitContent(_ height: CGFloat) {
        guard height.isFinite, height > 0, abs(height-requestedHeight) > 1 else { return }
        requestedHeight = height
        resizeWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, let panel = self.panel, !panel.inLiveResize,
                  let screen = panel.screen ?? NSScreen.main else { return }
            let bounds = screen.visibleFrame.insetBy(dx:12,dy:12)
            let chrome = panel.frame.height-panel.contentRect(forFrameRect:panel.frame).height
            let target = min(bounds.height,max(230,height)+chrome)
            guard abs(panel.frame.height-target) > 2 else { return }
            var frame = panel.frame
            let top = min(bounds.maxY,max(bounds.minY+target,frame.maxY))
            frame.origin.y = top-target
            frame.size.height = target
            panel.setFrame(frame,display:true,animate:false)
        }
        resizeWork = work
        DispatchQueue.main.asyncAfter(deadline:.now()+0.25,execute:work)
    }
    func windowDidEndLiveResize(_ notification: Notification) {
        let height = requestedHeight
        requestedHeight = 0
        fitContent(height)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem(title:"DJ Jev",action:nil,keyEquivalent:"")
        let appMenu = NSMenu(title:"DJ Jev")
        appMenu.addItem(withTitle:"Stop DJ Jev",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem(title:"Wijzig",action:nil,keyEquivalent:"")
        let editMenu = NSMenu(title:"Wijzig")
        editMenu.addItem(withTitle:"Knip",action:#selector(NSText.cut(_:)),keyEquivalent:"x")
        editMenu.addItem(withTitle:"Kopieer",action:#selector(NSText.copy(_:)),keyEquivalent:"c")
        editMenu.addItem(withTitle:"Plak",action:#selector(NSText.paste(_:)),keyEquivalent:"v")
        editMenu.addItem(withTitle:"Selecteer alles",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a")
        editItem.submenu = editMenu; menu.addItem(editItem); NSApplication.shared.mainMenu = menu
        let args = CommandLine.arguments
        let previewRoot = args.firstIndex(of:"--evidence-root").flatMap { $0+1 < args.count ? args[$0+1] : nil }
            ?? Bundle.main.object(forInfoDictionaryKey:"JevPreviewRoot") as? String
        let root = URL(fileURLWithPath:previewRoot ?? "/private/tmp/rekordbox-bridge-\(getuid())/widget",isDirectory:true)
        let model = Monitor(root:root); monitor = model
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
        let height = min(380.0,visible.height-70)
        let width = min(400.0,visible.width-40)
        let panel = FloatingPanel(contentRect:NSRect(x:visible.maxX-width-20,y:visible.maxY-height-30,width:width,height:height),
                                  styleMask:[.titled,.closable,.miniaturizable,.resizable,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.title = "DJ Jev"
        panel.titleVisibility = .visible; panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true; panel.level = .floating; panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        panel.isMovableByWindowBackground = false
        panel.contentMinSize = NSSize(width:376,height:230)
        panel.delegate = self; panel.isReleasedWhenClosed = false
        let hosting = NSHostingView(rootView:Inspector(monitor:model,readOnly:previewRoot != nil,fitContent:{ [weak self] height in self?.fitContent(height) }))
        hosting.sizingOptions = []
        panel.contentView = hosting
        panel.setFrameAutosaveName(previewRoot == nil ? "DJJevInspectorV3" : "DJJevInspectorPreviewV3")
        self.panel = panel
        panel.orderFrontRegardless()
    }
    func windowWillClose(_ notification: Notification) { NSApplication.shared.terminate(nil) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        panel?.deminiaturize(nil)
        panel?.orderFrontRegardless()
        return true
    }
}
@main private enum WidgetMain {
    static func main() {
        if CommandLine.arguments.contains("--check-monitor") {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("jev-monitor-test-"+UUID().uuidString)
            let events = root.appendingPathComponent("evidence/jev-events")
            try! FileManager.default.createDirectory(at:events,withIntermediateDirectories:true)
            defer { try? FileManager.default.removeItem(at:root) }
            func record(_ id: String, _ started: Double, _ status: String) {
                let value: Object = ["id":id,"started_at":started,"status":status,
                    "request":["payload_id":id,"payload":["questions":[:],"state":[:]]],
                    "result":status == "answered" ? ["payload_id":id,"answers":[:]] : [:]]
                try! JSONSerialization.data(withJSONObject:value).write(to:events.appendingPathComponent(id+".json"),options:.atomic)
            }
            record("one",1,"answered")
            record("pending",2,"pending")
            let model = Monitor(root:root)
            precondition(model.selectedID == "one", "A pending request must not hide the last answer")
            record("two",3,"answered")
            model.refresh()
            precondition(model.selectedID == "two", "Follow completed answers live")
            model.followLatest = false
            record("three",4,"answered")
            model.refresh()
            precondition(model.selectedID == "two", "Reading freeze must hold its request")
            try! FileManager.default.removeItem(at:events.appendingPathComponent("two.json"))
            record("four",5,"answered")
            model.refresh()
            precondition(model.selectedID == "two" && model.selected != nil, "Frozen answers survive history rotation")
            model.followLatest = true
            record("five",6,"answered")
            model.refresh()
            precondition(model.selectedID == "five")
            let sessionStatus = root.appendingPathComponent("evidence/dj-session-status.json")
            try! JSONSerialization.data(withJSONObject:["run_id":"new-session","status":"preparing",
                "message":"DJ Jev starten…"]).write(to:sessionStatus,options:.atomic)
            model.refresh()
            precondition(model.selected == nil, "Starting a new session must hide old answers even without new event files")
            try! JSONSerialization.data(withJSONObject:["run_id":"new-session","status":"blocked",
                "message":"Runner onverwacht gestopt"]).write(to:sessionStatus,options:.atomic)
            model.refresh()
            precondition(model.sessionPhase == "blocked" && model.sessionAction == "Runner onverwacht gestopt")
            precondition(model.selected == nil, "An early crash cannot resurrect an earlier session's answer")
            record("new-session-1",7,"answered")
            model.refresh()
            precondition(model.selected?.id == "new-session-1")
            print("Widget monitor: live antwoorden, wachtende aanvraag, leespauze en geschiedenis gecontroleerd.")
            return
        }
        if CommandLine.arguments.contains("--check-controls") {
            precondition(JevProbeControls.statusAfterConnection(active:false,wasRunning:false,previous:"Bridge niet bereikbaar") == "")
            precondition(JevProbeControls.statusAfterConnection(active:false,wasRunning:false,previous:"Sleutel niet beschikbaar") == "Sleutel niet beschikbaar")
            precondition(JevProbeControls.statusAfterConnection(active:false,wasRunning:true,previous:"Bridge niet bereikbaar · de set kan nog draaien").contains("gestopt"))
            precondition(JevProbeControls.statusAfterConnection(active:true,wasRunning:false,previous:"Bridge niet bereikbaar") == "")
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
        app.setActivationPolicy(.regular)
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
