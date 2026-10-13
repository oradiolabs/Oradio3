"""Isolated tests for the StateMachine locking/generation fixes in oradio_control.py."""
import ast, sys, threading as real_threading, time, types

SRC = sys.argv[1]
tree = ast.parse(open(SRC).read())
keep = []
for n in tree.body:
    if isinstance(n, ast.ClassDef) and n.name == "StateMachine":
        keep.append(n)
    elif isinstance(n, ast.FunctionDef) and n.name == "run_later":
        keep.append(n)
    elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() and t.id != "HANDLERS" for t in n.targets):
        keep.append(n)

SCALE = 0.05   # all timers run 20x faster

class FastTimer(real_threading.Timer):
    def __init__(self, interval, *a, **k):
        super().__init__(interval * SCALE, *a, **k)

fake_threading = types.SimpleNamespace(**{k: getattr(real_threading, k) for k in dir(real_threading) if not k.startswith("__")})
fake_threading.Timer = FastTimer

log = []
class Log:
    def __getattr__(self, name):
        return lambda msg, *a: log.append((name, msg % a if a else msg))

calls = []
class Leds:
    def __getattr__(self, name):
        return lambda *a: calls.append(("led", name) + a)

class Mpd:
    slow = 0.0
    ready = real_threading.Event()
    play_result = True
    def play(self, preset=None):
        calls.append(("play", preset)); time.sleep(self.slow); return self.play_result if preset else True
    def stop(self): calls.append(("stop",))
    def pause(self): calls.append(("pause",))
    def next(self): calls.append(("next",))
    def is_webradio(self, preset=None): return False
    def wait_for_library(self, timeout=None): return self.ready.wait(timeout * SCALE if timeout else None)

ns = dict(
    threading=fake_threading, leds=Leds(), mpd_control=Mpd(), oradio_log=Log(),
    play_sound=lambda s: calls.append(("sound", s)),
    usb_present=real_threading.Event(), web_service_active=real_threading.Event(),
    oradio_web_service=types.SimpleNamespace(stop=lambda: calls.append(("websvc_stop",))),
    oradio_wifi_service=types.SimpleNamespace(has_connectivity=lambda: True),
    has_internet=lambda: True,
    PLAY_STATES={"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"},
    PRESETS_BY_STATE={"StatePreset1": "Preset1", "StatePreset2": "Preset2", "StatePreset3": "Preset3"},
)
for name in ["LED_PLAY", "LED_STOP", "LED_PRESET1", "LED_PRESET2", "LED_PRESET3",
             "SOUND_PLAY", "SOUND_STOP", "SOUND_NEXT", "SOUND_NO_INTERNET", "SOUND_PRESET_EMPTY",
             "SOUND_USB_ABSENT", "SOUND_START", "SOUND_AP_START", "SOUND_1", "SOUND_2", "SOUND_3"]:
    ns[name] = name
ns["usb_present"].set()
for node in ast.walk(ast.Module(body=keep, type_ignores=[])):
    if isinstance(node, ast.Name) and node.id.isupper() and node.id not in ns:
        ns[node.id] = node.id
for n in keep:
    if isinstance(n, ast.Assign):
        for t in n.targets: ns.pop(t.id, None)
mod = ast.Module(body=keep, type_ignores=[])
exec(compile(ast.fix_missing_locations(mod), SRC, "exec"), ns)
ns["PRESETS"] = {"Preset1": ("LED_PRESET1", "SOUND_1"), "Preset2": ("LED_PRESET2", "SOUND_2"),
                 "Preset3": ("LED_PRESET3", "SOUND_3")}
# run_later in the module uses threading.Timer from ns -> already fast
SM = ns["StateMachine"]
mpd = ns["mpd_control"]

def fresh(state="StateIdle"):
    calls.clear(); log.clear()
    sm = SM(); sm.state = state
    return sm

def settle(t=0.6): time.sleep(t)
results = {}

# T1: Preset1 then Preset2 queued behind a slow handler -> Preset2 is what ends up playing
sm = fresh(); mpd.slow = 0.3; mpd.ready.set()
sm.transition("StatePlay"); time.sleep(0.05)        # slow handler holds task_lock
sm.transition("StatePreset1"); sm.transition("StatePreset2")
settle(1.0)
plays = [c[1] for c in calls if c[0] == "play"]
results["T1 last request wins, overtaken worker skipped"] = (plays == [None, "Preset2"], plays)

# T2: StateError while a Play worker is queued -> Play handler never runs after the error
sm = fresh(); mpd.slow = 0.3
sm.transition("StatePreset1"); time.sleep(0.05)
sm.transition("StatePlay"); sm.transition("StateError")
settle(1.0)
seq = [c[0] if c[0] != "play" else f"play:{c[1]}" for c in calls if c[0] in ("play", "stop")]
results["T2 no play after StateError"] = (seq == ["play:Preset1", "stop"], seq)

# T3: delayed transition already firing while a button is pressed -> button wins
sm = fresh("StateStop"); mpd.slow = 0
sm._handler_generation = sm._generation
with sm._lock:
    sm._arm_delayed_transition("StopToIdle", 4.0, "StateIdle", from_state="StateStop")
    time.sleep(4.0 * SCALE + 0.1)                  # timer fired, its callback waits on _lock
    sm.transition("StatePlay")                     # user presses Play meanwhile
settle(0.3)
results["T3 fired-but-overtaken timer does nothing"] = (sm.state == "StatePlay", sm.state)

# T4: stale handler cannot arm a timer after a newer transition
sm = fresh("StateStop")
sm._handler_generation = sm._generation - 1       # handler from an older generation
sm._arm_delayed_transition("StopToIdle", 4.0, "StateIdle", from_state="StateStop")
results["T4 stale handler does not arm"] = (sm._delayed_timers == {}, list(sm._delayed_timers))

# T5: Stop LED off-timer must not switch off an error blink of a newer state
sm = fresh("StatePlay"); mpd.slow = 0
sm.usb_absent = None
sm.transition("StateStop"); time.sleep(0.05)
ns["usb_present"].clear(); sm.transition("StateUSBAbsent")
time.sleep(4 * SCALE + 0.3)
offs_after = [c for c in calls if c[:2] == ("led", "turn_off_led") and c[2] == "LED_STOP"]
results["T5 old LED timer leaves new blink alone"] = (offs_after == [], offs_after)
ns["usb_present"].set()

# T6: Preset1, Preset2, Preset1 before library ready -> exactly one retry plays, Preset1
sm = fresh(); mpd.ready.clear(); mpd.play_result = None; mpd.slow = 0
for p in ("StatePreset1", "StatePreset2", "StatePreset1"):
    sm.transition(p); time.sleep(0.1)
calls.clear(); mpd.play_result = True; mpd.ready.set()
settle(0.5)
plays = [c[1] for c in calls if c[0] == "play"]
retries = [t.name for t in real_threading.enumerate() if t.name.startswith("preset-retry")]
results["T6 one retry plays, stale ones exit"] = (plays == ["Preset1"] and not retries, (plays, retries))

# T7: stress - concurrent transitions and timers, no exceptions, consistent end state
sm = fresh("StateStop"); mpd.slow = 0.001
errors = []
def hammer(states):
    try:
        for i in range(200):
            sm.transition(states[i % len(states)])
    except Exception as e: errors.append(repr(e))
ts = [real_threading.Thread(target=hammer, args=(s,)) for s in
      (["StateStop", "StatePlay"], ["StateStartUp", "StateIdle"], ["StatePreset1", "StateStop"])]
[t.start() for t in ts]; [t.join() for t in ts]
settle(1.0)
results["T7 stress: no exceptions"] = (not errors and not [l for l in log if l[0] == "error"], errors[:3])

ok = True
for k, (passed, detail) in results.items():
    ok &= passed
    print(("PASS " if passed else "FAIL ") + k + ("" if passed else f"  -> {detail}"))
sys.exit(0 if ok else 1)
